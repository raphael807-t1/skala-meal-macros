"""요리명 -> 탄/단/지/칼로리 최종 추정치를 만드는 오케스트레이션 모듈.

5주차 DB 수업의 "믿을 수 있는 데이터 우선" 원칙에 따라, 실측 DB를 최대한
활용하고 GPT는 "숫자를 만드는 역할"이 아니라 "DB에서 찾을 수 있도록 메뉴
구조를 분석하는 역할"로 최대한 제한한다. 최종 kcal는 항상 코드가 검증한다.

우선순위:
  Level 1) 식품안전나라 DB에서 원본 메뉴명 그대로 검색
  Level 2) DB에 없으면 GPT가 표기변형/동의어 검색어 후보를 만들고,
           그 후보들로 DB를 다시 검색
  Level 3) 그래도 없고 복합 메뉴면(예: "고추참치덮밥"), GPT가 구성요소를
           분해하고, 요소별로 DB 검색 -> 비율대로 가중합산
  최후수단) 위 전부 실패하면 GPT가 탄단지kcal를 직접 추정 (신뢰도 낮음으로 표시)

모든 경로의 최종 kcal는 "DB 실측값을 그대로 쓴 경우"만 예외이고, 나머지는
전부 코드가 탄×4 + 단×4 + 지×9 로 재계산해서 GPT가 부른 kcal를 신뢰하지 않는다.

결과에는 source(출처)/reliability(신뢰도)를 태깅해서, 화면에서 실측 데이터와
추정 데이터를 구분할 수 있게 한다.

참고: 로컬 sLLM(Ollama)도 원래 여기 있었는데, 단독 추정치가 실제보다 칼로리를
꽤 높게 잡는 경향이 있어서(사용자가 FatSecret 실측값과 비교해서 확인함) 잠시
빼놓음(주석처리). _estimate_dish_sllm 함수 자체는 아래에 남겨뒀다.
"""
import json
from pathlib import Path

import requests

import estimate_gpt
import food_heuristics
import nutrition_db

OLLAMA_URL = "http://localhost:11434/api/generate"
SLLM_MODEL = "qwen3:8b"
CACHE_PATH = Path(__file__).parent / "data" / "macro_cache.json"

# 구성요소 기반 추정을 "신뢰할 만하다"고 인정하는 최소 매칭 비율.
# 예: 구성요소 4개 중 3개(75%)만 DB에서 찾아지면 사용, 절반도 못 찾으면
# 그 결과는 버리고 최후수단(GPT 직접추정)으로 넘어간다.
COMPONENT_MATCH_THRESHOLD = 0.7

SLLM_PROMPT_TEMPLATE = """너는 영양사야. 한국 구내식당 메뉴 "{dish}" 1인분(1회 제공량) 기준 영양정보를 추정해줘.
반드시 아래 JSON 형식으로만 답해. 설명, 마크다운, 다른 텍스트는 절대 쓰지 마.
{{"serving_g": 1인분 중량(그램), "carb_g": 숫자, "protein_g": 숫자, "fat_g": 숫자, "kcal": 숫자}}"""


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text())
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2))


def _kcal_from_macros(m: dict) -> int:
    """탄수화물 4kcal/g, 단백질 4kcal/g, 지방 9kcal/g — 영양학 표준 환산식.
    GPT/구성요소 추정 경로는 전부 이 함수로 kcal를 다시 계산해서, GPT가
    자체적으로 부른 kcal와 탄단지 합산이 어긋나는 문제를 원천 차단한다."""
    return round(m["carb_g"] * 4 + m["protein_g"] * 4 + m["fat_g"] * 9)


def _tag(m: dict, source: str, reliability: str, outlier: bool = False) -> dict:
    m["source"] = source
    m["reliability"] = reliability
    m["outlier"] = outlier
    return m


def _scale_db_result_to_serving(db_result: dict, dish_name: str) -> tuple[dict, bool]:
    """DB는 항상 100g 기준 값을 준다. 실제 1회 제공량으로 스케일링해서
    반환한다. (사용자 피드백: "100g DB값 ≠ 실제 제공량 총합"이었던 버그 수정)
    이상치 판단(is_outlier)은 스케일링 전, 원본 100g당 kcal 기준으로 한다
    (제공량 추정치는 어차피 근사값이라 판단 기준을 흔들면 안 되니까).
    반환값: (스케일링된 dict, outlier 여부)
    """
    kcal_per_100g = db_result["kcal"]
    outlier = food_heuristics.is_outlier(dish_name, kcal_per_100g)

    serving_g = food_heuristics.guess_serving_g(dish_name)
    factor = serving_g / 100
    scaled = {
        "matched_name": db_result.get("matched_name"),
        "serving_g": serving_g,
        "carb_g": round(db_result["carb_g"] * factor, 1),
        "protein_g": round(db_result["protein_g"] * factor, 1),
        "fat_g": round(db_result["fat_g"] * factor, 1),
        "kcal": round(kcal_per_100g * factor),
    }
    if "similarity" in db_result:
        scaled["similarity"] = db_result["similarity"]
    return scaled, outlier


def _estimate_dish_sllm(dish: str) -> dict | None:
    """로컬 Ollama sLLM 호출. 실패하면 None. (현재 미사용, 아래 estimate_dish()
    에서 호출부가 주석처리돼있음 — 다시 켜려면 그 주석만 풀면 됨)"""
    resp = requests.post(
        OLLAMA_URL,
        json={
            "model": SLLM_MODEL,
            "prompt": SLLM_PROMPT_TEMPLATE.format(dish=dish),
            "stream": False,
            "think": False,
            "format": "json",
        },
        timeout=120,
    )
    resp.raise_for_status()
    raw = resp.json().get("response", "")
    import re
    match = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
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


