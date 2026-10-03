"""Queue ordering only: never preempt a call or change shared slot limits."""
BACKGROUND_PREFIXES = ('wiki_', 'paper_', 'research_')
POLICY = 'finish-first-v2'
FINISH_ROLES = {'synthesis', 'revision', 'risk_revision', 'strategy_patch', 'risk_patch'}


def queue_key(row, stamp, interactive):
    role = row['role']
    age = stamp - row['queued_at']
    # Preserve bounded aging so background work still makes progress.
    if role in interactive or age >= 60:
        tier = 0
    elif role.startswith(BACKGROUND_PREFIXES):
        tier = 4
    elif role.endswith('verification'):
        tier = 1
    elif role in FINISH_ROLES:
        tier = 2
    else:
        tier = 3
    return tier, row['id']
