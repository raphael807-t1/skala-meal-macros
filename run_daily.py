"""매일 실행되는 메인 스크립트: 메뉴 가져오기 -> 영양정보 추정 -> 페이지 생성 -> git push -> Slack 전송."""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import estimate_gpt
from estimate_macros import estimate_dishes
from fetch_menu import dish_names, fetch_today_menu, MENU_API
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
    바로 포기하지 않고 몇 초 간격으로 재시도한다.

    2026-08-27에 발견한 진짜 원인: cron은 GUI 로그인 세션이 아니라서 macOS
    키체인(git의 osxkeychain credential helper가 쓰는)에 접근을 못 한다.
    그래서 매번 "could not read Username for 'https://github.com': Device
    not configured"로 push가 조용히 실패하고 있었음 -> 로컬 commit은 매일
    쌓이는데 실제 push는 사용자가 나중에 수동으로 뭔가 할 때(키체인이 열려
    있는 타이밍)까지 안 돼서, 사이트가 "항상 하루 늦게" 보이는 현상으로
    나타났다. 재시도 횟수를 늘리는 걸로는 못 고치는 문제였음(인증 자체가
    안 되니 몇 번을 재시도해도 동일하게 실패).
    고침: keychain 대신 .env의 GITHUB_TOKEN(fine-grained PAT)을 push URL에
    직접 실어서 인증 -> GUI 세션 여부와 무관하게 항상 동작."""
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

    token = os.environ.get("GITHUB_TOKEN")
    if token:
        # 토큰을 원격 URL에 직접 실어서 push -> 이 명령의 인자로만 쓰이고
        # .git/config에는 남지 않으므로, 저장소 파일을 봐도 토큰이 노출 안 됨.
        push_target = [f"https://{token}@github.com/raphael807-t1/skala-meal-macros.git", "HEAD:main"]
    else:
        print("[경고] GITHUB_TOKEN이 .env에 없음 -> keychain 방식으로 push 시도(GUI 세션 아니면 실패함)")
        push_target = []

    max_attempts = 4
    for attempt in range(1, max_attempts + 1):
        result = subprocess.run(["git", "push", *push_target], cwd=repo_dir, capture_output=True, text=True)
        if result.returncode == 0:
            print("GitHub Pages 업데이트(push) 완료." + (f" ({attempt}번째 시도)" if attempt > 1 else ""))
            return
        print(f"[git push 실패 {attempt}/{max_attempts}] {result.stderr.strip()}")
        if attempt < max_attempts:
            time.sleep(15)
    print("[git push 최종 실패, 다음 실행 때 재시도됨]")


def _wait_for_network(max_wait_seconds: int = 180, interval_seconds: int = 10) -> None:
    """pmset이 맥을 깨운 직후(8:55 기상 -> 9:00 cron) Wi-Fi/DNS가 아직 안 붙어있는
    경우가 있다(2026-08-25에 NameResolutionError로 스크립트 전체가 죽은 적 있음,
    재시도 로직이 하나도 없어서 아예 실행이 안 됐었음). 그래서 첫 네트워크 호출
    전에 DNS가 풀리는지 먼저 확인하고, 안 풀리면 몇 초 간격으로 재시도한다."""
    from urllib.parse import urlparse

    host = urlparse(MENU_API).hostname
    deadline = time.time() + max_wait_seconds
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            socket.gethostbyname(host)
            if attempt > 1:
                print(f"네트워크 연결 확인됨 ({attempt}번째 시도)")
            return
        except socket.gaierror:
            print(f"[네트워크 대기 {attempt}] '{host}' DNS 해석 실패, {interval_seconds}초 후 재시도")
            time.sleep(interval_seconds)
    print("[네트워크 대기 시간 초과, 그래도 계속 진행 -> 이후 단계에서 실패할 수 있음]")


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
    _wait_for_network()
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
