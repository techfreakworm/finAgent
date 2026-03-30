"""
FinAgent Configuration
"""

import os
from datetime import datetime
from dataclasses import dataclass, field

# Load .env
from dotenv import load_dotenv
load_dotenv()


@dataclass
class DhanConfig:
    client_id: str = os.getenv("DHAN_CLIENT_ID", "")
    access_token: str = os.getenv("DHAN_ACCESS_TOKEN", "")
    base_url: str = os.getenv("DHAN_BASE_URL", "https://api.dhan.co/v2")


@dataclass
class RiskConfig:
    starting_capital: float = 500_000
    hard_floor: float = 400_000
    max_margin_utilization: float = 0.90  # don't use more than 90% capital as margin
    max_single_trade_loss_pct: float = 0.12  # exit if single trade loses 12% of capital
    max_consecutive_losers: int = 5  # pause strategy after 5 losers in a row


@dataclass
class NiftyConfig:
    security_id: int = 13
    instrument: str = "OPTIDX"
    lot_size_before_nov24: int = 25
    lot_size_after_nov24: int = 75
    lot_change_date: datetime = field(default_factory=lambda: datetime(2024, 11, 20))
    strangle_offset: int = 2  # ATM+2 CE, ATM-2 PE
    min_vix_for_entry: float = 12.0
    weekly_expiry: bool = True

    def lot_size(self, date: datetime) -> int:
        if isinstance(date, str):
            date = datetime.strptime(date, "%Y-%m-%d")
        return self.lot_size_after_nov24 if date >= self.lot_change_date else self.lot_size_before_nov24


@dataclass
class BankNiftyConfig:
    security_id: int = 25
    instrument: str = "OPTIDX"
    lot_size_before_nov24: int = 15
    lot_size_after_nov24: int = 30
    lot_change_date: datetime = field(default_factory=lambda: datetime(2024, 11, 20))
    strangle_offset: int = 2
    monthly_expiry: bool = True  # weekly discontinued post SEBI

    def lot_size(self, date: datetime) -> int:
        if isinstance(date, str):
            date = datetime.strptime(date, "%Y-%m-%d")
        return self.lot_size_after_nov24 if date >= self.lot_change_date else self.lot_size_before_nov24


@dataclass
class EquityConfig:
    universe: list = field(default_factory=lambda: [
        "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
        "SBIN.NS", "BHARTIARTL.NS", "ITC.NS", "KOTAKBANK.NS", "LT.NS",
        "AXISBANK.NS", "SUNPHARMA.NS", "TITAN.NS", "BAJFINANCE.NS", "WIPRO.NS",
        "HCLTECH.NS", "NTPC.NS", "POWERGRID.NS", "M&M.NS", "JSWSTEEL.NS",
    ])
    max_position_pct: float = 0.20  # 20% of capital per stock
    rsi_entry: float = 30.0
    rsi_exit: float = 50.0
    stop_loss_pct: float = -0.05
    max_hold_days: int = 20
    momentum_lookback: int = 12  # months
    momentum_skip: int = 1  # skip last N months
    momentum_top_n: int = 5


@dataclass
class AppConfig:
    dhan: DhanConfig = field(default_factory=DhanConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    nifty: NiftyConfig = field(default_factory=NiftyConfig)
    banknifty: BankNiftyConfig = field(default_factory=BankNiftyConfig)
    equity: EquityConfig = field(default_factory=EquityConfig)
    db_path: str = "finagent.db"
    telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    paper_trading: bool = True  # ALWAYS start in paper mode


config = AppConfig()
