"""Concise intermediate wire format; final report and review contracts are unchanged."""

def schema():
    from strategic_workflow import REPORT, object_schema
    return object_schema({key: REPORT['properties'][key] for key in ('claims', 'limitations')})


def normalize(value):
    # Older checkpoints/test adapters may include a summary. Keep it intact;
    # new models produce only evidence-linked claims and source limitations.
    if not isinstance(value, dict):raise ValueError('역할 메모 형식 오류')
    return dict(value, summary=value.get('summary', ''))
