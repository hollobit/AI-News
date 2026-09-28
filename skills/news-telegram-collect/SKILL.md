---
name: news-telegram-collect
description: 뉴스 프로젝트의 Telegram 신규 메시지 정기 수집과 신규·변경 뉴스 기본 분석을 실행하거나 점검한다. /Users/user/git/news 전용이며 심층 분석이나 외부 원문 전수 수집 요청에는 사용하지 않는다.
---

# 뉴스 정기 수집·기본 분석

프로젝트는 `/Users/user/git/news`, DB는 `data/news.sqlite3`, Python은 `.venv/bin/python`, 서버는 `http://127.0.0.1:8001`이다. 먼저 프로젝트 `Agents.md`와 `Plans.md` 최신 기록을 읽는다. 기록된 PID·건수는 현재 상태가 아니다.

## 실행

1. `ps -axo pid,ppid,etime,command`로 기존 `app.py collect`, `app.py serve`, 감독기와 기본 분석 owner를 확인한다. `scheduled_collection.py --check`로 실제 DB의 최신 기본 run과 수집 상태를 조회한다.
2. 기존 수집기·서버가 정상 실행 중이면 유지한다. 없을 때는 설치된 프로젝트 LaunchAgent `com.hollobit.ai-news.collector` / `.server`를 시작한다. 감독기가 설치되지 않았으면 `.venv/bin/python service_supervisor.py collector` / `server`를 사용한다. 포트에 바인딩됐으나 API가 응답하지 않는 서버는 owner와 작업을 확인한 후 정상 종료하고 감독기의 재시작을 확인한다.
3. `collector_last_success`가 실제로 갱신되고 `collector_corpus_snapshot.status=complete`인지 확인한다. 성공 확인은 보통 약 30초 간격이다. 신규 업데이트 확인 → 메시지·기사 저장 → URL·본문 중복 제거 → 고유 뉴스 추출 완료 순서를 지킨다. 토큰은 기존 `.env` 설정으로만 읽고 출력하지 않는다.
4. 프로젝트 루트에서 `.venv/bin/python scheduled_collection.py`를 실행한다. 최근 120초 안의 수집 확인 및 추출 완료 후 현재 입력 해시·독립 검토를 대조한다. 미분석/변경 입력이 있으면 기본 분석 API를 시작하고 유효 결과는 재사용한다. 2 workers·batch 1과 공유 LLM 예산을 따른다. 살아 있는 owner가 있으면 그 run을 관찰한다. 완료 후 다음 주기에는 새 입력을 확인한다.
5. 지속 정기 실행은 `ops/com.hollobit.ai-news.baseline.plist`의 5분 간격 LaunchAgent를 사용한다. 현재 등록 여부를 확인해 중복 설치하지 않는다. 활성화가 요청됐으면 사용자 LaunchAgents에 설치하고 bootstrap한다. 로그인 세션에서 동작하며 컴퓨터 잠자기 중에는 실행되지 않는다. 스킬 자체가 시간을 예약하는 것은 아니다.

[scripts/check_collection.py](scripts/check_collection.py)는 프로젝트 헬퍼 진입점이다. `--status`는 마지막 스케줄러 결과 조회, `--check`는 실제 DB 조회만 수행한다.

## 중단과 검증

- 자동 스케줄러는 paused run의 최신 엔진 이벤트와 종료 시각(최대 300초의 worker 마무리 간격), owner 부재, 이후 사용자 중지 이벤트 부재를 확인한다. timeout·queue_timeout·database_locked·network·capacity만 소규모 실제 엔진 확인 후 동일 run으로 재개한다. 사용량 제한·인증·권한·circuit_open 및 내용 검토 실패는 자동 해제하지 않는다.
- `.runtime/scheduled-collection-recovery.json`에 복구 예산을 보존한다. 5분부터 지수 대기하며 진척 없는 시도 3회·run당 총 20회가 상한이다. 시도는 호출 전에 예약하고 실제 검토 완료 증가가 있어야 무진척 횟수를 초기화한다. 사용자 중지 중복 확인은 엔진 probe 전후 모두 수행한다. 문서의 attempts나 검토 기준을 초기화하지 않는다.
- 실제 API `/api/baseline/<id>?view=status`와 DB 원장에서 verified/pending/retry/needs_review/failed 및 owner 생존을 확인한다. completed 행도 현재 해시·인용·저장 독립 검토가 맞아야 재사용한다.
- 심층 cycle, 논문 분석, MiroFish, 공개 사이트 게시를 이 스킬의 실행에 추가하지 않는다. 뉴스·논문 건수도 합치지 않는다.
- 마지막 Telegram 확인과 실제 추출 시각, 신규 고유 수와 누적 수, 기본 run 고정 대상과 완료/대기/실패를 구분해 보고한다. 기본 run 완료는 이후 새 입력의 완료를 뜻하지 않는다.
- 운영 상태가 바뀌면 프로젝트 `Plans.md`와 `HISTORY.md`를 함께 갱신한다.
