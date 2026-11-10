"""저녁 평가 (평일 20:15 KST) — 오늘 아침 추천 종목을 오늘 종가로 채점하고, 누적 기록으로 조언을 만든다.

대상 (오늘 아침에 나온 목록만)
  desk   이 시스템 후보 (latest.json, 오늘 07시대 생성분)          ─ 10일 보유 규칙 · 오늘은 보유 1일째
  h5     ⑥ H5-v1 외국인 보유율 상위 20%                          ─ 10일 보유 규칙 · 오늘은 보유 1일째
  v9_*   v9 아침 후보 다섯 칸 (v9_morning.json, 오늘 07:40 분)    ─ 당일 매매 관점
채점 (정규장 일봉, 비용 0.25% 차감)
  오늘 시가에 샀다면 → 종가까지 (시가→종가), 전일 대비 등락, 시가 뒤 장중 최고·최저, 같은 시장 지수 대비
  '맞음' = 시가→종가(비용 차감) > 0
  매도 방식 비교(관찰): ① 종가 매도  ② +3% 닿으면 익절, 아니면 종가  ③ +3% 익절 / −2% 손절
     (하루 봉으로는 고가·저가의 순서를 모르므로 둘 다 닿은 날은 손절로 본다 — 보수적)
조언 규칙 (미리 정함, 결과를 보고 바꾸지 않음)
  누적 20거래일 미만 → '표본 부족, 판단 보류'
  20일 이상 · 날짜별 평균 t ≤ −2 → '⛔ 시가 추격 손실 경향 — 매수 보류'
  20일 이상 · t ≥ 2 · 지수 대비 플러스 → '✅ 우위 신호 — 승격 규칙(60일 + 확인 20일) 대상'
  그 밖 → '➖ 뚜렷한 차이 없음'
  오늘 하루 관찰(갭 +3%↑ 종목 vs 나머지, +3% 닿음 비율 등)은 '관찰'로만 쓰고 결론을 내리지 않는다.
저장: state/eval_rows.json (누적 원자료) · docs/data/eval.json (오늘 결과와 조언, 대시보드·텔레그램용)
"""
from __future__ import annotations

import json
import math
import sys

import numpy as np
import pandas as pd

from desk import ai, notify
from desk.config import CFG
from desk.data import NaverSource

COST = CFG.cost_roundtrip
TGT, STP = 0.03, 0.02
ROWS = CFG.state_dir / "eval_rows.json"
OUT = CFG.docs_data_dir / "eval.json"
MIN_DAYS = 20


def _j(p):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def collect(today: str) -> list[dict]:
    """오늘 아침 목록. 오늘 만들어지지 않은 목록은 넣지 않는다 (어제 목록을 오늘 것처럼 채점하지 않게)."""
    out = []
    lat = _j(CFG.docs_data_dir / "latest.json") or {}
    if str(lat.get("generated", "")).startswith(today) and lat.get("source") != "synthetic":
        if lat.get("picks"):
            out.append({"key": "desk", "label": "이 시스템 후보 (외국인 보유율 검정 통과)", "horizon": "10d",
                        "items": [{"code": x["code"], "name": x["name"]} for x in lat["picks"]]})
        h = lat.get("h5") or {}
        if h.get("picks"):
            out.append({"key": "h5", "label": "⑥ H5-v1 외국인 보유율 상위 20%", "horizon": "10d",
                        "items": [{"code": x["code"], "name": x["name"]} for x in h["picks"]]})
    v9 = _j(CFG.docs_data_dir / "v9_morning.json") or {}
    if v9.get("day") == today:
        for s in v9.get("sections", []):
            if s.get("today"):
                out.append({"key": "v9_" + s["key"], "label": s["label"], "horizon": "day",
                            "items": [{"code": x["code"], "name": x["name"]} for x in s["today"]]})
    return out


def markets() -> dict[str, str]:
    u = _j(CFG.cache_dir.parent / "flow" / "universe.json") or []
    return {r["code"]: r.get("market", "") for r in u}


def bar_today(src, symbol: str, today: str):
    try:
        df = src.ohlcv(symbol, 3)
    except Exception:
        return None
    if len(df) < 2 or df.index[-1].strftime("%Y-%m-%d") != today:
        return None
    r, p = df.iloc[-1], df.iloc[-2]
    if r["open"] <= 0 or p["close"] <= 0:
        return None
    return {"o": float(r["open"]), "h": float(r["high"]), "l": float(r["low"]), "c": float(r["close"]), "pc": float(p["close"])}


