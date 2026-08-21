"""식품의약품안전처 식품영양성분DB API (data.go.kr, 2026-08-21에 새로 연동) 조회.

5주차 데이터베이스 수업에서 배운 것처럼, "추측"보다 "실제 데이터"가 있으면
그게 항상 더 정확하다. 그래서 GPT한테 무작정 물어보기 전에,
정부가 공개한 실측 영양성분 DB(31만 건)에서 먼저 이름으로 검색해본다.

검색은 3단계로 확장됨(estimate_macros.py가 호출하는 순서):
  Level 1) 원본 메뉴명 그대로 -> lookup()
  Level 2) GPT가 제안한 유사 표기/동의어 후보들 -> lookup() 반복 호출
  Level 3) 복합 메뉴 구성요소 각각 -> lookup() 반복 호출

교체 히스토리(디버깅 노트):
예전에는 openapi.foodsafetykorea.go.kr(I0750)를 썼는데, 그 API는 인증키를
URL "경로"에 그대로 넣는 방식이라, 하필 발급받은 키에 '/' 문자가 포함되어
있어서 영구적으로 실패했음(Apache가 %2F 인코딩은 404로 거부하고, 디코딩된
literal '/'는 경로 구분자로 오인식해서 파라미터가 다 밀림). 지금 쓰는
apis.data.go.kr 버전은 인증키를 "쿼리스트링"으로 보내는 표준 방식이라
같은 문제가 없다 (쿼리스트링의 %2F는 정상 처리됨).

API 상세:
  베이스: https://apis.data.go.kr/1471000/FoodNtrCpntDbInfo02/getFoodNtrCpntDbInq02
  검색파라미터: FOOD_NM_KR(식품명, 부분일치) — FOOD_NM은 틀린 이름이라 무시됨(전체목록 반환)
  응답: header.resultCode "00" = 정상. body.items[] 안에 결과.
  영양성분 필드(국가표준식품성분표 표준 순서, 100g 기준):
    AMT_NUM1=에너지(kcal), AMT_NUM3=단백질(g), AMT_NUM4=지방(g), AMT_NUM6=탄수화물(g)
    SERVING_SIZE는 "100g" 같은 문자열이라 숫자만 뽑아써야 함.
"""
import difflib
import os
import re
import urllib.parse

import requests

BASE_URL = "https://apis.data.go.kr/1471000/FoodNtrCpntDbInfo02/getFoodNtrCpntDbInq02"
SUCCESS_CODE = "00"


def _log(msg: str) -> None:
    print(f"  [DB로그] {msg}")


def _clean_dish_name(dish: str) -> str:
    """'새우까스*타르소스', '상추쌈*쌈장 / 명태미나리무침' 같은 구내식당 표기를
    DB 검색에 걸리기 좋은 단순한 이름으로 정리한다.
    '*'나 '/'는 재료 결합/병기 표시일 뿐, 실제 음식 DB에는 이런 기호가 없다."""
    return re.split(r"[*/]", dish)[0].strip()


def _parse_serving_g(serving_size: str) -> float:
    """'100g' -> 100.0. 숫자를 못 찾으면 기본값 100."""
    match = re.search(r"[\d.]+", serving_size or "")
    return float(match.group()) if match else 100.0


