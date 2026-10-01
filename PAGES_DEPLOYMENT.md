# GitHub Pages 읽기 전용 위키

## 현재 운영

- MiroFish 분석은12번째메뉴로제공한다. 공개본은뉴스선택/로컬실행화면연결이며,방문자의127.0.0.1:8001을명시클릭으로만연다. 자동로컬요청·실행없음. 실제엔진준비/실행/보고서는로컬에서확인한다.
- 뉴스의지식연결은hash article_id/source_url로선택기사의현재검토분석·기존위키인용·90일관측문맥을연다. 기사별자료만지연로딩하며관측추천은검토의미관계와구분한다. 검색어전달은유지하되선택주변그래프를숨기지않는다.

### 공유 가능한 URL

- 페이지 간 메뉴/지식 연결 이동은 현재 검색어 q를 이어받는다. index의 q는 knowledge의 hash q 및 관측 지도 q로 변환되며, 다른 화면으로 돌아와도 유지된다. 목적 링크가 이미 지정한 검색(예: 특정 출처 URL)은 덮어쓰지 않는다. 검색을 지우면 다음 메뉴 링크에서도 제거된다.
- Evidence Inspector는 선택 주제/키워드의 전체 고유 문서를 표시한다. 관계선은 정확한 문서·날짜 교집합이다. 50개씩 더 보기, 전체 목록 내 검색, 표시 제한과 무관한 전체/검색 결과 JSON 다운로드를 지원한다. `docq`와 `docs`로 문서 검색·표시 수를 복원한다. 원문 발췌 공개 정책은 바꾸지 않는다.

- 대시보드의 `곡선·근거·관계 보기`는 관측 기간·확장 범위·정확한 주제 ID를 전달한다.
- 관측 지도: `observatory.html?window=30&expand=1&id=...&date=2026-09-19&q=AI&filter=rising&log=1`. `id`는 주제/키워드/관계선 ID이며 `date`는 배열 순번이 아닌 날짜다. 필터 값은 all/rising/falling/automatic/control. 대상이 현재 스냅샷에 없으면 명시 안내하며 임의의 다른 대상으로 대체하지 않는다.
- 목록: `index.html?view=news&q=...&day=...&topic=...&reviewed=1&limit=80`. 뉴스/논문/위키/위험 항목의 고유 링크는 `view`와 `id`, 펼친 분석 링크는 `section`을 포함한다.
- 지식 지도: `knowledge.html#id=...&layer=semantic&color=community&limit=200`. 기본 3D 탐험의 카메라 방향·거리·초점은 az/el/dist/tx/ty/tz로 복원한다. `view=2d`는 SVG 보기이며 zoom/x/y를 복원한다. 검색 q, 관계 층, 색상, 표시 수와 기존 source_url/paper_id 링크도 지원한다.
- URL은 현재 공개 스냅샷의 내용을 가리키며 과거 원문/분석판의 영구 보존 URL은 아니다. 날짜/대상 삭제나 표시 범위 변경에 따라 현재 자료 없음 안내가 나올 수 있다. 일시적 재생 타이머와 수동 노드 배치 좌표는 콘텐츠 식별자가 아니다.

- 공개 주소: https://hollobit.github.io/AI-News/
- 저장소: hollobit/AI-News, 게시 브랜치 `gh-pages`, 루트 `/`.
- 기존 `master`는 변경·삭제하지 않는다. 웹 파일은 `gh-pages`만 갱신한다.
- 수동 동기화: `.venv/bin/python sync_wiki_pages.py --output .runtime/public-site`
- 지속 동기화: `.venv/bin/python sync_wiki_pages.py --output .runtime/public-site --watch` (5분 간격, 중복 실행 잠금).
- macOS LaunchAgent `com.hollobit.ai-news.pages`가 로그인 후 자동 시작하고 프로세스 종료 시 재시작한다. 컴퓨터가 꺼져 있거나 로그인 전에는 새 자료 동기화가 안 된다. Pages의 마지막 게시본은 계속 제공된다.
- 5분 점검 `com.hollobit.ai-news.health`는 `.runtime/verification/operations-health.json`에 수집 지연·분석 중단·검토 대기를 기록한다. 외부 알림 전송은 하지 않는다. 로그는 `.runtime/pages-sync*.log`, `.runtime/operations-health*.log`에 있다. plist 원본은 `ops/`에 있다.
- 관리 중에는 수동 `--watch`를 중복 실행하지 않는다. 서비스 중지는 `launchctl bootout gui/501/com.hollobit.ai-news.pages` 및 `launchctl bootout gui/501/com.hollobit.ai-news.health`. 자동 시작을 영구 해제하려면 해당 두 plist만 LaunchAgents에서 별도 보관한다.
- 내용이 같으면 커밋하지 않는다. 알려지지 않은 원격 파일이 있으면 중단하고, 강제 push하지 않는다. 인증은 기존 `gh` 설정을 사용하며 토큰을 파일에 복사하지 않는다.
- 게시 파일은 `public_site.published_files()`의 공통 자산 목록과 검증된 manifest의 내용 해시 JSON만 허용한다. 전체 읽기 전용 홈·뉴스·아카이브·검토 분석·위험/조건부 시나리오·논문·위키·출처 목록과 기존 관측 지도, three.js 3D/2D 지식 관계 지도 및 공개 JSON을 포함한다. 3D 모듈·three.js 모듈과 MIT 라이선스는 사이트 안에 포함하며 외부 CDN을 쓰지 않는다. 원문 발췌와 내부 운영 로그는 제외한다.
- 뉴스 제목/출처 메타데이터와 검토된 분석을 구분한다. 기본·심층·위험·논문 결과는 기존 독립 검토 및 현재 입력 게이트를 다시 통과한 것만 공개한다. 원자료 본문, DB 행, 소유 PID, 인증 설정은 복사하지 않는다.
- 관측 지도는 저장된 최신 14/30/90일 기본·확장 스냅샷을 사용한다. 공동 관측은 의미·인과 관계가 아니다. 저장본이 없으면 공개 자료 없음으로 표시한다.
- GraphRAG의 새 질문 생성, 주제/결정 편집, 수집/재분석, 시뮬레이션 실행은 로컬 전용이다. 공개본은 조회·검색·필터·지도 탐색·데이터 다운로드를 제공한다.

