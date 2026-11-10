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


def test_zero_open_rows_are_cleaned_and_no_inf():
    from desk.data import clean_ohlcv

    df = pd.DataFrame({"open": [100, 0, 102, 103], "high": [101, 0, 103, 104], "low": [99, 0, 101, 102],
                       "close": [100, 101, 102, 103], "volume": [1, 0, 1, 1]},
                      index=pd.bdate_range("2026-01-01", periods=4)).astype(float)
    c = clean_ohlcv(df)
    assert (c["open"] > 0).all()
    fr = forward_return(df, hold=1, cost=0.0)
    assert not fr.isin([float("inf"), float("-inf")]).any()


def test_missing_flow_is_nan_not_zero(monkeypatch):
    src = SyntheticSource(effect=0.0)
    monkeypatch.setattr(src, "flow", lambda code, pages: (_ for _ in ()).throw(RuntimeError("blocked")))
    df = load_stock(src, "000001", 300)
    assert df["frgn_net"].isna().all() and df["frgn_hold_pct"].isna().all()


def test_ex_dates_match_2023_year_end():
    from desk.events import ex_dates

    # 2023-12: 25일(성탄)·29일(연말 휴장) 휴장 → 기준일 12/31 이면 마지막 매수일 12/26, 배당락 12/27
    cal = pd.DatetimeIndex([d for d in pd.bdate_range("2023-12-18", "2024-01-05")
                            if d.strftime("%m-%d") not in ("12-25", "12-29") and d.strftime("%m-%d") != "01-01"])
    lb, ex = ex_dates(pd.Timestamp("2023-12-31"), cal)
    assert str(cal[lb].date()) == "2023-12-26" and str(cal[ex].date()) == "2023-12-27"


def test_dividend_event_gate_detects_runup(tmp_path, monkeypatch):
    import json as _json

    from desk import events

    src = SyntheticSource(effect=0.0)
    frames = {s.code: load_stock(src, s.code, 600) for s in src.universe(30)}
    cal = next(iter(frames.values())).index
    ev, rng = [], __import__("numpy").random.default_rng(3)
    for k, rec in enumerate(cal[30:-10:12]):
        for code in rng.choice(list(frames), 4, replace=False):
            ev.append({"code": code, "rcept_no": f"R{k}{code}", "disclosed": (rec - pd.Timedelta(days=20)).strftime("%Y%m%d"),
                       "record_date": str(rec.date()), "dps": 500, "kind": "결산배당", "report": "현금ㆍ현물배당결정"})
            # 배당락 전 3일 상승을 심는다
            lb, ex = events.ex_dates(rec, cal)
            f = frames[code]
            f.loc[cal[ex - 3]:cal[lb], ["close", "high"]] *= 1.04
    p = tmp_path / "events.json"
    p.write_text(_json.dumps(ev))
    monkeypatch.setattr(events, "EVENTS", p)
    r = events.run(frames, cal)
    assert r["dates"] >= 20 and r["status"] == "통과", r


def test_h5_selects_top_hold_and_grades():
    from desk import h5

    src = SyntheticSource(effect=0.0003)
    frames = {s.code: load_stock(src, s.code, 400) for s in src.universe(40)}
    panel = build_panel(frames, CFG.hold_days, CFG.cost_roundtrip)
    names = {c: (c, "") for c in frames}
    kospi = src.ohlcv("KOSPI", 400)["close"]
    for d in sorted(panel["date"].unique())[-40:-20]:  # 과거 날짜를 기록해 채점이 되는지
        sub = panel[panel["date"] <= d]
        h5.run(sub, names, kospi.shift(-10) / kospi - 1)
    r = h5.run(panel, names, kospi.shift(-10) / kospi - 1)
    last = panel[panel["date"] == panel["date"].max()]
    cut = last["frgn_hold"].quantile(0.8)
    assert r["picks"] and all(last.set_index("code").loc[p["code"], "frgn_hold"] >= cut - 1e-9 for p in r["picks"])
    assert r["score"]["days"] >= 15


