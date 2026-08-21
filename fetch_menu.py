"""SKALA 구내식당 주간 메뉴 API에서 오늘의 중식/석식 메뉴를 가져온다.

이 API는 원래 공식 문서가 없는 "비공개 API"다.
찾아낸 방법: 사이트(skala-lunch.ewkimhyunsu11.workers.dev)에 접속했더니
<div id="root"></div> 하나뿐인 SPA(React 등으로 그려지는 페이지)였음.
그래서 화면에 보이는 HTML이 아니라, 그 화면을 그리는 JS 번들 파일
(assets/index-*.js)을 직접 다운받아서 안에서 fetch(...) 호출을 grep으로 찾음.
그 결과 "/api/menus/current" 라는 엔드포인트가 인증 없이 열려있는 걸 확인.
"""
import datetime

import requests

# 이 URL 하나가 이 프로젝트 전체의 유일한 데이터 소스다.
MENU_API = "https://skala-lunch.ewkimhyunsu11.workers.dev/api/menus/current"


def fetch_today_menu(target_date: str | None = None) -> dict | None:
    """이번 주 메뉴 전체를 받아온 뒤, 그중 오늘 날짜에 해당하는 하루치만 골라 반환한다.

    target_date: 'YYYY-MM-DD' 형식 문자열. None이면 시스템의 오늘 날짜를 사용.
    반환값 예시(day 하나):
        {
          "date": "2026-08-19", "weekday": "수",
          "lunch": {"dishes": [{"name": "고추참치덮밥", "isMain": True}, ...]},
          "dinner": {...},
          "dessert": "결명자차"
        }
    해당 날짜 데이터가 없으면(주말/아직 등록 안 됨) None을 반환한다.
    """
    target_date = target_date or datetime.date.today().isoformat()

    # API가 "이번 주 월~금 전체"를 한 번에 내려준다. 캐싱/재시도 로직이 없는
    # 매우 단순한 GET 요청이라, timeout만 걸어서 무한 대기를 방지한다.
    resp = requests.get(MENU_API, timeout=10)
    resp.raise_for_status()  # 4xx/5xx면 여기서 바로 예외를 던져서 실패를 빨리 알아챈다.
    data = resp.json()

    # data["days"]는 "월~금" 리스트. 그중 오늘 날짜와 문자열이 정확히 일치하는
    # 하루만 골라낸다. (날짜 파싱 대신 문자열 비교로 충분히 간단하게 처리)
    for day in data.get("days", []):
        if day.get("date") == target_date:
            return day
    return None


def dish_names(day: dict) -> list[str]:
    """하루치 메뉴(day)에서 중식+석식에 등장하는 모든 요리명을 리스트로 평탄화한다.

    - "쌀밥", "김치" 같은 곁들이 반찬도 그대로 포함한다(따로 걸러내지 않음).
      -> 다음 단계(estimate_macros.py)에서 이것들도 각각 영양정보를 추정하게 됨.
    - dessert(예: "결명자차")는 dishes 리스트에 안 들어있어서 여기선 제외된다.
    """
    names = []
    for meal_key in ("lunch", "dinner"):
        meal = day.get(meal_key)
        if not meal:
            # 그날 중식/석식 정보가 아예 없을 수도 있음 (예: 특별 휴무)
            continue
        for dish in meal.get("dishes", []):
            names.append(dish["name"])
    return names


if __name__ == "__main__":
    # 이 파일만 단독으로 실행했을 때: 오늘 메뉴 원본 JSON을 그대로 찍어서 확인하는 용도.
    # (다른 파일에서 import해서 쓸 때는 이 블록은 실행되지 않는다)
    today = fetch_today_menu()
    if today is None:
        print("오늘은 메뉴 정보가 없습니다 (주말/공휴일 등).")
    else:
        print(today)
