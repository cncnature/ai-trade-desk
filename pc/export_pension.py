"""서버 PC → GitHub: flow.db 에 연기금 순매수 열이 있으면 data/flow/pension.csv.gz 로 내보내고 푸시 (연속 순매수 검증용).

    python pc\\export_pension.py            # 표 구조를 보여 주고, 연기금 열을 찾으면 내보냄
    python pc\\export_pension.py --col 열이름  # 자동으로 못 찾을 때 직접 지정
"""
from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "flow" / "pension.csv.gz"
HINTS = ("pension", "pen", "fund", "nps", "연기금", "pnsn", "annuity")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=r"C:\toss-core\flow.db")
    ap.add_argument("--col")
    a = ap.parse_args()
    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True, timeout=30)
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    found = None
    for t in tables:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"[{t}] {n:,}행  열: {', '.join(cols)}")
        for c in cols:
            if (a.col and c == a.col) or (not a.col and any(h in c.lower() for h in HINTS)):
                if {"symbol", "date"} <= set(cols):
                    found = found or (t, c)
    if not found:
        print("\n연기금 열을 찾지 못했습니다 — 위 출력 전체를 보내 주세요 (열 이름을 보고 --col 로 지정하거나 수집을 추가합니다).")
        return 1
    t, c = found
    print(f"\n연기금 열: {t}.{c} → 내보냄")
    df = pd.read_sql(f"SELECT symbol, date, {c} AS pen_q FROM {t}", con)
    px = pd.read_sql("SELECT symbol, date, c FROM px", con) if "px" in tables else None
    con.close()
    df["code"] = df["symbol"].astype(str).str[-6:]
    df["date"] = pd.to_datetime(df["date"].astype(str).str[:10], errors="coerce")
    if px is not None:
        px["code"] = px["symbol"].astype(str).str[-6:]
        px["date"] = pd.to_datetime(px["date"].astype(str).str[:10], errors="coerce")
        df = df.merge(px[["code", "date", "c"]], on=["code", "date"], how="left")
        df["pen_net"] = df["pen_q"] * df["c"].fillna(0)   # 수량 × 종가 (부호만 쓰므로 근사로 충분)
    else:
        df["pen_net"] = df["pen_q"]
    df = df.dropna(subset=["date"])[["date", "code", "pen_net"]].sort_values(["date", "code"])
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)
    print(f"{len(df):,}행 {df['code'].nunique()}종목 {df['date'].min()}~{df['date'].max()} · 0 아닌 비율 {(df['pen_net'] != 0).mean():.0%}")
    for g in (["pull", "--rebase", "-q", "origin", "main"], ["add", str(OUT.relative_to(REPO))],
              ["commit", "-q", "-m", "pension: flow.db 연기금 순매수"], ["push", "-q", "origin", "main"]):
        r = subprocess.run(["git", "-C", str(REPO), *g], capture_output=True, text=True)
        if r.returncode and g[0] != "commit":
            sys.exit(f"git {g[0]} 실패: {r.stderr}")
    print("푸시 완료 — 이제 research 워크플로를 다시 돌리면 연기금·외국인+연기금도 계산됩니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
