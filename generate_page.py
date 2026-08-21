"""오늘 메뉴 + 영양정보로 상세 페이지(docs/index.html) 생성.

Slack 메시지는 총합만 보여주고, 이 페이지는 "자세히보기"로 들어왔을 때
요리별 1회 제공량 / 100g당 칼로리 / 탄단지 비율까지 보여준다.
"""
from pathlib import Path

OUT_PATH = Path(__file__).parent / "docs" / "index.html"

MEAL_LABEL = {"lunch": "중식", "dinner": "석식"}


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


def _macro_ratio(m: dict) -> str:
    """탄:단:지 무게(g) 비율을 정수 비율 문자열로."""
    c, p, f = m.get("carb_g", 0), m.get("protein_g", 0), m.get("fat_g", 0)
    total = c + p + f
    if total <= 0:
        return "-"
    return f"{round(c / total * 100)}:{round(p / total * 100)}:{round(f / total * 100)}"


def _meal_totals(dishes: list[str], macros: dict[str, dict]) -> dict:
    total = {"carb_g": 0, "protein_g": 0, "fat_g": 0, "kcal": 0}
    for d in dishes:
        m = macros.get(d, {})
        for k in total:
            total[k] += m.get(k, 0)
    # 부동소수점 덧셈 오차(0.1+0.2=0.30000000000000004 같은) 방지 -> 소수점 첫째자리로 반올림
    for k in ("carb_g", "protein_g", "fat_g"):
        total[k] = round(total[k], 1)
    total["kcal"] = round(total["kcal"])
    return total


RELIABILITY_BADGE = {
    "high": "🟢 DB 정확매칭",
    "medium": "🟡 DB 유사/구성요소",
    "low": "🟠 GPT추정",
    "none": "🔴 실패",
}


def _meal_rows(dishes: list[str], macros: dict[str, dict]) -> str:
    rows = []
    for d in dishes:
        m = macros.get(d, {})
        p100 = _per_100g(m)
        badge = RELIABILITY_BADGE.get(m.get("reliability"), "-")
        if m.get("outlier"):
            badge += " ⚠️이상치의심"
        rows.append(
            "<tr>"
            f"<td>{d}</td>"
            f"<td>{m.get('serving_g', 0)}g</td>"
            f"<td>{p100['kcal']}kcal</td>"
            f"<td>{_macro_ratio(m)}</td>"
            f"<td>{m.get('carb_g', 0)}g / {m.get('protein_g', 0)}g / {m.get('fat_g', 0)}g</td>"
            f"<td>{m.get('kcal', 0)}kcal</td>"
            f"<td>{badge}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def build_html(date: str, day: dict, macros: dict[str, dict]) -> str:
    sections = []
    for meal_key, label in MEAL_LABEL.items():
        meal = day.get(meal_key)
        if not meal:
            continue
        dishes = [d["name"] for d in meal["dishes"]]
        totals = _meal_totals(dishes, macros)
        sections.append(f"""
        <section>
          <h2>{label}</h2>
          <table>
            <thead>
              <tr>
                <th>메뉴</th><th>1회 제공량</th><th>100g당 칼로리</th>
                <th>탄:단:지 비율</th><th>탄/단/지</th><th>칼로리</th><th>출처</th>
              </tr>
            </thead>
            <tbody>
              {_meal_rows(dishes, macros)}
              <tr class="total">
                <td colspan="4">합계</td>
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
  table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid #eee; white-space: nowrap; }}
  tr.total {{ font-weight: bold; border-top: 2px solid #333; }}
  .note {{ color: #888; font-size: 0.8rem; margin-top: 2rem; }}
  .scroll {{ overflow-x: auto; }}
</style>
</head>
<body>
  <h1>SKALA 오늘의 영양정보 ({date})</h1>
  <div class="scroll">
  {"".join(sections) if sections else "<p>오늘은 메뉴 정보가 없습니다.</p>"}
  </div>
  <p class="note">
    출처: 🟢 DB 정확매칭(검색어와 이름이 매우 비슷) · 🟡 DB 유사매칭/구성요소 추정 · 🟠 GPT 추정(DB에 없는 메뉴) —
    100g당 DB 실측치를 메뉴 종류별 예상 제공량으로 환산한 값이며, 실제와 다를 수 있습니다.
    ⚠️이상치의심은 같은 종류 음식 대비 칼로리가 비정상적으로 높게 나온 DB 원본값입니다(값은 수정하지 않고 표시만 함).
  </p>
</body>
</html>"""


def generate(date: str, day: dict, macros: dict[str, dict]) -> Path:
    OUT_PATH.parent.mkdir(exist_ok=True)
    OUT_PATH.write_text(build_html(date, day, macros), encoding="utf-8")
    return OUT_PATH
