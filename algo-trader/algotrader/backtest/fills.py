"""Pure fill-price functions implementing two-branch stop/target semantics.

ARCHITECTURE §3.2:
  - Entry: signal on bar t → fill at bar t+1 open ± slippage.
  - Stop/target: Branch A (gap at open) fill at open; Branch B (intrabar sweep)
    fill at stop/target ± slippage (conservative).
  - Both stop AND target in same bar → STOP WINS (pessimistic tie-break).
  - Gap-through events are flagged for logging/metrics.

All functions are PURE (no side effects, no I/O).
"""
from __future__ import annotations

from algotrader.core import Bar, ExitReason, Position, Side


def entry_fill(next_bar: Bar, side: Side, slippage: float) -> float:
    """Fill price for a pending entry at the open of *next_bar*.

    Slippage is adverse to the trader:
      BUY  → open + slippage  (pay more)
      SELL → open - slippage  (receive less)

    Args:
        next_bar:  The first complete bar after the signal bar.
        side:      Direction of the entry (BUY or SELL).
        slippage:  Non-negative scalar in price units (e.g. ticks or
                   fraction of ATR).  Computed by SlippageModel from bars
                   strictly before *next_bar*.

    Returns:
        Actual fill price.
    """
    if side is Side.BUY:
        return next_bar.open + slippage
    return next_bar.open - slippage


def stop_fill(
    bar: Bar,
    position: Position,
    slippage: float,
) -> tuple[float, bool]:
    """Fill price when a stop is triggered on *bar*.

    Branch A — gap at open (bar opens BEYOND the stop):
      Fill at bar.open (which may be worse than the stop price).
      gap_through = True.

    Branch B — open is safe, but intrabar range crosses the stop:
      Fill at stop_price ± slippage (adverse to the trader).
      gap_through = False.

    The caller (engine or resolve_bar) guarantees the stop IS triggered;
    raises ValueError if neither branch matches (defensive check).

    Args:
        bar:       The bar on which the stop triggers.
        position:  The open position whose stop is being evaluated.
        slippage:  Non-negative scalar; applied adverse to the position
                   on Branch B only (gap fills have no further slippage).

    Returns:
        (fill_price, gap_through)
    """
    stop = position.stop_price

    if position.side is Side.BUY:
        # Long: stop is below entry; triggered when price falls to/below stop.
        if bar.open <= stop:                     # Branch A: gap down through stop
            return bar.open, True
        if bar.low <= stop:                      # Branch B: intrabar sweep
            return stop - slippage, False

    else:  # SELL (short)
        # Short: stop is above entry; triggered when price rises to/above stop.
        if bar.open >= stop:                     # Branch A: gap up through stop
            return bar.open, True
        if bar.high >= stop:                     # Branch B: intrabar sweep
            return stop + slippage, False

    raise ValueError(
        f"stop_fill called but stop not triggered: "
        f"stop={stop}, bar O/H/L={bar.open}/{bar.high}/{bar.low}, "
        f"side={position.side}"
    )


def target_fill(
    bar: Bar,
    position: Position,
    slippage: float,
) -> float:
    """Fill price when a target is triggered on *bar* (conservative side).

    Branch A — bar opens BEYOND the target (favorable gap):
      Fill at bar.open; the trader receives the gap-improved price.

    Branch B — open is within target, intrabar range hits target:
      Fill at target_price ± slippage (conservative: slippage reduces gain).
        BUY  long → sell at target − slippage
        SELL short → buy at target + slippage

    The caller guarantees target IS triggered.  Raises ValueError otherwise.

    Args:
        bar:       The bar on which the target triggers.
        position:  The open position with a target_price set.
        slippage:  Non-negative scalar; applied adverse on Branch B.

    Returns:
        Actual fill price.
    """
    if position.target_price is None:
        raise ValueError("target_fill called but position.target_price is None")

    target = position.target_price

    if position.side is Side.BUY:
        # Long: target is above entry; triggered when price rises to/above target.
        if bar.open >= target:               # Branch A: gap up through target
            return bar.open
        if bar.high >= target:               # Branch B: intrabar hit
            return target - slippage        # conservative: sell at slightly below target

    else:  # SELL (short)
        # Short: target is below entry; triggered when price falls to/below target.
        if bar.open <= target:               # Branch A: gap down through target
            return bar.open
        if bar.low <= target:                # Branch B: intrabar hit
            return target + slippage        # conservative: cover at slightly above target

    raise ValueError(
        f"target_fill called but target not triggered: "
        f"target={target}, bar O/H/L={bar.open}/{bar.high}/{bar.low}, "
        f"side={position.side}"
    )


def resolve_bar(bar: Bar, position: Position) -> ExitReason | None:
    """Determine which exit (if any) is triggered for *position* on *bar*.

    Checks both stop and target in one pass so the pessimistic tie-break
    (STOP WINS) is applied atomically.

    Stop trigger (BUY long):   bar.open <= stop OR bar.low  <= stop
    Stop trigger (SELL short): bar.open >= stop OR bar.high >= stop

    Target trigger (BUY long):   bar.open >= target OR bar.high >= target
    Target trigger (SELL short): bar.open <= target OR bar.low  <= target

    Returns:
        ExitReason.STOP   if stop triggered (regardless of target).
        ExitReason.TARGET if only target triggered.
        None              if neither triggered.
    """
    stop = position.stop_price
    stop_hit: bool

    if position.side is Side.BUY:
        stop_hit = bar.open <= stop or bar.low <= stop
    else:
        stop_hit = bar.open >= stop or bar.high >= stop

    if stop_hit:
        return ExitReason.STOP          # pessimistic tie-break: stop always wins

    if position.target_price is None:
        return None

    target = position.target_price
    target_hit: bool

    if position.side is Side.BUY:
        target_hit = bar.open >= target or bar.high >= target
    else:
        target_hit = bar.open <= target or bar.low <= target

    return ExitReason.TARGET if target_hit else None
