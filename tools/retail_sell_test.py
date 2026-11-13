"""개인 연속 순매도 → 급등? 검증 (사전 등록 2026-11-14, 결과 보기 전에 정함)

개인 순매수 = −(외국인 + 기관) 순매수 (수량)  — 기타법인을 뺀 근사. 부호(사는지 파는지)만 쓴다.

① 앞으로 보기 (신호 → 이후)
  신호  R5 / R10   : 개인 순매도가 5일 / 10일 연속이 된 첫날
        R15of20    : 최근 20일 중 15일 이상 개인 순매도가 된 첫날
        Q 접미사    : 위 조건 + 그 기간 종가 변동이 ±5% 안 (주가는 조용한데 개인만 계속 팖 = '매집' 가설)
  대상  20일 평균 거래대금 5억 원 이상, 주가 1,000원 이상
  매매  다음 날 시가 매수, H(1·5·10·20)거래일 뒤 종가 매도, 비용 0.25%
  비교  같은 날 전 종목 평균 대비 초과, 코스피 대비
  급등  매수 후 20거래일 안에 종가가 매수가 대비 +20% 이상 된 비율 (전 종목 같은 날 비율 대비 배수)
  판정  초과 t ≥ 2, 탐색·확인(최근 250거래일) 모두 플러스

② 거꾸로 보기 (급등 → 직전)
  급등일 = 하루 종가 +15% 이상 (그 전 20일 안에 급등 없던 첫날), 직전일까지의 개인 연속 순매도 일수·20일 중 순매도 일수
  비교  전체 종목-일의 같은 값 분포 (급등 전에 개인 매도가 '평소보다' 많았는지)
    python tools/retail_sell_test.py  →  reports/retail_sell_test.md
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from desk.data import NaverSource, flow_store, load_stock  # noqa: E402

HS, COST, LIQ, MINPX, CONFIRM, JUMP, SURGE = (1, 5, 10, 20), 0.0025, 5e8, 1000, 250, 0.20, 0.15


def streak(neg: pd.Series) -> pd.Series:
    x = neg.astype(int)
    return x.groupby((x == 0).cumsum()).cumsum()


def tstat(x) -> float | None:
    x = pd.Series(x).dropna()
    return None if len(x) < 5 or x.std() == 0 else float(x.mean() / x.std(ddof=1) * math.sqrt(len(x)))


def main() -> int:
    src = NaverSource()
    store = flow_store() or {}
    sig_rows, base_rows, surge_rows, dist = [], [], [], []
    for i, code in enumerate(sorted(store)):
        try:
            df = load_stock(src, code, 1500)
        except Exception:
            continue
        df = df.dropna(subset=["open", "close"])
        df = df[(df["open"] > 0) & (df["close"] > 0) & (df.index >= store[code].index.min())]
        if len(df) < 80:
            continue
        indiv = -(df["frgn_net"].fillna(0) + df["inst_net"].fillna(0))
        sell = indiv < 0
        s_run = streak(sell)
        s_20 = sell.astype(int).rolling(20).sum()
        val = (df["close"] * df["volume"]).rolling(20).mean()
        ok = ((val >= LIQ) & (df["close"] >= MINPX)).values
        o, c, n = df["open"].values, df["close"].values, len(df)
        fwd = {}
        for H in HS:
            r = np.full(n, np.nan)
            if n > H + 1:
                r[:-(H + 1)] = c[H + 1:] / o[1:n - H] - 1 - COST
            fwd[H] = r
        jump = np.full(n, np.nan)  # 20일 안 종가 최고 / 매수가 ≥ +20%
        for j in range(n - 21):
            jump[j] = float(c[j + 1:j + 21].max() / o[j + 1] - 1 >= JUMP)
        rng = lambda w: (df["close"].rolling(w).max() / df["close"].rolling(w).min() - 1)  # noqa: E731
        quiet = {5: rng(5) <= 0.10, 10: rng(10) <= 0.10, 20: rng(20) <= 0.10}  # ±5% ≈ 고저폭 10% 이내
        sigs = {"R5": s_run == 5, "R10": s_run == 10,
                "R15of20": (s_20 >= 15) & (s_20.shift(1) < 15)}
        sigs["R5Q"] = sigs["R5"] & quiet[5]
        sigs["R10Q"] = sigs["R10"] & quiet[10]
        sigs["R15of20Q"] = sigs["R15of20"] & quiet[20]
        b = pd.DataFrame({"date": df.index, **{f"r{H}": fwd[H] for H in HS}, "jump": jump})
        base_rows.append(b[ok])
        for k, s in sigs.items():
            for j in np.where(s.values & ok)[0]:
                sig_rows.append({"date": df.index[j], "code": code, "cond": k, **{f"r{H}": fwd[H][j] for H in HS}, "jump": jump[j]})
        # ② 거꾸로: 급등일
        dret = df["close"].pct_change().values
        last = -99
        for j in range(21, n):
            if ok[j - 1] and dret[j] >= SURGE and j - last > 20:
                surge_rows.append({"date": df.index[j], "code": code, "run": int(s_run.iloc[j - 1]), "s20": float(s_20.iloc[j - 1]),
                                   "pre20": float(c[j - 1] / c[j - 21] - 1)})
                last = j
            elif dret[j] >= SURGE:
                last = j
        okm = ok & s_20.notna().values
        dist.append(pd.DataFrame({"run": s_run.values[okm], "s20": s_20.values[okm]}))
        if i % 300 == 0:
            print(f"  {i}/{len(store)} 신호 {len(sig_rows)} 급등 {len(surge_rows)}", flush=True)
    sig, base, sr, dd = pd.DataFrame(sig_rows), pd.concat(base_rows), pd.DataFrame(surge_rows), pd.concat(dist)
    bench = base.groupby("date")[[f"r{H}" for H in HS] + ["jump"]].mean()
    dates = sorted(bench.index)
    cut = dates[-CONFIRM]
    k = src.ohlcv("KOSPI", 1500)
    kos = {}
    for H in HS:
        r = np.full(len(k), np.nan)
        r[:-(H + 1)] = k["close"].values[H + 1:] / k["open"].values[1:len(k) - H] - 1
        kos[H] = pd.Series(r, index=k.index)
    P = lambda x: "—" if x is None or x != x else f"{x * 100:+.2f}%"  # noqa: E731
    T = lambda x: "—" if x is None else f"{x:.2f}"  # noqa: E731
    name = {"R5": "개인 5일 연속 순매도", "R10": "개인 10일 연속 순매도", "R15of20": "20일 중 15일+ 개인 순매도",
            "R5Q": "  └ + 주가 조용(5일 고저 10%↓)", "R10Q": "  └ + 주가 조용(10일 고저 10%↓)", "R15of20Q": "  └ + 주가 조용(20일 고저 10%↓)"}
    L = [f"# 개인 연속 순매도 → 급등? ({dates[0].date()} ~ {dates[-1].date()}, 확인 구간 {cut.date()}~)", "",
         "## ① 신호 뒤 수익 (다음 날 시가 매수, 비용 0.25% 뺀 값)", "",
         "| 조건 | 보유 | 신호 | 순수익 | 전종목 대비 | t | 코스피 대비 | 탐색 초과 | 확인 초과 (t) | 20일 내 +20% 급등 비율 (전종목) | 판정 |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for cond in name:
        g0 = sig[sig["cond"] == cond]
        if g0.empty:
            continue
        for H in HS:
            col = f"r{H}"
            g = g0.dropna(subset=[col]).copy()
            g["ex"] = g[col] - g["date"].map(bench[col])
            g["exk"] = g[col] - g["date"].map(kos[H])
            d = g.groupby("date")[["ex", "exk", col]].mean()
            e, cc = d[d.index < cut], d[d.index >= cut]
            t = tstat(d["ex"])
            ok2 = t is not None and t >= 2 and e["ex"].mean() > 0 and cc["ex"].mean() > 0
            jr = ""
            if H == HS[0]:
                gj = g0.dropna(subset=["jump"])
                bj = gj["date"].map(bench["jump"]).mean()
                jr = f"{gj['jump'].mean() * 100:.1f}% ({bj * 100:.1f}%, {gj['jump'].mean() / bj:.2f}배)" if bj else ""
            L.append(f"| {name[cond]} | {H}일 | {len(g):,} | {P(d[col].mean())} | {P(d['ex'].mean())} | {T(t)} | {P(d['exk'].mean())} | "
                     f"{P(e['ex'].mean())} | {P(cc['ex'].mean())} ({T(tstat(cc['ex']))}) | {jr} | {'✅' if ok2 else '—'} |")
    L += ["", f"## ② 급등일(하루 +15% 이상, {len(sr):,}건) 직전의 개인 매매", "",
          "| 항목 | 급등 직전 | 평소 (전 종목-일) |", "|---|---|---|",
          f"| 개인 순매도 연속 일수 평균 | {sr['run'].mean():.2f}일 | {dd['run'].mean():.2f}일 |",
          f"| 5일 이상 연속 순매도 중이던 비율 | {(sr['run'] >= 5).mean() * 100:.1f}% | {(dd['run'] >= 5).mean() * 100:.1f}% |",
          f"| 10일 이상 연속 순매도 중이던 비율 | {(sr['run'] >= 10).mean() * 100:.1f}% | {(dd['run'] >= 10).mean() * 100:.1f}% |",
          f"| 20일 중 개인 순매도 일수 평균 | {sr['s20'].mean():.1f}일 | {dd['s20'].mean():.1f}일 |",
          f"| 20일 중 15일+ 순매도 비율 | {(sr['s20'] >= 15).mean() * 100:.1f}% | {(dd['s20'] >= 15).mean() * 100:.1f}% |",
          f"| 급등 직전 20일 주가 변화 (중앙값) | {sr['pre20'].median() * 100:+.1f}% | — |", ""]
    import json
    try:
        nm = {x["code"]: x["name"] for x in json.loads((ROOT / "data" / "flow" / "universe.json").read_text(encoding="utf-8"))}
    except Exception:
        nm = {}
    hi = sr[sr["run"] >= 5]
    L.append(f"개인 5일+ 연속 순매도 뒤 급등한 최근 사례 (최근 15건): " + ", ".join(
        f"{nm.get(r.code, r.code)}({r.date.date()}, {r.run}일)" for r in hi.sort_values("date").tail(15).itertuples()))
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "retail_sell_test.md").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
