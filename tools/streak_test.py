"""연속 순매수 기법 검증 — 외국인 / 연기금 / 외국인+연기금 이 N일 연속 순매수한 종목을 다음 날 시가에 사면?

사전 등록 (2026-11-14, 결과를 보기 전에 정함)
  신호: 장 마감 후, 투자자 X 의 순매수가 N일 연속(> 0)이 '처음' 된 날 (N = 3, 5, 10) — 같은 연속 구간에서 한 번만
        조건 F = 외국인, P = 연기금, FP = 외국인과 연기금이 둘 다 N일 이상 연속인 첫날
  대상: 수급 파일 전 종목, 20일 평균 거래대금 5억 원 이상, 주가 1,000원 이상
  매매: 다음 날 시가 매수, H 거래일 뒤 종가 매도 (H = 1, 5, 10, 20), 비용 왕복 0.25%
  비교: 같은 날 같은 방법으로 산 '조건 없는 전 종목 평균' 대비 초과 수익, 코스피 대비
  판정: 신호일별 평균 초과의 t ≥ 2 그리고 탐색·확인 구간 모두 플러스여야 '가능성 있음'
        탐색 = 마지막 250거래일 전, 확인 = 마지막 250거래일 (최근 1년)
  가격 변화: 신호일 기준 −10 ~ +20 거래일 누적 초과 수익 곡선 (사기 전 이미 얼마나 올랐는지 포함)
연기금 자료가 없으면 F 만 계산하고 P·FP 는 '자료 없음' 으로 남긴다.
    python tools/streak_test.py  →  reports/streak_test.md, docs/data/streak_test.json
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from desk.data import NaverSource, flow_store, load_index, load_stock  # noqa: E402

NS, HS, COST, LIQ, MINPX, CONFIRM = (3, 5, 10), (1, 5, 10, 20), 0.0025, 5e8, 1000, 250
PEN_FILES = [ROOT / "data" / "krx" / "inv.csv.gz", ROOT / "data" / "flow" / "pension.csv.gz"]


def pension() -> dict[str, pd.Series]:
    for p in PEN_FILES:
        if p.exists():
            d = pd.read_csv(p, dtype={"code": str}, parse_dates=["date"])
            if "pen_net" in d and d["pen_net"].abs().sum() > 0:
                d["code"] = d["code"].str.zfill(6)
                print(f"연기금 자료: {p.name} {d['date'].min().date()}~{d['date'].max().date()} {d['code'].nunique()}종목")
                return {c: g.set_index("date")["pen_net"].sort_index() for c, g in d.groupby("code")}
    print("연기금 자료 없음 — 외국인만 계산")
    return {}


def streak(x: pd.Series) -> pd.Series:
    pos = (x > 0).astype(int)
    grp = (pos == 0).cumsum()
    return pos.groupby(grp).cumsum()


def tstat(x) -> float | None:
    x = pd.Series(x).dropna()
    if len(x) < 5 or x.std() == 0:
        return None
    return float(x.mean() / x.std(ddof=1) * math.sqrt(len(x)))


def main() -> int:
    src = NaverSource()
    store = flow_store() or {}
    pen = pension()
    rows, fwd_all, curves = [], [], []
    codes = sorted(store)
    for i, code in enumerate(codes):
        try:
            df = load_stock(src, code, 1500)
        except Exception:
            continue
        df = df.dropna(subset=["open", "close"])
        df = df[(df["open"] > 0) & (df["close"] > 0)]
        if len(df) < 80 or df["frgn_net"].notna().sum() < 60:
            continue
        df = df[df.index >= store[code].index.min()]
        val = (df["close"] * df["volume"]).rolling(20).mean()
        ok = (val >= LIQ) & (df["close"] >= MINPX)
        o, c = df["open"].values, df["close"].values
        n = len(df)
        fwd = {}
        for H in HS:
            r = np.full(n, np.nan)
            if n > H + 1:
                r[:-(H + 1)] = c[H + 1:] / o[1:n - H] - 1 - COST
            fwd[H] = r
        sf = streak(df["frgn_net"].fillna(0))
        sp = None
        if code in pen:
            p = pen[code].reindex(df.index)
            have = p.notna()
            sp = streak(p.fillna(0)).where(have)
        base = pd.DataFrame({"date": df.index, "code": code, "ok": ok.values, **{f"r{H}": fwd[H] for H in HS}})
        fwd_all.append(base[base["ok"]])
        lc = np.log(c)
        for N in NS:
            sigs = {"F": (sf == N)}
            if sp is not None:
                sigs["P"] = sp == N
                both = (sf >= N) & (sp >= N)
                sigs["FP"] = both & ~both.shift(1, fill_value=False)
            for k, s in sigs.items():
                idx = np.where(s.values & ok.values)[0]
                for j in idx:
                    rows.append({"date": df.index[j], "code": code, "cond": k, "N": N, **{f"r{H}": fwd[H][j] for H in HS}})
                    if j >= 10 and j + 20 < n:  # 가격 경로: 신호일 종가 기준 로그수익
                        curves.append({"date": df.index[j], "cond": k, "N": N, "code": code,
                                       **{f"d{t}": lc[j + t] - lc[j] for t in range(-10, 21)}})
        if i % 200 == 0:
            print(f"  {i}/{len(codes)} 신호 {len(rows)}", flush=True)
    sig = pd.DataFrame(rows)
    base = pd.concat(fwd_all)
    bench = base.groupby("date")[[f"r{H}" for H in HS]].mean()
    dates = sorted(bench.index)
    cut = dates[-CONFIRM] if len(dates) > CONFIRM else dates[0]
    # 코스피
    try:
        k = src.ohlcv("KOSPI", 1500)
        ko, kc = k["open"].values, k["close"].values
        kos = {}
        for H in HS:
            r = np.full(len(k), np.nan)
            r[:-(H + 1)] = kc[H + 1:] / ko[1:len(k) - H] - 1
            kos[H] = pd.Series(r, index=k.index)
    except Exception:
        kos = {}
    # 전 종목 평균 경로 (같은 날 기준) — 경로 초과 계산용
    res = {"generated": pd.Timestamp.now(tz="Asia/Seoul").strftime("%Y-%m-%d %H:%M"),
           "period": [str(dates[0].date()), str(dates[-1].date())], "confirm_from": str(cut.date()),
           "has_pension": bool(pen), "rules": __doc__.split("연기금 자료가")[0].strip(), "table": [], "curve": {}}
    for (cond, N), g in sig.groupby(["cond", "N"]):
        for H in HS:
            col = f"r{H}"
            gg = g.dropna(subset=[col]).copy()
            if gg.empty:
                continue
            gg["ex"] = gg[col] - gg["date"].map(bench[col])
            if H in kos:
                gg["exk"] = gg[col] - gg["date"].map(kos[H])
            daily = gg.groupby("date")[["ex", col] + (["exk"] if "exk" in gg else [])].mean()
            e, c2 = daily[daily.index < cut], daily[daily.index >= cut]
            row = {"cond": cond, "N": N, "H": H, "events": int(len(gg)), "days": int(len(daily)),
                   "net": float(daily[col].mean()), "ex": float(daily["ex"].mean()), "t": tstat(daily["ex"]),
                   "exk": float(daily["exk"].mean()) if "exk" in daily else None,
                   "hit": float((gg[col] > 0).mean()),
                   "ex_explore": float(e["ex"].mean()) if len(e) else None, "t_explore": tstat(e["ex"]),
                   "ex_confirm": float(c2["ex"].mean()) if len(c2) else None, "t_confirm": tstat(c2["ex"]),
                   "n_confirm": int(len(c2))}
            row["pass"] = bool(row["t"] is not None and row["t"] >= 2 and (row["ex_explore"] or 0) > 0 and (row["ex_confirm"] or 0) > 0)
            res["table"].append(row)
    cv = pd.DataFrame(curves)
    if len(cv):
        for (cond, N), g in cv.groupby(["cond", "N"]):
            res["curve"][f"{cond}{N}"] = {str(t): float(g[f"d{t}"].mean()) for t in range(-10, 21)}
    out = ROOT / "docs" / "data" / "streak_test.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    P = lambda x: "—" if x is None or x != x else f"{x * 100:+.2f}%"  # noqa: E731
    T = lambda x: "—" if x is None else f"{x:.2f}"  # noqa: E731
    L = [f"# 연속 순매수 검증 ({res['period'][0]} ~ {res['period'][1]}, 확인 구간 {res['confirm_from']}~)", "",
         "| 조건 | N일 | 보유 | 신호 | 순수익 | 전종목 대비 | t | 코스피 대비 | 수익 비율 | 탐색 초과 | 확인 초과 (t) | 판정 |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    name = {"F": "외국인", "P": "연기금", "FP": "외국인+연기금"}
    for r in res["table"]:
        L.append(f"| {name[r['cond']]} | {r['N']} | {r['H']}일 | {r['events']} | {P(r['net'])} | {P(r['ex'])} | {T(r['t'])} | {P(r['exk'])} | "
                 f"{r['hit'] * 100:.0f}% | {P(r['ex_explore'])} | {P(r['ex_confirm'])} ({T(r['t_confirm'])}) | {'✅' if r['pass'] else '—'} |")
    L += ["", "## 신호일 전후 평균 누적 수익 (신호일 종가 = 0)", "",
          "| 조건 | −10일 | −5일 | −1일 | +1일 | +5일 | +10일 | +20일 |", "|---|---|---|---|---|---|---|---|"]
    for k2, v in res["curve"].items():
        L.append(f"| {name[k2.rstrip('0123456789')]} {k2.lstrip('FP')}일 | " + " | ".join(P(v[str(t)]) for t in (-10, -5, -1, 1, 5, 10, 20)) + " |")
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "streak_test.md").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