GitHub Pages는 HTML·CSS·JavaScript·JSON 스냅샷을 제공합니다. 뉴스 수집,
SQLite 갱신, LLM 생성·독립 검토는 기존 서버에서 계속 수행합니다.
정적 사이트에는 변경·재분석·질문 생성 API가 없습니다.

## 분할 데이터와 게시 계약

`site-manifest.json`에는 schema/version·자료 범위·내용 해시 파일 경로를 기록합니다. 새 UI는 화면별 데이터와 선택 문서/지도 주변만 읽습니다. 전체 검색 색인은 검색 요청에만 읽으며 일부 파일의 검색을 전체 검색으로 표시하지 않습니다. `site.json`·`knowledge.json`은 호환/완전성 점검용으로 남깁니다. manifest와 모든 파일은 같은 Git tree에서 게시하며 직전 세대 파일을 보존합니다. 일시적 업로드 오류는 같은 내용/SHA의 blob/tree에 한해서 최대 3회 재시도합니다. commit/ref 게시와 인증/권한 오류는 자동 재시도하지 않습니다. 알려지지 않은 원격 해시 파일은 파일명의 SHA256과 실제 내용이 일치하는 경우에만 오래된 세대로 제거합니다. HTML/모듈 자산 누락·중복 식별자·없는 관계 끝점은 게시 전에 거부합니다.

## 로컬 생성과 확인

```bash
.venv/bin/python export_wiki_site.py --full-site --output .runtime/public-site
python3 -m http.server 8003 --bind 127.0.0.1 --directory .runtime/public-site
```

`http://127.0.0.1:8003`에서 전체 메뉴·지도·검색·인용·읽기 전용 설명을 확인합니다.
기본 출력은 현재 검토된 요약, 관계, 출처 링크뿐입니다. 원문 발췌,
DB, 환경 설정, 인증 파일, 내부 작업 상태, 원문 의존성 식별자는 내보내지 않습니다.
출처 URL의 사용자 인증 정보가 있으면 제외합니다. 일반 쿼리·프래그먼트는 순서와 인코딩을 보존하며, 알려진 인증·서명 파라미터만 제외합니다. 보관된 원본 및 근거가 유일한 복구 주소를 공개 링크에 반영합니다.
요약에도 민감한 내용이 있을 수 있으므로 게시 전 사람이 확인해야 합니다.

원문 발췌 공개 권한을 확인한 경우에만 `--include-excerpts`를 명시합니다.
현재 사이트는 생성 시점 스냅샷입니다. 이후 원문 변경·삭제는 다음 내보내기와
게시 때 반영됩니다. 오래된 공개본의 외부 복사까지 회수할 수는 없습니다.

## 원격 게시

1. 공개 범위와 대상 저장소를 먼저 확정합니다.
2. **프로젝트 전체가 아닌 `.runtime/wiki-site`의 생성 파일만** Pages 전용 저장소에 넣습니다.
3. 저장소의 Settings → Pages에서 게시 브랜치의 루트를 선택합니다.
4. `https://OWNER.github.io/REPOSITORY/`에서 확인합니다. 상대 자산 경로를 사용하므로 하위 경로를 지원합니다.
5. 최신 서버 결과를 다시 내보내고 동일 파일을 갱신해 게시합니다.

이 문서와 생성 명령은 저장소 생성·push·공개 설정 변경을 수행하지 않습니다.
GitHub 저장소 및 공개 범위가 확정되기 전 원격 게시를 하지 않습니다.

Obsidian 용도는 로컬 서버의 `/api/wiki/vault` ZIP을 사용합니다. 이 ZIP은
인용한 원문 발췌와 작업 이력을 포함하므로 공개 사이트 파일과 구분해야 합니다.

이전 데이터 판은 최신 순으로 완전한 세대 단위로 최대 24시간·현재 조각 포함 400MiB까지 보관합니다. 현재 세대는 삭제하지 않습니다. 상한으로 만료된 탭은 기존 manifest 재조회로 최신 판을 복구합니다.
