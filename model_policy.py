"""Explicit news-analysis model routing; never retry an outage on another model."""
import hashlib
import os
import re

VERSION = 'news-models-v1'
# Latest models explicitly requested by the operator. Unavailable models must
# stop admission; never silently downgrade or route around an account limit.
MODELS = {'luna': 'gpt-6-luna', 'sol': 'gpt-6.1-sol', 'astra': 'gpt-6-astra'}
ALLOWED = {tier: {model} for tier, model in MODELS.items()}


def question_draft_role(question, plan=None):
    """Focused technical comparisons can use Luna; broad synthesis stays on Sol."""
    axes=(plan or {}).get('axes',[])
    focused=re.search(r'차이|비교|작동\s*(?:방식|원리)|벤치마크|데이터세트|데이터셋|\b(?:compare|comparison|benchmark|dataset)',question,re.I)
    broad=re.search(r'대응|전략|예측|전망|추천|정책|국제|\b(?:strategy|forecast|recommend|policy)',question,re.I)
    # The planner may add a fourth integration axis to a three-part comparison;
    # that does not turn the same focused question into broad strategic advice.
    return 'graph_draft' if focused and not broad and 1<=len(axes)<=4 and len(question)<=250 else 'graph_answer'


class StructuredResult(dict):
    """Keep call provenance outside the model's strict output schema."""
    def __init__(self, value, provenance):
        super().__init__(value)
        self.provenance = provenance


def policy(role, *, escalation=False, prompt=None):
    tier = 'luna' if role in ('baseline_analysis', 'engine_probe', 'graph_draft') else 'sol'
    announcement=False
    if role=='integrated_analysis' and prompt and not escalation:
        from workflow_model_profile import simple_announcement
        announcement=simple_announcement(prompt)
        if announcement:tier='luna'
    if escalation:
        tier = 'astra'
    model = os.environ.get('NEWS_MODEL_' + tier.upper(), MODELS[tier])
    if model not in ALLOWED[tier]:
        raise ValueError('지원되지 않는 뉴스 분석 모델 설정: ' + tier)
    return {'policy_version': 'news-models-question-v1' if role=='graph_draft' else 'news-models-announcement-v1' if announcement else VERSION, 'tier': tier, 'model': model,
            'reasoning_effort': 'medium' if announcement or role=='graph_draft' else 'high' if tier == 'luna' else 'medium' if tier == 'sol' else 'low'}


def input_digest(prompt):
    return hashlib.sha256(prompt.encode()).hexdigest()
