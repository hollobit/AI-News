# 뉴스 분석 프로젝트 작업자 안내

갱신일: 2026-09-17. 대상 경로는 `/Users/user/git/news`이다. 사용자 요청 파일명인 `Agents.md`로 작성했다. 대소문자를 구분하는 도구에서 `AGENTS.md`로 자동 인식된다고 가정하지 말고 이 파일을 직접 읽는다.

## 먼저 읽을 문서

- [Plans.md](Plans.md): 구현 상태, 남은 작업, 완료 기준.
- [HISTORY.md](HISTORY.md): 변경 이력과 검증 근거.
- [README.md](README.md): 실행 및 기능 설명.
- [.runtime/verification/ACTIVE_WORK.md](.runtime/verification/ACTIVE_WORK.md): 세션별 상세 기록. 오래된 PID·건수·상태는 현재 사실로 사용하지 않는다.

상위 디렉터리 문서에는 다른 프로젝트(RTC2025) 구조가 포함되어 있다. 이 뉴스 프로젝트의 실제 파일·DB·실행 명령을 확인하고 다른 프로젝트의 경로와 수치를 혼용하지 않는다.

## 구조와 실행

Python 서버 `app.py`, SQLite `data/news.sqlite3`, 정적 프런트엔드 `static/`, Python 가상환경 `.venv/`를 사용한다. 기본 사이트는 `http://127.0.0.1:8001`이다.

```bash
.venv/bin/python app.py serve --port 8001
.venv/bin/python -m pytest tests -q
```

서버 실행 명령은 현재 서버가 없는지 확인한 뒤 사용한다. 루트 전체를 대상으로 pytest를 실행하면 별도 환경을 사용하는 vendored 프로젝트 테스트까지 수집하므로 프로젝트 테스트 범위인 `tests`를 지정한다.

Playwright 브라우저 경로는 `.runtime/browsers`이다. 관련 검증 스크립트는 `tests/ui_paper_context.py`, `.runtime/verification/ui_period_dynamic.py`, `.runtime/verification/ui_observatory_extended.py` 등을 참고한다.

## 기능별 주요 파일

| 영역 | 주요 파일 |
|---|---|
| 서버·뉴스 수집 | `app.py` 및 collector 관련 모듈 |
| 전략 주제 | `strategy.py`, `strategy_views.py`, `strategy_trends.py` |
| 자동 발견·관리·통합 | `dynamic_topics.py`, `dynamic_strategy.py`, `dynamic_registry.py`, `monitoring_groups.py` |
| 관측 지도 | `observatory.py`, `observatory_runtime.py`, `static/observatory.js`, `static/observatory-search.js` |
| GraphRAG | `graph_rag.py`, `graph_questions.py`, `graph_snapshot.py`, `graph_quick_sources.py` |
| 지식 위키 | `knowledge_wiki.py`, `wiki_sources.py`, `WIKI_SCHEMA.md`, `static/wiki.*` |
| 원문 및 Agent-Reach | `agent_reach_runtime.py`, `source_store.py`, `reach_pipeline.py` |
| 기사 해설 | `article_explanations.py`, `static/article.js` |
| 논문 확보 | `arxiv_papers.py`, `paper_metadata_sources.py`, `import_arxiv_snapshot.py` |
| 논문 분석·검토·연결 | `paper_pipeline.py`, `paper_analysis.py`, `paper_graph.py`, `paper_context.py`, `static/paper-context.js` |
| 전수 처리 | `corpus_completion.py`, 기본 분석 원장·관련 실행 스크립트 |

## 유지해야 할 동작

