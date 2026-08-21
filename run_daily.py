"""매일 실행되는 메인 스크립트: 메뉴 가져오기 -> 영양정보 추정 -> 페이지 생성 -> Slack 전송."""
import os
import sys
from pathlib import Path

from estimate_macros import estimate_dishes
from fetch_menu import dish_names, fetch_today_menu
from generate_page import generate
from notify_slack import build_message, send_to_slack


def _load_dotenv() -> None:
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def main() -> None:
    _load_dotenv()
    day = fetch_today_menu()
    if day is None:
        print("오늘은 메뉴 정보가 없습니다 (주말/공휴일/미등록).")
        return
    if day.get("isHoliday"):
        print(f"{day['date']}는 휴일입니다. 스킵합니다.")
        return

    dishes = dish_names(day)
    if not dishes:
        print("등록된 메뉴가 없습니다.")
        return

    print(f"[{day['date']}] 메뉴 {len(dishes)}개 영양정보 추정 중...")
    macros = estimate_dishes(dishes)

    page_path = generate(day["date"], day, macros)
    print(f"페이지 생성 완료: {page_path}")

    message = build_message(day["date"], day, macros)
    send_to_slack(message)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"실행 중 오류: {e}", file=sys.stderr)
        raise