def exits(b: dict) -> dict:
    up, dn = b["h"] / b["o"] - 1, b["l"] / b["o"] - 1
    plain = b["c"] / b["o"] - 1
    t3 = TGT if up >= TGT else plain
    t3s2 = -STP if dn <= -STP else (TGT if up >= TGT else plain)
    return {"plain": plain - COST, "t3": t3 - COST, "t3s2": t3s2 - COST}


def tstat(x: pd.Series):
    x = x.replace([np.inf, -np.inf], np.nan).dropna()
    if len(x) < 3 or x.std() == 0:
        return None, len(x)
    return float(x.mean() / x.std(ddof=1) * math.sqrt(len(x))), len(x)


def verdict(days: int, t, ex) -> str:
    if days < MIN_DAYS:
        return f"표본 {days}일 — 판단 보류 ({MIN_DAYS}일부터)"
    if t is not None and t <= -2:
        return "⛔ 시가에 따라 사면 손실 경향 — 매수 보류"
    if t is not None and t >= 2 and (ex or 0) > 0:
        return "✅ 우위 신호 — 승격 규칙(60일 + 확인 20일) 대상"
    return "➖ 뚜렷한 차이 없음"


def P(x):
    return "—" if x is None or x != x else f"{x * 100:+.2f}%"


