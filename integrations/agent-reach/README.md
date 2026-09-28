# Agent-Reach 연결

원본: https://github.com/Panniantong/Agent-Reach
고정 소스: `a19a171fa980a0785849596492e0af4db800c82f` (1.5.0, MIT). 원본 LICENSE는 upstream/LICENSE에 있다.

프로젝트 전용 `.runtime/agent-reach/venv`에서 실행한다. 앱의 기본 Python 환경과 전역 도구·브라우저 로그인 설정은 변경하지 않는다.

```bash
uv venv .runtime/agent-reach/venv --python .venv/bin/python --cache-dir .runtime/agent-reach/cache
uv pip install --python .runtime/agent-reach/venv/bin/python --cache-dir .runtime/agent-reach/cache ./integrations/agent-reach/upstream
.venv/bin/python agent_reach_runtime.py status
.venv/bin/python agent_reach_runtime.py read https://github.com/Panniantong/Agent-Reach
```

`/sources`에서 URL을 입력하면 읽기 작업을 제출하고 완료 결과를 확인할 수 있다. 기존 뉴스 원문 보강도 같은 reader를 사용한다. 작업은 2개씩 실행하고 대기열은 최대 8개, 결과 이력은 100개다. 원문 캐시는 기존 7일 정책을 사용하며, 3,500자 호환 발췌와 최대 250,000자 본문 버전을 함께 저장한다. 새 URL 읽기는 명시적 사용자 동작이고 Q&A마다 외부 네트워크를 호출하지 않는다.

- 웹: 기존 DNS 검증·IP 고정 수집 후 공개 Jina Reader 보조 경로.
- X: 공개 단일 게시물 본문(FxTwitter 공개 API 보조 reader). 타임라인·검색·영상 전사는 별도 기능이다.
- GitHub: 공개 저장소 README, 그 외 공개 페이지는 웹 reader.
- YouTube: yt-dlp로 설명과 제공되는 한국어/영어 자막. 자막이 없으면 설명만 확보했다고 표시한다. 영상 프레임의 의미 분석은 하지 않는다.
- RSS/Atom: feedparser, 최대20개 피드 항목 요약. 피드 요약 자체는 기사 전문이 아니다. 정기 구독 파이프라인은 각 기사 URL을 별도로 읽는다.
- 그 외 플랫폼: Agent-Reach 레지스트리에 표시하되 추가 도구·로그인이 필요한 상태. 설치 목록을 실제 읽기 성공으로 표시하지 않는다.

공개 읽기 결과는 reader/platform/evidence_scope/fetched_at/content_hash와 함께 기존 source_excerpts에 저장된다. GraphRAG에 원문 노드와 미검토 근거로 연결한다. 원문의 사실성 검토를 통과한 주장으로 만들지 않는다. 게시 날짜를 확인하지 못한 자료는 날짜를 지어내지 않으며 날짜 제한 검색에서는 제외될 수 있다.

`GET /api/agent-reach`, `POST /api/agent-reach/read`, `GET /api/agent-reach/jobs/{id}`. 인증이 필요한 사이트, 내부 IP, URL 자격증명은 자동 수집하지 않는다. 로그인 환경을 자동 탐색하거나 브라우저 쿠키를 가져오지 않는다. Agent-Reach의 전체 doctor 대신 웹앱에서는 구현한 읽기 경로의 설치 상태를 보여 준다.

실제 공개 GitHub/X/RSS/웹 읽기 검증: `.runtime/verification/agent-reach-live.json`. 시각화 확장 제안: `../../VISUALIZATION_PROPOSAL.md`.

## 2026-09-17 원문 복구·정기 관측·추가 근거 보강

`/sources`에서 실패 또는 잘린 기존 원문을 최대 50건씩 복구하고, RSS/단일 자료 관측을 등록·중지·재개하며, 출처별 확인/성공 시각·오류·재시도 시각과 본문 버전을 확인한다. 복구 작업은 최대 3회 시도하며 429·접근 안내·일시 장애의 대기 시간을 구분한다. 404·내부 주소 차단·미지원 경로는 반복 복구 대상에서 제외한다.

수집 본문은 최대 250,000자를 별도 저장하고 URL별 최근 3개 버전을 보존한다. 기존 3,500자 발췌는 호환 목적으로 유지한다. 실패한 새 읽기는 현재 근거로 사용하지 않으며, 과거 성공 본문은 버전 이력에 보존한다. 문단 검색 결과는 본문 hash와 문자 시작/끝 위치를 포함한다. 수집 한도로 잘렸는지도 별도 표시한다. 이전에 보관하지 않은 긴 본문은 재수집에 성공해야 확보된다.

외부 검색은 Agent-Reach가 안내하는 Exa MCP 백엔드를 프로젝트의 고정 HTTP 어댑터로 호출한다. 전역 CLI/계정 설정은 바꾸지 않는다. 검색 결과는 후보 URL로만 사용하고 각 원문을 다시 읽는다. 부족한 근거가 감지된 범위 제한 없는 GraphRAG 질문은 첫 응답 후 한 번의 추가 검색(최대5후보/4원문)과 독립 검토를 예약한다. 검토된 추가 답변은 질문 작업 조회에 반영한다. 노드/날짜/주제 범위를 지정한 질문을 임의로 광역 외부 검색하지 않는다. 외부 제공자 오류 시 재시도 상태를 표시한다.

기본 공식 구독: NVIDIA 공식 블로그와 Google AI 공식 블로그, 6시간 주기. RSS 요약을 기사 전문으로 취급하지 않고 항목별 URL을 읽는다. 외부 관측은 별도 테이블/출처로 저장하고 전략 목록·키워드·관측 지도에 연결한다. Telegram 메시지 수나 기존 Telegram 전수 분석 대상을 가짜 메시지로 늘리지 않는다. 정규 URL 중복 제거는 공유한다. 발행일이 없으면 수집 관측일로 구분한다. 단일 URL 구독은 동일 URL의 본문 변경을 확인한다.

본문 hash가 바뀐 URL만 source_reanalysis 대기열로 보내 별도 전략·위험 검토를 수행한다. 검토 완료·미통과·실패는 구분한다. 외부 source origin은 Telegram으로 위장하지 않으며 위험 검토와 현재 원문 일치 검증에서도 별도로 인식한다.

추가 API: `GET /api/agent-reach/pipeline`, `GET /api/agent-reach/source?url=…`, `GET /api/agent-reach/passages?url=…&q=…`, `GET /api/agent-reach/tasks/{id}`, `POST /api/agent-reach/repair`, `POST /api/agent-reach/subscriptions`, `POST /api/agent-reach/subscription-toggle`.

검증: tests/test_reach_pipeline.py, tests/test_graph_questions.py, tests/ui_sources_pipeline.py. 실제 결과는 .runtime/verification/reach-upgrade-live.json 및 reach-upgrade-ui.png. 기사별 독자용 상세 해설은 ../../ARTICLE_ANALYSIS_PROPOSAL.md에 별도 제안했다.
