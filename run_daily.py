"""장 마감 후 1회 실행: 수집 → 검증 → 후보 → 모의매매 → 대시보드 JSON → 텔레그램.

    python run_daily.py                 # 기본 (검증은 월요일 또는 캐시 없을 때만 재실행)
    python run_daily.py --revalidate    # 검증 강제 재실행
    DESK_DATA=synthetic python run_daily.py   # 오프라인 시험(가짜 시세)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date

import pandas as pd

from desk import ai, notify, score, validate
from desk.broker import PaperBroker
from desk.config import CFG
from desk.data import get_source, load_index, load_stock
from desk.factors import build_panel


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--revalidate", action="store_true")
    ap.add_argument("--no-notify", action="store_true")
    ap.add_argument("--diag", action="store_true", help="데이터 주소 접속 점검만 하고 종료")
    ap.add_argument("--skip-if-done-today", action="store_true",
                    help="오늘(KST) 이미 실행됐으면 건너뜀 — 아침 예비 실행용")
    args = ap.parse_args()
    CFG.ensure_dirs()
    if args.skip_if_done_today:
        lp = CFG.docs_data_dir / "latest.json"
        today = pd.Timestamp.now(tz="Asia/Seoul").strftime("%Y-%m-%d")
        if lp.exists() and json.loads(lp.read_text()).get("generated", "").startswith(today) \
                and json.loads(lp.read_text()).get("source") != "synthetic":
            print(f"오늘({today}) 이미 실행됨 — 건너뜀")
            return 0
    if args.diag:
        from desk.data import diagnose

        diagnose()
        return 0

    src = get_source()
    uni = src.universe(CFG.top_n_per_market)
    names = {s.code: (s.name, s.market) for s in uni}
    frames, failed = {}, []
    for i, s in enumerate(uni, 1):
        try:
            frames[s.code] = load_stock(src, s.code, CFG.history_days)
        except Exception as e:  # 한 종목 실패가 전체를 멈추지 않게
            failed.append((s.code, type(e).__name__))
        if i % 25 == 0:
            print(f"  수집 {i}/{len(uni)}", flush=True)
    if not frames:
        print(f"수집된 종목이 없습니다. 유니버스 {len(uni)}종목, 실패 예시 {failed[:5]}")
        print("→ python run_daily.py --diag 로 어느 주소가 막혔는지 확인하세요.")
        return 1

    kospi = load_index(src, "KOSPI", CFG.history_days)
    kosdaq = load_index(src, "KOSDAQ", CFG.history_days)
    panel = build_panel(frames, CFG.hold_days, CFG.cost_roundtrip)
    asof = str(panel["date"].max().date())

    # ── 검증 (주 1회) ──
    vpath = CFG.state_dir / "validation.json"
    if args.revalidate or not vpath.exists() or date.today().weekday() == 0:
        index_fwd = kospi.shift(-CFG.hold_days) / kospi - 1
        val = validate.clean_json(validate.run(panel, index_fwd))
        vpath.write_text(json.dumps(val, ensure_ascii=False, indent=1))
    else:
        val = json.loads(vpath.read_text())

    # ── 후보 ──
    paper = PaperBroker()
    picks = score.pick(panel, val, names, paper.equity())
    for p in picks:
        p["spark"] = [round(x) for x in frames[p["code"]]["close"].tail(60).tolist()]
        p["ai_note"] = ai.explain(p, val)

    # ── 모의매매: 오늘 봉으로 체결·청산 후, 내일 시가 주문 등록 ──
    events = []
    last_done = paper.s["curve"][-1]["date"] if paper.s["curve"] else ""
    if asof > last_done:
        bars = {}
        for code, df in frames.items():
            if df.index[-1].strftime("%Y-%m-%d") == asof:
                r = df.iloc[-1]
                bars[code] = {"open": float(r["open"]), "high": float(r["high"]),
                              "low": float(r["low"]), "close": float(r["close"])}
        events = paper.process_day(asof, bars, CFG.cost_roundtrip)
        paper.queue(picks)
        paper.save()
    psum = paper.summary()

    # ── 대시보드 데이터 ──
    def idx(s: pd.Series) -> dict:
        return {"close": round(float(s.iloc[-1]), 2), "chg_pct": round(float(s.iloc[-1] / s.iloc[-2] - 1) * 100, 2),
                "spark": [round(float(x), 2) for x in s.tail(120)]}

    out = validate.clean_json({
        "asof": asof, "generated": pd.Timestamp.now(tz="Asia/Seoul").isoformat(timespec="minutes"),
        "source": CFG.data_source, "universe": len(frames), "failed": len(failed),
        "flow_coverage": sum(1 for f in frames.values() if f["frgn_hold_pct"].notna().any()),
        "market": {"KOSPI": idx(kospi), "KOSDAQ": idx(kosdaq)},
        "validation": val, "picks": picks, "paper": psum, "events": events,
    })
    (CFG.docs_data_dir / "latest.json").write_text(json.dumps(out, ensure_ascii=False))
    hist = CFG.docs_data_dir / "history"
    hist.mkdir(exist_ok=True)
    (hist / f"{asof}.json").write_text(json.dumps({"asof": asof, "picks": picks, "passed": val["passed"]},
                                                  ensure_ascii=False))

    msg = notify.daily_message(asof, val, picks, psum, events)
    if args.no_notify:
        print(msg)
    else:
        notify.send(msg)
    print(f"완료: {asof} · 종목 {len(frames)} · 수급 확보 {out['flow_coverage']} · 통과 팩터 {val['passed']} · 후보 {len(picks)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
