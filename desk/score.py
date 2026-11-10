"""오늘의 후보 선정 + 진입·손절·목표·수량 계산.

점수 = Σ (통과 팩터의 가중치 × 오늘 단면 백분위). 통과 팩터가 하나도 없으면
매수 후보는 0 종목이다 — 근거 없이 억지로 추천하지 않는다.
"""
from __future__ import annotations

import math

import pandas as pd

from .config import CFG


def latest_snapshot(panel: pd.DataFrame) -> pd.DataFrame:
    last = panel["date"].max()
    snap = panel[panel["date"] == last].copy()
    if CFG.liq_cut <= 0:
        return snap
    return snap[snap["value_traded"] >= snap["value_traded"].quantile(CFG.liq_cut)]


def rank(snap: pd.DataFrame, validation: dict) -> pd.DataFrame:
    weights = {r["factor"]: r["weight"] for r in validation["factors"] if r.get("weight", 0) > 0}
    snap = snap.copy()
    snap["score"] = 0.0
    for f, w in weights.items():
        snap["score"] += w * snap[f].rank(pct=True).fillna(0)
    total = sum(weights.values())
    snap["score_pct"] = snap["score"] / total * 100 if total else 0.0
    snap["votes"] = [
        [f for f in weights if pd.notna(row[f]) and row[f] >= snap[f].quantile(1 - CFG.top_quantile)]
        for _, row in snap.iterrows()
    ]
    return snap.sort_values("score", ascending=False)


def plan_trade(row: pd.Series, equity: float) -> dict:
    """ATR 기반 손절·목표, 1% 리스크 사이징 (Van Tharp 고정비율), 종목당 상한."""
    price = float(row["close"])
    atr_pct = float(row["atr_pct"]) if pd.notna(row["atr_pct"]) else 0.03
    stop = price * (1 - CFG.atr_stop_mult * atr_pct)
    target = price * (1 + CFG.atr_target_mult * atr_pct)
    risk_per_share = max(price - stop, price * 0.005)
    qty_risk = (equity * CFG.risk_per_trade) / risk_per_share
    qty_cap = CFG.position_cap(equity) / price
    qty = int(max(0, math.floor(min(qty_risk, qty_cap))))
    return {
        "entry_ref": round(price), "stop": round(stop), "target": round(target),
        "qty": qty, "amount": round(qty * price), "atr_pct": round(atr_pct * 100, 2),
        "hold_days": CFG.hold_days,
    }


def pick(panel: pd.DataFrame, validation: dict, names: dict[str, tuple[str, str]], equity: float) -> list[dict]:
    if not validation["passed"]:
        return []
    ranked = rank(latest_snapshot(panel), validation)
    picks = []
    for _, row in ranked.head(CFG.max_positions).iterrows():
        name, market = names.get(row["code"], (row["code"], ""))
        picks.append({
            "code": row["code"], "name": name, "market": market,
            "score_pct": round(float(row["score_pct"]), 1), "votes": row["votes"],
            **plan_trade(row, equity),
        })
    return picks
