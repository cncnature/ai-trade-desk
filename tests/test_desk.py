import os
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from desk import validate  # noqa: E402
from desk.broker.paper import PaperBroker  # noqa: E402
from desk.config import CFG  # noqa: E402
from desk.data import SyntheticSource, load_stock  # noqa: E402
from desk.factors import build_panel, forward_return  # noqa: E402


@pytest.fixture(autouse=True)
def tmp_state(tmp_path, monkeypatch):
    monkeypatch.setattr(CFG, "state_dir", tmp_path / "state")
    monkeypatch.setattr(CFG, "cache_dir", tmp_path / "cache")
    monkeypatch.setattr(CFG, "docs_data_dir", tmp_path / "docs")


def _panel(effect):
    src = SyntheticSource(effect=effect)
    frames = {s.code: load_stock(src, s.code, 600) for s in src.universe(50)}
    return build_panel(frames, CFG.hold_days, CFG.cost_roundtrip)


def test_gate_finds_planted_effect():
    v = validate.run(_panel(0.0003))
    assert "frgn_hold" in v["passed"]


def test_gate_rejects_pure_noise():
    v = validate.run(_panel(0.0))
    # 효과가 없는 시장에서는 (다중검정 보정 덕분에) 통과 팩터가 거의 없어야 한다
    assert len(v["passed"]) <= 1


def test_confirm_uses_are_logged():
    p = _panel(0.0003)
    validate.run(p)
    v = validate.run(p)
    row = next(r for r in v["factors"] if r["factor"] == "frgn_hold")
    assert row["confirm"]["uses"] == 2


def test_forward_return_has_no_lookahead_on_signal_day():
    df = pd.DataFrame({"open": [100, 110, 120, 130], "close": [105, 115, 125, 135]},
                      index=pd.bdate_range("2026-01-01", periods=4))
    fr = forward_return(df, hold=2, cost=0.0)
    # t=0 신호 → t+1 시가 110 매수, t+2 종가 125 매도
    assert fr.iloc[0] == pytest.approx(125 / 110 - 1)


def _pick(stop=95, target=110, hold=3):
    return {"code": "000001", "name": "A", "qty": 10, "entry_ref": 100, "stop": stop, "target": target, "hold_days": hold}


def test_paper_stop_has_priority_and_gap():
    b = PaperBroker(equity=1_000_000)
    b.queue([_pick()])
    b.process_day("d1", {"000001": {"open": 100, "high": 101, "low": 99, "close": 100}}, 0.0)
    ev = b.process_day("d2", {"000001": {"open": 90, "high": 120, "low": 85, "close": 100}}, 0.0)
    sell = [e for e in ev if e["type"] == "SELL"][0]
    assert sell["reason"] == "손절" and sell["exit"] == 90  # 갭하락은 시가 체결, 목표 동시 터치여도 손절


def test_paper_time_exit():
    b = PaperBroker(equity=1_000_000)
    b.queue([_pick(hold=2)])
    bar = {"000001": {"open": 100, "high": 101, "low": 99, "close": 100}}
    b.process_day("d1", bar, 0.0)
    ev = b.process_day("d2", bar, 0.0)
    assert ev and ev[0]["reason"] == "시간청산"


def test_live_order_locked_without_phrase(monkeypatch):
    for k in ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setattr(CFG, "live_confirm", "")
    from desk.broker.kis import KISBroker

    with pytest.raises(PermissionError):
        KISBroker("live").order("005930", 1, "buy")
