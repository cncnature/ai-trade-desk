"""증권사 계좌 자동매매 (내 PC에서 장 시작 전 실행).

    DESK_MODE=kis_mock python run_trade.py          # KIS 모의투자 계좌
    DESK_MODE=live DESK_LIVE_CONFIRM=I_UNDERSTAND_REAL_MONEY python run_trade.py   # 실전

동작
1. 09:00 이후 docs/data/latest.json 의 후보를 시장가 매수 (보유·자리·일손실 한도 확인)
2. 장중 30초마다 현재가 확인 → 손절가 이탈 / 목표가 도달 시 시장가 매도
3. 보유 hold_days 거래일째 15:15 에 시간청산
보유 기록: state/live_positions_<mode>.json
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime

from desk import notify
from desk.broker.kis import KISBroker
from desk.config import CFG


def now() -> datetime:
    return datetime.now()


def main() -> int:
    if CFG.mode not in ("kis_mock", "live"):
        print("run_trade.py 는 DESK_MODE=kis_mock 또는 live 에서만 실행합니다. (모의 장부는 run_daily.py 가 처리)")
        return 1
    b = KISBroker()
    pos_path = CFG.state_dir / f"live_positions_{CFG.mode}.json"
    pos = json.loads(pos_path.read_text()) if pos_path.exists() else {}
    latest = json.loads((CFG.docs_data_dir / "latest.json").read_text())
    today = now().strftime("%Y-%m-%d")

    def save():
        pos_path.write_text(json.dumps(pos, ensure_ascii=False, indent=1))

    # 하루 1회: 보유일 증가
    for p in pos.values():
        if p.get("counted") != today:
            p["days"] = p.get("days", 0) + 1
            p["counted"] = today
    save()

    while now().strftime("%H:%M") < "09:00":
        time.sleep(10)

    # ── 1) 매수 ──
    start_cash = b.balance()["cash"]
    slots = CFG.max_positions - len(pos)
    for pk in latest.get("picks", [])[: max(0, slots)]:
        if pk["code"] in pos or pk["qty"] <= 0:
            continue
        q = b.price(pk["code"])
        qty = min(pk["qty"], int(start_cash * CFG.max_weight // max(q["price"], 1)))
        if qty <= 0:
            continue
        r = b.order(pk["code"], qty, "buy")
        if r["ok"]:
            ratio_s, ratio_t = pk["stop"] / pk["entry_ref"], pk["target"] / pk["entry_ref"]
            pos[pk["code"]] = {"name": pk["name"], "qty": qty, "entry": q["price"], "stop": q["price"] * ratio_s,
                               "target": q["price"] * ratio_t, "days": 1, "counted": today,
                               "hold_days": pk["hold_days"]}
            notify.send(f"[{CFG.mode}] 매수 {pk['name']} {qty}주 ≈{q['price']:,.0f}")
        else:
            notify.send(f"[{CFG.mode}] 매수 실패 {pk['name']}: {r['msg']}")
        save()

    # ── 2) 장중 감시 ──
    while now().strftime("%H:%M") < "15:20" and pos:
        for code, p in list(pos.items()):
            try:
                px = b.price(code)["price"]
            except Exception:
                continue
            why = None
            if px <= p["stop"]:
                why = "손절"
            elif px >= p["target"]:
                why = "목표"
            elif p["days"] >= p["hold_days"] and now().strftime("%H:%M") >= "15:15":
                why = "시간청산"
            if why:
                r = b.order(code, p["qty"], "sell")
                if r["ok"]:
                    ret = (px / p["entry"] - 1) * 100
                    notify.send(f"[{CFG.mode}] {why} {p['name']} {ret:+.2f}%")
                    del pos[code]
                    save()
        time.sleep(30)
    save()
    return 0


if __name__ == "__main__":
    sys.exit(main())