1. Telegram 신규 메시지 확인 → 기사 추출 → 정규 URL·내용 중복 제거 → 고유 뉴스 집계 순서를 지킨다. 마지막 읽기 시각, 추출 시각, 고정 분석 대상 수를 구분한다.
2. 현재 관측 근거가 없는 주제는 표시하지 않는다. 이전 기간만 0건이면 현재 근거 있는 주제를 숨기지 않고 신규 관측·비교 한계를 표시한다. 이전 0건의 증감률은 만들지 않는다.
3. 의료·공공 AX의 독립 주제를 유지하고, 동일 별칭 자동 주제의 중복을 방지한다. 고정·자동 신호 통합 시 고유 문서의 합집합으로 집계한다.
4. 관측 지도 선택 기간 14·30·90일이 후보 순위, 키워드, 관계와 비교 구간에 실제로 반영돼야 한다. 최근 14일 선정·7일 비교로 고정하는 동작을 되살리지 않는다. 다른 화면의 기본 7일 추이까지 자동 변경된 것으로 가정하지 않는다.
5. 관계선은 같은 문서·같은 날짜의 공동 관측이다. 인과관계·실제 도입·과거에 생성된 지도 이력을 뜻하지 않는다. 일별 합계와 기간 고유 문서 수는 다를 수 있다.
6. 기사 해설은 원문 사실·작동 방식·독자의 효용을 중심으로 작성한다. 요약·사실·의미 외에는 근거와 설명 가치가 있을 때만 추가한다. 일반적인 한계 목록이나 무의미한 후속 과제로 빈 절을 채우지 않는다.
7. 뉴스·외부 원문·공식 논문 초록·외부 학술 색인·검토된 해석의 출처와 검증 수준을 구분한다. 논문 수를 Telegram 고유 뉴스 수에 더하지 않는다.
8. 현재 입력 해시·인용·독립 검토를 만족하는 분석만 공개·재사용한다. 완료 상태의 행이 있어도 입력이 바뀌면 유효한 결과가 아니다. 근거가 부족한 결과를 통과시키려고 검토 기준을 낮추지 않는다.
9. 논문 공식 API 3회 연속 실패는 해당 API만 대기시킨다. 사용자 전체 일시중지와 구분하고, 대체 공급자·확보 자료 분석은 계속할 수 있게 한다. Retry-After·제공자별 대기·일일 한도를 보존한다.
10. API 키는 환경 설정으로 관리한다. 로그·문서·검증 산출물에 토큰이나 개인 인증 정보를 기록하지 않는다.

## 장기 작업 인수인계

- 원장의 `running`만으로 실제 실행을 판단하지 않는다. `owner_pid`, 프로세스 생존, 갱신 시각, 오류 및 체크포인트를 확인한다.
- 살아 있는 기존 서버·collector·기본/심층 분석 driver를 중복 실행하지 않는다. 정상 종료 후에도 기존 분석 worker가 마무리 중일 수 있다.
- 기존 run/cycle과 검토 이력을 보존한다. 실패 횟수나 검토 거절을 무조건 초기화하지 않는다.
- 최신 기본 run과 심층 cycle 식별자는 [Plans.md](Plans.md)에 있다. 재개 시 DB에서 다시 확인한다.
- 관측 저장 뷰는 `data/news.sqlite3.observatory/*-v2.json`이다. 집계 날짜·비교 구간·오류를 확인하고, 오래된 스키마의 캐시를 새 의미로 표시하지 않는다.
- 공식 논문 배포본 `.runtime/papers/arxiv-metadata.snapshot`은 약 5.53GB JSONL이다. 기존 파일과 import 결과를 먼저 확인하고 불필요하게 재다운로드하지 않는다. 제목·초록 배포본이며 논문 PDF 모음이 아니다.
- 마지막 서버 세션 기록은 55517, 포트 8001이었다. 이 식별자는 과거 기록이며 다음 작업에서 생존 여부를 확인한다.

## 검증 및 보고

변경에 맞는 Python 테스트, JavaScript 구문 검사, 필요한 실제 UI 검증을 수행한다. 완료·실패·검토 대기·stale을 분리해 보고하고, 단위 테스트와 실데이터 검증을 구분한다. GraphRAG 첫 근거 응답, LLM 최종 답변, 캐시 응답의 시간을 서로 바꾸어 보고하지 않는다.

최근 전체 검사 결과는 587개 테스트와 56개 subtest 통과다(2026-09-20). 기능·운영 상태가 바뀌면 Plans.md의 상태와 HISTORY.md의 이력을 함께 갱신한다.
