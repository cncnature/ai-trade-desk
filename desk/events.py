"""이벤트 전략 — 배당 D-3 (사전 등록 2026-11-10, 결과를 본 뒤 바꾸지 않음).

규칙
  배당락일(ex) 3거래일 전 시가 매수 → 마지막 매수일(ex-1) 종가 매도. 왕복 비용 차감.
  초과수익 = 이벤트 종목 수익 − 같은 3일 동안 전 종목 평균 수익.
날짜 계산 (T+2 결제)
  기준일 이전(포함) 마지막 거래일 R → 마지막 매수일 = R 의 2거래일 전, 배당락일 = 그 다음 거래일.
검정
  같은 배당락일에 몰린 이벤트는 서로 독립이 아니므로 '배당락일별 평균 초과수익' 을 표본 1개로 본다.
  앞 70% 날짜 탐색 t ≥ 2.0, 뒤 30% 확인 t ≥ 1.5 (평균 > 0). 날짜가 20개 미만이면 '자료 부족'.
참고 통계(판정에는 안 씀): 공시가 진입 전에 나온 이벤트만 / 2024년 배당절차 개선 전후.
근거: Lakonishok & Vermaelen (1986) 배당락 전 초과수익, Elton & Gruber (1970)·Kalay (1982) 배당락일 가격 반응.
"""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

from .config import CFG, ROOT

EVENTS = ROOT / "data" / "dividends" / "events.json"
NAME = "div_d3"
LABEL = "배당 D-3 (배당락 3거래일 전 시가 → 마지막 매수일 종가)"
MIN_DATES = 20


def load_events() -> pd.DataFrame:
    if not EVENTS.exists():
        return pd.DataFrame()
    ev = pd.DataFrame(json.loads(EVENTS.read_text(encoding="utf-8")))
    if ev.empty:
        return ev
    ev = ev.dropna(subset=["record_date"])
    ev["record_date"] = pd.to_datetime(ev["record_date"], errors="coerce")
    ev["disclosed"] = pd.to_datetime(ev["disclosed"].astype(str), format="%Y%m%d", errors="coerce")
    ev = ev.dropna(subset=["record_date"])
    # 정정공시는 같은 (종목, 기준일)의 최신 접수번호만 남긴다. 단, '공시일' 은 최초 공시일로.
    first = ev.groupby(["code", "record_date"])["disclosed"].min()
    ev = ev.sort_values("rcept_no").groupby(["code", "record_date"]).tail(1).set_index(["code", "record_date"])
    ev["disclosed"] = first
    ev = ev.reset_index()
    ev = ev[ev["dps"].fillna(0) > 0]  # 배당금 0·미기재 제외
    return ev


def ex_dates(record: pd.Timestamp, cal: pd.DatetimeIndex) -> tuple[int, int] | None:
    """(마지막 매수일 위치, 배당락일 위치) — cal 은 거래일 목록."""
    r = cal.searchsorted(record, side="right") - 1  # 기준일 이전(포함) 마지막 거래일
    if r < 3 or r >= len(cal):
        return None
    last_buy = r - 2
    return last_buy, last_buy + 1


def _t(x: pd.Series) -> tuple[float, float, int]:
    x = x.replace([np.inf, -np.inf], np.nan).dropna()
    n = len(x)
    if n < 5 or x.std() == 0:
        return float("nan"), float(x.mean()) if n else float("nan"), n
    return float(x.mean() / x.std(ddof=1) * math.sqrt(n)), float(x.mean()), n


def event_returns(frames: dict[str, pd.DataFrame], cal: pd.DatetimeIndex) -> pd.DataFrame:
    ev = load_events()
    if ev.empty:
        return pd.DataFrame()
    opens = pd.DataFrame({c: f["open"] for c, f in frames.items()}).reindex(cal)
    closes = pd.DataFrame({c: f["close"] for c, f in frames.items()}).reindex(cal)
    rows = []
    for e in ev.itertuples():
        if e.code not in opens.columns:
            continue
        pos = ex_dates(e.record_date, cal)
        if not pos:
            continue
        last_buy, ex = pos
        entry = ex - 3
        if entry < 0 or last_buy >= len(cal):
            continue
        o, c = opens.iloc[entry], closes.iloc[last_buy]
        r_all = (c / o.where(o > 0) - 1).replace([np.inf, -np.inf], np.nan)
        r = r_all.get(e.code)
        if r is None or pd.isna(r):
            continue
        rows.append({"code": e.code, "ex_date": cal[ex], "entry_date": cal[entry], "record_date": e.record_date,
                     "disclosed": e.disclosed, "dps": e.dps, "ret": r - CFG.cost_roundtrip,
                     "excess": r - r_all.mean(), "known_before": bool(pd.notna(e.disclosed) and e.disclosed < cal[entry])})
    return pd.DataFrame(rows)


