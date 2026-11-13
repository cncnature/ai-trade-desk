"""KRX 종목별 투자자 순매수 (연기금·외국인) 일별 수집 — 연구용.

하루 × 투자자마다 KRX '투자자별 순매수 상위종목'(전 종목) 1회 호출. 거래일이 아니면 빈 결과라 건너뜀.
출력: data/krx/inv.csv.gz  (date, code, pen_net, frg_net — 순매수 거래대금, 원)  — 이미 받은 날은 다시 받지 않음.
    python tools/fetch_krx_investor.py --days 520
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from pykrx import stock

OUT = Path(__file__).resolve().parent.parent / "data" / "krx" / "inv.csv.gz"
INV = {"연기금": "pen_net", "외국인": "frg_net"}


def one_day(d: str) -> pd.DataFrame | None:
    parts = []
    for inv, col in INV.items():
        for k in range(3):
            try:
                df = stock.get_market_net_purchases_of_equities(d, d, "ALL", inv)
                break
            except Exception as ex:
                print(f"  {d} {inv} 재시도 {k + 1}: {type(ex).__name__}: {ex}", flush=True)
                time.sleep(3 * (k + 1))
        else:
            return None
        if df is None or df.empty:
            return pd.DataFrame()
        parts.append(df["순매수거래대금"].rename(col))
        time.sleep(0.6)
    out = pd.concat(parts, axis=1).fillna(0).reset_index()
    out.columns = ["code", *INV.values()]
    out.insert(0, "date", f"{d[:4]}-{d[4:6]}-{d[6:]}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=520, help="달력일 수 (오늘부터 거꾸로)")
    ap.add_argument("--max-minutes", type=float, default=150)
    a = ap.parse_args()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    old = pd.read_csv(OUT, dtype={"code": str}) if OUT.exists() else pd.DataFrame(columns=["date", "code", *INV.values()])
    have = set(old["date"])
    t0, new, fails = time.time(), [], 0
    days = [date.today() - timedelta(days=i) for i in range(a.days)]
    for dd in days:
        if dd.weekday() >= 5 or dd.isoformat() in have:
            continue
        if (time.time() - t0) / 60 > a.max_minutes:
            print("시간 한도 — 다음 실행에서 이어 받음")
            break
        df = one_day(dd.strftime("%Y%m%d"))
        if df is None:
            fails += 1
            if fails >= 5 and not new:
                print("[중단] KRX 응답이 계속 실패 — 접속 차단 또는 로그인 필요로 보임")
                return 1
            continue
        if len(df):
            new.append(df)
            print(f"{dd} {len(df)}종목", flush=True)
        if len(new) % 20 == 0 and new:
            pd.concat([old, *new]).drop_duplicates(["date", "code"]).sort_values(["date", "code"]).to_csv(OUT, index=False)
    allv = pd.concat([old, *new]).drop_duplicates(["date", "code"]).sort_values(["date", "code"])
    allv.to_csv(OUT, index=False)
    print(f"완료: 새 {len(new)}일 · 전체 {allv['date'].nunique()}일 {allv['date'].min()}~{allv['date'].max()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
