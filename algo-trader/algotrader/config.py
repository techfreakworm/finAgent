"""Central configuration for the intraday algo-trader.

All money-relevant defaults are conservative and overridable from the
operator-confirmed parameters. Times are IST (Asia/Kolkata) — NSE session.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

IST = "Asia/Kolkata"

# NSE intraday session landmarks (30s grace buffers per ops review:
# Dhan RMS admin square-off starts ~15:19; never race it)
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)
VOLUNTARY_EXIT_FROM = time(15, 15)        # strategies begin winding down
HARD_FLAT = time(15, 19, 30)              # force-flat everything by here
NO_NEW_ENTRIES_AFTER = time(14, 44, 30)


@dataclass(frozen=True)
class DhanConfig:
    client_id: str = os.getenv("DHAN_CLIENT_ID", "")
    access_token: str = os.getenv("DHAN_ACCESS_TOKEN", "")
    base_url: str = os.getenv("DHAN_BASE_URL", "https://api.dhan.co/v2")

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.access_token)


@dataclass(frozen=True)
class RiskConfig:
    """Operator-tunable risk parameters (suggested defaults pending confirmation)."""
    capital: float = 500_000.0           # ₹5L (finAgent assumption — confirm)
    hard_floor: float = 400_000.0        # force-flat + halt below this
    max_daily_loss_pct: float = 0.02     # 2% of capital → circuit breaker
    per_trade_risk_pct: float = 0.0075   # 0.75% of capital risked per trade
    max_open_positions: int = 3
    max_margin_util: float = 0.90


@dataclass(frozen=True)
class SafetyConfig:
    """Money guardrail. Live trading is structurally off.

    Every code path that could place a real order must check `live_enabled`
    AND receive an explicit per-order operator approval token. Paper mode is
    the only default execution path.
    """
    _SENTINEL = "i-approve-live-trading"
    live_env: str = os.getenv("ALGOTRADER_LIVE_ENABLE", "")

    @property
    def live_enabled(self) -> bool:
        return self.live_env == self._SENTINEL


@dataclass(frozen=True)
class Paths:
    cache: Path = PROJECT_ROOT / "data" / "cache"
    instruments: Path = PROJECT_ROOT / "data" / "instruments"
    reports: Path = PROJECT_ROOT / "reports"


@dataclass(frozen=True)
class Config:
    dhan: DhanConfig = field(default_factory=DhanConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    paths: Paths = field(default_factory=Paths)


def load() -> Config:
    cfg = Config()
    for p in (cfg.paths.cache, cfg.paths.instruments, cfg.paths.reports):
        p.mkdir(parents=True, exist_ok=True)
    return cfg
