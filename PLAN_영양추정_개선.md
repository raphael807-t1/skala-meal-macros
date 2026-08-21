# 영양성분 추정 엔진 개선 계획

> GPT와 상의해서 받아온 개선안을 이 프로젝트 실제 코드 구조에 맞게 정리한 실행 계획.
> 구현 전 검토용 — 이 파일 승인되면 estimate_macros.py / estimate_gpt.py / nutrition_db.py 순서로 수정 착수.

## 1. 지금 뭐가 문제인가

현재(`estimate_macros.py`) 흐름:
```
메뉴명 → DB 정확매칭 검색(1회) → 실패 → GPT에게 "탄단지kcal 다 뽑아줘" → 끝
```

문제:
- DB 검색이 "원본 메뉴명 그대로" 딱 1번뿐이라, 표기가 조금만 달라도(`콩나물불고기` vs `콩나물 불고기볶음`) DB에 데이터가 있어도 못 찾음
- 복합 메뉴("고추참치덮밥" 같은)는 애초에 DB에 그 이름 그대로 있을 리가 없어서 항상 GPT로 직행
- GPT가 탄/단/지/kcal를 한 번에 다 만들어내다 보니, **탄단지 합산 kcal와 GPT가 부른 kcal가 서로 안 맞을 수 있음** (검증 안 하고 있음)
- DB값을 썼는지 GPT 추정값을 썼는지 결과에 구분이 없어서, 사용자가 신뢰도를 판단할 수 없음

## 2. 개선 후 흐름

```
메뉴명
 → [DB] Level 1: 원본 메뉴명 그대로 검색
 → 실패 →  [DB] Level 2: 표기 변형/유사어 검색 (GPT가 검색어 후보만 생성)
 → 실패 →  [DB] Level 3: 복합 메뉴 구성요소 분해 → 요소별 DB 검색 (GPT가 구성요소+비율만 생성)
 → 그래도 부족 → GPT 자체 추정 (최후 수단, confidence 낮게 표시)
 → [코드] 탄/단/지 → kcal 재계산해서 검증 (4/4/9 kcal 공식)
 → [코드] source + reliability 태깅해서 최종 반환
```

**핵심 원칙**: GPT는 "숫자를 만드는 역할"에서 "메뉴 구조를 분석해서 DB 검색을 돕는 역할"로 축소. 최종 계산과 검증은 항상 코드가 한다.

## 3. 파일별 변경 사항

### `nutrition_db.py`
- 지금은 `lookup(dish)` 하나만 있고 정확 매칭 실패하면 바로 None → 이걸 다단계로 확장
- `lookup_multi(query: str) -> list[dict]` 추가: DESC_KOR 검색 결과를 여러 개 받아서(현재는 `rows[0]`만 씀), 후보 리스트 반환 — Level 2/3에서 여러 검색어를 시도할 때 재사용
- 기존 `lookup()`은 그대로 두되 내부적으로 `lookup_multi()`를 쓰도록 리팩터링 (Level 1 용도로 계속 사용)
- **삭제 금지** — API가 지금 불안정해도 코드는 그대로 유지 (사용자 요구사항)

### `estimate_gpt.py` — 역할이 바뀜
- 기존 `estimate_dish()`: 메뉴명 → 탄단지kcal 직접 생성 (지금 GPT-only 폴백 경로에서 씀) → **이름을 `estimate_dish_direct()`로 바꾸고, "최후 수단"으로만 남김**
- 새 함수 `analyze_dish_structure(dish, serving_g)` 추가:
  - system prompt를 사용자가 준 "② GPT 추정 엔진용 개선 프롬프트"로 교체
  - 반환값: `{dish_name, search_queries: [...], is_composite, components: [{name, ratio}], confidence, notes}`
  - **숫자(탄/단/지/kcal)는 요청하지 않음** — 구조 분석까지만 GPT가 담당
- kcal 계산 함수는 GPT 응답에서 절대 신뢰하지 않고, 아래 `estimate_macros.py`의 검증 함수로만 계산

### `estimate_macros.py` — 오케스트레이션 재작성
새 우선순위 로직 (`estimate_dish()` 전면 개편):