def run(frames: dict[str, pd.DataFrame], cal: pd.DatetimeIndex) -> dict:
    er = event_returns(frames, cal)
    base = {"factor": NAME, "label": LABEL, "kind": "event", "weight": 0.0, "confirm": None}
    if er.empty:
        return {**base, "status": "자료 부족", "events": 0, "dates": 0,
                "note": "배당 공시 수집 전 (DART_API_KEY 설정 후 dividends 워크플로 실행)"}
    by = er.groupby("ex_date")["excess"].mean().sort_index()
    out = {**base, "events": int(len(er)), "dates": int(len(by))}
    if len(by) < MIN_DATES:
        return {**out, "status": "자료 부족"}
    split = int(len(by) * CFG.explore_frac)
    exp, conf = by.iloc[:split], by.iloc[split:]
    te, me, ne = _t(exp)
    out["explore"] = {"t": te, "mean": me, "n": ne, "from": str(exp.index[0].date()), "to": str(exp.index[-1].date())}
    if not (te >= 2.0 and me > 0):
        out["status"] = "탐색 불통과"
    else:
        log_p = CFG.state_dir / "confirm_log.json"
        log = json.loads(log_p.read_text()) if log_p.exists() else {}
        log[NAME] = log.get(NAME, 0) + 1
        log_p.write_text(json.dumps(log, ensure_ascii=False, indent=1))
        tc, mc, nc = _t(conf)
        out["confirm"] = {"t": tc, "mean": mc, "n": nc, "from": str(conf.index[0].date()),
                          "to": str(conf.index[-1].date()), "uses": log[NAME]}
        ok = mc > 0 and tc >= CFG.confirm_t_min
        out["status"] = "통과" if ok else "확인 불통과"
        out["weight"] = 1.0 if ok else 0.0
    # 참고 통계 (판정에 쓰지 않음)
    ref = {}
    for key, sub in (("공시 선행 이벤트만", er[er["known_before"]]),
                     ("2023년 이전", er[er["ex_date"] < "2024-01-01"]),
                     ("2024년 이후", er[er["ex_date"] >= "2024-01-01"])):
        if len(sub):
            t, m, n = _t(sub.groupby("ex_date")["excess"].mean())
            ref[key] = {"t": t, "mean": m, "dates": n, "events": int(len(sub))}
    out["reference"] = ref
    return out


def upcoming(frames: dict[str, pd.DataFrame], cal: pd.DatetimeIndex, asof: pd.Timestamp,
             names: dict[str, tuple[str, str]]) -> list[dict]:
    """내일 시가가 'ex-3' 인 이벤트 — 오늘까지 공시된 것만 (미래 정보 금지)."""
    ev = load_events()
    if ev.empty:
        return []
    # 내일 이후의 거래일은 주말만 건너뛴 근사 달력으로 계산 (공휴일이 끼면 하루 어긋날 수 있음 — 표시)
    fut = pd.bdate_range(asof + pd.Timedelta(days=1), periods=15)
    full = cal[cal <= asof].append(fut)
    nxt = full[full > asof][0]
    out = []
    for e in ev[(ev["disclosed"] <= asof) & (ev["record_date"] > asof)].itertuples():
        pos = ex_dates(e.record_date, full)
        if not pos or full[pos[1] - 3] != nxt or e.code not in frames:
            continue
        f = frames[e.code]
        name, market = names.get(e.code, (e.code, ""))
        price = float(f["close"].iloc[-1])
        qty = int(CFG.position_cap(CFG.equity) // price) if price > 0 else 0
        out.append({"code": e.code, "name": name, "market": market, "score_pct": 100.0, "votes": [NAME],
                    "entry_ref": round(price), "stop": round(price * 0.9), "target": round(price * 1.2),
                    "qty": qty, "amount": round(qty * price), "atr_pct": None, "hold_days": 3,
                    "event": {"record_date": str(e.record_date.date()), "ex_date": str(full[pos[1]].date()),
                              "last_buy": str(full[pos[0]].date()), "dps": e.dps,
                              "yield_pct": round(e.dps / price * 100, 2) if price else None}})
    return out
