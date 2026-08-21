"""GPT를 두 가지 역할로 나눠서 쓴다 (영양추정 개선안 반영):

  1. analyze_dish_structure() — 메인 경로. 메뉴가 DB에서 검색되도록
     "구조"만 분석한다 (유사 검색어 후보 / 복합메뉴면 구성요소+비율).
     탄단지/kcal 같은 숫자는 여기서 만들지 않는다 — GPT가 숫자를
     직접 만들면 근거 없이 그럴듯한 값이 나올 위험이 크기 때문.

  2. estimate_dish_direct() — 최후 수단. DB도, DB 구성요소 조합도 다
     실패했을 때만 호출되는 예전 방식(메뉴명 -> 탄단지kcal 직접 추정).
     여기서 나온 kcal도 estimate_macros.py가 탄단지 기준으로 다시
     검산하므로, GPT가 부른 kcal 자체는 참고용일 뿐 최종값이 아니다.

  3. check_meal_duplicates() — 요리 하나가 아니라 "한 끼 전체 메뉴 리스트"를
     한 번에 넣고 부르는 마지막 검증 단계. 예: "장터국밥 + 쌀밥"이 같이 나오면,
     장터국밥 DB 데이터에 이미 밥이 포함돼있을 수 있어서 쌀밥을 또 더하면
     이중계산이 된다. 요리 개수만큼이 아니라 끼니당 딱 1번만 부르므로 비용이
     거의 안 든다(하루 최대 2번, 중식/석식 각 1번).
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

DUPLICATE_CHECK_PROMPT = """너는 급식 영양성분 계산을 검증하는 모델이다.
아래는 한 끼(중식 또는 석식)에 나온 메뉴 목록이다. 각 메뉴는 개별적으로 영양성분이
이미 계산되어 있고, 지금 이 메뉴들의 칼로리를 전부 더해서 "이 끼니의 총 칼로리"를
낼 예정이다.

문제 상황: 메뉴 이름이 "OO국밥" 또는 "OO비빔밥"인 경우, 그 이름 자체에 이미 밥이
포함되어 있다는 뜻이다. 이럴 때 같은 끼니에 "쌀밥"이 별도 메뉴로도 나와 있으면,
쌀밥을 또 더하는 순간 밥이 두 번 계산되는 이중계산 오류가 생긴다.

주의: 이 판단은 반드시 메뉴 이름이 "국밥" 또는 "비빔밥"으로 끝나는 경우에만 한다.
"~쌀국수"(면 요리, 밥 아님), "~탕"/"~곰탕"/"~찌개"(밥이 따로 나오는 게 일반적)처럼
이름에 "쌀"이 들어있거나 국물 요리라고 해서 밥이 포함됐다고 넘겨짚지 마라. 오직
"국밥"/"비빔밥"으로 끝나는 이름일 때만 중복 후보로 고려하라.

메뉴 목록: {dish_list}

확실하지 않으면 제외하지 말고 그대로 둬라(과도한 제외가 더 위험하다 — 애매하면
있는 그대로 계산). "국밥"/"비빔밥"으로 끝나지 않는 메뉴는 절대 contained_in으로
쓰지 마라.

반드시 아래 JSON 형식으로만 답하라. 설명, 마크다운, 다른 텍스트는 절대 쓰지 마.
각 제외 항목마다 "그 메뉴를 이미 포함하고 있다고 판단한 다른 메뉴"를 반드시
`contained_in`에 명시하라 (메뉴 목록에 실제로 있는 이름 그대로). 근거가 되는
다른 메뉴가 목록에 없으면 절대 제외하지 마라.
{{
  "exclude": [
    {{"dish": "중복이라 총합에서 빼야 할 메뉴명(메뉴 목록에 있는 문자열 그대로)",
      "contained_in": "그 메뉴를 이미 포함한다고 판단한 다른 메뉴명(반드시 메뉴 목록 안에 있는 것)",
      "reason": "이 항목만의 판단 이유"}}
  ]
}}
중복이 없다고 판단되면 exclude는 빈 배열([])로 둬라."""


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


def check_meal_duplicates(dish_names: list[str], api_key: str | None = None) -> dict:
    """끼니 전체 메뉴 목록을 한 번에 검사해서 이중계산 위험이 있는 메뉴를 찾는다.
    반환값의 "exclude"는 [{"dish":..., "contained_in":..., "reason":...}] 형태.
    실패/키없음/메뉴 1개 이하(비교 대상 없음)면 제외 없음으로 취급."""
    if len(dish_names) < 2:
        return {"exclude": []}

    client = _client(api_key)
    if client is None:
        return {"exclude": []}

    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": DUPLICATE_CHECK_PROMPT.format(dish_list=dish_names)}],
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content
    except Exception as e:
        print(f"  [GPT 중복검사 실패] {e}")
        return {"exclude": []}

    obj = _extract_json(raw)
    if obj is None:
        return {"exclude": []}

    # 환각 방지: dish도 contained_in도 반드시 "이번에 실제로 넘긴 메뉴 목록"
    # 안에 있어야 채택한다. 근거 메뉴(contained_in)가 목록에 없으면(예: 다른
    # 끼니 메뉴를 착각해서 언급) 그 제외 판단 자체를 무시한다.
    #
    # + 화이트리스트 게이트: 프롬프트로 "국밥/비빔밥으로 끝날 때만" 이라고
    # 지시해도 GPT가 가끔 "쌀국수"(쌀==밥 아님), "곰탕"(밥 안 들어있는 경우
    # 많음) 같은 걸 넘겨짚는 게 테스트로 확인됨(5건 중 2건 오탐).
    # 그래서 프롬프트 지시와 별개로 코드에서 한 번 더 강제로 걸러낸다 -
    # contained_in 이름이 "국밥" 또는 "비빔밥"으로 끝나지 않으면 무조건 기각.
    RICE_INCLUDED_SUFFIXES = ("국밥", "비빔밥")
    valid = []
    for item in obj.get("exclude") or []:
        dish = item.get("dish")
        contained_in = item.get("contained_in")
        if dish not in dish_names or contained_in not in dish_names or dish == contained_in:
            print(f"  [중복검사 무시] 근거 불충분(메뉴 목록 불일치): {item}")
            continue
        if not contained_in.endswith(RICE_INCLUDED_SUFFIXES):
            print(f"  [중복검사 무시] '{contained_in}'는 국밥/비빔밥이 아님 -> 화이트리스트 기각: {item}")
            continue
        valid.append(item)
    return {"exclude": valid}


if __name__ == "__main__":
    print(analyze_dish_structure("콩나물불고기덮밥"))
    print(estimate_dish_direct("고추참치덮밥"))
    print(check_meal_duplicates(["장터국밥", "매콤두부조림", "쌀밥", "갈비만두찜", "부추겉절이", "포기김치"]))
    print(check_meal_duplicates(["콩나물불고기", "감자고로케", "쌀밥", "시래기된장국", "열무김치"]))
