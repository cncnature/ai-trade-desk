"""장 마감 후 1회 실행: 수집 → 검증 → 후보 → 모의매매 → 대시보드 JSON → 텔레그램.

    python run_daily.py                 # 기본 (검증은 월요일 또는 캐시 없을 때만 재실행)
    python run_daily.py --revalidate    # 검증 강제 재실행
    DESK_DATA=synthetic python run_daily.py   # 오프라인 시험(가짜 시세)
"""
from __future__ import annotations

import argparse
import json
import sys

import pandas as pd

from desk import ai, notify, score, validate
from desk import events as event_strategy
from desk import h5
from desk.broker import PaperBroker
from desk.config import CFG
from desk.data import flow_universe, get_source, load_index, load_stock
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
    uni = flow_universe() if CFG.universe_mode == "flow" else None
    if not uni:
        if CFG.universe_mode == "flow":
            print("  flow 유니버스 파일이 없어 시총 상위로 대신합니다.")
        uni = src.universe(CFG.top_n_per_market)
    names = {s.code: (s.name, s.market) for s in uni}
    frames, failed = {}, []
    now_kst = pd.Timestamp.now(tz="Asia/Seoul")
    today_kst = now_kst.normalize().tz_localize(None)
    for i, s in enumerate(uni, 1):
        try:
            df = load_stock(src, s.code, CFG.history_days)
            # 장중(15:40 전)에 돌면 오늘 봉은 미완성 — 판정·체결에 쓰지 않는다
            if CFG.data_source != "synthetic" and len(df) and df.index[-1] >= today_kst and now_kst.strftime("%H:%M") < "15:40":
                df = df[df.index < today_kst]
            frames[s.code] = df
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
    cal = kospi.index  # 거래일 달력

    # ── 검증: 주 1회(월요일 KST) + 자료 범위가 바뀌면 즉시 ──
    # 매일 다시 보면 확인 구간이 사실상 탐색 구간이 되므로 평소엔 재사용한다.
    # 단, 수집 기간·종목 수·수급 연결 여부·규칙이 바뀌면 이전 판정은 다른 자료에 대한 것이므로 다시 한다.
    flow_cov = sum(1 for f in frames.values() if f["frgn_hold_pct"].notna().any())
    basis = {
        "panel_from": str(panel["date"].min().date()), "codes": int(panel["code"].nunique()),
        "flow": int(round(flow_cov / 20)) * 20,  # 소폭 변동에는 반응하지 않게 20 단위
        "hold": CFG.hold_days, "q": CFG.top_quantile, "cost": CFG.cost_roundtrip,
        "factors": sorted(validate.FACTORS), "tested": CFG.tested(validate.FACTORS),
        "universe": CFG.universe_mode, "liq": CFG.liq_cut,
        "div_events": len(event_strategy.load_events()) // 100 * 100,  # 배당 공시가 100건 늘 때마다 재검증
    }
    vpath = CFG.state_dir / "validation.json"
    old = json.loads(vpath.read_text()) if vpath.exists() else None
    monday = pd.Timestamp.now(tz="Asia/Seoul").weekday() == 0
    reason = ("강제" if args.revalidate else "첫 실행" if old is None else
              "자료 범위 변경" if old.get("basis") != basis else "월요일" if monday else None)
    if reason:
        print(f"  검증 재실행 ({reason})", flush=True)
        index_fwd = kospi.shift(-CFG.hold_days) / kospi - 1
        val = validate.clean_json(validate.run(panel, index_fwd))
        val["events"] = [validate.clean_json(event_strategy.run(frames, cal))]
        val["passed"] += [e["factor"] for e in val["events"] if e["status"] == "통과"]
        val["basis"], val["reason"] = basis, reason
        vpath.write_text(json.dumps(val, ensure_ascii=False, indent=1))
    else:
        val = old

    # ── 후보 ──
    paper = PaperBroker()
    picks = score.pick(panel, val, names, paper.equity())
    if event_strategy.NAME in val["passed"]:  # 배당 D-3 은 검정을 통과했을 때만 후보로
        have = {p["code"] for p in picks}
        picks += [p for p in event_strategy.upcoming(frames, cal, pd.Timestamp(asof), names) if p["code"] not in have]
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
    # 내일 시가 주문은 매 실행마다 최신 후보로 다시 등록한다 (같은 날 재실행해도 주문이 빠지지 않게)
    paper.s["pending"] = []
    paper.queue(picks)
    paper.save()
    paper.mark_index({d.strftime("%Y-%m-%d"): float(v) for d, v in kospi.items()},
                      {d.strftime("%Y-%m-%d"): float(v) for d, v in kosdaq.items()})
    paper.save()
    psum = paper.summary()
    try:  # 참고 칸: H5-v1 외국인 보유율 상위 20% (v9 아침 리포트와 같은 선정) + 자체 채점
        h5res = h5.run(panel, names, kospi.shift(-CFG.hold_days) / kospi - 1)
    except Exception as ex:
        h5res = {"error": f"{type(ex).__name__}: {ex}"[:200]}

    # ── 대시보드 데이터 ──
    def idx(s: pd.Series) -> dict:
        return {"close": round(float(s.iloc[-1]), 2), "chg_pct": round(float(s.iloc[-1] / s.iloc[-2] - 1) * 100, 2),
                "spark": [round(float(x), 2) for x in s.tail(120)]}

    out = validate.clean_json({
        "asof": asof, "generated": pd.Timestamp.now(tz="Asia/Seoul").isoformat(timespec="minutes"),
        "source": CFG.data_source, "universe": len(frames), "failed": len(failed),
        "telegram": bool(CFG.telegram_token and CFG.telegram_chat),
        "flow_coverage": sum(1 for f in frames.values() if f["frgn_hold_pct"].notna().any()),
        "market": {"KOSPI": idx(kospi), "KOSDAQ": idx(kosdaq)},
        "validation": val, "picks": picks, "paper": psum, "events": events, "h5": h5res,
    })
    (CFG.docs_data_dir / "latest.json").write_text(json.dumps(out, ensure_ascii=False))
    hist = CFG.docs_data_dir / "history"
    hist.mkdir(exist_ok=True)
    (hist / f"{asof}.json").write_text(json.dumps({"asof": asof, "picks": picks, "passed": val["passed"]},
                                                  ensure_ascii=False))

    try:  # 아침 브리핑: 어제 평가 요약 + 누적 + 오늘 주문 + 지수 대비 + 점검 (텔레그램 한 통, 대시보드 맨 위 카드)
        from desk import brief

        b = brief.build(out)
        (CFG.docs_data_dir / "brief.json").write_text(json.dumps(b, ensure_ascii=False, default=float), encoding="utf-8")
        msg = brief.text(b)
    except Exception as ex:
        print(f"아침 브리핑 실패 — 기존 메시지로 대신: {type(ex).__name__}: {ex}")
        msg = notify.daily_message(asof, val, picks, psum, events)
    if args.no_notify:
        print(msg)
    else:
        notify.send(msg)
    print(f"완료: {asof} · 종목 {len(frames)} · 수급 확보 {out['flow_coverage']} · 통과 팩터 {val['passed']} · 후보 {len(picks)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
