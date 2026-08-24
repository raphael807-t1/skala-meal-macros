"""매일 실행되는 메인 스크립트: 메뉴 가져오기 -> 영양정보 추정 -> 페이지 생성 -> git push -> Slack 전송."""
import os
import subprocess
import sys
from pathlib import Path

import estimate_gpt
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


def _push_page_to_github(date: str) -> None:
    """GitHub Pages(docs/)가 오늘자 상세페이지를 서빙하도록 자동 커밋+푸시.
    실패해도(오프라인 등) 전체 파이프라인은 계속 진행 -> Slack 전송은 살아있게.

    cron이 pmset 웨이크 직후(8:55 기상 -> 9:00 실행)에 도는데, 그 사이 Wi-Fi가
    아직 완전히 재연결 안 된 상태에서 push가 실패한 적이 있어서(2026-08-24),
    바로 포기하지 않고 몇 초 간격으로 재시도한다."""
    import time

    repo_dir = Path(__file__).parent
    try:
        subprocess.run(["git", "add", "docs/index.html"], cwd=repo_dir, check=True)
        result = subprocess.run(
            ["git", "commit", "-m", f"메뉴 업데이트: {date}"],
            cwd=repo_dir, capture_output=True, text=True,
        )
        if result.returncode != 0 and "nothing to commit" not in result.stdout:
            print(f"[git commit 경고] {result.stdout}{result.stderr}")
            return
    except subprocess.CalledProcessError as e:
        print(f"[git add/commit 실패, 계속 진행] {e}")
        return

    max_attempts = 4
    for attempt in range(1, max_attempts + 1):
        result = subprocess.run(["git", "push"], cwd=repo_dir, capture_output=True, text=True)
        if result.returncode == 0:
            print("GitHub Pages 업데이트(push) 완료." + (f" ({attempt}번째 시도)" if attempt > 1 else ""))
            return
        print(f"[git push 실패 {attempt}/{max_attempts}] {result.stderr.strip()}")
        if attempt < max_attempts:
            time.sleep(15)
    print("[git push 최종 실패, 다음 실행 때 재시도됨]")


def _find_meal_duplicates(day: dict) -> dict[str, dict[str, str]]:
    """끼니(중식/석식)별로 딱 1번씩 GPT를 불러서 이중계산 위험 메뉴를 찾는다.
    반환값: {"lunch": {}, "dinner": {"쌀밥": "장터국밥"}} 형태 -- 제외된 메뉴명 ->
    "그 메뉴에 이미 포함됐다고 판단한 다른 메뉴명". 화면에 "왜 빠졌는지"를
    바로 보여주기 위해 이유(contained_in)까지 같이 들고 다닌다.

    주의: 같은 메뉴명(예: "쌀밥")이 점심/저녁에 둘 다 나올 수 있는데, macros는
    메뉴명을 키로 쓰는 딕셔너리라 점심 쌀밥과 저녁 쌀밥이 "같은 객체"를
    공유한다. 그래서 예전엔 저녁에서만 제외 판단이 나도 macros[dish]에 직접
    플래그를 박아버려서 점심 쌀밥까지 같이 제외되는 버그가 있었다.
    그래서 절대 macros를 직접 수정하지 않고, 끼니별로 분리된 dict를 따로
    반환해서 generate_page.py/notify_slack.py가 "지금 보고 있는 끼니 안에서만"
    이 메뉴가 제외 대상인지 판단하게 한다."""
    excluded_by_meal: dict[str, dict[str, str]] = {}
    for meal_key in ("lunch", "dinner"):
        meal = day.get(meal_key)
        if not meal:
            continue
        names = [d["name"] for d in meal["dishes"]]
        result = estimate_gpt.check_meal_duplicates(names)
        excluded: dict[str, str] = {}
        for item in result["exclude"]:
            dish = item["dish"]
            excluded[dish] = item["contained_in"]
            print(f"  [중복검사:{meal_key}] '{dish}' 총합에서 제외 (근거: {item['contained_in']}) - {item.get('reason', '')}")
        excluded_by_meal[meal_key] = excluded
    return excluded_by_meal


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

    excluded_by_meal = _find_meal_duplicates(day)

    page_path = generate(day["date"], day, macros, excluded_by_meal)
    print(f"페이지 생성 완료: {page_path}")

    _push_page_to_github(day["date"])

    message = build_message(day["date"], day, macros, excluded_by_meal)
    send_to_slack(message)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"실행 중 오류: {e}", file=sys.stderr)
        raise
