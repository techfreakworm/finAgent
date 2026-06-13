"""Test fixtures for algo-trader.

Exports deterministic, seeded synthetic NSE 1-min bar generators
(see synthetic.py).  Import directly:

    from tests.fixtures.synthetic import trend_day, range_day, TEST_INSTRUMENT
"""
from tests.fixtures.synthetic import (  # noqa: F401
    TEST_INSTRUMENT,
    NIFTY_FUT_INSTRUMENT,
    trend_day,
    range_day,
    gap_through_price,
    quiet_then_spike,
    multi_session,
)
