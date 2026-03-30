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
    # Full NIFTY 50 universe — {NSE_SYMBOL: dhan_security_id}
    universe_map: dict = field(default_factory=lambda: {
        "ADANIPORTS": 15083, "APOLLOHOSP": 157, "ASIANPAINT": 236,
        "AXISBANK": 5900, "BAJAJ-AUTO": 16669, "BAJFINANCE": 317,
        "BAJAJFINSV": 16675, "BEL": 383, "BPCL": 526, "BHARTIARTL": 10604,
        "BRITANNIA": 547, "CIPLA": 694, "COALINDIA": 20374, "DRREDDY": 881,
        "EICHERMOT": 910, "ETERNAL": 5097, "GRASIM": 1232, "HCLTECH": 7229,
        "HDFCBANK": 1333, "HDFCLIFE": 467, "HEROMOTOCO": 1348, "HINDALCO": 1363,
        "HINDUNILVR": 1394, "ICICIBANK": 4963, "INDUSINDBK": 5258, "INFY": 1594,
        "ITC": 10453, "JSWSTEEL": 11723, "KOTAKBANK": 1922, "LT": 11483,
        "M&M": 2031, "MARUTI": 10999, "NESTLEIND": 17963, "NTPC": 11630,
        "ONGC": 2475, "POWERGRID": 14977, "RELIANCE": 2885, "SBILIFE": 21808,
        "SBIN": 3045, "SHRIRAMFIN": 4306, "SUNPHARMA": 14788,
        "TATACONSUM": 3432, "TATAMOTORS": 3456, "TATASTEEL": 3499,
        "TCS": 11536, "TECHM": 13538, "TITAN": 3506, "TRENT": 1964,
        "ULTRACEMCO": 11532, "WIPRO": 3787,
    })
    niftybees_security_id: int = 10576  # NIFTYBEES ETF for index exposure
    india_vix_security_id: int = 21
    # Legacy: list of symbols for backward compatibility
    universe: list = field(default_factory=lambda: [])
    max_position_pct: float = 0.20  # 20% of capital per stock
    rsi_entry: float = 30.0
    rsi_exit: float = 55.0
    stop_loss_pct: float = -0.10
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

    def __post_init__(self):
        # Populate legacy universe list from universe_map
        if not self.equity.universe:
            self.equity.universe = list(self.equity.universe_map.keys())


config = AppConfig()
