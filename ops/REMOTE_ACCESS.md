# 원격 통합 분석실 운영

GitHub Pages `context.html`은 원격 분석실의 진입점이다. 로그인 후에는 같은 Mac의 기존 Python 서버를 HTTPS로 사용하며 단일 질의·자료 탐색·관계/통찰 분석·조건부 전망과 기존 운영 기능을 제공한다. 정적 사이트에 인증 정보나 원문/운영 DB를 게시하지 않는다.

현재 접속은 Cloudflare Quick Tunnel이다. **임시 연결이며 가동 시간 보장과 고정 호스트명이 없다.** Mac이 켜져 있고 로그인한 상태에서 분석 서버·모델 실행 환경이 정상이어야 한다. 터널 재시작 시 주소가 바뀌며 기존 Pages 동기화(5분 간격+Pages 빌드 시간)가 진입 링크를 갱신한다. 기존 임시 주소 대신 Pages의 `context.html`을 즐겨찾기한다. 서비스가 꺼진 동안 공개된 과거 링크는 작동하지 않을 수 있으며 접속 링크의 존재를 서버 생존 확인으로 표시하지 않는다.

## 실행 구조

`Pages context.html → HTTPS 터널 → 127.0.0.1:8002 인증 프록시 → 기존 127.0.0.1:8001 서버`

- `ops/remote_service.py`: 프로세스 잠금, 게이트웨이/터널 소유 및 재접속, 공개 주소 파일의 원자적 갱신.
- `ops/remote_access.py`: 소유자 암호 로그인, 최대8시간 세션, Secure/HttpOnly/SameSite=Strict 쿠키, Host·Origin 검사, 로그인/실행 요청 제한. 외부 인증·쿠키는 내부 서버로 전달하지 않는다. 기존 localhost Origin 규칙은 인증을 통과한 요청에 대해서만 프록시에서 변환한다.
- 로그인 후 전체 운영 기능에 접근하므로 접속 암호는 소유자 전용이다. 익명 공유용 역할/다중 사용자 권한 분리는 제공하지 않는다.
- 원문·질문·비밀번호·쿠키를 접속 로그에 기록하지 않는다. 공개 `remote-access.json`에는 HTTPS origin과 임시/고정 모드만 들어간다.
- 접속 암호: `.runtime/remote-access/password.txt` (자동 생성, 파일 권한600). 저장소·문서·공개 사이트에 복사하지 않는다. 암호 교체 후 서비스를 재시작하면 모든 기존 세션이 무효화된다.
- `cloudflared` 바이너리는 공식 GitHub 릴리스의 Darwin ARM64 아카이브를 받아 릴리스 SHA256 digest를 검증했다. 바이너리는 `.runtime/remote-access/cloudflared`에만 둔다.

LaunchAgent 원본은 `ops/com.hollobit.ai-news.remote.plist`이며 설치 위치는 `~/Library/LaunchAgents/`다. 중지는 `launchctl bootout gui/501/com.hollobit.ai-news.remote`, 재시작은 `launchctl kickstart -k gui/501/com.hollobit.ai-news.remote`이다. 기존 서버·수집기·Pages 서비스는 별도이므로 중복 실행하지 않는다.

## 고정 주소·상시 운영 전환

고정 호스트명 운영에는 Cloudflare 계정/도메인의 named tunnel 또는 별도 TLS reverse proxy와 상시 서버가 필요하다. 현재는 이 설정이 제공되지 않아 임시 연결까지만 적용했다. 고정 연결 시 `public.json`의 origin을 해당 HTTPS 주소로 설정하고 인증 게이트웨이로만 전달한다. 기존 로컬 서버8001을 직접 외부에 노출하지 않는다. 별도 서버로 옮기려면 DB·모델 인증·수집기 소유와 저장소 동기화도 함께 이전해야 한다.

공식 문서: [Quick Tunnel의 용도와 제한](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/), [Cloudflare Tunnel](https://developers.cloudflare.com/tunnel/).
