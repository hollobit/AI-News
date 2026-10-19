"""Explicit news-analysis model routing; never retry an outage on another model."""
import hashlib
import os
import re

VERSION = 'news-models-v1'
# Latest models explicitly requested by the operator. Unavailable models must
# stop admission; never silently downgrade or route around an account limit.
MODELS = {'luna': 'gpt-6-luna', 'sol': 'gpt-6.1-sol', 'astra': 'gpt-6-astra'}
ALLOWED = {tier: {model} for tier, model in MODELS.items()}
QUESTION_ROLES = {'graph_answer','graph_draft','strategic_question','strategic_question_verification'}


def execution_tier(role):
    """Opt-in faster question review; generation and bulk work stay standard."""
    if role not in QUESTION_ROLES:return 'standard'
    value=os.environ.get('NEWS_QUESTION_SERVICE_TIER','standard').strip().lower()
    if value not in {'standard','fast'}:
        raise ValueError('NEWS_QUESTION_SERVICE_TIER는 standard 또는 fast여야 합니다.')
    return value if role=='strategic_question_verification' else 'standard'


def question_draft_role(question, plan=None, *, evidence=None):
    """Focused technical comparisons can use Luna; broad synthesis stays on Sol."""
    axes=(plan or {}).get('axes',[])
    focused=re.search(r'차이|비교|작동\s*(?:방식|원리)|벤치마크|데이터세트|데이터셋|\b(?:compare|comparison|benchmark|dataset)',question,re.I)
    broad=re.search(r'대응|전략|예측|전망|추천|정책|국제|\b(?:strategy|forecast|recommend|policy)',question,re.I)
    if not focused or broad or not 1<=len(axes)<=4 or len(question)>250:return 'graph_answer'
    extended=_extended_question_evidence(evidence)
    if evidence is not None and (len(evidence)>32 or sum(len(e.get('text') or '') for e in evidence)>64000):
        return 'graph_answer'
    if len(axes)<=3 and not extended:return 'graph_draft'
    # Four-facet overviews often include an added integration axis. Explicit
    # interpretation/causality questions retain Sol: those drafts needed extra
    # repair in evaluation. Never discard an axis to make a question eligible.
    interpret=re.search(r'해석|원인|인과|왜|타당|우열|효과|\b(?:interpret|causal|causation|why|validity|trade.?offs?|justify)',question,re.I)
    overview=re.search(r'현황|종류|목록|유형|\b(?:overview|types|list|survey)',question,re.I)
    return 'graph_draft' if evidence and overview and not interpret else 'graph_answer'


def _extended_question_evidence(evidence):
    return evidence is not None and (len(evidence)>24 or sum(len(e.get('text') or '') for e in evidence)>48000)


def question_draft_effort(role, *, evidence=None):
    """Larger overview bundles need more draft reasoning to avoid repair calls."""
    return 'high' if role=='graph_draft' and _extended_question_evidence(evidence) else 'medium'


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
    return {'policy_version': 'news-models-question-v2-overview' if role=='graph_draft' else 'news-models-announcement-v1' if announcement else VERSION, 'tier': tier, 'model': model,
            'instruction_profile':'grounded-analysis-v1',
            'reasoning_effort': 'medium' if announcement or role=='graph_draft' else 'high' if tier == 'luna' else 'medium' if tier == 'sol' else 'low'}


def input_digest(prompt):
    return hashlib.sha256(prompt.encode()).hexdigest()
