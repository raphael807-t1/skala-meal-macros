"""오늘 메뉴 + 영양정보로 상세 페이지(docs/index.html) 생성.

Slack 메시지는 총합만 보여주고, 이 페이지는 "자세히보기"로 들어왔을 때
요리별 1회 제공량 / 100g당 칼로리 / 탄단지 비율까지 보여준다.
"""
from pathlib import Path

OUT_PATH = Path(__file__).parent / "docs" / "index.html"

MEAL_LABEL = {"lunch": "🥗 중식", "dinner": "🍲 석식"}


def _per_100g(m: dict) -> dict:
    """1회 제공량(serving_g) 기준 값을 100g 기준으로 환산."""
    serving_g = m.get("serving_g") or 0
    if serving_g <= 0:
        return {"carb_g": 0, "protein_g": 0, "fat_g": 0, "kcal": 0}
    ratio = 100 / serving_g
    return {
        "carb_g": round(m.get("carb_g", 0) * ratio, 1),
        "protein_g": round(m.get("protein_g", 0) * ratio, 1),
        "fat_g": round(m.get("fat_g", 0) * ratio, 1),
        "kcal": round(m.get("kcal", 0) * ratio),
    }


def _meal_totals(dishes: list[str], macros: dict[str, dict], excluded: dict[str, str]) -> dict:
    total = {"carb_g": 0, "protein_g": 0, "fat_g": 0, "kcal": 0}
    for d in dishes:
        if d in excluded:
            # 예: "장터국밥"에 밥이 이미 포함돼있다고 이 끼니에서 GPT가 판단한
            # 경우, 같이 나온 "쌀밥"을 총합에 또 더하면 이중계산이라 스킵.
            # excluded는 "이 끼니 한정" 집합이라, 다른 끼니의 같은 이름
            # 메뉴(예: 점심 쌀밥)까지 같이 빠지는 일은 없다.
            continue
        m = macros.get(d, {})
        for k in total:
            total[k] += m.get(k, 0)
    # 부동소수점 덧셈 오차(0.1+0.2=0.30000000000000004 같은) 방지 -> 소수점 첫째자리로 반올림
    for k in ("carb_g", "protein_g", "fat_g"):
        total[k] = round(total[k], 1)
    total["kcal"] = round(total["kcal"])
    return total


RELIABILITY_BADGE = {
    "high": "🟢 DB",
    "medium": "🟡 DB유사",
    "low": "🟠 GPT추정",
    "none": "🔴 실패",
}


def _source_badge(m: dict) -> str:
    # 이상치 의심이면 그게 제일 중요한 정보라 출처 대신 그것부터 보여준다.
    if m.get("outlier"):
        return "⚠️ 이상치의심"
    return RELIABILITY_BADGE.get(m.get("reliability"), "-")


def _meal_rows(dishes: list[str], macros: dict[str, dict], excluded: dict[str, str]) -> str:
    rows = []
    for d in dishes:
        m = macros.get(d, {})
        p100 = _per_100g(m)
        contained_in = excluded.get(d)
        if contained_in:
            # 그냥 흐리게만 하면 "왜 빠졌는지" 안 보여서, 값 칸에 취소선을 긋고
            # 그 옆에 이유를 텍스트로 바로 보여준다 (예: "장터국밥에 포함").
            row = (
                '<tr class="excluded">'
                f"<td>{d}</td>"
                f"<td>{m.get('serving_g', 0)}g</td>"
                f"<td>{p100['kcal']}kcal</td>"
                f'<td><s>{m.get("carb_g", 0)}g / {m.get("protein_g", 0)}g / {m.get("fat_g", 0)}g</s></td>'
                f'<td><s>{m.get("kcal", 0)}kcal</s></td>'
                f'<td class="src">🔁 {contained_in}에 포함</td>'
                "</tr>"
            )
        else:
            badge = _source_badge(m)
            row = (
                "<tr>"
                f"<td>{d}</td>"
                f"<td>{m.get('serving_g', 0)}g</td>"
                f"<td>{p100['kcal']}kcal</td>"
                f"<td>{m.get('carb_g', 0)}g / {m.get('protein_g', 0)}g / {m.get('fat_g', 0)}g</td>"
                f"<td>{m.get('kcal', 0)}kcal</td>"
                f'<td class="src">{badge}</td>'
                "</tr>"
            )
        rows.append(row)
    return "\n".join(rows)


def build_html(date: str, day: dict, macros: dict[str, dict], excluded_by_meal: dict[str, dict[str, str]] | None = None) -> str:
    excluded_by_meal = excluded_by_meal or {}
    sections = []
    for meal_key, label in MEAL_LABEL.items():
        meal = day.get(meal_key)
        if not meal:
            continue
        dishes = [d["name"] for d in meal["dishes"]]
        excluded = excluded_by_meal.get(meal_key, {})
        totals = _meal_totals(dishes, macros, excluded)
        sections.append(f"""
        <section>
          <h2>{label}</h2>
          <table>
            <colgroup>
              <col style="width:20%"><col style="width:10%"><col style="width:12%">
              <col style="width:21%"><col style="width:9%"><col style="width:28%">
            </colgroup>
            <thead>
              <tr>
                <th>메뉴</th><th>1회 제공량</th><th>100g당 칼로리</th>
                <th>탄/단/지</th><th>칼로리</th><th>출처</th>
              </tr>
            </thead>
            <tbody>
              {_meal_rows(dishes, macros, excluded)}
              <tr class="total">
                <td colspan="3">합계</td>
                <td>{totals['carb_g']}g / {totals['protein_g']}g / {totals['fat_g']}g</td>
                <td>{totals['kcal']}kcal</td>
                <td></td>
              </tr>
            </tbody>
          </table>
        </section>""")

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SKALA 오늘의 영양정보</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 720px; margin: 40px auto; padding: 0 16px; color: #222; }}
  h1 {{ font-size: 1.4rem; }}
  h2 {{ font-size: 1.1rem; margin-top: 2rem; }}
  table {{ width: 100%; min-width: 560px; table-layout: fixed; border-collapse: collapse; font-size: 0.85rem; }}
  th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid #eee; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  td.src {{ white-space: normal; overflow: visible; text-overflow: clip; }}
  tr.total {{ font-weight: bold; border-top: 2px solid #333; }}
  tr.excluded {{ color: #999; }}
  tr.excluded s {{ opacity: 0.7; }}
  .note {{ color: #888; font-size: 0.8rem; margin-top: 2rem; }}
  .scroll {{ overflow-x: auto; }}
</style>
</head>
<body>
  <h1>🍽️ SKALA 오늘의 영양정보 ({date})</h1>
  <div class="scroll">
  {"".join(sections) if sections else "<p>오늘은 메뉴 정보가 없습니다.</p>"}
  </div>
  <p class="note">출처: 식품의약품안전처 식품영양성분DB API (data.go.kr)</p>
</body>
</html>"""


def generate(date: str, day: dict, macros: dict[str, dict], excluded_by_meal: dict[str, dict[str, str]] | None = None) -> Path:
    OUT_PATH.parent.mkdir(exist_ok=True)
    OUT_PATH.write_text(build_html(date, day, macros, excluded_by_meal), encoding="utf-8")
    return OUT_PATH
