"""오늘 메뉴 + 영양정보로 간단한 index.html 생성."""
from pathlib import Path

OUT_PATH = Path(__file__).parent / "docs" / "index.html"

MEAL_LABEL = {"lunch": "중식", "dinner": "석식"}


def _meal_totals(dishes: list[str], macros: dict[str, dict]) -> dict:
    total = {"carb_g": 0, "protein_g": 0, "fat_g": 0, "kcal": 0}
    for d in dishes:
        m = macros.get(d, {})
        for k in total:
            total[k] += m.get(k, 0)
    return total


def _meal_rows(dishes: list[str], macros: dict[str, dict]) -> str:
    rows = []
    for d in dishes:
        m = macros.get(d, {})
        rows.append(
            f"<tr><td>{d}</td><td>{m.get('carb_g', 0)}g</td>"
            f"<td>{m.get('protein_g', 0)}g</td><td>{m.get('fat_g', 0)}g</td>"
            f"<td>{m.get('kcal', 0)}kcal</td></tr>"
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
            <thead><tr><th>메뉴</th><th>탄수화물</th><th>단백질</th><th>지방</th><th>칼로리</th></tr></thead>
            <tbody>
              {_meal_rows(dishes, macros)}
              <tr class="total"><td>합계</td><td>{totals['carb_g']}g</td>
                <td>{totals['protein_g']}g</td><td>{totals['fat_g']}g</td>
                <td>{totals['kcal']}kcal</td></tr>
            </tbody>
          </table>
        </section>""")

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>SKALA 오늘의 영양정보</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 640px; margin: 40px auto; padding: 0 16px; color: #222; }}
  h1 {{ font-size: 1.4rem; }}
  h2 {{ font-size: 1.1rem; margin-top: 2rem; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
  th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid #eee; }}
  tr.total {{ font-weight: bold; border-top: 2px solid #333; }}
  .note {{ color: #888; font-size: 0.8rem; margin-top: 2rem; }}
</style>
</head>
<body>
  <h1>SKALA 오늘의 영양정보 ({date})</h1>
  {"".join(sections) if sections else "<p>오늘은 메뉴 정보가 없습니다.</p>"}
  <p class="note">영양정보는 로컬 sLLM(Qwen3 8B) 추정치이며 실제와 다를 수 있습니다.</p>
</body>
</html>"""


def generate(date: str, day: dict, macros: dict[str, dict]) -> Path:
    OUT_PATH.parent.mkdir(exist_ok=True)
    OUT_PATH.write_text(build_html(date, day, macros), encoding="utf-8")
    return OUT_PATH
