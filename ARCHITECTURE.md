# 실행 경계와 자료 계약

## 실행 경계

`app.py`는 CLI·Telegram 입력을 담당한다. `database.prepare_database()`는 startup/CLI의 스키마·backfill·색인 준비이고, `open_db()`는 평범한 연결이다. `news_repository.py`는 기존 메시지/기사 중복 판정과 뉴스 조회를 제공한다. `server_bootstrap.py`가 서비스를 조립·복구·종료하며 `ServerContext`가 의존 타입을 명시하며 `server_http.py`는 공통 HTTP 계약, `server_routes/`는 기능별 조회/명령을 처리한다.

HTTP GET은 `read_projections()` 안에서 수행한다. 준비된 revision만 재사용하고 새 schema는 캐시를 우회한다. 키워드 색인이 현재 입력과 다르면 503을 반환해 백그라운드 준비를 기다린다. 형태소의 누락된 계산은 메모리에서 처리하며 GET에서 테이블 생성·저장·commit을 하지 않는다. 백그라운드 준비와 수집은 기존 색인 갱신을 수행한다. 모든 GET 서비스가 읽기 전용이라는 의미는 아니다. 작업 상태의 stale 판정·복구 등 도메인별 원장 동작은 기존 계약을 유지한다.

기본 분석 facade는 원장을 조회하고 별도 worker를 시작한다. worker의 DB별 파일 lease와 원장의 실제 owner 확인을 함께 사용한다. HTTP 종료는 worker 종료 신호가 아니다. pause 이벤트는 원장에 기록하고 worker가 현재 문서를 마무리하면서 적용한다. 준비 요청의 timeout은 살아 있는 worker를 죽이거나 새 worker를 자동 생성하지 않는다. 논문 확보/분석/pipeline·위키·기사 해설은 `background_jobs.py` facade와 `background_worker.py`의 DB별 lease로 별도 실행한다. HTTP 재시작은 worker를 멈추지 않는다. 원장/실제 owner·체크포인트·provider 정책을 보존한다. `/api/runtime/workers`는 로컬 worker 생존 상태다. 심층·원문·질문·실험 서비스에는 각각 기존 실행 정책이 있다.

## 문서 식별자

| 식별자 | 의미와 보존 규칙 |
|---|---|
| `(chat_id, message_id)` | Telegram 메시지. 수정된 메시지는 같은 키의 새 내용이다. |
| 위 키 + `item_index` | 메시지에서 추출한 개별 기사. 한 메시지의 여러 기사를 합치지 않는다. |
| 정규 URL / URL 없는 내용 identity | 분석 원장의 고유 문서. URL 정규화와 기존 `news_identity` 규칙을 유지한다. |
| 입력 해시 | 특정 원문·제목·내용·분석 규칙의 판. 같은 문서 ID도 입력이 바뀌면 재검토한다. |
| 공개 뉴스/논문/그래프 ID | 검토된 공개 스냅샷의 탐색 키. 기존 ID 생성과 deep link를 유지한다. |
| `public-data-<SHA256>.json` | 공개 파일 내용의 무결성·버전 식별자. 문서 ID와 다르다. |
| FNV-1a bucket | 지도 파일을 찾는 256개 partition 중 하나. 무결성·검토·문서 identity가 아니다. |

export는 공개 뉴스/노드/관계 ID의 중복과 없는 관계 끝점을 거부한다. news ID·원출처 URL→그래프 노드의 lookup을 보존한다. 동일 제목·별칭을 동일 문서로 합치지 않는다. 논문 수, Telegram 고유 뉴스 수, 위키 페이지 수, 그래프 노드 수는 별도다.

## 근거와 검토

저장 검토의 공통 무결성 계약은 `evidence_contracts.reviewed_payload()`다. `accepted is True`, issues 정책, 정확한 report/evidence hash만 확인한다. 이 helper 하나로 공개를 승인하지 않는다. 기본 뉴스·논문·심층·위키의 현재 입력 해시, 실제 인용 대조, 독립 검토와 종류별 공개 기준을 각각 유지한다. 내용 검토 실패·stale·원문 부족은 캐시 적중이나 완료 행 존재로 우회하지 않는다.

근거 표현은 원출처 URL·origin/evidence_scope·입력 판·인용 위치·현재 검토 상태를 함께 유지한다. Telegram 메시지, 확보한 외부 원문 일부, 공식 논문 초록, 외부 학술 색인, 검토된 해석을 구분한다. `historical_unchecked`는 현재 검토 자료가 아니다. 관측 지도 관계는 같은 문서/날짜의 공동 관측이며 인과관계가 아니다.

## 계산 캐시와 공개본

`document_features.py`는 정확한 title/text/excerpt/abstract와 source status/title/text 및 규칙 파일 hash로 분류·전략 점수만 저장한다. 별도 `.features.sqlite3`은 폐기·재생성할 수 있는 계산 캐시다. 모델 분석과 승인 상태를 저장하지 않으며 원장의 검토를 대체하지 않는다. source 입력 변경·규칙 변경·캐시 손상 시 기존 계산 경로를 사용한다. 기존 형태소 내용 캐시와 process-local single-flight/메모리 예산도 보존한다.

공개 export는 먼저 기존 allowlist로 자료를 정제하고 그 결과를 분할한다. 한 탭은 하나의 manifest를 사용하며 검색 시에는 완전한 검색 색인을 읽는다. 첫 지도는 bootstrap, 선택 지도는 인접 관계만 읽는다. 현재·직전 세대와 최근 24시간 세대 파일을 보존한다. 보존 원장은 출력 디렉터리 옆 로컬 sidecar로 관리하며 공개 manifest를 누적시키지 않는다. 인접 노드 최소 메타데이터로 필터/한도를 적용한 뒤 조각을 읽으며 클라이언트 완료 캐시는 48개/24MiB다. 만료된 조각은 새 manifest로 요청 전체를 한 번 재시도하고 URL을 보존해 화면을 갱신한다. Git tree/commit/ref를 순서대로 게시하므로 manifest와 자산이 한 판으로 배포된다. 원문 발췌·owner PID·운영 로그·인증은 공개 파일에 추가하지 않는다.

`projection_store.py`는 같은 폐기 가능 cache DB에 현재 입력/규칙/registry 해시가 있는 문서별 전략 투영·관측 주제 소속을 저장한다. 변경/삭제 원장은 최근 10,000개로 제한한다. 저장은 background/CLI만 수행한다. 전체 뉴스의 정규 중복 판정·목록 조립과 최초 그래프 준비는 계속 필요하며 모델 검토 유효성을 이 캐시로 대체하지 않는다. 전략 패널은 `strategy-*.js`의 명시적 factory/context로 각자의 상태와 이벤트를 소유한다.

대용량 호환 스냅샷이 포함되면 게시 파일/해시/의존성 검증 후 Git transport를 사용한다. 대상 origin이 지정 프로젝트와 일치해야 하며 source checkout/index를 변경하지 않고 gh-pages만 non-force 갱신한다. 다른 writer가 부모 ref를 갱신하면 거절하고 재검증을 기다린다. 작은 스냅샷의 기존 API 게시 경로는 유지한다.
