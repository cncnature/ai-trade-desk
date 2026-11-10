"""장 마감 뒤(평일 16:00 KST) 화면에 나온 종목들의 오늘 시세를 docs/data/close.json 으로 저장.

대상: v9 아침 후보(docs/data/v9_morning.json) · 이 시스템 후보(latest.json) · 다가오는 배당(배당 캘린더 14일)
값 : 종가, 전일 종가 대비 등락률, 시가→종가, 그리고 v9 후보는 아침 기준가 대비
읽기만 하며 판정·매매에는 쓰지 않는다.
"""
from __future__ import annotations

import json
import sys
from datetime import timedelta

import pandas as pd
import requests

from desk.config import CFG
from desk.data import NaverSource

CAL_URL = "https://raw.githubusercontent.com/cncnature/kr-dividend-calendar/main/site/data.json"


def targets() -> dict[str, float | None]:
    """code → 아침 기준가(없으면 None)."""
    out: dict[str, float | None] = {}
    d = CFG.docs_data_dir
    p = d / "v9_morning.json"
    if p.exists():
        for s in json.loads(p.read_text(encoding="utf-8"))["sections"]:
            for x in s["today"]:
                out[x["code"]] = x.get("ref")
    p = d / "latest.json"
    if p.exists():
        for x in json.loads(p.read_text(encoding="utf-8")).get("picks", []):
            out.setdefault(x["code"], x.get("entry_ref"))
        for c, x in (json.loads(p.read_text(encoding="utf-8")).get("paper") or {}).get("positions", {}).items():
            out.setdefault(c, x.get("entry"))
        for x in (json.loads(p.read_text(encoding="utf-8")).get("h5") or {}).get("picks", []):
            out.setdefault(x["code"], x.get("close"))
    try:
        cal = requests.get(CAL_URL, timeout=20).json()
        today = pd.Timestamp.now(tz="Asia/Seoul").normalize().tz_localize(None)
        lim = today + timedelta(days=14)
        for e in cal.get("events", []):
            if today <= pd.Timestamp(e["last_buy_date"]) <= lim:
                out.setdefault(e["code"], None)
    except Exception as ex:
        print(f"배당 캘린더 읽기 실패: {type(ex).__name__}")
    return out


def main() -> int:
    tg = targets()
    src = NaverSource()
    quotes, day = {}, None
    for code, ref in tg.items():
        try:
            df = src.ohlcv(code, 5)
        except Exception:
            continue
        if len(df) < 2:
            continue
        r, prev = df.iloc[-1], df.iloc[-2]
        day = max(day or "", df.index[-1].strftime("%Y-%m-%d"))
        quotes[code] = {
            "date": df.index[-1].strftime("%Y-%m-%d"), "close": float(r["close"]),
            "chg_pct": round((r["close"] / prev["close"] - 1) * 100, 2),
            "oc_pct": round((r["close"] / r["open"] - 1) * 100, 2) if r["open"] > 0 else None,
            "vs_ref_pct": round((r["close"] / ref - 1) * 100, 2) if ref else None,
        }
    out = {"day": day, "generated": pd.Timestamp.now(tz="Asia/Seoul").isoformat(timespec="minutes"), "quotes": quotes}
    (CFG.docs_data_dir / "close.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"종가 {day} · {len(quotes)}/{len(tg)}종목")
    return 0


if __name__ == "__main__":
    sys.exit(main())
