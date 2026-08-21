# SKALA 오늘의 메뉴 & 영양정보

SKALA 구내식당 메뉴 API(`skala-lunch.ewkimhyunsu11.workers.dev`)에서 오늘의 중식/석식을 가져와
로컬 sLLM(Ollama, qwen3:8b)으로 요리별 탄/단/지/칼로리를 추정하고,
간단한 웹페이지(`docs/index.html`)와 Slack 채널 알림으로 보여주는 개인 프로젝트.

## 구성
- `fetch_menu.py` — 메뉴 API 호출, 오늘 메뉴 추출
- `estimate_macros.py` — Ollama로 요리별 영양정보 추정 (`data/macro_cache.json`에 캐시)
- `generate_page.py` — `docs/index.html` 생성
- `notify_slack.py` — Slack Incoming Webhook으로 메시지 전송
- `run_daily.py` — 위 과정을 순서대로 실행하는 진입점

## 준비
```bash
pip install -r requirements.txt
ollama pull qwen3:8b   # 이미 있으면 생략
```

### Slack 채널 알림 받으려면
1. Slack에서 알림용 채널 생성 (예: `#오늘의-식단`)
2. 해당 워크스페이스에서 Incoming Webhook 앱 추가 → 채널 선택 → Webhook URL 발급
   (워크스페이스 설정에 따라 관리자 승인이 필요할 수 있음)
3. 아래처럼 환경변수로 등록
   ```bash
   export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/XXX/YYY/ZZZ"
   ```
   Webhook URL 없이 실행하면 콘솔에만 메시지가 출력됨(전송 안 됨).

## 실행
```bash
python3 run_daily.py
```
`docs/index.html`이 생성되고, `SLACK_WEBHOOK_URL`이 설정돼 있으면 Slack으로도 전송됨.

## 매일 자동 실행 (로컬 cron)
평일 오전 8시에 실행하는 예시:
```bash
crontab -e
# 아래 줄 추가 (경로/파이썬 실제 경로에 맞게 수정)
0 8 * * 1-5 cd /Users/raphael807/Downloads/skala-meal-macros && SLACK_WEBHOOK_URL="https://hooks.slack.com/services/XXX/YYY/ZZZ" /usr/bin/python3 run_daily.py >> cron.log 2>&1
```
Mac이 잠자기 상태면 cron이 안 돌 수 있으니, 항상 켜두거나 `pmset` 웨이크 스케줄을 같이 걸어두면 좋음.

## 참고
- 영양정보는 로컬 sLLM 추정치이며 실제 영양성분표와 다를 수 있음.
- 동일한 요리명은 캐시(`data/macro_cache.json`)를 재사용하므로 매번 다시 추정하지 않음.