def _weighted_merge(dish_name: str, component_results: list[tuple[dict, float]]) -> dict:
    """구성요소별 DB 영양정보(전부 100g 기준)를 실제 물리량으로 환산해서 합산한다.
    1) 전체 메뉴의 예상 1회 제공량을 휴리스틱으로 정한다 (예: "조림"류 -> 200g)
    2) 각 구성요소가 그 제공량 중 ratio만큼을 차지한다고 보고 실제 g을 계산
    3) 구성요소의 100g당 값 × (그 구성요소의 실제 g / 100) 을 합산
    -> "비율대로 그냥 더하기"가 아니라 실제 그램수 기반 계산이라 물리적으로 맞다."""
    total_serving_g = food_heuristics.guess_serving_g(dish_name)
    total = {"carb_g": 0.0, "protein_g": 0.0, "fat_g": 0.0}
    for data, ratio in component_results:
        component_g = total_serving_g * ratio
        factor = component_g / 100
        total["carb_g"] += data["carb_g"] * factor
        total["protein_g"] += data["protein_g"] * factor
        total["fat_g"] += data["fat_g"] * factor

    merged = {
        "serving_g": total_serving_g,
        "carb_g": round(total["carb_g"], 1),
        "protein_g": round(total["protein_g"], 1),
        "fat_g": round(total["fat_g"], 1),
    }
    merged["kcal"] = _kcal_from_macros(merged)
    return merged


def estimate_dish(dish: str) -> dict:
    # Level 1: 원본 메뉴명 그대로 DB 검색 (nutrition_db가 이제 후보들 중
    # 이름이 가장 비슷한 것을 골라줌 -> 결과에 "similarity" 포함)
    db_result = nutrition_db.lookup(dish)
    if db_result:
        scaled, outlier = _scale_db_result_to_serving(db_result, dish)
        similarity = db_result.get("similarity", 1.0)
        reliability = "high" if similarity >= nutrition_db.SIMILARITY_HIGH else "medium"
        print(f"  - {dish}: [Lv1 DB 직접매칭, 유사도{similarity}] {scaled}")
        return _tag(scaled, source="food_safety_db", reliability=reliability, outlier=outlier)

    # GPT한테 구조 분석 요청 (숫자 X, 검색어 후보/구성요소만) — Level 2, 3 공용
    structure = estimate_gpt.analyze_dish_structure(dish)

    if structure:
        # Level 2: GPT가 제안한 유사 검색어들로 DB 재검색
        for query in structure.get("search_queries", []):
            db_result = nutrition_db.lookup(query)
            if db_result:
                scaled, outlier = _scale_db_result_to_serving(db_result, dish)
                similarity = db_result.get("similarity", 1.0)
                # Level2는 원본 메뉴명이 아니라 "GPT가 제안한 유사어"로 찾은 거라
                # 아무리 유사도가 높아도 원본과 100% 동일 음식이라는 보장이 약함
                # -> 최고 등급(high)은 안 주고 medium까지만.
                reliability = "medium"
                print(f"  - {dish}: [Lv2 DB 유사검색 '{query}', 유사도{similarity}] {scaled}")
                return _tag(scaled, source="food_safety_db(유사검색)", reliability=reliability, outlier=outlier)

        # Level 3: 복합 메뉴면 구성요소별로 DB 검색 -> 실제 그램수 기반 합산
        components = structure.get("components") or []
        if structure.get("is_composite") and components:
            component_results = []
            for comp in components:
                comp_db = nutrition_db.lookup(comp["name"])
                if comp_db:
                    component_results.append((comp_db, comp["ratio"]))

            match_rate = len(component_results) / len(components)
            if match_rate >= COMPONENT_MATCH_THRESHOLD:
                merged = _weighted_merge(dish, component_results)
                outlier = food_heuristics.is_outlier(dish, merged["kcal"] / merged["serving_g"] * 100)
                print(f"  - {dish}: [Lv3 구성요소 {len(component_results)}/{len(components)}매칭] {merged}")
                return _tag(merged, source="component_db", reliability="medium", outlier=outlier)

    # 최후 수단: GPT 직접 추정. kcal는 GPT 응답을 버리고 코드가 재계산.
    gpt_direct = estimate_gpt.estimate_dish_direct(dish)
    if gpt_direct:
        gpt_direct["kcal"] = _kcal_from_macros(gpt_direct)
        print(f"  - {dish}: [최후수단 GPT직접추정] {gpt_direct}")
        return _tag(gpt_direct, source="gpt_estimate", reliability="low")

    return _tag({"serving_g": 0, "carb_g": 0, "protein_g": 0, "fat_g": 0, "kcal": 0}, source="실패", reliability="none")


def estimate_dishes(dishes: list[str]) -> dict[str, dict]:
    cache = _load_cache()
    updated = False
    results = {}
    for dish in dishes:
        cached = cache.get(dish)
        # "실패"(source=='실패', 예: .env를 안 불러온 채로 돌려서 DB/GPT 키가
        # 둘 다 없었던 경우)는 캐시에 있어도 재사용하지 않고 다시 시도한다.
        # 그렇지 않으면 한 번의 일시적 오류가 캐시에 영구히 박혀서, 나중에
        # 키를 제대로 넣고 재실행해도 계속 실패로 나오는 문제가 있었다.
        if cached and cached.get("source") != "실패":
            results[dish] = cached
            continue
        macros = estimate_dish(dish)
        cache[dish] = macros
        results[dish] = macros
        updated = True
    if updated:
        _save_cache(cache)
    return results


if __name__ == "__main__":
    sample = ["청양풍돈육볶음", "쌀밥", "고추참치덮밥"]
    print(estimate_dishes(sample))