```python
def estimate_dish(dish, serving_g_hint=None):
    # Level 1: 원본 메뉴명 그대로 DB 검색
    result = nutrition_db.lookup(dish)
    if result:
        return _tag(result, source="food_safety_db", reliability="high")

    # Level 2 + 3: GPT한테 구조 분석 요청 (숫자 X, 검색어/구성요소만)
    structure = estimate_gpt.analyze_dish_structure(dish)

    # Level 2: GPT가 제안한 유사 검색어들로 DB 재검색
    for query in structure.get("search_queries", []):
        result = nutrition_db.lookup(query)
        if result:
            return _tag(result, source="food_safety_db(유사검색)", reliability="high")

    # Level 3: 복합 메뉴면 구성요소별로 DB 검색 → 비율대로 가중합산
    if structure.get("is_composite") and structure.get("components"):
        component_results = []
        for comp in structure["components"]:
            comp_data = nutrition_db.lookup(comp["name"])
            if comp_data:
                component_results.append((comp_data, comp["ratio"]))
        if len(component_results) == len(structure["components"]):  # 전부 매칭된 경우만 신뢰
            merged = _weighted_merge(component_results, serving_g_hint)
            return _tag(merged, source="component_db", reliability="medium")

    # 최후 수단: GPT 직접 추정 (기존 방식, 지금 쓰던 것)
    gpt_direct = estimate_gpt.estimate_dish_direct(dish)
    if gpt_direct:
        gpt_direct["kcal"] = _kcal_from_macros(gpt_direct)  # 검산은 항상 코드가
        return _tag(gpt_direct, source="gpt_estimate", reliability="low")

    return _tag({...0...}, source="실패", reliability="none")
```

- `_kcal_from_macros()`: 이미 있음(탄×4 + 단×4 + 지×9), **모든 경로의 최종 kcal는 이 함수로 재계산** — DB에서 kcal를 이미 줬어도(1회제공량당 실측값이니 그대로 신뢰) DB값은 예외, GPT/구성요소 추정 경로만 강제 재계산
- `_weighted_merge()`: 구성요소별 DB값 × 비율 → 가중합산 (신규)
- `_tag()`: 결과 dict에 `source`, `reliability` 필드 추가하는 헬퍼 (신규)
- **sLLM 관련 코드/주석은 그대로 유지** — 이번 개편과 무관, 건드리지 않음

### `generate_page.py`, `notify_slack.py`
- 상세페이지 표에 **출처/신뢰도 컬럼 추가** (🟢DB / 🟡구성요소 / 🟠GPT추정)
- Slack 메시지는 지금처럼 총합 위주로 짧게 유지하되, 혹시 신뢰도 낮은(🟠) 항목이 섞여있으면 자세히보기 링크 옆에 살짝 표시할지는 선택 사항 (일단 페이지에만 반영하고 Slack은 그대로 두는 걸 추천)

## 4. 구성비율 관련 주의사항 (사용자가 강조한 부분)

- 복합 메뉴를 무조건 N등분하지 않는다 — `components[].ratio`는 GPT가 음식 특성 고려해서 판단한 값을 그대로 사용
- 구성요소 중 하나라도 DB 매칭 안 되면(예: "양념/조리유" 같은 건 DB에 없을 가능성 큼) **그 결과는 신뢰 낮음으로 표시하고, 통째로 GPT 직접 추정으로 넘어가는 게 나을 수도 있음** — 이 부분은 구현하면서 실제 매칭률 보고 임계값 정하기 (예: 구성요소 70% 이상 매칭되면 사용, 아니면 폴백)

## 5. 단계별 구현 순서 (제안)

1. `nutrition_db.py`에 `lookup_multi()` 추가 (기존 `lookup()` 호환 유지)
2. `estimate_gpt.py`에 `analyze_dish_structure()` 추가, 기존 함수는 `estimate_dish_direct()`로 이름만 변경
3. `estimate_macros.py` 오케스트레이션 재작성 (Level 1~3 + 최후수단 + kcal 검증 + source 태깅)
4. `generate_page.py`에 출처/신뢰도 컬럼 추가
5. `data/macro_cache.json` 스키마 바뀌므로 캐시 초기화하고 실제 메뉴로 end-to-end 재실행 테스트
6. 실행 로그로 각 메뉴가 어느 레벨에서 잡혔는지 확인 (Level 1/2/3/최후수단 분포 체크)

## 6. 보존 확인 체크리스트 (사용자 요구사항)

- [ ] `nutrition_db.py` 코드 삭제 안 함, API 실패해도 계속 1순위로 시도
- [ ] `_estimate_dish_sllm()` 함수 삭제 안 함, 계속 비활성(주석) 상태 유지
- [ ] 기존 GPT 단독 추정 함수는 삭제 대신 `estimate_dish_direct()`로 이름 변경해서 "최후 수단"으로 보존
- [ ] 최종 kcal는 항상 코드가 탄×4+단×4+지×9로 검증/계산 (DB 실측값 제외)
- [ ] 각 결과에 source/reliability 표시

---
이 계획대로 진행할까요, 아니면 순서나 우선순위 조정하고 싶은 부분 있으신가요?
