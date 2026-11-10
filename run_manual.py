"""대시보드 버튼 → GitHub 이슈 명령 → 모의 장부 수정 (저장소 주인이 연 이슈만, manual 워크플로가 실행).

이슈 제목 명령 (대소문자 무관)
  CLOSE <코드|ALL>              지금 가격으로 청산 (수익이면 '수동 익절', 손실이면 '수동 손절')
  TARGET <코드|ALL> <퍼센트>     목표가 = 매수가 × (1 + %)        예) TARGET 005930 3
  STOP <코드|ALL> <퍼센트>       손절가 = 매수가 × (1 − |%|)      예) STOP ALL 2
  RESET <코드|ALL>              목표·손절을 ATR 기본값으로 되돌림 (ALL 이면 앞으로 살 종목의 기본값도 해제)
  ALL 은 지금 보유 종목 전체 + 앞으로 사는 종목의 기본값으로 적용된다.
목표·손절을 새로 걸었을 때 현재가가 이미 그 선을 넘었으면 바로 그 가격에 청산한다.
'지금 가격' = 네이버 일봉의 오늘 값 (장중이면 그 시점 가격, 장 마감 뒤면 종가).

    python run_manual.py "TARGET 005930 3"
"""
from __future__ import annotations

import json
import re
import sys

import pandas as pd

from desk import notify
from desk.broker import PaperBroker
from desk.config import CFG
from desk.data import NaverSource

CMD = re.compile(r"^\s*(CLOSE|TARGET|STOP|RESET)\s+(\d{6}|ALL)(?:\s+([+-]?\d+(?:\.\d+)?)\s*%?)?\s*$", re.I)


def price_now(src, code: str) -> float | None:
    try:
        df = src.ohlcv(code, 2)
        return float(df["close"].iloc[-1]) if len(df) else None
    except Exception:
        return None


def run(title: str, src=None) -> tuple[bool, str]:
    m = CMD.match(title or "")
    if not m:
        return False, f"명령을 이해하지 못했습니다: `{title}`\n형식: CLOSE 005930 · TARGET 005930 3 · STOP ALL 2 · RESET ALL"
    cmd, who, val = m.group(1).upper(), m.group(2).upper(), m.group(3)
    if cmd in ("TARGET", "STOP") and val is None:
        return False, f"{cmd} 에는 퍼센트가 필요합니다. 예) {cmd} {who} 3"
    pct = float(val) if val is not None else None
    if cmd == "TARGET" and pct is not None and pct <= 0:
        return False, "목표 %는 0보다 커야 합니다."
    src = src or NaverSource()
    b = PaperBroker()
    now = pd.Timestamp.now(tz="Asia/Seoul").strftime("%Y-%m-%d %H:%M")
    codes = list(b.s["positions"]) if who == "ALL" else [who]
    missing = [c for c in codes if c not in b.s["positions"]]
    if who != "ALL" and missing:
        return False, f"{who} 은(는) 보유 중이 아닙니다."
    lines = []
    if who == "ALL" and cmd in ("TARGET", "STOP", "RESET"):
        r = b.rules()
        if cmd == "RESET":
            r["target_pct"] = r["stop_pct"] = None
            lines.append("앞으로 사는 종목: ATR 기본 목표·손절")
        else:
            r["target_pct" if cmd == "TARGET" else "stop_pct"] = pct
            lines.append(f"앞으로 사는 종목 기본 {'목표' if cmd == 'TARGET' else '손절'}: {pct:+g}%")
    for c in codes:
        p = b.s["positions"][c]
        if cmd == "CLOSE":
            px = price_now(src, c)
            if px is None:
                lines.append(f"{p['name']}({c}) 현재가를 받지 못해 청산 보류")
                continue
            rec = b.close_now(c, px, now, CFG.cost_roundtrip)
            lines.append(f"{rec['name']}({c}) {rec['qty']}주 {rec['exit']:,}원 청산 · {rec['ret_pct']:+.2f}% ({rec['reason']})")
            continue
        if cmd == "RESET":
            b.set_level(c, "target", None)
            b.set_level(c, "stop", None)
        else:
            b.set_level(c, "target" if cmd == "TARGET" else "stop", pct)
        px = price_now(src, c)
        msg = f"{p['name']}({c}) 목표 {p['target']:,.0f} · 손절 {p['stop']:,.0f}"
        if px is not None and (px >= p["target"] or px <= p["stop"]):
            why = "목표" if px >= p["target"] else "손절"
            rec = b.close_now(c, px, now, CFG.cost_roundtrip, f"{why}(설정 즉시)")
            msg += f" → 현재가 {px:,.0f} 이 이미 {why}선을 넘어 바로 청산 {rec['ret_pct']:+.2f}%"
        lines.append(msg)
    b.save()
    # 화면도 바로 반영 (다음 아침 분석을 기다리지 않게)
    lp = CFG.docs_data_dir / "latest.json"
    if lp.exists():
        d = json.loads(lp.read_text(encoding="utf-8"))
        d["paper"] = json.loads(json.dumps(b.summary(), default=float))
        lp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    text = f"✅ {cmd} {who}{'' if pct is None else f' {pct:g}%'} ({now})\n" + "\n".join(f"- {x}" for x in lines or ["보유 종목 없음"])
    return True, text


def main() -> int:
    title = sys.argv[1] if len(sys.argv) > 1 else ""
    ok, text = run(title)
    print(text)
    with open("manual_result.md", "w", encoding="utf-8") as f:
        f.write(text + "\n\n※ 모의 장부만 바뀝니다 (실계좌 주문 없음).")
    if ok:
        notify.send("[모의] " + text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
