# SKALA 오늘의 메뉴 & 영양정보

SKALA 구내식당 메뉴 서비스(김현수님 제작, `skala-lunch.ewkimhyunsu11.workers.dev`)의
API에서 오늘의 중식/석식을 가져와 요리별 탄/단/지/칼로리를 계산하고,
웹페이지(`docs/index.html`, GitHub Pages로 배포)와 Slack 채널 알림으로 보여주는 프로젝트.

## 영양성분 계산 방식

**"GPT가 숫자를 지어내는 것"이 아니라 "실제 정부 DB를 최우선으로 쓰고, GPT는 검색을 돕는 역할"**로 설계했습니다.

```
요리명
  │
  ▼
1) 식품의약품안전처 식품영양성분DB API(apis.data.go.kr)에서 원본 메뉴명 그대로 검색
   → 검색 후보 여러 개 중 이름이 가장 비슷한 걸 채택 (문자열 유사도 기반)
   │ 못 찾으면
   ▼
2) GPT가 유사 검색어(표기 변형/동의어) 후보를 만들고, 그걸로 DB 재검색
   복합 메뉴(예: "고추참치덮밥")면 GPT가 구성요소로 분해해서 요소별로 DB 검색 후 합산
   │ 그래도 못 찾으면 (아주 드묾)
   ▼
3) GPT가 직접 추정 (페이지에 "GPT추정"이라고 출처를 명확히 표시)
   이 경우도 kcal은 GPT가 부른 값을 그대로 안 믿고, 코드가 탄×4+단×4+지×9로 재계산
```

추가로:
- **1회 제공량 환산**: DB는 100g당 값만 주기 때문에, 음식 종류(국밥/찌개/반찬 등)별로
  실제 제공량을 추정해서 환산합니다 (`food_heuristics.py`).
- **끼니 단위 이중계산 방지**: "장터국밥"처럼 이름에 이미 밥이 포함된 메뉴와 별도
  "쌀밥"이 같이 나오면, 총합 계산에서 쌀밥을 자동으로 제외합니다. 단 "국밥"/"비빔밥"으로
  끝나는 메뉴만 인정하도록 화이트리스트를 걸어서, GPT가 "쌀국수" 같은 걸 잘못 판단하지
  않게 막아뒀습니다 (`test_duplicate_detection.py`로 검증).
- **로컬 sLLM(Ollama)은 현재 미사용**입니다. 처음엔 Ollama로 돌렸었는데 칼로리를 실제보다
  높게 잡는 경향이 있어서(FatSecret 등 실측치와 비교해서 확인) 뺐고, 지금은 DB + GPT
  조합만 씁니다. 코드는 `estimate_macros.py`에 주석 처리된 채로 남아있어 재활성화 가능.

## 파일 구성
- `fetch_menu.py` — 메뉴 API 호출, 오늘 메뉴 추출
- `nutrition_db.py` — 식품의약품안전처 식품영양성분DB API 조회 (유사도 기반 매칭)
- `estimate_gpt.py` — GPT 호출 3종: 검색어/구성요소 분석, 최후수단 직접추정, 끼니 중복검사
- `food_heuristics.py` — 카테고리별 예상 제공량 추정 + 이상치 탐지 (GPT 호출 없이 코드로)
- `estimate_macros.py` — 위 모듈들을 엮어서 요리별 최종 영양정보를 만드는 오케스트레이션
- `generate_page.py` — `docs/index.html` 생성
- `notify_slack.py` — Slack Incoming Webhook으로 메시지 전송
- `run_daily.py` — 전체 파이프라인 실행 + GitHub Pages 자동 push
- `test_duplicate_detection.py` — 중복계산 판단 로직 회귀 테스트
- `CHANGELOG.md` — 만들면서 겪은 문제/해결 과정 기록

## 준비
```bash
pip install -r requirements.txt
```

`.env` 파일을 만들고 아래 키들을 채워주세요 (`.gitignore`에 포함되어 있어 저장소엔 안 올라감):
```bash
SLACK_WEBHOOK_URL=          # Slack Incoming Webhook URL
FOOD_SAFETY_API_KEY=        # data.go.kr에서 "식품의약품안전처_식품영양성분DB정보" 활용신청 후 발급받은 키
OPENAI_API_KEY=             # GPT API 키 (유료, gpt-4o-mini만 써서 비용은 적음)
```
어떤 키가 없어도 파이프라인 자체는 안 죽습니다 — DB 키가 없으면 GPT 단독으로,
Slack 키가 없으면 콘솔 출력으로 폴백합니다.

## 실행
```bash
python3 run_daily.py
```
`docs/index.html`이 생성되고, GitHub Pages에 자동 push되고, Slack으로 전송됩니다.

## 매일 자동 실행 (로컬 cron + 자동 기상)
```bash
crontab -e
# 평일 오전 9시 실행 예시 (경로/파이썬 실제 경로에 맞게 수정)
0 9 * * 1-5 cd /path/to/skala-meal-macros && /usr/bin/python3 run_daily.py >> cron.log 2>&1
```
Mac이 잠자기 상태면 cron이 안 돌 수 있어서, 실행 직전에 자동으로 깨어나도록 걸어둠:
```bash
sudo pmset repeat wakeorpoweron MTWRF 08:55:00
```

## 캐시
같은 요리명은 `data/macro_cache.json`에 저장해서 재사용합니다(DB/GPT를 매번 다시
부르지 않음). 계산 로직을 바꾸면 캐시 스키마가 안 맞을 수 있으니
`rm data/macro_cache.json` 후 재실행하는 게 안전합니다.
