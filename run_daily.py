"""매일 실행되는 메인 스크립트: 메뉴 가져오기 -> 영양정보 추정 -> 페이지 생성 -> git push -> Slack 전송."""
import os
import subprocess
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


def _push_page_to_github(date: str) -> None:
    """GitHub Pages(docs/)가 오늘자 상세페이지를 서빙하도록 자동 커밋+푸시.
    실패해도(오프라인 등) 전체 파이프라인은 계속 진행 -> Slack 전송은 살아있게."""
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
        subprocess.run(["git", "push"], cwd=repo_dir, check=True)
        print("GitHub Pages 업데이트(push) 완료.")
    except subprocess.CalledProcessError as e:
        print(f"[git push 실패, 계속 진행] {e}")


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

    _push_page_to_github(day["date"])

    message = build_message(day["date"], day, macros)
    send_to_slack(message)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"실행 중 오류: {e}", file=sys.stderr)
        raise
