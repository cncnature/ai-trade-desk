"""서버 PC → GitHub 실행 신호. GitHub 예약 실행은 몇 시간씩 밀릴 수 있어, 정해진 시각에 이 파일을 바꿔 푸시하면
해당 워크플로가 바로 시작된다 (예약은 예비로 남겨 둠).

    python pc\\kick.py close        # 종가 수집 (16:05)
    python pc\\kick.py eval         # 저녁 평가 (20:15)
    python pc\\kick.py dividends    # 배당 공시 수집 (06:30)
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NAMES = {"close", "eval", "dividends"}


def git(*a: str) -> None:
    r = subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        sys.exit(f"[git {' '.join(a)}] 실패:\n{r.stderr.strip()}")


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    if name not in NAMES:
        sys.exit(f"사용법: python pc\\kick.py {{{'|'.join(sorted(NAMES))}}}")
    now = datetime.now()
    if now.weekday() >= 5:
        print("주말 — 신호 보내지 않음")
        return 0
    git("pull", "--rebase", "-q", "origin", "main")
    p = REPO / "kick" / name
    p.parent.mkdir(exist_ok=True)
    p.write_text(now.strftime("%Y-%m-%d %H:%M:%S") + "\n", encoding="utf-8")
    git("add", str(p.relative_to(REPO)))
    git("commit", "-q", "-m", f"kick: {name} {now:%Y-%m-%d %H:%M}")
    git("push", "-q", "origin", "main")
    print(f"신호 보냄: {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
