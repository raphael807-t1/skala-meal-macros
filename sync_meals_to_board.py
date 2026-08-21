"""이번 주(월~금) 급식 메뉴 + 탄단지 분석 결과를 게시판(PostgreSQL) DB에 넣는 배치 스크립트.

역할 분리 원칙 (6주차_클라우드 day2/homework_revised와 합의한 구조):
  - 이 스크립트(배치) : 급식 API 호출 + 영양정보 계산 + DB에 INSERT까지 담당
  - Spring Boot(웹서비스) : 이 DB를 읽어서 보여주기 + 좋아요/댓글만 담당 (급식 데이터는 안 씀)

DB 구조 (끼니 단위로 좋아요/댓글):
  meal_session : 하루의 한 끼니 전체 (예: "2026-08-20 중식" 하나가 한 행). like_count를 가짐.
  meal         : 그 끼니에 속한 요리 하나하나. session_id로 meal_session을 가리킴.
  meal_comment : meal_session 하나에 달리는 댓글.

전제 조건:
  1) 게시판 쪽에서 `docker compose up -d`를 먼저 실행해서 Spring Boot(Hibernate)가
     meal_session/meal/meal_comment 테이블을 자동 생성해둔 상태여야 함 (ddl-auto=update)
  2) docker-compose.yml에서 db 포트를 5433:5432로 호스트에 열어뒀으므로, 이 스크립트는
     컨테이너 안이 아니라 "내 컴퓨터에서 직접" DB에 접속함

실행:
  pip install -r requirements.txt   (psycopg2-binary 포함)
  python3 sync_meals_to_board.py
"""
import psycopg2

import estimate_macros
import fetch_menu
from run_daily import _load_dotenv

# 게시판 docker-compose.yml에서 정한 값과 동일해야 함 (backend의 application.properties와 짝)
DB_CONFIG = dict(
    host="localhost",
    port=5433,
    dbname="board",
    user="board_user",
    password="board_pw",
)


def this_week_dates() -> list[str]:
    """이번 주 API가 원래 내려주는 월~금 날짜를 그대로 다시 구해서 반환."""
    import requests
    resp = requests.get(fetch_menu.MENU_API, timeout=10)
    resp.raise_for_status()
    return [day["date"] for day in resp.json().get("days", [])]


def get_or_create_session(cur, date: str, meal_type: str) -> int:
    """이 (날짜, 끼니)에 해당하는 meal_session 행을 찾고, 없으면 새로 만들어서 id를 돌려준다.
    -> Spring Boot가 좋아요/댓글을 붙일 대상(끼니 전체)이 바로 이 id."""
    cur.execute("SELECT id FROM meal_session WHERE date=%s AND meal_type=%s", (date, meal_type))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO meal_session (date, meal_type, like_count) VALUES (%s, %s, 0) RETURNING id",
        (date, meal_type),
    )
    return cur.fetchone()[0]


def insert_dishes(cur, session_id: int, date: str, meal_type: str, day: dict) -> int:
    """한 끼니(session_id)에 속한 요리들을 계산해서 INSERT. 이미 들어간 요리는 건너뜀
    (같은 세션에 같은 요리명이 이미 있으면 중복 삽입 방지)."""
    meal = day.get(meal_type)
    if not meal:
        return 0
    names = [d["name"] for d in meal.get("dishes", [])]
    if not names:
        return 0

    macros = estimate_macros.estimate_dishes(names)  # 캐시(macro_cache.json) 재사용됨
    inserted = 0
    for name in names:
        cur.execute("SELECT 1 FROM meal WHERE session_id=%s AND dish_name=%s", (session_id, name))
        if cur.fetchone():
            continue
        m = macros[name]
        cur.execute(
            """
            INSERT INTO meal (session_id, dish_name, carb_g, protein_g, fat_g, kcal, source, reliability)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (session_id, name, m["carb_g"], m["protein_g"], m["fat_g"], m["kcal"], m["source"], m["reliability"]),
        )
        inserted += 1
    return inserted


def main():
    # 이게 빠져있으면 FOOD_SAFETY_API_KEY/OPENAI_API_KEY가 둘 다 안 읽혀서
    # DB검색·GPT추정이 전부 조용히 실패하고 "실패"/kcal 0으로 채워짐.
    _load_dotenv()

    dates = this_week_dates()
    print(f"이번 주 대상 날짜: {dates}")

    conn = psycopg2.connect(**DB_CONFIG)
    total_inserted = 0
    with conn, conn.cursor() as cur:
        for date in dates:
            day = fetch_menu.fetch_today_menu(date)
            if day is None:
                print(f"  - {date}: 메뉴 없음, 건너뜀")
                continue
            for meal_type in ("lunch", "dinner"):
                if not day.get(meal_type):
                    continue
                session_id = get_or_create_session(cur, date, meal_type)
                inserted = insert_dishes(cur, session_id, date, meal_type, day)
                label = "중식" if meal_type == "lunch" else "석식"
                print(f"  - {date} {label} (session_id={session_id}): {inserted}개 요리 새로 추가")
                total_inserted += inserted
    conn.close()

    print(f"총 새로 추가된 요리 행: {total_inserted}개 (나머지는 이미 있어서 건너뜀)")


if __name__ == "__main__":
    main()
