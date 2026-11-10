"""서버 PC → GitHub: v9 아침 후보 (참고용, 매매 안 함) — 평일 07:45.

C:\\chart-scout\\morning_pdf.db (07:40 아침 PDF 가 기록) 를 읽기 전용으로 열어
오늘 후보와 기법별 누적 채점 결과를 docs/data/v9_morning.json 으로 올린다.

    python pc\\export_v9_morning.py
    python pc\\export_v9_morning.py --db D:\\x\\morning_pdf.db --dry-run

기법·문턱 (사용자 지정 2026-11-10)
  open      ① 시초가 단타 후보 (단타 무기고)        점수 ≥ 65
  closing   ② 종가매수 → 시초가매도 (단타 무기고)   점수 ≥ 65
  limitup   ③ 상한가 후보 (수급 검증, 모의매매 관제) 전부
  ignition  ④ 점화 레이더 (차트의 신)              점수 ≥ 50
  advisor   ⑤ AI 자가조언 — 매수 추천 브리핑        전부
채점 (morning_pdf 가 정규장 일봉으로 계산한 값 그대로): gap 전일 기준가→시가, oc 시가→종가, omax/omin 시가→장중 최고/최저, d3 3일 뒤
통계는 문턱을 넘은 후보만, 날짜별 평균을 표본 1개로 본다(같은 날 종목끼리는 독립이 아님).
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "docs" / "data" / "v9_morning.json"
SRC = {
    "open": ("① 시초가 단타 후보", "단타 무기고", 65),
    "closing": ("② 종가매수 → 시초가매도", "단타 무기고", 65),
    "limitup": ("③ 상한가 후보 (수급 검증)", "모의매매 관제", None),
    "ignition": ("④ 점화 레이더", "차트의 신", 50),
    "advisor": ("⑤ AI 자가조언 매수 브리핑", "모의매매 AI 자가조언", None),
}
METRICS = ("gap", "oc", "omax", "omin", "d3")


def git(*a: str) -> str:
    r = subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        sys.exit(f"[git {' '.join(a)}] 실패:\n{r.stderr.strip()}")
    return r.stdout.strip()


def tstat(x: pd.Series) -> tuple[float | None, int]:
    x = x.dropna()
    n = len(x)
    if n < 3 or x.std() == 0:
        return None, n
    return float(x.mean() / x.std(ddof=1) * math.sqrt(n)), n


def clean(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def build(db: Path) -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
    picks = pd.read_sql("SELECT * FROM picks", con)
    res = pd.read_sql("SELECT * FROM res", con)
    con.close()
    picks = picks[picks["src"].isin(SRC)]
    keep = picks.apply(lambda r: SRC[r["src"]][2] is None or (r["score"] or 0) >= SRC[r["src"]][2], axis=1)
    picks = picks[keep]
    m = picks.merge(res, on=["day", "src", "code"], how="left")
    last = picks["day"].max() if len(picks) else None

    out = {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "day": last,
           "note": "v9 서버 아침 PDF 후보를 그대로 옮긴 참고 목록 — 이 시스템은 이 종목들로 매매하지 않는다.",
           "sections": []}
    for key, (label, origin, th) in SRC.items():
        g = m[m["src"] == key]
        today = g[g["day"] == last].sort_values("rank")
        stats = {}
        for k in METRICS:
            daily = g.groupby("day")[k].mean()
            t, n = tstat(daily)
            stats[k] = {"mean": clean(float(daily.mean())) if n else None, "t": t, "days": n,
                        "picks": int(g[k].notna().sum())}
        oc = g["oc"].dropna()
        stats["win_oc"] = clean(float((oc > 0).mean())) if len(oc) else None
        out["sections"].append({
            "key": key, "label": label, "origin": origin, "threshold": th,
            "today": [{"code": r.code, "name": r.name, "ref": clean(r.ref), "score": clean(r.score), "rank": int(r.rank)}
                      for r in today.itertuples()],
            "stats": stats, "first_day": g["day"].min() if len(g) else None,
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=r"C:\chart-scout\morning_pdf.db")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    db = Path(a.db)
    if not db.exists():
        sys.exit(f"[중단] {db} 가 없습니다.")
    if not a.dry_run:
        git("pull", "--rebase", "-q", "origin", "main")
    data = build(db)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    n = sum(len(s["today"]) for s in data["sections"])
    print(f"v9 아침 후보 {data['day']} · {n}종목 (문턱 적용)")
    if a.dry_run:
        return 0
    git("add", str(OUT.relative_to(REPO)))
    if not git("diff", "--cached", "--name-only"):
        print("변경 없음")
        return 0
    git("commit", "-q", "-m", f"v9 morning: {data['day']} ({n}종목)")
    git("push", "-q", "origin", "main")
    print("푸시 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
