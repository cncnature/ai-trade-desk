"""아침 브리핑 — 아침 분석 직후(평일 07:30~08:10) 텔레그램 한 통 + 대시보드 맨 위 카드.

① 어제 결과   직전 거래일 저녁 평가(eval.json): 목록별 시가→종가·맞음·지수 대비, 가장 나은/나쁜 목록
② 누적 판단   목록별 채점 일수·평균·t·판정, 20일 판단까지 남은 날
③ 모의 계좌   평가 자산, '지수만 샀다면'(코스피 / 코스피·코스닥 반반) 대비, 보유·오늘 시가 주문, 목표·손절 설정
④ 오늘 후보   검증 통과 규칙의 후보 수와 상위 몇 종목 (순서·점수는 규칙이 낸 그대로)
⑤ 점검        수급 자료 날짜·배당 판정 상태 등 아침에 확인 가능한 것
숫자는 모두 저장된 결과를 옮길 뿐 새로 해석하지 않는다. 하루 결과로 결론을 내리지 않는다.
"""
from __future__ import annotations

import json

from .config import CFG

MIN_DAYS = 20


def P(x, d=2):
    return "—" if x is None or x != x else f"{x * 100:+.{d}f}%"


def _j(name):
    p = CFG.docs_data_dir / name
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except Exception:
        return None


def build(latest: dict) -> dict:
    asof = latest["asof"]                       # 직전 거래일 (분석 기준일)
    today = str(latest["generated"])[:10]
    ev = _j("eval.json")
    b = {"date": today, "asof": asof, "generated": latest["generated"]}

    # ① 어제 결과 — 직전 거래일 평가만 (더 오래된 평가는 '없음'으로)
    if ev and ev.get("day") == asof:
        rows = []
        for L in ev["lists"]:
            t = L.get("today")
            if t:
                rows.append({"label": L["label"], "key": L["key"], "n": t["n"], "mean": t["mean"], "hit": t["hit"],
                             "ex_idx": t["ex_idx"]})
        m = ev.get("market") or {}
        b["yesterday"] = {"day": ev["day"], "rows": rows,
                          "kospi": (m.get("KOSPI") or {}).get("chg"), "kosdaq": (m.get("KOSDAQ") or {}).get("chg")}
        if rows:
            b["yesterday"]["best"] = max(rows, key=lambda r: r["ex_idx"])["label"]
            b["yesterday"]["worst"] = min(rows, key=lambda r: r["ex_idx"])["label"]
    else:
        b["yesterday"] = None

    # ② 누적 판단
    cum = []
    for L in (ev or {}).get("lists", []):
        c = L["cum"]
        cum.append({"label": L["label"], "days": c["days"], "mean": c["mean"], "t": c["t"], "ex_idx": c["ex_idx"],
                    "verdict": L["verdict"], "left": max(0, MIN_DAYS - c["days"])})
    b["cumulative"] = cum

    # ③ 모의 계좌
    p = latest["paper"]
    bench = p.get("bench") or {}
    b["paper"] = {"equity": p["equity"], "ret_pct": p["total_ret_pct"], "trades": p["trades"], "win_rate": p["win_rate"],
                  "positions": len(p["positions"]), "pending": [o["name"] for o in p["pending"]],
                  "pending_at": (p["pending"][0].get("queued_at") if p["pending"] else None),
                  "rules": p.get("rules") or {}, "bench": {k: bench.get(k) for k in
                                                           ("from", "kospi_ret_pct", "mix_ret_pct", "vs_kospi_pct", "vs_mix_pct")}}

    # ④ 오늘 후보
    b["picks"] = {"n": len(latest["picks"]), "top": [x["name"] for x in latest["picks"][:5]],
                  "passed": latest["validation"]["passed"]}
    h5 = latest.get("h5") or {}
    b["h5"] = [x["name"] for x in (h5.get("picks") or [])[:5]]

    # ⑤ 점검 (아침에 알 수 있는 것)
    checks = []
    hb = None
    try:
        hb = json.loads((CFG.cache_dir.parent / "flow" / "heartbeat.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    if hb and hb.get("flow_last"):
        ok = hb["flow_last"] >= asof
        checks.append(("수급 자료", "✅" if ok else "⚠", f"{hb['flow_last']}" + ("" if ok else f" — 기준일 {asof} 수급 아님 (어제 수급으로 분석)")))
    if latest.get("telegram") is False:
        checks.append(("텔레그램", "⚠", "미설정"))
    for e in latest["validation"].get("events") or []:
        checks.append(("배당 D-3", "✅" if e["status"] == "통과" else "ℹ", f"{e['status']} · 이벤트 {e.get('events', 0)}건"))
    b["checks"] = [{"name": n, "st": s, "note": t} for n, s, t in checks]
    return b


def text(b: dict) -> str:
    L = [f"☀️ 아침 브리핑 · {b['date']} (기준일 {b['asof']})"]
    y = b.get("yesterday")
    if y:
        L.append(f"\n① 어제 결과 ({y['day']}) — 코스피 {P(y['kospi'])} · 코스닥 {P(y['kosdaq'])}")
        for r in y["rows"]:
            L.append(f"• {r['label']}: 시가→종가 {P(r['mean'])} · 맞음 {r['hit'] * 100:.0f}% · 지수 대비 {P(r['ex_idx'])}")
        if y.get("best"):
            L.append(f"지수 대비 가장 나음: {y['best']} / 가장 나쁨: {y['worst']} (하루 결과 — 결론 아님)")
    else:
        L.append("\n① 어제 결과: 직전 거래일 평가 없음")
    if b["cumulative"]:
        L.append("\n② 누적 판단")
        for c in b["cumulative"]:
            t = "" if c["t"] is None else f", t {c['t']:.2f}"
            L.append(f"• {c['label']}: {c['days']}일 평균 {P(c['mean'])}{t}"
                     + (f" — 판단까지 {c['left']}일" if c["left"] else f" — {c['verdict']}"))
    p = b["paper"]
    bn = p["bench"]
    L.append(f"\n③ 모의 계좌 {p['equity']:,}원 ({p['ret_pct']:+.2f}%)"
             + (f" · 코스피만 샀다면 {bn['kospi_ret_pct']:+.2f}% (차이 {bn['vs_kospi_pct']:+.2f}%p) · 반반 {bn['mix_ret_pct']:+.2f}%"
                if bn.get("kospi_ret_pct") is not None else ""))
    r = p["rules"]
    rule = " · ".join(x for x in (f"전체 목표 +{r['target_pct']:g}%" if r.get("target_pct") else "",
                                  f"전체 손절 -{abs(r['stop_pct']):g}%" if r.get("stop_pct") else "") if x)
    L.append(f"보유 {p['positions']}종목 · 오늘 시가 주문 {len(p['pending'])}종목" + (f" ({rule})" if rule else ""))
    if p["pending"]:
        L.append("  " + ", ".join(p["pending"][:8]) + (" 외" if len(p["pending"]) > 8 else ""))
    pk = b["picks"]
    L.append(f"\n④ 오늘 후보 {pk['n']}종목 (검정 통과: {', '.join(pk['passed']) or '없음'})")
    if pk["top"]:
        L.append("  " + ", ".join(pk["top"]))
    if b.get("h5"):
        L.append("  H5 참고: " + ", ".join(b["h5"]))
    if b["checks"]:
        L.append("\n⑤ 점검: " + " · ".join(f"{c['st']} {c['name']} {c['note']}" for c in b["checks"]))
    L.append("\n※ 모의매매 · 투자 권유 아님 · 지수 기준선은 첫날부터 전액 투자 가정")
    return "\n".join(L)
