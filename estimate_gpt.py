"""GPT를 두 가지 역할로 나눠서 쓴다 (영양추정 개선안 반영):

  1. analyze_dish_structure() — 메인 경로. 메뉴가 DB에서 검색되도록
     "구조"만 분석한다 (유사 검색어 후보 / 복합메뉴면 구성요소+비율).
     탄단지/kcal 같은 숫자는 여기서 만들지 않는다 — GPT가 숫자를
     직접 만들면 근거 없이 그럴듯한 값이 나올 위험이 크기 때문.

  2. estimate_dish_direct() — 최후 수단. DB도, DB 구성요소 조합도 다
     실패했을 때만 호출되는 예전 방식(메뉴명 -> 탄단지kcal 직접 추정).
     여기서 나온 kcal도 estimate_macros.py가 탄단지 기준으로 다시
     검산하므로, GPT가 부른 kcal 자체는 참고용일 뿐 최종값이 아니다.
"""
import json
import os
import re

from openai import OpenAI

MODEL = "gpt-4o-mini"  # 이런 단순 분석/추정 작업엔 제일 저렴한 모델로 충분

STRUCTURE_PROMPT = """당신은 급식 메뉴의 영양성분을 분석하고, 가능한 경우 식품영양성분
데이터베이스에서 검색할 수 있도록 메뉴를 구조화하는 영양 분석 보조 모델이다.

가장 중요한 원칙은 "임의의 영양성분 숫자를 그럴듯하게 만들어내지 않는 것"이다.
당신은 탄수화물/단백질/지방/칼로리 같은 숫자를 생성하지 않는다.
대신 이 메뉴가 DB에서 검색되도록 돕는 역할만 한다:
1. 원본 메뉴명이 단일 음식인지 복합 음식인지 판단한다.
2. DB 검색에 걸릴 만한 표기 변형/동의어 검색어 후보를 만든다 (무분별하게 많이 X, 실제
   음식 구조를 고려한 합리적인 후보만).
3. 복합 음식이면(예: "콩나물불고기덮밥" = 불고기 + 콩나물 + 쌀밥) 구성요소와 그 중량 비율을
   추정한다. 절대 균등 분할(N등분)하지 않는다 — 실제 조리법/구성 비중을 고려해서 판단.

메뉴: "{dish}"

반드시 아래 JSON 형식으로만 답하라. 설명, 마크다운, 다른 텍스트는 절대 쓰지 마.
{{
  "search_queries": ["표기변형1", "동의어2", ...],
  "is_composite": true 또는 false,
  "components": [{{"name": "구성요소명", "ratio": 0.0~1.0}}, ...],
  "confidence": "high" 또는 "medium" 또는 "low"
}}
단일 음식이면 components는 빈 배열([])로 둔다."""

DIRECT_PROMPT = """너는 영양사야. 한국 구내식당 메뉴 "{dish}" 1인분(1회 제공량) 기준 영양정보를 추정해줘.
반드시 아래 JSON 형식으로만 답해. 설명, 마크다운, 다른 텍스트는 절대 쓰지 마.
{{"serving_g": 1인분 중량(그램), "carb_g": 숫자, "protein_g": 숫자, "fat_g": 숫자, "kcal": 숫자}}"""


def _client(api_key: str | None = None) -> OpenAI | None:
    api_key = api_key or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    return OpenAI(api_key=api_key)


def _extract_json(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def analyze_dish_structure(dish: str, api_key: str | None = None) -> dict | None:
    """DB 검색을 돕기 위한 구조 분석. 실패/키없음이면 None."""
    client = _client(api_key)
    if client is None:
        return None

    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": STRUCTURE_PROMPT.format(dish=dish)}],
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content
    except Exception as e:
        print(f"  [GPT 구조분석 실패] {dish}: {e}")
        return None

    obj = _extract_json(raw)
    if obj is None:
        return None

    return {
        "search_queries": obj.get("search_queries") or [],
        "is_composite": bool(obj.get("is_composite")),
        "components": obj.get("components") or [],
        "confidence": obj.get("confidence", "low"),
    }


def _parse_macros_response(text: str) -> dict | None:
    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
        return {
            "serving_g": round(float(obj["serving_g"])),
            "carb_g": round(float(obj["carb_g"]), 1),
            "protein_g": round(float(obj["protein_g"]), 1),
            "fat_g": round(float(obj["fat_g"]), 1),
        }
    except (KeyError, ValueError, TypeError):
        return None


def estimate_dish_direct(dish: str, api_key: str | None = None) -> dict | None:
    """최후 수단: DB/구성요소 조합 다 실패했을 때만 호출.
    kcal는 여기서 만든 값을 쓰지 않고, 호출부(estimate_macros.py)가
    carb/protein/fat 기준으로 다시 계산해서 검증한다."""
    client = _client(api_key)
    if client is None:
        return None

    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": DIRECT_PROMPT.format(dish=dish)}],
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content
    except Exception as e:
        print(f"  [GPT 직접추정 실패] {dish}: {e}")
        return None

    return _parse_macros_response(raw)


if __name__ == "__main__":
    print(analyze_dish_structure("콩나물불고기덮밥"))
    print(estimate_dish_direct("고추참치덮밥"))
