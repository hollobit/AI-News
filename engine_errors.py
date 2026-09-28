"""Content-free classification of CLI failures; never persist stderr or credentials."""
import re

MESSAGES = {
    'authentication': '분석 엔진 인증이 만료되었거나 거절되었습니다. Codex 로그인을 갱신해 주세요.',
    'rate_limit': '분석 서비스의 사용량 한도에 도달했습니다. 한도가 복구된 뒤 재개해 주세요.',
    'capacity': '선택한 분석 모델의 처리 용량이 가득 찼습니다. 서비스 혼잡이 해소된 뒤 같은 단계에서 재개할 수 있습니다.',
    'network': '분석 서비스 연결이 끊겼습니다. 네트워크 복구 후 재개해 주세요.',
    'permission': '분석 엔진 프로세스 실행 권한이 차단되었습니다. 실행 환경을 확인해 주세요.',
    'configuration': '분석 엔진 설정 또는 요청 형식이 거절되었습니다. CLI 호환성을 확인해 주세요.',
    'execution_failed': '분석 엔진 실행에 실패했습니다. 실행 진단 코드를 확인해 주세요.',
    'timeout': '분석 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.',
    'circuit_open': '분석 서비스 오류가 반복되어 새 호출을 잠시 중지했습니다. 복구 확인 후 재개해 주세요.',
    'database_locked': '분석 저장소를 다른 작업이 갱신하고 있습니다. 잠금 해제 후 같은 체크포인트에서 재개할 수 있습니다.',
}
INFRASTRUCTURE_CODES = frozenset(MESSAGES) | {'queue_timeout'}


class EngineError(RuntimeError):
    """Safe actionable error that can travel through existing persisted workflows."""
    def __init__(self, code, returncode=None):
        self.code = code if code in INFRASTRUCTURE_CODES else 'execution_failed'
        self.returncode = returncode
        super().__init__(f'[engine:{self.code}] {MESSAGES.get(self.code, "모델 실행 대기 시간이 초과되었습니다.")}')


def classify_failure(stderr, returncode=None):
    """Select an allowlisted code without reflecting arbitrary provider output."""
    text = str(stderr or '').casefold()
    explicit = re.findall(r'^.*?\berror\b\s*:\s*(.+)$', text, re.M)
    if explicit:
        text='\n'.join(explicit[-5:])
    patterns = (
        ('capacity', r'(?:model|server|service).*(?:at capacity|overloaded)|insufficient.*capacity'),
        ('rate_limit', r'usage limit|rate.?limit|quota|too many requests|\b429\b|credits? (?:exhausted|remaining)'),
        ('authentication', r'\b401\b|unauthorized|token_invalidated|refresh_token|not logged in|authentication'),
        ('permission', r'operation not permitted|permission denied|sandbox.*denied'),
        ('configuration', r'unexpected argument|unknown (?:feature|model)|invalid.*schema|unsupported|model.*not.*found'),
        ('network', r'connection|network|dns|stream disconnected|timed out|\b50[234]\b|transport'),
    )
    return next((code for code, pattern in patterns if re.search(pattern, text)), 'execution_failed')


def infrastructure_error(message):
    """Recognize new safe codes and the old engine-only failure messages."""
    if str(message or '').strip() in {'database is locked','database table is locked'}:
        return 'database_locked'
    match = re.search(r'\[engine:([a-z_]+)\]', str(message or ''))
    if match and match[1] in INFRASTRUCTURE_CODES:
        return match[1]
    for text, code in [('분석 엔진 실행에 실패', 'execution_failed'), ('분석 시간이 초과', 'timeout'),
                       ('모델 실행 대기 시간이 초과', 'queue_timeout')]:
        if text in str(message or ''):
            return code
    return None
