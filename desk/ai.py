"""AI 해설 (선택). LLM 은 '판정'이 아니라 '설명 문장'만 만든다.

매수 여부는 검증된 숫자가 정하고, LLM 은 그 숫자를 사람이 읽기 쉬운 한 문단으로 풀어 줄 뿐이다.
LLM 이 숫자를 바꾸거나 새 근거를 지어내지 못하도록, 입력으로 준 수치 밖의 주장은 금지하는 지시를 넣는다.

DESK_AI=anthropic  (ANTHROPIC_API_KEY 필요)  |  DESK_AI=ollama (로컬, OLLAMA_URL 기본 http://localhost:11434)
"""
from __future__ import annotations

import json
import os

from .config import CFG

SYSTEM = ("너는 주식 리포트 편집자다. 주어진 JSON 수치만 사용해 한국어 두 문장으로 설명하라. "
          "JSON 에 없는 사실·전망·목표가를 만들지 말고, 매수 권유 표현을 쓰지 마라.")


def explain(pick: dict, validation: dict) -> str | None:
    if CFG.ai_provider == "off":
        return None
    facts = {"종목": pick["name"], "점수": pick["score_pct"], "근거_팩터": pick["votes"],
             "손절": pick["stop"], "목표": pick["target"], "보유일": pick["hold_days"],
             "팩터_검증": {r["factor"]: r.get("confirm") for r in validation["factors"] if r["factor"] in pick["votes"]}}
    prompt = json.dumps(facts, ensure_ascii=False, default=str)
    try:
        import requests

        if CFG.ai_provider == "anthropic":
            r = requests.post("https://api.anthropic.com/v1/messages", timeout=30, headers={
                "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
                "content-type": "application/json"},
                json={"model": CFG.ai_model, "max_tokens": 300, "system": SYSTEM,
                      "messages": [{"role": "user", "content": prompt}]})
            return r.json()["content"][0]["text"].strip()
        if CFG.ai_provider == "ollama":
            url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
            r = requests.post(f"{url}/api/generate", timeout=60,
                              json={"model": CFG.ai_model, "system": SYSTEM, "prompt": prompt, "stream": False})
            return r.json().get("response", "").strip()
    except Exception as e:  # 설명 실패는 매매에 영향 없음
        return f"(AI 설명 실패: {type(e).__name__})"
    return None
