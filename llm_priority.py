"""Queue ordering only: never preempt a call or change shared slot limits."""
BACKGROUND_PREFIXES = ('wiki_', 'paper_', 'research_')
POLICY = 'detail-first-v1'


def queue_key(row, stamp, interactive):
    role = row['role']
    age = stamp - row['queued_at']
    # Preserve bounded aging so background work still makes progress.
    if role in interactive or age >= 60:
        tier = 0
    elif role.startswith(BACKGROUND_PREFIXES):
        tier = 2
    else:
        tier = 1
    return tier, row['id']
