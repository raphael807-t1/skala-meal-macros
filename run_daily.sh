#!/bin/bash
# launchd가 이 스크립트를 호출한다. cd/리다이렉트를 cron의 "cmd1 && cmd2 >> file"
# 형태로 안 쓰고 여기서 직접 처리해서, 셸 파싱 방식에 따른 오류 여지를 없앤다.
cd "$(dirname "$0")" || exit 1
exec /opt/homebrew/opt/python@3.11/bin/python3.11 run_daily.py >> cron.log 2>&1
