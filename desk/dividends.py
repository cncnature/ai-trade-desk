"""전 종목 배당 이벤트 수집 (OpenDART '현금·현물배당결정' 공시).

배당 캘린더(cncnature/kr-dividend-calendar)의 원문 파서를 그대로 옮겨 전 종목·2020년 이후로 넓혔다.
OpenDART 일일 호출 한도(2만)를 배당 캘린더와 나눠 쓰므로 실행마다 --budget 만큼만 쓰고 이어서 모은다.

    DART_API_KEY=... python -m desk.dividends --budget 8000

저장
  data/dividends/events.json   [{code, rcept_no, disclosed, record_date, dps, kind, report}]
  data/dividends/state.json    {"corps_done": [...], "listed": {code: [rcept...]}, "parsed": [rcept...], "last_scan": "YYYYMMDD"}
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import date, timedelta

from .config import ROOT

OUT = ROOT / "data" / "dividends"
EVENTS = OUT / "events.json"
STATE = OUT / "state.json"
KEY = os.environ.get("DART_API_KEY", "")
LIST_URL = "https://opendart.fss.or.kr/api/list.json?"
DOC_URL = "https://opendart.fss.or.kr/api/document.xml?"
CORP_URL = "https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key="
START = "20200101"


class Budget(Exception):
    pass


class Dart:
    def __init__(self, budget: int):
        self.left = budget

    def get(self, url: str) -> bytes:
        if self.left <= 0:
            raise Budget()
        self.left -= 1
        for i in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    return r.read()
            except Exception:
                if i == 2:
                    raise
                time.sleep(2 * (i + 1))
        return b""

    def corp_codes(self) -> dict[str, str]:
        cache = OUT / "corp_codes.json"
        if cache.exists() and time.time() - cache.stat().st_mtime < 30 * 86400:
            return json.loads(cache.read_text(encoding="utf-8"))
        z = zipfile.ZipFile(io.BytesIO(self.get(CORP_URL + KEY)))
        xml = z.read(z.namelist()[0]).decode("utf-8")
        m = {}
        for blk in re.findall(r"<list>(.*?)</list>", xml, re.S):
            cc = re.search(r"<corp_code>(\d+)</corp_code>", blk)
            sc = re.search(r"<stock_code>\s*(\d{6})\s*</stock_code>", blk)
            if cc and sc:
                m[sc.group(1)] = cc.group(1)
        cache.write_text(json.dumps(m), encoding="utf-8")
        return m

    def list(self, params: dict) -> list[dict]:
        out, page = [], 1
        while True:
            q = dict(crtfc_key=KEY, pblntf_ty="I", page_count=100, page_no=page, **params)
            d = json.loads(self.get(LIST_URL + urllib.parse.urlencode(q)))
            if d.get("status") != "000":
                return out  # 013 = 조회 결과 없음
            out += d.get("list", [])
            if page >= int(d.get("total_page", 1)):
                return out
            page += 1

    def parse_doc(self, rcept_no: str) -> dict:
        z = zipfile.ZipFile(io.BytesIO(self.get(DOC_URL + urllib.parse.urlencode(dict(crtfc_key=KEY, rcept_no=rcept_no)))))
        raw = b"".join(z.read(n) for n in z.namelist() if n.endswith(".xml"))
        for enc in ("utf-8", "euc-kr", "cp949"):
            try:
                txt = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            txt = raw.decode("utf-8", "ignore")
        txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", txt))

        def grab(p):
            m = re.search(p, txt)
            return m.group(1).strip() if m else None

        dps = grab(r"1주당\s*배당금\s*\(?원?\)?\s*보통주식\s*([\d,]+)") or grab(r"보통주식\s*1주당\s*배당금[^\d]*([\d,]+)")
        rec = grab(r"배당기준일\s*(\d{4}-\d{2}-\d{2})")
        kind = grab(r"배당종류\s*([가-힣]+배당)")
        return {"dps": int(dps.replace(",", "")) if dps else None, "record_date": rec, "kind": kind}


def is_div(x: dict) -> bool:
    n = x.get("report_nm", "")
    return "배당" in n and "결정" in n and "자회사" not in n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=8000)
    ap.add_argument("--codes", help="universe.json 경로 (기본 data/flow/universe.json)")
    a = ap.parse_args()
    if not KEY:
        print("DART_API_KEY 없음 — 배당 수집 건너뜀")
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    st = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    st.setdefault("corps_done", []); st.setdefault("listed", {}); st.setdefault("parsed", [])
    events = json.loads(EVENTS.read_text(encoding="utf-8")) if EVENTS.exists() else []
    seen = {e["rcept_no"] for e in events}
    uni_p = a.codes or (ROOT / "data" / "flow" / "universe.json")
    codes = [r["code"] for r in json.loads(open(uni_p, encoding="utf-8").read())]
    dart = Dart(a.budget)
    today = date.today().strftime("%Y%m%d")
    try:
        cmap = dart.corp_codes()
        # ① 종목별 과거 목록 (한 번만)
        done = set(st["corps_done"])
        for code in codes:
            if code in done or code not in cmap:
                continue
            rows = [x for x in dart.list({"corp_code": cmap[code], "bgn_de": START, "end_de": today}) if is_div(x)]
            st["listed"][code] = [[x["rcept_no"], x["rcept_dt"], x["report_nm"].strip()] for x in rows]
            st["corps_done"].append(code)
            done.add(code)
        # ② 과거 수집이 끝난 뒤에는 최근 공시만 시장 전체로 훑는다 (3개월 이내 범위 제한)
        if len(done) >= len([c for c in codes if c in cmap]):
            since = st.get("last_scan") or (date.today() - timedelta(days=60)).strftime("%Y%m%d")
            want = set(codes)
            for x in dart.list({"bgn_de": since, "end_de": today}):
                if is_div(x) and x.get("stock_code") in want:
                    lst = st["listed"].setdefault(x["stock_code"], [])
                    if x["rcept_no"] not in {r[0] for r in lst}:
                        lst.append([x["rcept_no"], x["rcept_dt"], x["report_nm"].strip()])
            st["last_scan"] = today
        # ③ 원문 파싱
        parsed = set(st["parsed"])
        for code, rows in st["listed"].items():
            for rno, rdt, rnm in rows:
                if rno in parsed:
                    continue
                d = dart.parse_doc(rno)
                parsed.add(rno)
                st["parsed"].append(rno)
                if d["record_date"] and rno not in seen:
                    events.append({"code": code, "rcept_no": rno, "disclosed": rdt, "report": rnm, **d})
                    seen.add(rno)
    except Budget:
        print("이번 실행 호출 한도 도달 — 다음 실행에서 이어서 수집")
    finally:
        STATE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        events.sort(key=lambda e: (e["record_date"], e["code"]))
        EVENTS.write_text(json.dumps(events, ensure_ascii=False, indent=0), encoding="utf-8")
    total_listed = sum(len(v) for v in st["listed"].values())
    print(f"종목 {len(st['corps_done'])}/{len(codes)} 목록 완료 · 공시 {total_listed}건 중 {len(st['parsed'])}건 파싱 · "
          f"이벤트 {len(events)}건 · 남은 호출 {dart.left}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
