"""
Historical Replay Engine.

Streams pre-fetched candles one by one through the configured strategies,
tracking simulated positions, P&L, and risk in complete isolation from the
live / paper trading path.
"""

import logging
import threading
import time
import uuid
from datetime import datetime
from typing import Callable, Optional

from config import config
from replay.data_provider import ReplayDataProvider
from risk.cost_model import ZerodhaCosts
from risk.floor_monitor import FloorMonitor
from risk.margin_checker import MarginChecker
from risk.position_manager import Position, PositionManager
from strategies.base import Signal
from strategies.equity_mean_reversion import EquityMeanReversion
from strategies.nifty_strangle import NiftyStrangle
from backend.db.models import save_trade, save_signal

logger = logging.getLogger(__name__)


class ReplayEngine:
    """Replay engine that streams historical candles through strategies.

    Parameters
    ----------
    data_provider : ReplayDataProvider
        A fully prepared data provider.
    strategies : list[str]
        Strategy names to execute (``"nifty_strangle"``,
        ``"equity_mean_reversion"``).
    starting_capital : float
        Simulated starting capital in INR.
    hard_floor : float
        Hard-floor capital value for the ``FloorMonitor``.
    account_id : str
        Replay account id (used for DB persistence).
    """

    def __init__(
        self,
        data_provider: ReplayDataProvider,
        strategies: list[str],
        starting_capital: float,
        hard_floor: float,
        account_id: str,
    ) -> None:
        self.data = data_provider
        self.strategies_names = strategies
        self.capital = starting_capital
        self.starting_capital = starting_capital
        self.hard_floor = hard_floor
        self.account_id = account_id

        # Playback state
        self.current_index: int = 0
        self.speed: int = 1  # candles per second
        self.playing: bool = False
        self.paused: bool = False
        self._pause_event = threading.Event()
        self._pause_event.set()  # not paused initially
        self._stop_flag: bool = False
        self._thread: Optional[threading.Thread] = None

        # Isolated trading state
        self.floor_monitor = FloorMonitor(hard_floor, starting_capital)
        self.margin_checker = MarginChecker(starting_capital)
        self.position_manager = PositionManager()

        # Strategy instances
        self._strategy_instances: dict = {}
        if "nifty_strangle" in strategies:
            self._strategy_instances["nifty_strangle"] = NiftyStrangle()
        if "equity_mean_reversion" in strategies:
            self._strategy_instances["equity_mean_reversion"] = EquityMeanReversion()

        # History tracking
        self.signals_history: list[dict] = []
        self.trades_history: list[dict] = []
        self.equity_curve: list[tuple[float, Optional[str]]] = [
            (starting_capital, None),
        ]

        # Event callback — set by the API layer: fn(event_type, data)
        self.on_event: Optional[Callable[[str, dict], None]] = None

        logger.info(
            "ReplayEngine initialised: strategies=%s  capital=%.0f  "
            "floor=%.0f  account=%s  total_candles=%d",
            strategies, starting_capital, hard_floor, account_id,
            data_provider.get_total(),
        )

    # ------------------------------------------------------------------
    # Playback controls
    # ------------------------------------------------------------------

    def play(self) -> None:
        """Start (or restart) streaming in a background thread."""
        if self.playing:
            logger.warning("Replay already playing")
            return
        self._stop_flag = False
        self.playing = True
        self.paused = False
        self._pause_event.set()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("Replay playback started at speed=%d", self.speed)

    def pause(self) -> None:
        """Pause playback (blocks the streaming thread)."""
        self._pause_event.clear()
        self.paused = True
        logger.info("Replay paused at index %d", self.current_index)

    def resume(self) -> None:
        """Resume from a paused state."""
        self._pause_event.set()
        self.paused = False
        logger.info("Replay resumed from index %d", self.current_index)

    def stop(self) -> None:
        """Stop playback entirely."""
        self._stop_flag = True
        self._pause_event.set()  # unblock if paused
        self.playing = False
        self.paused = False
        logger.info("Replay stopped at index %d", self.current_index)

    def step(self) -> None:
        """Advance exactly one candle (manual stepping)."""
        if self.current_index < self.data.get_total():
            self._process_candle(self.current_index)
            self.current_index += 1
        else:
            logger.info("Step: already at end of data")

    def set_speed(self, speed: int) -> None:
        """Set playback speed (candles per second, 1-100)."""
        self.speed = max(1, min(100, speed))
        logger.info("Replay speed set to %d candles/sec", self.speed)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        """Background thread: process candles sequentially."""
        try:
            total = self.data.get_total()
            while self.current_index < total and not self._stop_flag:
                self._pause_event.wait()  # blocks while paused
                if self._stop_flag:
                    break

                self._process_candle(self.current_index)
                self.current_index += 1

                if self.speed > 0:
                    time.sleep(1.0 / self.speed)

        except Exception as exc:
            logger.exception("Replay loop crashed: %s", exc)
            self._emit("error", {"message": str(exc)})
        finally:
            self.playing = False
            self._emit("complete", self.get_summary())
            logger.info("Replay loop finished at index %d", self.current_index)

    # ------------------------------------------------------------------
    # Core candle processing
    # ------------------------------------------------------------------

    def _process_candle(self, idx: int) -> None:
        """Run all strategies on a single candle and update state."""
        candle = self.data.get_candle(idx)
        view = self.data.get_view_up_to(idx)
        timestamp = candle.get("timestamp")
        ts_dt = self._to_datetime(timestamp)

        # ------ Floor check ------
        unrealized = self.position_manager.get_unrealized_pnl()
        floor_status = self.floor_monitor.check_mtm(unrealized)
        floor_breached = floor_status["breached"]

        if floor_breached:
            self._force_close_all(ts_dt, candle, "floor_breach")
            self._emit("floor_breach", {
                "index": idx,
                "timestamp": str(timestamp),
                "projected": floor_status["projected"],
                "floor": floor_status["floor"],
            })

        # ------ Update existing positions with current prices ------
        self._update_position_prices(candle, ts_dt)

        # ------ Check exits on open positions ------
        self._check_exits(candle, ts_dt, floor_breached)

        # ------ Generate new signals (only if floor is intact) ------
        if not floor_breached:
            self._generate_and_execute_signals(candle, view, ts_dt)

        # ------ Track equity curve ------
        total_unrealized = self.position_manager.get_unrealized_pnl()
        equity = self.capital + total_unrealized
        self.equity_curve.append((equity, str(timestamp)))

        # ------ Emit candle event ------
        open_positions = self.position_manager.get_open_positions()
        self._emit("candle", {
            "index": idx,
            "total": self.data.get_total(),
            "timestamp": str(timestamp),
            "candle": candle,
            "portfolio": {
                "capital": round(self.capital, 2),
                "equity": round(equity, 2),
                "unrealized_pnl": round(total_unrealized, 2),
                "positions": len(open_positions),
                "floor_breached": floor_breached,
            },
        })

    # ------------------------------------------------------------------
    # Signal generation per strategy
    # ------------------------------------------------------------------

    def _generate_and_execute_signals(
        self, candle: dict, view: dict, ts_dt: datetime,
    ) -> None:
        """Run each active strategy and execute viable signals."""
        for name, strategy in self._strategy_instances.items():
            try:
                signals = self._run_strategy(name, strategy, candle, view, ts_dt)
                for signal in signals:
                    self._try_execute_signal(signal, candle, ts_dt)
            except Exception as exc:
                logger.error("Strategy %s error: %s", name, exc)

    def _run_strategy(
        self,
        name: str,
        strategy,
        candle: dict,
        view: dict,
        ts_dt: datetime,
    ) -> list[Signal]:
        """Prepare strategy-specific data dict and call generate_signals."""
        open_positions = [
            p for p in self.position_manager.get_open_positions()
            if p.strategy == name
        ]

        if name == "nifty_strangle":
            nifty = candle.get("nifty", {})
            options = candle.get("options", {})
            vix = candle.get("vix", 0)
            spot = options.get("spot") or nifty.get("c", 0)

            # Build a minimal option-chain-like dict for the strategy.
            # NiftyStrangle.generate_signals expects an option_chain arg, but in
            # replay mode we only have ATM CE/PE close prices from the rolling
            # option API.  Build a synthetic chain with just enough info.
            ce_premium = options.get("ce_close", 0)
            pe_premium = options.get("pe_close", 0)

            if ce_premium == 0 and pe_premium == 0:
                return []

            # We cannot call the live option chain helpers, so we generate the
            # signal directly from the available data.
            signals = self._nifty_strangle_replay_signals(
                spot=spot,
                vix=vix,
                ce_premium=ce_premium,
                pe_premium=pe_premium,
                ts_dt=ts_dt,
                open_positions=open_positions,
            )
            return signals

        elif name == "equity_mean_reversion":
            # Only generate equity signals at the daily close — NOT every
            # intraday candle.  Check if this is the last candle of the day
            # (next candle is a different date, or this is the last candle).
            total = self.data.get_total()
            current_idx = self.current_index
            is_last_candle_of_day = True

            if current_idx + 1 < total:
                next_candle = self.data.get_candle(current_idx + 1)
                next_ts = self._to_datetime(next_candle.get("timestamp"))
                if next_ts and ts_dt and next_ts.date() == ts_dt.date():
                    is_last_candle_of_day = False

            if not is_last_candle_of_day:
                return []  # Skip — not end of day yet

            # Build per-stock DataFrame slices from the view
            stock_prices: dict = {}
            for sym in config.equity.universe:
                key = f"stock_{sym}"
                df = view.get(key)
                if df is not None and len(df) >= 20:
                    stock_prices[sym] = df

            data_dict = {
                "stock_prices": stock_prices,
                "capital": self.capital,
                "current_date": ts_dt,
            }
            return strategy.generate_signals(data_dict)

        return []

    def _nifty_strangle_replay_signals(
        self,
        spot: float,
        vix: float,
        ce_premium: float,
        pe_premium: float,
        ts_dt: datetime,
        open_positions: list,
    ) -> list[Signal]:
        """Generate a NiftyStrangle signal from replay option data.

        In replay mode we do not have a full option chain, so we build the
        signal directly from ATM CE/PE premiums fetched via the rolling
        option API.
        """
        cfg = config.nifty

        # Conditions
        if vix < cfg.min_vix_for_entry:
            return []
        if ts_dt.weekday() != 0:  # Monday
            return []
        if open_positions:
            return []
        if ce_premium <= 0 and pe_premium <= 0:
            return []

        total_premium = ce_premium + pe_premium
        lot_size = cfg.lot_size(ts_dt)
        margin = spot * lot_size * max(0.12, vix / 100 * 0.8)
        stop_loss = total_premium * 2

        signal = Signal(
            strategy="nifty_strangle",
            symbol="NIFTY",
            direction="SELL",
            entry_price=total_premium,
            stop_loss=stop_loss,
            target=0,
            lot_size=lot_size,
            margin_required=margin,
            confidence=min(0.9, vix / 20),
            reasoning=(
                f"Replay strangle: sell ATM CE @ {ce_premium:.1f} + "
                f"ATM PE @ {pe_premium:.1f}. VIX={vix:.1f}, "
                f"Spot={spot:.0f}. Total premium={total_premium:.1f}/unit."
            ),
            timestamp=ts_dt,
            metadata={
                "ce_premium": ce_premium,
                "pe_premium": pe_premium,
                "vix": vix,
                "spot": spot,
            },
        )
        return [signal]

    # ------------------------------------------------------------------
    # Signal execution
    # ------------------------------------------------------------------

    def _try_execute_signal(
        self, signal: Signal, candle: dict, ts_dt: datetime,
    ) -> None:
        """Check margin, then open a position for the signal."""
        # Margin check
        if not self.margin_checker.can_trade(signal.margin_required):
            logger.info(
                "Signal rejected (margin): %s %s margin=%.0f",
                signal.strategy, signal.symbol, signal.margin_required,
            )
            self._record_signal(signal, status="REJECTED_MARGIN")
            return

        # Floor proximity check
        unrealized = self.position_manager.get_unrealized_pnl()
        floor_status = self.floor_monitor.check_mtm(unrealized)
        if floor_status["action"] == "EXIT_ALL":
            logger.info("Signal rejected (floor proximity): %s", signal.symbol)
            self._record_signal(signal, status="REJECTED_FLOOR")
            return

        # Open the position
        direction = "SHORT" if signal.direction == "SELL" else "LONG"
        position = Position(
            symbol=signal.symbol,
            strategy=signal.strategy,
            entry_date=ts_dt,
            entry_price=signal.entry_price,
            quantity=signal.lot_size,
            direction=direction,
            stop_loss=signal.stop_loss,
            target=signal.target,
            current_price=signal.entry_price,
            metadata=signal.metadata,
        )
        pos_id = self.position_manager.add_position(position)

        self._record_signal(signal, status="EXECUTED")

        # Emit signal event
        self._emit("signal", {
            "strategy": signal.strategy,
            "symbol": signal.symbol,
            "direction": signal.direction,
            "entry_price": signal.entry_price,
            "lot_size": signal.lot_size,
            "margin": signal.margin_required,
            "confidence": signal.confidence,
            "reasoning": signal.reasoning,
            "position_id": pos_id,
            "timestamp": str(ts_dt),
        })

        logger.info(
            "Position opened: %s %s %s @ %.2f x %d (id=%s)",
            signal.strategy, signal.direction, signal.symbol,
            signal.entry_price, signal.lot_size, pos_id,
        )

    # ------------------------------------------------------------------
    # Position updates and exit checks
    # ------------------------------------------------------------------

    def _update_position_prices(self, candle: dict, ts_dt: datetime) -> None:
        """Mark-to-market all open positions with current candle data."""
        for pos in self.position_manager.get_open_positions():
            if pos.strategy == "nifty_strangle":
                options = candle.get("options", {})
                ce_price = options.get("ce_close", 0)
                pe_price = options.get("pe_close", 0)
                current_combined = ce_price + pe_price
                if current_combined > 0:
                    pos.current_price = current_combined

            elif pos.strategy == "equity_mean_reversion":
                stocks = candle.get("stocks", {})
                stock_data = stocks.get(pos.symbol, {})
                close = stock_data.get("close", 0)
                if close > 0:
                    pos.current_price = close

    def _check_exits(
        self, candle: dict, ts_dt: datetime, floor_breached: bool,
    ) -> None:
        """Check every open position for exit conditions."""
        for pos in list(self.position_manager.get_open_positions()):
            should_exit, reason = self._check_position_exit(
                pos, candle, ts_dt, floor_breached,
            )
            if should_exit:
                self._close_position(pos, reason, candle, ts_dt)

    def _check_position_exit(
        self,
        pos: Position,
        candle: dict,
        ts_dt: datetime,
        floor_breached: bool,
    ) -> tuple[bool, str]:
        """Delegate exit check to the appropriate strategy."""
        if pos.strategy == "nifty_strangle":
            strategy = self._strategy_instances.get("nifty_strangle")
            if not strategy:
                return False, ""
            options = candle.get("options", {})
            current_data = {
                "current_date": ts_dt,
                "ce_current_premium": options.get("ce_close", 0),
                "pe_current_premium": options.get("pe_close", 0),
                "floor_breached": floor_breached,
            }
            return strategy.should_exit(pos, current_data)

        elif pos.strategy == "equity_mean_reversion":
            strategy = self._strategy_instances.get("equity_mean_reversion")
            if not strategy:
                return False, ""
            stocks = candle.get("stocks", {})
            stock_data = stocks.get(pos.symbol, {})
            current_data = {
                "current_rsi": stock_data.get("rsi", 50),
                "current_price": stock_data.get("close", 0),
                "current_date": ts_dt,
            }
            return strategy.should_exit(pos, current_data)

        return False, ""

    def _close_position(
        self,
        pos: Position,
        reason: str,
        candle: dict,
        ts_dt: datetime,
    ) -> None:
        """Close a position, calculate costs, update capital, persist trade."""
        exit_price = pos.current_price

        # Close in PositionManager
        trade_record = self.position_manager.close_position(
            pos.position_id, exit_price, reason,
        )

        # Calculate transaction costs
        cost = self._calculate_trade_cost(pos, exit_price)
        slippage = ZerodhaCosts.estimate_slippage(
            pos.entry_price, pos.quantity,
            n_legs=4 if pos.strategy == "nifty_strangle" else 2,
            emergency=(reason == "floor_breach"),
        )

        pnl_gross = trade_record["pnl"]
        pnl_net = pnl_gross - cost - slippage

        # Update capital
        self.capital += pnl_net
        self.floor_monitor.update_capital(self.capital)
        self.margin_checker.update_capital(self.capital)

        # Build full trade dict for DB
        trade_db = {
            "position_id": pos.position_id,
            "strategy": pos.strategy,
            "symbol": pos.symbol,
            "direction": pos.direction,
            "entry_date": str(pos.entry_date),
            "exit_date": str(ts_dt),
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "quantity": pos.quantity,
            "pnl_gross": round(pnl_gross, 2),
            "cost": round(cost, 2),
            "slippage": round(slippage, 2),
            "pnl_net": round(pnl_net, 2),
            "exit_reason": reason,
            "margin_used": pos.entry_price * pos.quantity,
            "metadata": pos.metadata,
        }

        self.trades_history.append(trade_db)

        # Persist to DB
        try:
            save_trade(trade_db, self.account_id)
        except Exception as exc:
            logger.error("Failed to save trade to DB: %s", exc)

        # Emit trade event
        self._emit("trade", {
            "position_id": pos.position_id,
            "strategy": pos.strategy,
            "symbol": pos.symbol,
            "direction": pos.direction,
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "quantity": pos.quantity,
            "pnl_net": round(pnl_net, 2),
            "cost": round(cost, 2),
            "slippage": round(slippage, 2),
            "exit_reason": reason,
            "capital_after": round(self.capital, 2),
            "timestamp": str(ts_dt),
        })

        logger.info(
            "Trade closed: %s %s pnl_net=%.2f reason=%s capital=%.2f",
            pos.strategy, pos.symbol, pnl_net, reason, self.capital,
        )

    def _calculate_trade_cost(self, pos: Position, exit_price: float) -> float:
        """Calculate round-trip transaction cost for a closed position."""
        if pos.strategy == "nifty_strangle":
            # Strangle: 4 legs (sell CE + sell PE entry, buy CE + buy PE exit)
            # We approximate: split entry/exit premium equally between CE and PE
            entry_leg = pos.entry_price * pos.quantity / 2
            exit_leg = exit_price * pos.quantity / 2
            return ZerodhaCosts.strangle_round_trip_cost(
                ce_sell_val=entry_leg,
                pe_sell_val=entry_leg,
                ce_buy_val=exit_leg,
                pe_buy_val=exit_leg,
            )
        elif pos.strategy == "equity_mean_reversion":
            buy_value = pos.entry_price * pos.quantity
            sell_value = exit_price * pos.quantity
            return ZerodhaCosts.delivery_round_trip_cost(buy_value, sell_value)
        return 0.0

    def _force_close_all(
        self, ts_dt: datetime, candle: dict, reason: str,
    ) -> None:
        """Emergency close all open positions (floor breach)."""
        for pos in list(self.position_manager.get_open_positions()):
            self._close_position(pos, reason, candle, ts_dt)
        logger.critical(
            "All positions force-closed: reason=%s capital=%.2f",
            reason, self.capital,
        )

    # ------------------------------------------------------------------
    # Signal recording
    # ------------------------------------------------------------------

    def _record_signal(self, signal: Signal, status: str = "PENDING") -> None:
        """Persist signal to history and DB."""
        signal_dict = {
            "strategy": signal.strategy,
            "symbol": signal.symbol,
            "direction": signal.direction,
            "entry_price": signal.entry_price,
            "stop_loss": signal.stop_loss,
            "target": signal.target,
            "lot_size": signal.lot_size,
            "margin_required": signal.margin_required,
            "confidence": signal.confidence,
            "reasoning": signal.reasoning,
            "metadata": signal.metadata,
            "status": status,
        }
        self.signals_history.append(signal_dict)

        try:
            save_signal(signal_dict, self.account_id)
        except Exception as exc:
            logger.error("Failed to save signal to DB: %s", exc)

    # ------------------------------------------------------------------
    # Event emission
    # ------------------------------------------------------------------

    def _emit(self, event_type: str, data: dict) -> None:
        """Dispatch an event to the registered callback."""
        if self.on_event:
            try:
                self.on_event(event_type, data)
            except Exception as exc:
                logger.error("Event callback error (%s): %s", event_type, exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_datetime(ts) -> datetime:
        """Convert various timestamp types to a plain datetime."""
        if isinstance(ts, datetime):
            return ts
        if hasattr(ts, "to_pydatetime"):
            return ts.to_pydatetime()
        try:
            return datetime.fromisoformat(str(ts))
        except Exception:
            return datetime.now()

    # ------------------------------------------------------------------
    # State / summary queries
    # ------------------------------------------------------------------

    def get_state(self) -> dict:
        """Return the current replay state snapshot.

        Returns
        -------
        dict
            Keys: ``capital``, ``equity``, ``unrealized_pnl``,
            ``positions``, ``signals_count``, ``trades_count``,
            ``current_index``, ``total_candles``, ``playing``, ``paused``.
        """
        unrealized = self.position_manager.get_unrealized_pnl()
        open_positions = self.position_manager.get_open_positions()
        return {
            "capital": round(self.capital, 2),
            "equity": round(self.capital + unrealized, 2),
            "unrealized_pnl": round(unrealized, 2),
            "positions": [
                {
                    "position_id": p.position_id,
                    "symbol": p.symbol,
                    "strategy": p.strategy,
                    "direction": p.direction,
                    "entry_price": p.entry_price,
                    "current_price": p.current_price,
                    "quantity": p.quantity,
                    "unrealized_pnl": round(p.unrealized_pnl, 2),
                }
                for p in open_positions
            ],
            "signals_count": len(self.signals_history),
            "trades_count": len(self.trades_history),
            "current_index": self.current_index,
            "total_candles": self.data.get_total(),
            "playing": self.playing,
            "paused": self.paused,
        }

    def get_summary(self) -> dict:
        """Return a full summary after replay ends.

        Returns
        -------
        dict
            Keys: ``starting_capital``, ``final_capital``, ``total_pnl``,
            ``total_pnl_pct``, ``total_trades``, ``winners``, ``losers``,
            ``win_rate``, ``max_drawdown``, ``max_drawdown_pct``,
            ``strategy_stats``, ``equity_curve``.
        """
        total_trades = len(self.trades_history)
        winners = sum(1 for t in self.trades_history if (t.get("pnl_net", 0) or 0) > 0)
        losers = total_trades - winners
        total_pnl = self.capital - self.starting_capital
        total_pnl_pct = (total_pnl / self.starting_capital * 100) if self.starting_capital else 0

        # Calculate max drawdown from equity curve
        max_drawdown = 0.0
        peak = self.starting_capital
        for equity_val, _ in self.equity_curve:
            if equity_val > peak:
                peak = equity_val
            dd = peak - equity_val
            if dd > max_drawdown:
                max_drawdown = dd
        max_drawdown_pct = (max_drawdown / peak * 100) if peak else 0

        # Per-strategy stats
        strategy_stats = {}
        for name in self.strategies_names:
            stats = self.position_manager.get_strategy_stats(name)
            strategy_stats[name] = stats

        # Downsample equity curve for transmission (max 500 points)
        curve = self.equity_curve
        if len(curve) > 500:
            step = len(curve) // 500
            curve = curve[::step]

        return {
            "starting_capital": self.starting_capital,
            "final_capital": round(self.capital, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl_pct, 2),
            "total_trades": total_trades,
            "winners": winners,
            "losers": losers,
            "win_rate": round(winners / total_trades, 4) if total_trades else 0,
            "max_drawdown": round(max_drawdown, 2),
            "max_drawdown_pct": round(max_drawdown_pct, 2),
            "strategy_stats": strategy_stats,
            "equity_curve": [
                {"equity": round(eq, 2), "timestamp": ts}
                for eq, ts in curve
            ],
            "trades": self.trades_history[-50:],  # last 50 trades
        }
