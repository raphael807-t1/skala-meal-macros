"""Slack 채널로 오늘의 메뉴 요약 전송 (Incoming Webhook 사용).

메시지는 일부러 짧게: 메뉴 나열 + 탄단지 총합만.
요리별 상세(1회 제공량/100g당 칼로리/탄단지 비율)는 generate_page.py가 만드는
웹페이지로 넘기고, 여기선 그 링크만 붙인다.
"""
import datetime
import os

import requests

MEAL_ICON = {"lunch": ":green_salad:", "dinner": ":stew:"}
MEAL_LABEL = {"lunch": "중식", "dinner": "석식"}

PAGE_URL = "https://raphael807-t1.github.io/skala-meal-macros/"


def _weekday_kr(date: str) -> str:
    dt = datetime.date.fromisoformat(date)
    return ["월", "화", "수", "목", "금", "토", "일"][dt.weekday()]


def _meal_line(meal: dict, excluded: set[str]) -> str:
    names = []
    for d in meal["dishes"]:
        name = d["name"]
        if name in excluded:
            name += "(중복제외)"
        names.append(name)
    return " · ".join(names)


def _meal_totals(dishes: list[str], macros: dict[str, dict], excluded: set[str]) -> dict:
    total = {"carb_g": 0, "protein_g": 0, "fat_g": 0, "kcal": 0}
    for d in dishes:
        if d in excluded:
            # 예: "장터국밥"에 밥이 이미 포함된 걸로 이 끼니에서 판단되면,
            # 같이 나온 "쌀밥"은 총합에서 제외(이중계산 방지). 메뉴 목록에는
            # 그대로 노출. excluded는 이 끼니 한정 집합이라 다른 끼니의
            # 같은 이름 메뉴까지 같이 빠지지 않는다.
            continue
        m = macros.get(d, {})
        for k in total:
            total[k] += m.get(k, 0)
    # 부동소수점 덧셈 오차(0.1+0.2=0.30000000000000004 같은) 방지 -> 소수점 첫째자리로 반올림
    for k in ("carb_g", "protein_g", "fat_g"):
        total[k] = round(total[k], 1)
    total["kcal"] = round(total["kcal"])
    return total


def build_message(date: str, day: dict, macros: dict[str, dict], excluded_by_meal: dict[str, set[str]] | None = None) -> str:
    excluded_by_meal = excluded_by_meal or {}
    dt = datetime.date.fromisoformat(date)
    header = f":knife_fork_plate: 오늘의 메뉴 · {dt.strftime('%m/%d')} ({_weekday_kr(date)})"

    blocks = [header]
    for meal_key in ("lunch", "dinner"):
        meal = day.get(meal_key)
        if not meal:
            continue
        dishes = [d["name"] for d in meal["dishes"]]
        excluded = excluded_by_meal.get(meal_key, set())
        icon = MEAL_ICON[meal_key]
        label = MEAL_LABEL[meal_key]
        meal_totals = _meal_totals(dishes, macros, excluded)
        blocks.append(
            f"{icon} {label}\n"
            f"{_meal_line(meal, excluded)}\n"
            f":fire:총 칼로리: {meal_totals['kcal']}kcal, 총 탄수: {meal_totals['carb_g']}g, "
            f"총 단백질: {meal_totals['protein_g']}g, 총 지방: {meal_totals['fat_g']}g"
        )

    blocks.append(f"<{PAGE_URL}|자세히보기>")

    # 각 블록 사이 빈 줄 하나씩 (요청하신 예시처럼 끼니 단락 사이를 띄움)
    return "\n\n".join(blocks)


def send_to_slack(text: str, webhook_url: str | None = None) -> None:
    webhook_url = webhook_url or os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        print("[SLACK 미전송] SLACK_WEBHOOK_URL이 설정되지 않았습니다.\n---\n" + text)
        return
    resp = requests.post(webhook_url, json={"text": text}, timeout=10)
    resp.raise_for_status()
    print("Slack 전송 완료.")
