"""서버 PC → GitHub: 네이버 테마 (관찰용, 매매 안 함) — 평일 07:25 (07:30 아침 분석 전에 준비).

네이버 금융 테마 페이지는 해외(GitHub 서버)에서 막혀 국내 서버 PC 에서 받는다.
  · 테마 목록: 전일 등락률, 최근 3일 등락률, 상승·하락 종목 수
  · 상위 테마의 구성 종목 → flow.db 에서 그 종목들의 전일 외국인+기관 순매수 금액 합 (수급 몰린 테마)
  · 순환 진입: 오늘 상위 10 인데 지난 5거래일 평균 순위는 30위 밖이던 테마 (docs/data/themes_hist.json 에 순위 기록)
  · 대표 종목: 상위 3개 테마에서 전일 등락률 상위 종목 → 저녁 평가가 '어제 강한 테마를 오늘 시가에 샀다면' 으로 채점
모두 관찰용 — 테마 추종이 통한다는 검증은 아직 없다. 채점이 쌓이면 판단한다.

    python pc\\export_themes.py            # 받고 올림
    python pc\\export_themes.py --dry-run  # 받기만
출처: 네이버 모바일 증권 API(m.stock.naver.com/api/stocks/theme) 먼저, 안 되면 옛 PC 테마 페이지.
실패하면 reports\\theme_probe.txt 에 요청·응답 앞부분을 남긴다 (그 파일을 보내 주면 맞춰 고침).
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "docs" / "data" / "themes.json"
HIST = REPO / "docs" / "data" / "themes_hist.json"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": "https://finance.naver.com/sise/theme.naver"}
PCT = re.compile(r"([+-]?\d+(?:\.\d+)?)%")


def git(*a: str) -> str:
    r = subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        sys.exit(f"[git {' '.join(a)}] 실패:\n{r.stderr.strip()}")
    return r.stdout.strip()


DBG = REPO / "reports"
PROBE: list[str] = []          # 실패 시 reports\\theme_probe.txt 로 남길 요청 기록


def _fetch(url: str) -> requests.Response | None:
    for i in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=15)
            PROBE.append(f"{r.status_code} {url} [{r.headers.get('content-type', '')}] {r.content[:300]!r}")
            if r.status_code == 200:
                time.sleep(0.2)
                return r
        except Exception as ex:
            PROBE.append(f"ERR {url} {type(ex).__name__}: {ex}")
        time.sleep(1.5 * (i + 1))
    return None


def get(url: str) -> str:
    """HTML — 문자셋을 응답에서 판단 (네이버 새 페이지는 UTF-8, 옛 페이지는 EUC-KR)."""
    r = _fetch(url)
    if r is None:
        return ""
    raw = r.content
    m = re.search(rb'charset=["\']?([\w-]+)', raw[:3000]) or re.search(r"charset=([\w-]+)", r.headers.get("content-type", ""))
    enc = (m.group(1).decode() if isinstance(m.group(1), bytes) else m.group(1)) if m else "utf-8"
    return raw.decode(enc, errors="replace")


def jget(url: str):
    r = _fetch(url)
    if r is None:
        return None
    try:
        return r.json()
    except Exception:
        return None


def num(x):
    if x is None:
        return None
    try:
        return float(str(x).replace(",", "").replace("%", "").replace("+", ""))
    except ValueError:
        return None


def pick(d: dict, *keys):
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return None


def rows_of(d, must: tuple[str, ...]) -> list[dict]:
    """JSON 어디에 있든 must 중 하나를 가진 dict 들의 목록을 찾아 돌려준다 (필드 이름이 바뀌어도 버티게)."""
    if isinstance(d, list):
        if d and isinstance(d[0], dict) and any(k in d[0] for k in must):
            return d
        for x in d:
            r = rows_of(x, must)
            if r:
                return r
    elif isinstance(d, dict):
        for v in d.values():
            r = rows_of(v, must)
            if r:
                return r
    return []


M_API = "https://m.stock.naver.com/api/stocks/theme"


def theme_list_api() -> list[dict]:
    out, seen = [], set()
    for page in range(1, 11):
        d = jget(f"{M_API}?page={page}&pageSize=100")
        rows = rows_of(d, ("no", "themeNo", "themeId")) if d is not None else []
        n0 = len(out)
        for g in rows:
            no = str(pick(g, "no", "themeNo", "themeId", "id") or "")
            chg = num(pick(g, "changeRate", "fluctuationsRatio", "changeRatio", "rate"))
            if not no or no in seen or chg is None:
                continue
            seen.add(no)
            out.append({"no": no, "name": str(pick(g, "name", "themeName", "groupName") or no).strip(), "chg": chg,
                        "chg3": num(pick(g, "threeDaysChangeRate", "changeRate3d", "recent3DaysChangeRate")),
                        "up": pick(g, "riseCount", "upCount"), "flat": pick(g, "steadyCount", "flatCount"),
                        "down": pick(g, "fallCount", "downCount")})
        if len(out) == n0:
            break
    return out


def members_api(no: str) -> list[dict]:
    d = jget(f"{M_API}/{no}?page=1&pageSize=100")
    out, seen = [], set()
    for x in rows_of(d, ("itemCode", "stockCode", "reutersCode")) if d is not None else []:
        code = str(pick(x, "itemCode", "stockCode", "reutersCode") or "")[:6]
        if not re.fullmatch(r"\d{6}", code) or code in seen:
            continue
        seen.add(code)
        out.append({"code": code, "name": str(pick(x, "stockName", "itemName", "name") or code).strip(),
                    "chg": num(pick(x, "fluctuationsRatio", "changeRate", "changeRatio"))})
    return out


def theme_list_html() -> list[dict]:
    """옛 PC 페이지 (sise/theme.naver) — 새 페이지로 바뀌어 비면 빈 목록."""
    out, seen = [], set()
    for page in range(1, 9):
        html = get(f"https://finance.naver.com/sise/theme.naver?&page={page}")
        n0 = len(out)
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
            m = re.search(r'sise_group_detail\.naver\?type=theme&(?:amp;)?no=(\d+)"[^>]*>([^<]+)</a>', row)
            if not m or m.group(1) in seen:
                continue
            pcts = [float(x) for x in PCT.findall(re.sub(r"<[^>]+>", " ", row))]
            nums = [int(x) for x in re.findall(r'class="number col_type[456]"[^>]*>\s*(\d+)\s*<', row)]
            if not pcts:
                continue
            seen.add(m.group(1))
            out.append({"no": m.group(1), "name": m.group(2).strip(), "chg": pcts[0],
                        "chg3": pcts[1] if len(pcts) > 1 else None,
                        "up": nums[0] if len(nums) > 0 else None, "flat": nums[1] if len(nums) > 1 else None,
                        "down": nums[2] if len(nums) > 2 else None})
        if len(out) == n0:
            if page == 1:
                DBG.mkdir(exist_ok=True)
                (DBG / "theme_debug.html").write_text(html, encoding="utf-8")
            break
    return out


def members_html(no: str) -> list[dict]:
    html = get(f"https://finance.naver.com/sise/sise_group_detail.naver?type=theme&no={no}")
    out, seen = [], set()
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        m = re.search(r'/item/main\.naver\?code=(\d{6})"[^>]*>([^<]+)</a>', row)
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        pcts = [float(x) for x in PCT.findall(re.sub(r"<[^>]+>", " ", row))]
        out.append({"code": m.group(1), "name": m.group(2).strip(), "chg": pcts[0] if pcts else None})
    return out


SRC = {"list": None}


def theme_list() -> list[dict]:
    """네이버 모바일 증권 API 먼저, 안 되면 옛 PC 페이지."""
    t = theme_list_api()
    SRC["list"] = "api" if t else "html"
    return t or theme_list_html()


def members(no: str) -> list[dict]:
    m = members_api(no) if SRC["list"] == "api" else []
    return m or members_html(no)


def flow_sum(db: Path, codes: list[str]) -> tuple[float | None, str | None]:
    """구성 종목의 최근일 외국인+기관 순매수 금액(억 원) 합."""
    if not codes or not db.exists():
        return None, None
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
    try:
        day = con.execute("SELECT MAX(date) FROM inv").fetchone()[0]
        q = ",".join("?" * len(codes))
        syms = [r[0] for r in con.execute(f"SELECT symbol FROM u WHERE substr(symbol,-6) IN ({q})", codes)]
        if not syms:
            return None, day
        q2 = ",".join("?" * len(syms))
        rows = con.execute(f"SELECT i.frg, i.inst, p.c FROM inv i LEFT JOIN px p ON p.symbol=i.symbol AND p.date=i.date "
                           f"WHERE i.date=? AND i.symbol IN ({q2})", [day, *syms]).fetchall()
    finally:
        con.close()
    tot = sum(((f or 0) + (s or 0)) * (c or 0) for f, s, c in rows)
    return tot / 1e8, day


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=r"C:\toss-core\flow.db")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not a.dry_run:
        git("pull", "--rebase", "-q", "origin", "main")
    themes = theme_list()
    if not themes:
        DBG.mkdir(exist_ok=True)
        (DBG / "theme_probe.txt").write_text("\n".join(PROBE), encoding="utf-8")
        sys.exit("[중단] 테마 목록을 읽지 못했습니다 — reports\\theme_probe.txt 를 보내 주세요.")
    print(f"테마 목록 출처: {SRC['list']}")
    by_chg = sorted(themes, key=lambda t: t["chg"], reverse=True)
    rank = {t["no"]: i + 1 for i, t in enumerate(by_chg)}
    hist = json.loads(HIST.read_text(encoding="utf-8")) if HIST.exists() else {}
    day = datetime.now().strftime("%Y-%m-%d")
    past = [v for k, v in sorted(hist.items()) if k < day][-5:]
    rotation = []
    for t in by_chg[:10]:
        prev = [p.get(t["no"]) for p in past if p.get(t["no"])]
        if len(prev) >= 3 and sum(prev) / len(prev) > 30:
            rotation.append({"name": t["name"], "no": t["no"], "rank": rank[t["no"]], "prev_avg_rank": round(sum(prev) / len(prev))})
    hist[day] = rank
    hist = dict(sorted(hist.items())[-30:])
    # 상위 테마 구성 종목·수급
    db = Path(a.db)
    detail, flow_day = [], None
    cand = {t["no"]: t for t in by_chg[:12] + sorted(themes, key=lambda t: t["chg3"] or -99, reverse=True)[:8]}
    for no, t in cand.items():
        mem = members(no)
        fs, flow_day = flow_sum(db, [m["code"] for m in mem])
        detail.append({**t, "rank": rank[no], "n": len(mem), "flow_eok": None if fs is None else round(fs, 1),
                       "leaders": sorted([m for m in mem if m["chg"] is not None], key=lambda m: m["chg"], reverse=True)[:5]})
    top = sorted(detail, key=lambda t: t["chg"], reverse=True)[:10]
    leaders, seen = [], set()
    for t in top[:3]:
        for m in t["leaders"][:4]:
            if m["code"] not in seen:
                seen.add(m["code"])
                leaders.append({"code": m["code"], "name": m["name"], "theme": t["name"], "chg": m["chg"]})
    out = {"day": day, "generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "n_themes": len(themes),
           "note": "네이버 테마 전일 등락 기준 관찰 목록 — 테마 추종이 통한다는 검증은 아직 없음 (저녁 평가로 채점 중)",
           "top": top, "source": SRC["list"],
           "top3d": sorted([t for t in detail if t["chg3"] is not None], key=lambda t: t["chg3"], reverse=True)[:8],
           "rotation": rotation, "flow_day": flow_day,
           "flow": sorted([t for t in detail if t["flow_eok"] is not None], key=lambda t: t["flow_eok"], reverse=True)[:8],
           "leaders": leaders[:10]}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    HIST.write_text(json.dumps(hist, ensure_ascii=False), encoding="utf-8")
    print(f"테마 {len(themes)}개 · 상위 {', '.join(t['name'] for t in top[:5])} · 순환 {len(rotation)} · 대표 종목 {len(leaders)}")
    if a.dry_run:
        return 0
    git("add", str(OUT.relative_to(REPO)), str(HIST.relative_to(REPO)))
    if not git("diff", "--cached", "--name-only"):
        print("변경 없음")
        return 0
    git("commit", "-q", "-m", f"themes: {day}")
    git("push", "-q", "origin", "main")
    print("푸시 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
