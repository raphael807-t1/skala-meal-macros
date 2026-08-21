"""메뉴명 키워드 기반 휴리스틱 (GPT 호출 없이 코드로 처리 — 비용 절감).

두 가지 역할:
1. 실제 1회 제공량(g) 추정 — 식품안전나라 DB는 전부 "100g 기준"으로 주는데,
   실제 급식 한 그릇/한 접시 양은 음식 종류에 따라 천차만별이다(국밥 한 그릇
   500g vs 김치 반찬 한 젓가락 50g). GPT한테 매번 물어보면 비용이 드니,
   키워드로 대략적인 카테고리를 판단해서 합리적인 기본값을 쓴다.
2. 이상치 탐지 — "국/찌개인데 100g당 200kcal 넘음" 같은, DB 데이터 자체가
   의심스러운 경우를 계산 없이 규칙 기반으로 짚어낸다. DB 원본값은 그대로
   두고 "⚠️ 이상치 의심" 표시만 붙인다 (임의로 수정하지 않음).
"""

# (키워드들, 기본 제공량 g, "100g당 kcal이 이 값을 넘으면 이상치 의심" 상한선)
# 순서대로 검사해서 먼저 매칭되는 규칙을 사용 -> 구체적인 키워드를 앞쪽에 배치.
_CATEGORY_RULES = [
    (("국밥", "탕", "찌개", "전골", "국수", "라면"), 450, 150),
    (("국",), 300, 120),
    (("쌀밥", "볶음밥", "비빔밥", "덮밥", "죽"), 200, 250),
    (("김치", "무침", "겉절이", "장아찌", "지"), 50, 150),
    (("쌈장", "소스", "드레싱", "양념", "토핑"), 20, 400),
    (("샐러드",), 150, 200),
    (("만두", "튀김", "까스", "돈까스", "고로케", "전"), 180, 350),
    (("조림", "볶음", "구이", "찜"), 200, 300),
]

_DEFAULT_SERVING_G = 150
_DEFAULT_KCAL_CEILING = 350


def guess_serving_g(dish_name: str) -> int:
    """메뉴명에 포함된 키워드로 대략적인 1회 제공량을 추정한다.
    완벽한 계량이 아니라 "100g으로 퉁치는 것보다는 나은" 근사치."""
    for keywords, serving_g, _ in _CATEGORY_RULES:
        if any(kw in dish_name for kw in keywords):
            return serving_g
    return _DEFAULT_SERVING_G


def is_outlier(dish_name: str, kcal_per_100g: float) -> bool:
    """이 음식 카테고리 기준으로 100g당 칼로리가 비정상적으로 높으면 True.
    DB 데이터 자체의 신뢰도를 의심할 때 쓰는 용도 (값을 고치지는 않음)."""
    for keywords, _, ceiling in _CATEGORY_RULES:
        if any(kw in dish_name for kw in keywords):
            return kcal_per_100g > ceiling
    return kcal_per_100g > _DEFAULT_KCAL_CEILING
