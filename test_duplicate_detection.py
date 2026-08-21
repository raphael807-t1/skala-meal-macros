"""check_meal_duplicates()가 실제 과거 메뉴 조합에서 잘 판단하는지 확인하는 테스트.

정답(expected)은 내가 사람이 보고 직접 판단한 것 -> GPT 판단과 비교해서 평가.
케이스를 일부러 까다롭게 골랐다:
  - 진짜 중복(true positive)이어야 하는 경우
  - 이름에 "쌀"이 들어가지만 실제로는 밥이 아닌 함정(소고기쌀국수)
  - 밥이 포함된 메뉴인데 애초에 별도 쌀밥이 없어서 제외할 게 없는 경우
  - 애매해서 보수적으로(제외 안 함) 처리해야 맞는 경우
"""
import estimate_gpt
from run_daily import _load_dotenv

_load_dotenv()

CASES = [
    {
        "name": "08/14 석식 - 진짜 국밥+밥 중복 (True Positive여야 함)",
        "dishes": ["맑은시래기양지국밥", "비엔나야채볶음", "쌀밥", "명엽채볶음", "오이생채", "포기김치"],
        "expect_excluded": {"쌀밥"},
    },
    {
        "name": "08/18 석식 - '쌀국수' 함정 (쌀이 들어가지만 밥 아님, 제외되면 안 됨)",
        "dishes": ["소고기쌀국수", "점보춘권&칠리소스", "쌀밥", "단호박샐러드", "짜사이무침", "포기김치"],
        "expect_excluded": set(),
    },
    {
        "name": "08/13 중식 - 비빔밥 자체엔 밥 포함, 근데 별도 쌀밥이 아예 없음 (제외할 대상 없음)",
        "dishes": ["열무비빔밥&후라이", "간장두부조림", "어묵국", "김말이강정", "미나리무생채", "열무김치"],
        "expect_excluded": set(),
    },
    {
        "name": "08/12 석식 - 김칫국(밥 없는 국)+쌀밥, 중복 아님",
        "dishes": ["유부김칫국", "쌀밥", "떡갈비아몬드강정", "스크램블에그", "오이지무침", "열무김치"],
        "expect_excluded": set(),
    },
    {
        "name": "08/19 석식 - 닭곰탕(애매함, 곰탕은 보통 밥 별도)+쌀밥, 보수적으로 제외 안 하는 게 맞음",
        "dishes": ["닭곰탕&당면", "메밀전병", "쌀밥", "두부쑥갓무침", "양념깻잎지", "깍두기"],
        "expect_excluded": set(),
    },
]


def main():
    correct = 0
    for case in CASES:
        result = estimate_gpt.check_meal_duplicates(case["dishes"])
        actual_excluded = {item["dish"] for item in result["exclude"]}
        is_correct = actual_excluded == case["expect_excluded"]
        correct += is_correct
        print(f"\n[{'OK' if is_correct else 'FAIL'}] {case['name']}")
        print(f"  메뉴: {case['dishes']}")
        print(f"  예상 제외: {case['expect_excluded'] or '(없음)'}")
        print(f"  실제 제외: {actual_excluded or '(없음)'}")
        if result["exclude"]:
            for item in result["exclude"]:
                print(f"    - {item['dish']} <- {item['contained_in']}: {item.get('reason', '')}")

    print(f"\n=== {correct}/{len(CASES)} 정답 ===")


if __name__ == "__main__":
    main()