class _FakeSrc:
    def __init__(self, px):
        self.px = px

    def ohlcv(self, code, n):
        return pd.DataFrame({"close": [self.px[code]]}, index=[pd.Timestamp("2026-11-12")])


def _ledger_with_two():
    b = PaperBroker(equity=200_000_000)
    b.queue([dict(_pick(), code="000001", name="A"), dict(_pick(), code="000002", name="B")])
    bar = {"open": 100, "high": 101, "low": 99, "close": 100}
    b.process_day("2026-11-11", {"000001": bar, "000002": bar}, 0.0)
    b.save()
    return b


def test_manual_commands():
    import run_manual

    _ledger_with_two()
    ok, txt = run_manual.run("TARGET 000001 3", _FakeSrc({"000001": 101, "000002": 100}))
    p = PaperBroker().s["positions"]["000001"]
    assert ok and abs(p["target"] - 103) < 1e-6 and p["target_src"] == "수동 +3%"
    ok, txt = run_manual.run("TARGET 000002 3", _FakeSrc({"000001": 101, "000002": 104}))  # 이미 넘었으면 즉시 청산
    assert ok and "000002" not in PaperBroker().s["positions"]
    ok, txt = run_manual.run("close 000001", _FakeSrc({"000001": 98, "000002": 0}))
    b = PaperBroker()
    assert ok and not b.s["positions"] and b.s["closed"][-1]["reason"] == "수동 손절"
    ok, _ = run_manual.run("STOP ALL 2", _FakeSrc({}))
    assert ok and PaperBroker().rules()["stop_pct"] == 2
    ok, _ = run_manual.run("hello", _FakeSrc({}))
    assert not ok


def test_evening_eval(monkeypatch, tmp_path):
    import json as _json

    import run_eval

    monkeypatch.setattr(run_eval, "ROWS", CFG.state_dir / "eval_rows.json")
    monkeypatch.setattr(run_eval, "OUT", CFG.docs_data_dir / "eval.json")
    CFG.ensure_dirs()
    (CFG.docs_data_dir / "v9_morning.json").write_text(_json.dumps({"day": "2026-11-11", "sections": [
        {"key": "open", "label": "① 시초가", "today": [{"code": "000001", "name": "A"}, {"code": "000002", "name": "B"}]}]}))
    bars = {"KOSPI": (100, 101, 99, 100.5, 100), "KOSDAQ": (50, 51, 49, 50, 50),
            "000001": (1030, 1070, 1000, 1010, 1000),   # 갭 +3%, 장중 +3.9%, 종가는 시가 아래
            "000002": (1000, 1010, 970, 1005, 1000)}

    class Src:
        def ohlcv(self, code, n):
            o, h, l, c, pc = bars[code]
            return pd.DataFrame({"open": [pc, o], "high": [pc, h], "low": [pc, l], "close": [pc, c]},
                                index=pd.to_datetime(["2026-11-10", "2026-11-11"]))

    assert run_eval.main(Src(), pd.Timestamp("2026-11-11 20:15", tz="Asia/Seoul")) == 0
    e = _json.loads((CFG.docs_data_dir / "eval.json").read_text())
    L = e["lists"][0]
    assert L["today"]["n"] == 2 and L["cum"]["days"] == 1 and "판단 보류" in L["verdict"]
    a = L["items"][0]
    assert abs(a["oc"] - (1010 / 1030 - 1)) < 1e-9 and not a["hit"]
    rows = _json.loads((CFG.state_dir / "eval_rows.json").read_text())
    r1 = next(r for r in rows if r["code"] == "000001")
    assert abs(r1["t3"] - (0.03 - CFG.cost_roundtrip)) < 1e-9   # +3% 익절 시뮬
    assert any("갭 +3%" in x for x in e["advice"])
    # 같은 날 다시 돌면 덮어씀
    run_eval.main(Src(), pd.Timestamp("2026-11-11 20:30", tz="Asia/Seoul"))
    assert len(_json.loads((CFG.state_dir / "eval_rows.json").read_text())) == 2
