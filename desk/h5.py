"""H5-v1 — 외국인 보유율 상위 20% (v9 아침 리포트 morning_brief.py 와 같은 선정, 참고 칸).

v9 검증 기록 (2026-10-06, flow.db 전 종목)
  탐색 2023-09-14~2026-01-02(557일): 전 종목 대비 10일 +0.51% (t 2.57), 전·후반 모두 플러스
  확인 2026-01-05~09-01: +1.41% (t 2.50) — 지금까지 v9 에서 탐색·확인을 모두 통과한 유일한 규칙
  한계: 코스피 지수 대비 10일 −1.16% (t −2.40) — 지수 대신이 아니라 '개별주를 산다면 이 안에서'
선정: 종가 ≥ 1,000원 · 20일 평균 거래대금 ≥ 5억 · 당일 거래 있음 → 외국인 보유율 상위 20% → 거래대금 순 10종 표시
채점: 매일 표시한 10종을 state/h5_log.json 에 남기고, 10거래일 뒤 (다음 날 시가 → 10거래일째 종가, 비용 0.25%)를
      같은 날 전 종목 평균·코스피와 비교한다. 날짜별 평균을 표본 1개로 본다.
"""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

from .config import CFG

TOP, N_SHOW, MIN_PRICE, MIN_VALUE = 0.20, 10, 1000, 5e8
EVIDENCE = {"explore": {"excess": 0.0051, "t": 2.57, "days": 557},
            "confirm": {"excess": 0.0141, "t": 2.50},
            "vs_kospi": {"excess": -0.0116, "t": -2.40}}


def _t(x: pd.Series):
    x = x.replace([np.inf, -np.inf], np.nan).dropna()
    if len(x) < 3 or x.std() == 0:
        return None, len(x)
    return float(x.mean() / x.std(ddof=1) * math.sqrt(len(x))), len(x)


def run(panel: pd.DataFrame, names: dict, kospi_fwd: pd.Series) -> dict:
    last = panel["date"].max()
    snap = panel[panel["date"] == last]
    ok = snap[(snap["close"] >= MIN_PRICE) & (snap["value_traded"] >= MIN_VALUE) & snap["frgn_hold"].notna()]
    grp = ok[ok["frgn_hold"].rank(pct=True) > 1 - TOP]
    show = grp.sort_values("value_traded", ascending=False).head(N_SHOW)
    asof = str(last.date())
    picks = [{"code": r.code, "name": names.get(r.code, (r.code, ""))[0], "close": round(float(r.close)),
              "hold": round(float(r.frgn_hold), 1)} for r in show.itertuples()]

    # 기록 (같은 날 다시 돌면 덮어씀 — 그날의 마지막 선정이 기록)
    log_p = CFG.state_dir / "h5_log.json"
    log = json.loads(log_p.read_text()) if log_p.exists() else {}
    if picks:
        log[asof] = [p["code"] for p in picks]
        log_p.write_text(json.dumps(log, ensure_ascii=False, indent=0))

    # 채점: panel 의 fwd = 다음 날 시가 → 10거래일째 종가 − 비용
    fw = panel.pivot_table(index="date", columns="code", values="fwd")
    rows = []
    for d, codes in log.items():
        ts = pd.Timestamp(d)
        if ts not in fw.index:
            continue
        r = fw.loc[ts]
        mine = r.reindex(codes).dropna()
        if mine.empty:
            continue
        k = kospi_fwd.get(ts, np.nan)
        rows.append({"date": ts, "ret": mine.mean(), "ex_all": mine.mean() - r.mean(),
                     "ex_kospi": mine.mean() - k if k == k else np.nan})
    score = {"days": 0, "pending": len(log)}
    if rows:
        g = pd.DataFrame(rows).set_index("date")
        t_all, n = _t(g["ex_all"])
        t_k, _ = _t(g["ex_kospi"])
        score = {"days": n, "pending": len(log) - n, "first": str(g.index.min().date()),
                 "ret": float(g["ret"].mean()), "win": float((g["ret"] > 0).mean()),
                 "ex_all": float(g["ex_all"].mean()), "t_all": t_all,
                 "ex_kospi": float(g["ex_kospi"].mean()) if g["ex_kospi"].notna().any() else None, "t_kospi": t_k}
    return {"asof": asof, "n_group": int(len(grp)), "n_universe": int(len(ok)), "picks": picks,
            "evidence": EVIDENCE, "score": score}
