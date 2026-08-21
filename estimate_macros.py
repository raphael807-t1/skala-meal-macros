"""로컬 Ollama(sLLM)로 요리명 -> 탄/단/지/칼로리 추정. 결과는 캐시해서 재사용."""
import json
import re
from pathlib import Path

import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen3:8b"
CACHE_PATH = Path(__file__).parent / "data" / "macro_cache.json"

PROMPT_TEMPLATE = """너는 영양사야. 한국 구내식당 메뉴 "{dish}" 1인분 기준 영양정보를 추정해줘.
반드시 아래 JSON 형식으로만 답해. 설명, 마크다운, 다른 텍스트는 절대 쓰지 마.
{{"carb_g": 숫자, "protein_g": 숫자, "fat_g": 숫자, "kcal": 숫자}}"""


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text())
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2))


def _parse_json_response(text: str) -> dict | None:
    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
        return {
            "carb_g": round(float(obj["carb_g"]), 1),
            "protein_g": round(float(obj["protein_g"]), 1),
            "fat_g": round(float(obj["fat_g"]), 1),
            "kcal": round(float(obj["kcal"])),
        }
    except (KeyError, ValueError, TypeError):
        return None


def estimate_dish(dish: str) -> dict:
    resp = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL,
            "prompt": PROMPT_TEMPLATE.format(dish=dish),
            "stream": False,
            "think": False,
            "format": "json",
        },
        timeout=120,
    )
    resp.raise_for_status()
    raw = resp.json().get("response", "")
    parsed = _parse_json_response(raw)
    if parsed is None:
        # 파싱 실패 시 0으로 채워서 최소한 파이프라인은 안 끊기게
        return {"carb_g": 0, "protein_g": 0, "fat_g": 0, "kcal": 0, "error": raw[:200]}
    return parsed


def estimate_dishes(dishes: list[str]) -> dict[str, dict]:
    cache = _load_cache()
    updated = False
    results = {}
    for dish in dishes:
        if dish in cache:
            results[dish] = cache[dish]
            continue
        macros = estimate_dish(dish)
        cache[dish] = macros
        results[dish] = macros
        updated = True
        print(f"  - {dish}: {macros}")
    if updated:
        _save_cache(cache)
    return results


if __name__ == "__main__":
    sample = ["청양풍돈육볶음", "쌀밥", "고추장찌개"]
    print(estimate_dishes(sample))
