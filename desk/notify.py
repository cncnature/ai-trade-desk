"""텔레그램 알림. 토큰이 없으면 콘솔에만 출력."""
from __future__ import annotations

from .config import CFG


def send(text: str) -> bool:
    if not (CFG.telegram_token and CFG.telegram_chat):
        print("[telegram 미설정]\n" + text)
        return False
    import requests

    r = requests.post(f"https://api.telegram.org/bot{CFG.telegram_token}/sendMessage", timeout=10,
                      data={"chat_id": CFG.telegram_chat, "text": text[:4000], "disable_web_page_preview": True})
    return r.ok


def daily_message(asof: str, validation: dict, picks: list[dict], paper: dict, events: list[dict]) -> str:
    lines = [f"📊 AI Trade Desk · {asof}"]
    passed = validation["passed"]
    lines.append(f"검증 통과 팩터: {', '.join(passed) if passed else '없음 → 오늘 매수 후보 없음'}")
    for p in picks:
        lines.append(f"• {p['name']}({p['code']}) 점수 {p['score_pct']} | 기준 {p['entry_ref']:,} "
                     f"손절 {p['stop']:,} 목표 {p['target']:,} | {p['qty']}주")
    for e in events:
        if e["type"] == "SELL":
            lines.append(f"↩ 청산 {e['name']} {e['ret_pct']:+.2f}% ({e['reason']})")
        else:
            lines.append(f"↪ 매수 체결 {e['name']} {e['qty']}주 @ {e['price']:,}")
    lines.append(f"모의 자산 {paper['equity']:,}원 ({paper['total_ret_pct']:+.2f}%) · 청산 {paper['trades']}건")
    lines.append("※ 과거 검증 결과일 뿐 수익을 보장하지 않습니다.")
    return "\n".join(lines)
