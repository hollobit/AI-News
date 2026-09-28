/* Shared evidence text and citation rendering for workbench panels. */
(() => {
  'use strict';
  window.StrategyPanels ||= {};
  window.StrategyPanels.evidence = ({ node, link }) => {
    const metricNames = {
      round_count: '회차',
      completed_rounds: '완료 회차',
      no_progress_rounds: '변화 없는 회차',
      seen_documents: '누적 근거',
      source_count: '원문 근거',
      new_document_count: '신규 근거',
      verified_claims: '검토된 주장',
      claims: '주장',
      issues: '검토 지적',
      rules: '개선 규칙',
      concepts: '개념',
      verified: '검토 통과',
      cited_claims: '근거 인용 주장',
      evidence_count: '근거 수',
      audit_issues: '검토 지적',
      failed_sources: '원문 조회 실패',
      requested_sources: '원문 조회 요청',
      fetched_sources: '확보 원문',
      strategic_concepts: '전략 개념',
      total_unique: '전체 고유 뉴스',
      processed_unique: '처리 뉴스',
      remaining: '남은 뉴스',
      verified_unique: '검토 통과 뉴스',
      needs_review_unique: '검토 필요 뉴스',
      reused_verified: '기존 검토 결과 활용',
      duplicates_excluded: '제외한 중복',
      failed_unique: '분석 실패',
      verification_pending: '검토 미완료',
      all_processed: '전체 분석 처리 여부',
      all_verified: '전체 검토 통과 여부',
      risk_assessed_unique: '위험 평가 뉴스',
      risk_unassessed_unique: '위험 미평가 뉴스',
      risk_assessments: '위험 평가 수',
      critical_risks: '심각 등급 평가',
      risk_issues: '위험 검토 지적',
      risk_verified: '위험 근거 검토 통과',
    };
    function improvementText(value) {
      if (value == null) return '';
      if (typeof value === 'boolean') return value ? '예' : '아니요';
      if (typeof value === 'string' || typeof value === 'number') return String(value);
      if (Array.isArray(value)) return value.map(improvementText).filter(Boolean).join(' · ');
      return (
        value.text ||
        value.summary ||
        value.assessment ||
        value.description ||
        Object.entries(value)
          .map(([k, v]) => `${metricNames[k] || k}: ${improvementText(v)}`)
          .join(' · ')
      );
    }
    function improvementEvidence(el, records) {
      (records || []).slice(0, 4).forEach((e) => {
        if (typeof e === 'object' && (e.url || e.source_url))
          el.append(link(e.title || e.url || '연결 근거', e.url || e.source_url));
        else if (typeof e === 'string') el.append(node('small', 'subtle', '근거 ' + e));
      });
    }

    return { improvementText, improvementEvidence };
  };
})();
