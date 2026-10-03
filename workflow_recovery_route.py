"""Recognize only machine-generated admission feedback, never semantic rejections."""
# This path still receives fresh evidence, full coverage checks and independent review.
ADMISSION_ISSUES = frozenset({
    '현재 문서 문맥 또는 URL 발췌와 분석 스냅샷이 일치하지 않습니다.',
    '이 문서가 전략 보고서의 실제 인용에서 누락되었습니다.',
    '이 문서의 위험 평가 또는 평가 불가 분류에 대한 독립 대조가 미완료입니다.',
    '분석 엔진 실행에 실패했습니다. Codex 로그인과 네트워크 상태를 확인해 주세요.',
})


def operational_feedback_only(request):
    if not request.get('completion') or request.get('completion_attempt', 1) != 1:return False
    context = request.get('improvement_context') or {}
    issues = context.get('review_issues') or []
    # Require the exact known stale/engine admission bundle. Unknown or additional
    # review issues stay on the original path, even if they mention these words.
    if not isinstance(issues, list) or set(issues) != ADMISSION_ISSUES:return False
    tasks = context.get('followup_tasks') or []
    if not tasks:return False
    for task in tasks:
        if task.get('kind') != 'completion_review' or set(task.get('text', '').splitlines()) != ADMISSION_ISSUES:return False
    return True