def lookup_multi(query: str, api_key: str | None = None, limit: int = 5) -> list[dict]:
    """검색어 하나로 DB를 조회해서, 매칭되는 결과를 전부(최대 limit개) 반환.
    Level 1/2/3 어느 단계에서든 이 함수 하나로 재사용된다.
    API 키가 없거나 요청이 실패하면 빈 리스트를 반환한다 (예외를 던지지 않음
    -> 호출부의 폴백 체인이 안 끊기게). 실패 이유는 전부 _log로 남긴다.
    """
    key = api_key or os.environ.get("FOOD_SAFETY_API_KEY")
    if not key:
        _log(f"'{query}': FOOD_SAFETY_API_KEY가 설정되지 않음 -> 스킵")
        return []
    # .env에 저장된 키는 마이페이지에서 복사한 그대로라 이미 URL-인코딩된 상태
    # (%2B, %2F, %3D%3D 등). requests가 params로 넘길 때 또 인코딩해버리면
    # 이중 인코딩이 되어 서버가 다른 키로 오인식한다. 디코딩해서 원본으로 복원.
    key = urllib.parse.unquote(key)

    clean_query = _clean_dish_name(query)
    params = {
        "serviceKey": key,
        "type": "json",
        "pageNo": 1,
        "numOfRows": limit,
        "FOOD_NM_KR": clean_query,
    }

    try:
        resp = requests.get(BASE_URL, params=params, timeout=15)
    except requests.RequestException as e:
        _log(f"'{query}': 연결 실패(네트워크/타임아웃) - {e}")
        return []

    if resp.status_code != 200:
        _log(f"'{query}': HTTP {resp.status_code} 응답 | body={resp.text[:200]!r}")
        return []

    try:
        data = resp.json()
    except ValueError:
        _log(f"'{query}': JSON 파싱 실패 | body={resp.text[:200]!r}")
        return []

    header = data.get("header", {})
    code = header.get("resultCode")
    if code != SUCCESS_CODE:
        _log(f"'{query}': API 에러코드 {code} - {header.get('resultMsg')}")
        return []

    items = (data.get("body") or {}).get("items") or []
    if not items:
        _log(f"'{query}': 정상 응답이지만 매칭 결과 0건 (DB에 그 음식이 없음)")
        return []

    _log(f"'{query}': {len(items)}건 매칭 (전체 {data['body'].get('totalCount')}건 중)")

    results = []
    for item in items:
        try:
            results.append({
                "matched_name": item.get("FOOD_NM_KR", clean_query),
                "serving_g": _parse_serving_g(item.get("SERVING_SIZE")),
                "kcal": round(float(item.get("AMT_NUM1") or 0)),
                "protein_g": round(float(item.get("AMT_NUM3") or 0), 1),
                "fat_g": round(float(item.get("AMT_NUM4") or 0), 1),
                "carb_g": round(float(item.get("AMT_NUM6") or 0), 1),
            })
        except (TypeError, ValueError):
            continue
    return results


# 유사도 점수 구간. difflib.SequenceMatcher.ratio()는 0~1 사이 값을 준다.
# "검색 결과가 있다 = 정확하다"가 아니라는 사용자 피드백을 반영해서, 검색어와
# matched_name의 문자열 유사도로 "진짜 같은 음식인지"를 가늠한다 (GPT를 또
# 호출하면 비용이 배로 드니, 코드로 계산 가능한 이 방식을 우선 사용).
SIMILARITY_HIGH = 0.6   # 이 이상이면 "정확 매칭"으로 취급
SIMILARITY_LOW = 0.25   # 이 미만이면 매칭을 신뢰하지 않고 버림


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def lookup_best(dish: str, api_key: str | None = None) -> dict | None:
    """검색 결과 후보들 중 검색어와 이름이 가장 비슷한 것을 골라 반환.
    단순히 '검색 결과 첫 번째'를 채택하지 않는 이유: DB가 부분일치로 동작하다
    보니, "쌀밥"을 검색해도 "잡곡쌀밥", "쌀밥_알레르기유발" 같은 게 먼저 나올
    수 있음 -> 이름이 원본 검색어와 가장 가까운 것을 우선한다.
    유사도가 SIMILARITY_LOW 미만이면 아예 다른 음식일 가능성이 높다고 보고
    None을 반환한다 (호출부가 다음 단계로 폴백).
    """
    clean_query = _clean_dish_name(dish)
    candidates = lookup_multi(dish, api_key=api_key, limit=10)
    if not candidates:
        return None

    scored = [(c, _similarity(clean_query, c["matched_name"])) for c in candidates]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    best, score = scored[0]

    if score < SIMILARITY_LOW:
        _log(f"'{dish}': 최고 유사도 후보('{best['matched_name']}')도 유사도 {score:.2f}로 너무 낮음 -> 폐기")
        return None

    best = dict(best)
    best["similarity"] = round(score, 2)
    _log(f"'{dish}': 최종 채택 '{best['matched_name']}' (유사도 {score:.2f})")
    return best


def lookup(dish: str, api_key: str | None = None) -> dict | None:
    """호환용 별칭. 이제 첫 결과가 아니라 유사도 기반 최선 결과를 반환한다."""
    return lookup_best(dish, api_key=api_key)


if __name__ == "__main__":
    for name in ["쌀밥", "김치", "돈까스", "콩나물불고기", "제육볶음"]:
        print(name, "->", lookup(name))
