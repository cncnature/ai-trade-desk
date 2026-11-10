"""검증 게이트 — 이 저장소의 핵심 차별점.

강의형 시스템은 팩터 점수(예: 15점 만점)를 사람이 정한 가중치로 더한다.
여기서는 각 팩터를 과거 자료로 먼저 시험하고, 통과한 팩터만 점수에 '투표권'을 준다.

절차 (팩터마다)
1. 날짜마다 팩터 상위 20% 바스켓의 hold일 수익률 − 같은 날 전체 평균 = 초과수익
2. 보유기간이 겹치지 않도록 hold일 간격으로 표본 추출 (자기상관에 의한 t 부풀림 방지)
3. 앞 70% 탐색 구간: t ≥ 다중검정(Bonferroni) 보정 임계값 이어야 후보
4. 뒤 30% 확인 구간: 평균 > 0 이고 t ≥ 1.5 여야 '통과'
5. 확인 구간을 몇 번 들여다봤는지 state/confirm_log.json 에 누적 기록(자기기만 방지)
"""
from __future__ import annotations

import json
import math
from datetime import date
from statistics import NormalDist

import numpy as np
import pandas as pd

from .config import CFG
from .factors import FACTORS


def bonferroni_t(n_tests: int, alpha: float = 0.05) -> float:
    return NormalDist().inv_cdf(1 - alpha / (2 * n_tests))


def _tstat(x: pd.Series) -> tuple[float, float, int]:
    x = x.replace([np.inf, -np.inf], np.nan).dropna()
    n = len(x)
    if n < 8 or x.std() == 0:
        return float("nan"), float(x.mean()) if n else float("nan"), n
    return float(x.mean() / x.std(ddof=1) * math.sqrt(n)), float(x.mean()), n


def excess_series(panel: pd.DataFrame, factor: str, q: float, hold: int, min_names: int = 15) -> pd.DataFrame:
    """날짜별 (바스켓 수익, 전체 평균, 초과) — 겹치지 않는 표본만."""
    d = panel.dropna(subset=[factor, "fwd"])
    # 유동성 하위 20% 제외 (체결 불가 종목으로 성적이 부풀지 않게)
    liq_cut = d.groupby("date")["value_traded"].transform(lambda s: s.quantile(0.2))
    d = d[d["value_traded"] >= liq_cut]
    dates = sorted(d["date"].unique())[::hold]
    d = d[d["date"].isin(dates)]
    rows = []
    for dt, g in d.groupby("date"):
        if len(g) < min_names:
            continue
        cut = g[factor].quantile(1 - q)
        top = g[g[factor] >= cut]
        rows.append((dt, top["fwd"].mean(), g["fwd"].mean()))
    out = pd.DataFrame(rows, columns=["date", "basket", "universe"]).set_index("date")
    out["excess"] = out["basket"] - out["universe"]
    return out


def run(panel: pd.DataFrame, index_fwd: pd.Series | None = None) -> dict:
    hold, q = CFG.hold_days, CFG.top_quantile
    t_explore = max(CFG.explore_t_min, bonferroni_t(len(FACTORS)))
    log_path = CFG.state_dir / "confirm_log.json"
    log = json.loads(log_path.read_text()) if log_path.exists() else {}

    results = []
    for f, label in FACTORS.items():
        s = excess_series(panel, f, q, hold)
        if len(s) < 20:
            results.append({"factor": f, "label": label, "status": "자료 부족", "weight": 0.0})
            continue
        split = int(len(s) * CFG.explore_frac)
        exp, conf = s.iloc[:split], s.iloc[split:]
        te, me, ne = _tstat(exp["excess"])
        row = {
            "factor": f, "label": label,
            "explore": {"t": te, "mean": me, "n": ne, "from": str(exp.index[0].date()), "to": str(exp.index[-1].date())},
            "confirm": None, "weight": 0.0,
        }
        if not (te >= t_explore and me > 0):
            row["status"] = "탐색 불통과"
        else:
            tc, mc, nc = _tstat(conf["excess"])
            log[f] = log.get(f, 0) + 1
            row["confirm"] = {"t": tc, "mean": mc, "n": nc, "from": str(conf.index[0].date()),
                              "to": str(conf.index[-1].date()), "uses": log[f]}
            if mc > 0 and tc >= CFG.confirm_t_min:
                row["status"] = "통과"
                row["weight"] = float(min(tc, 3.0))
            else:
                row["status"] = "확인 불통과"
        # 지수 대비: 바스켓 절대수익이 지수 보유보다 나았는가
        if index_fwd is not None:
            bi = s["basket"] - index_fwd.reindex(s.index)
            ti, mi, _ = _tstat(bi)
            row["vs_index"] = {"t": ti, "mean": mi}
        results.append(row)

    CFG.ensure_dirs()
    log_path.write_text(json.dumps(log, ensure_ascii=False, indent=1))
    return {
        "asof": str(date.today()),
        "hold_days": hold, "top_quantile": q, "cost_roundtrip": CFG.cost_roundtrip,
        "t_explore": round(t_explore, 2), "t_confirm": CFG.confirm_t_min,
        "factors": results,
        "passed": [r["factor"] for r in results if r.get("status") == "통과"],
    }


def _clean(o):
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    if isinstance(o, (np.floating,)):
        return _clean(float(o))
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_clean(v) for v in o]
    return o


clean_json = _clean
