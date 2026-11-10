"""서버 PC → GitHub 수급 업로드 (매일 07:30, 작업 스케줄러).

C:\\toss-core\\flow.db 의 inv 표(투자자별 매매)를 읽어 이 저장소의 data/flow 로 내보내고 푸시한다.
푸시되면 GitHub Actions(daily-desk)가 바로 아침 분석을 시작한다.

    python pc\\export_flow.py                      # 매일: 새 날짜만 daily 파일로
    python pc\\export_flow.py --rebuild            # 전체 이력 다시 만들기 (첫 실행·월 1회 자동)
    python pc\\export_flow.py --dry-run            # 파일만 만들고 푸시 안 함
    python pc\\export_flow.py --db D:\\other\\flow.db

파일 구성 (저장소 크기 증가를 막기 위해 나눔)
  data/flow/flow.csv.gz            유니버스 종목 전체 이력 (월 1회 재작성)
  data/flow/daily/YYYY-MM-DD.csv   그 뒤로 매일 추가되는 하루치
열: date,code,inst_net,frgn_net,frgn_hold_pct   (순매매는 '주' 단위)
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "flow"
BASE = OUT / "flow.csv.gz"
DAILY = OUT / "daily"
COLS = ["date", "code", "inst_net", "frgn_net", "frgn_hold_pct"]


def git(*args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, encoding="utf-8")
    if check and r.returncode != 0:
        sys.exit(f"[git {' '.join(args)}] 실패:\n{r.stderr.strip()}")
    return r.stdout.strip()


def norm_date(s: pd.Series) -> pd.Series:
    s = s.astype(str).str.replace("-", "").str.slice(0, 8)
    return pd.to_datetime(s, format="%Y%m%d", errors="coerce")


def read_inv(db: Path, codes: set[str] | None, since: str | None) -> pd.DataFrame:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)  # 읽기 전용 — 토스 코어와 충돌 없음
    inv = pd.read_sql("SELECT symbol, date, frg, inst, hold FROM inv", con)
    px = pd.read_sql("SELECT symbol, date, c, v FROM px", con)
    con.close()
    for d in (inv, px):
        d["code"] = d["symbol"].astype(str).str[-6:]
        d["date"] = norm_date(d["date"])
    if codes:
        inv, px = inv[inv["code"].isin(codes)], px[px["code"].isin(codes)]
    if since:
        inv = inv[inv["date"] > pd.Timestamp(since)]
    inv = inv.dropna(subset=["date"])

    # 단위 자동 판정: 순매매가 '금액(원)' 이면 종가로 나눠 '주' 로 맞춘다.
    m = inv.merge(px[["code", "date", "c", "v"]], on=["code", "date"], how="left")
    ratio = (m["frg"].abs() / m["v"].replace(0, float("nan"))).median()
    is_amount = bool(ratio and ratio > 50)
    if is_amount:
        for col in ("frg", "inst"):
            m[col] = m[col] / m["c"]
    print(f"  순매매 단위: {'금액→주 환산' if is_amount else '주'} (중앙값 |외국인|/거래량 = {ratio:.3g})")
    out = m.rename(columns={"inst": "inst_net", "frg": "frgn_net", "hold": "frgn_hold_pct"})[COLS]
    return out.sort_values(["date", "code"])


def universe_codes() -> set[str] | None:
    p = REPO / "state" / "universe.json"
    if not p.exists():
        return None
    return {d["code"] for d in json.loads(p.read_text(encoding="utf-8"))}


def last_exported() -> str | None:
    days = sorted(p.stem for p in DAILY.glob("*.csv")) if DAILY.exists() else []
    if days:
        return days[-1]
    if BASE.exists():
        return str(pd.read_csv(BASE, usecols=["date"])["date"].max())[:10]
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=r"C:\toss-core\flow.db")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    db = Path(a.db)
    if not db.exists():
        sys.exit(f"[중단] {db} 가 없습니다. --db 로 위치를 알려 주세요.")

    if not a.dry_run:
        git("pull", "--rebase", "-q", "origin", "main")
    codes = universe_codes()
    print(f"유니버스 {len(codes) if codes else '전체'}종목 · DB {db}")
    OUT.mkdir(parents=True, exist_ok=True)
    DAILY.mkdir(exist_ok=True)

    # 월이 바뀌었거나 기본 파일이 없으면 전체 재작성 (새로 편입된 종목의 이력 확보)
    meta_p = OUT / "meta.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else {}
    this_month = date.today().strftime("%Y-%m")
    rebuild = a.rebuild or not BASE.exists() or meta.get("base_month") != this_month

    if rebuild:
        df = read_inv(db, codes, None)
        df.to_csv(BASE, index=False, date_format="%Y-%m-%d", compression="gzip")
        for p in DAILY.glob("*.csv"):
            p.unlink()
        meta["base_month"] = this_month
        meta_p.write_text(json.dumps(meta), encoding="utf-8")
        msg = f"flow: 전체 재작성 {df['date'].min().date()}~{df['date'].max().date()} ({df['code'].nunique()}종목)"
    else:
        since = last_exported()
        df = read_inv(db, codes, since)
        if df.empty:
            print(f"새 수급 없음 (마지막 {since}) — 푸시하지 않음")
            return 0
        for d, g in df.groupby(df["date"].dt.strftime("%Y-%m-%d")):
            g.to_csv(DAILY / f"{d}.csv", index=False, date_format="%Y-%m-%d")
        msg = f"flow: {df['date'].min().date()}~{df['date'].max().date()} 추가 ({df['code'].nunique()}종목)"
    print(msg)

    if a.dry_run:
        print("--dry-run: 푸시하지 않음")
        return 0
    git("add", "data/flow")
    if not git("diff", "--cached", "--name-only"):
        print("변경 없음")
        return 0
    git("commit", "-q", "-m", msg)
    git("push", "-q", "origin", "main")
    print("푸시 완료 → GitHub Actions 아침 분석 시작")
    return 0


if __name__ == "__main__":
    sys.exit(main())
