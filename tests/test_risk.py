"""Risk cap, position sizing, and the reward:risk gate (LOGIC.md §16)."""

import pytest

from src.config import DEFAULT_CONFIG as CFG
from src.risk import position_size, rr_multiple, rr_ok, RiskCapExceeded


def test_risk_cap_is_hard():
    with pytest.raises(RiskCapExceeded):
        position_size(10_000, entry=2000, stop=1990, cfg=CFG, risk_per_trade=0.05)


def test_position_size_formula():
    # risk 1% of 10k = $100; stop distance 10; contract 100 -> 0.1 units
    s = position_size(10_000, entry=2000, stop=1990, cfg=CFG)
    assert s.risk_amount == pytest.approx(100.0)
    assert s.stop_distance == pytest.approx(10.0)
    assert s.units == pytest.approx(100.0 / (10.0 * CFG.contract_value))


def test_zero_stop_distance_is_safe():
    s = position_size(10_000, entry=2000, stop=2000, cfg=CFG)
    assert s.units == 0.0


def test_rr_gate():
    # LONG entry 2000 stop 1990 (risk 10), tp 2020 (reward 20) -> RR 2.0
    assert rr_multiple(2000, 1990, 2020, "LONG") == pytest.approx(2.0)
    assert rr_ok(2000, 1990, 2020, "LONG", CFG)
    # reward only 1R -> below the 1.5 gate
    assert not rr_ok(2000, 1990, 2010, "LONG", CFG)


def test_rr_invalid_geometry():
    assert rr_multiple(2000, 2000, 2020, "LONG") == 0.0   # zero stop distance
    # tp below entry on a LONG is not reward -> clamped to 0
    assert rr_multiple(2000, 1990, 1995, "LONG") == 0.0
    # tp 5 above entry, risk 10 -> 0.5R
    assert rr_multiple(2000, 1990, 2005, "LONG") == pytest.approx(0.5)
