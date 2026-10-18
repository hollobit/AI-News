"""Allowlisted wiki failure stages; never reflect provider output or input text."""
STAGES = {
    'preparation': '편집 자료 준비',
    'wiki_compile': '본문 작성',
    'wiki_review': '독립 검토',
    'publication': '최신 근거 확인·저장',
}


def describe(error, stage):
    from engine_errors import infrastructure_error, EngineError
    stage = stage if stage in STAGES else 'preparation'
    code = infrastructure_error(str(error))
    if code:
        detail = str(EngineError(code))
    elif isinstance(error, (ValueError, TypeError, KeyError)):
        detail = '응답 형식 또는 근거 검증에 실패했습니다. 공개 기준은 유지됩니다.'
    else:
        detail = '작업을 마치지 못했습니다. 공개 기준은 유지됩니다.'
    return f'[wiki:{stage}] {STAGES[stage]}: {detail}'
