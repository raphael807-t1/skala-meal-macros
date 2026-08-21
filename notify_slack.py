"""Slack 채널로 오늘의 메뉴+영양정보 전송 (Incoming Webhook 사용)."""
import os

import requests

MEAL_LABEL = {"lunch": "중식", "dinner": "석식"}


def _meal_block(meal_key: str, meal: dict, macros: dict[str, dict]) -> str:
    label = MEAL_LABEL[meal_key]
    lines = [f"*{label}*"]
    total = {"carb_g": 0, "protein_g": 0, "fat_g": 0, "kcal": 0}
    for dish in meal["dishes"]:
        name = dish["name"]
        m = macros.get(name, {})
        lines.append(
            f"  - {name}: 탄 {m.get('carb_g', 0)}g / 단 {m.get('protein_g', 0)}g "
            f"/ 지 {m.get('fat_g', 0)}g / {m.get('kcal', 0)}kcal"
        )
        for k in total:
            total[k] += m.get(k, 0)
    lines.append(
        f"  *합계: 탄 {total['carb_g']}g / 단 {total['protein_g']}g "
        f"/ 지 {total['fat_g']}g / {total['kcal']}kcal*"
    )
    return "\n".join(lines)


def build_message(date: str, day: dict, macros: dict[str, dict]) -> str:
    parts = [f":rice: *SKALA 오늘의 메뉴 & 영양정보 ({date})*"]
    for meal_key in ("lunch", "dinner"):
        meal = day.get(meal_key)
        if meal:
            parts.append(_meal_block(meal_key, meal, macros))
    parts.append("_영양정보는 로컬 sLLM 추정치입니다._")
    return "\n\n".join(parts)


def send_to_slack(text: str, webhook_url: str | None = None) -> None:
    webhook_url = webhook_url or os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        print("[SLACK 미전송] SLACK_WEBHOOK_URL이 설정되지 않았습니다.\n---\n" + text)
        return
    resp = requests.post(webhook_url, json={"text": text}, timeout=10)
    resp.raise_for_status()
    print("Slack 전송 완료.")
