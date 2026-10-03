"""Explicit news-analysis model routing; never retry an outage on another model."""
import hashlib
import os

VERSION = 'news-models-v1'
# Latest models explicitly requested by the operator. Unavailable models must
# stop admission; never silently downgrade or route around an account limit.
MODELS = {'luna': 'gpt-6-luna', 'sol': 'gpt-6.1-sol', 'astra': 'gpt-6-astra'}
ALLOWED = {tier: {model} for tier, model in MODELS.items()}


class StructuredResult(dict):
    """Keep call provenance outside the model's strict output schema."""
    def __init__(self, value, provenance):
        super().__init__(value)
        self.provenance = provenance


def policy(role, *, escalation=False, prompt=None):
    tier = 'luna' if role in ('baseline_analysis', 'engine_probe') else 'sol'
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
    return {'policy_version': 'news-models-announcement-v1' if announcement else VERSION, 'tier': tier, 'model': model,
            'reasoning_effort': 'medium' if announcement else 'high' if tier == 'luna' else 'medium' if tier == 'sol' else 'low'}


def input_digest(prompt):
    return hashlib.sha256(prompt.encode()).hexdigest()