def main(src=None, now: pd.Timestamp | None = None) -> int:
    now = now or pd.Timestamp.now(tz="Asia/Seoul")
    today = now.strftime("%Y-%m-%d")
    src = src or NaverSource()
    idx = {m: bar_today(src, m, today) for m in ("KOSPI", "KOSDAQ")}
    if not idx["KOSPI"]:
        print(f"{today}: 오늘 일봉이 없음 (휴장이거나 아직 미반영) — 평가 건너뜀")
        return 0
    lists = collect(today)
    if not lists:
        print(f"{today}: 오늘 아침 목록이 없음 — 평가 건너뜀")
        return 0
    mk = markets()
    rows = _j(ROWS) or []
    rows = [r for r in rows if r["day"] != today]  # 같은 날 다시 돌면 덮어씀
    bars = {}
    for L in lists:
        for it in L["items"]:
            c = it["code"]
            if c not in bars:
                bars[c] = bar_today(src, c, today)
            b = bars[c]
            if not b:
                continue
            m = mk.get(c) or "KOSPI"
            ib = idx.get(m if m in idx else "KOSPI") or idx["KOSPI"]
            ex = exits(b)
            rows.append({"day": today, "list": L["key"], "code": c, "name": it["name"], "market": m,
                         "gap": b["o"] / b["pc"] - 1, "chg": b["c"] / b["pc"] - 1,
                         "omax": b["h"] / b["o"] - 1, "omin": b["l"] / b["o"] - 1, "close": b["c"],
                         "idx_oc": ib["c"] / ib["o"] - 1, **ex})
    CFG.ensure_dirs()
    ROWS.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")

    R = pd.DataFrame(rows)
    out_lists, advice = [], []
    T = R[R["day"] == today]
    for L in lists:
        t = T[T["list"] == L["key"]]
        h = R[R["list"] == L["key"]]
        daily = h.groupby("day").agg(plain=("plain", "mean"), t3=("t3", "mean"), t3s2=("t3s2", "mean"),
                                     idx=("idx_oc", "mean"))
        tt, nd = tstat(daily["plain"])
        ex_all = float((daily["plain"] - (daily["idx"] - COST)).mean()) if nd else None
        cum = {"days": nd, "picks": int(len(h)), "mean": float(daily["plain"].mean()) if nd else None, "t": tt,
               "hit": float((h["plain"] > 0).mean()) if len(h) else None, "ex_idx": ex_all,
               "rules": {k: float(daily[k].mean()) for k in ("plain", "t3", "t3s2")} if nd else {},
               "tgt3": float((h["omax"] >= TGT).mean()) if len(h) else None}
        today_s = None
        if len(t):
            today_s = {"n": int(len(t)), "mean": float(t["plain"].mean()), "hit": float((t["plain"] > 0).mean()),
                       "ex_idx": float((t["plain"] - (t["idx_oc"] - COST)).mean()),
                       "tgt3": float((t["omax"] >= TGT).mean()), "stp2": float((t["omin"] <= -STP).mean()),
                       "both": float(((t["omax"] >= TGT) & (t["omin"] <= -STP)).mean())}
        out_lists.append({
            "key": L["key"], "label": L["label"], "horizon": L["horizon"], "today": today_s, "cum": cum,
            "verdict": verdict(nd, tt, ex_all),
            "items": [{"code": r.code, "name": r.name, "gap": r.gap, "oc": r.plain + COST, "chg": r.chg,
                       "omax": r.omax, "omin": r.omin, "hit": bool(r.plain > 0)} for r in t.itertuples()],
        })

    # ── 조언 ──
    kchg = idx["KOSPI"]["c"] / idx["KOSPI"]["pc"] - 1
    kq = idx["KOSDAQ"]
    advice.append(f"시장: 코스피 {P(kchg)}" + (f" · 코스닥 {P(kq['c'] / kq['pc'] - 1)}" if kq else ""))
    scored = [x for x in out_lists if x["today"]]
    if scored:
        best = max(scored, key=lambda x: x["today"]["ex_idx"])
        worst = min(scored, key=lambda x: x["today"]["ex_idx"])
        advice.append(f"오늘 가장 나은 목록: {best['label']} — 시가→종가 {P(best['today']['mean'])} (지수 대비 {P(best['today']['ex_idx'])})")
        if worst is not best:
            advice.append(f"오늘 가장 나쁜 목록: {worst['label']} — {P(worst['today']['mean'])} (지수 대비 {P(worst['today']['ex_idx'])})")
    if len(T):
        U = T.drop_duplicates("code")  # 여러 목록에 겹친 종목은 한 번만
        g = U[U["gap"] >= 0.03]
        if len(g) and len(g) < len(U):
            rest = U[U["gap"] < 0.03]
            advice.append(f"관찰: 갭 +3% 이상으로 시작한 {len(g)}종목의 시가→종가 {P(g['plain'].mean())} vs 나머지 {P(rest['plain'].mean())}"
                          " (기존 검증: 갭상승 시가 매수 하루 평균 −1.11%)")
        day = T[T["list"].str.startswith("v9_")].drop_duplicates("code")
        if len(day):
            hit3 = day[day["omax"] >= TGT]
            faded = hit3[hit3["chg"] < hit3["gap"]]  # 장중 +3% 닿았지만 종가는 시가보다 낮게
            msg = f"관찰: v9 아침 후보 {len(day)}종목 중 장중 시가 대비 +3% 닿은 비율 {len(hit3) / len(day) * 100:.0f}%"
            if len(hit3):
                msg += f" · 그중 {len(faded)}종목은 종가가 시가 아래 — +3% 익절이 종가 보유보다 나았던 경우"
            advice.append(msg)
    for x in out_lists:
        c = x["cum"]
        line = f"{x['label']}: {x['verdict']}"
        if c["days"] >= 5 and c["rules"]:
            r = c["rules"]
            line += f" · 누적 매도 방식 비교 종가 {P(r['plain'])} / +3% 익절 {P(r['t3'])} / +3%·−2% {P(r['t3s2'])} (관찰)"
        advice.append(line)
    advice.append("10일 보유 목록(이 시스템·H5)은 오늘이 보유 1일째 — 판정은 10거래일 뒤 채점으로 한다.")

    out = {"day": today, "generated": now.isoformat(timespec="minutes"), "cost": COST,
           "market": {k: (None if v is None else {"chg": v["c"] / v["pc"] - 1, "oc": v["c"] / v["o"] - 1}) for k, v in idx.items()},
           "lists": out_lists, "advice": advice, "rules_note": __doc__.split("조언 규칙")[1].split("저장:")[0].strip()}
    out["ai"] = ai.comment({"날짜": today, "조언": advice[:8]}) if CFG.ai_provider != "off" else None
    OUT.write_text(json.dumps(out, ensure_ascii=False, default=float), encoding="utf-8")

    msg = [f"🌙 오늘 추천 평가 · {today} (비용 0.25% 차감)"]
    for x in out_lists:
        s = x["today"]
        if s:
            msg.append(f"• {x['label']}: {s['n']}종목 시가→종가 {P(s['mean'])} · 맞음 {s['hit'] * 100:.0f}% · 지수 대비 {P(s['ex_idx'])}")
    msg.append("")
    msg += [f"- {a}" for a in advice]
    if out["ai"]:
        msg.append("\n🤖 " + out["ai"])
    notify.send("\n".join(msg))
    print(f"평가 완료 {today}: 목록 {len(out_lists)} · 종목 {len(T)} · 누적 {R['day'].nunique()}일")
    return 0


if __name__ == "__main__":
    sys.exit(main())
