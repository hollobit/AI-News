"""Version computation by responsibility; review rules remain conservative."""
from functools import lru_cache
from pathlib import Path
import hashlib
import ast

# Presentation/export modules cannot change evidence admission. Unknown new
# modules stay included in validation until deliberately classified here.
PRESENTATION = frozenset('source_titles news_views publication_state publication_assets public_site public_data export_wiki_site sync_wiki_pages export_staging static_dependencies site_templates'.split())
SOURCE = frozenset('code_policy source_projection source_changes news_repository classification url_parser url_context link_groups source_enrichment source_store source_navigation improvement_selection keyword_index keyword_registry keyword_rules agent_reach_runtime strategic_value reach_pipeline url_archive task_lifecycle'.split())


def digest(scope, root):
    paths = sorted(Path(root).glob('*.py'))
    if scope == 'source':
        # Include transitive local dependencies, including imports inside functions.
        modules={p.stem:p for p in paths};selected=set();pending=list(SOURCE)
        while pending:
            name=pending.pop()
            if name in selected or name in PRESENTATION or name not in modules:continue
            selected.add(name)
            for node in ast.walk(ast.parse(modules[name].read_text())):
                if isinstance(node,ast.ImportFrom) and node.module:pending.append(node.module.split('.')[0])
                elif isinstance(node,ast.Import):pending.extend(a.name.split('.')[0] for a in node.names)
        paths=[p for p in paths if p.stem in selected]
    elif scope == 'validation': paths = [p for p in paths if p.stem not in PRESENTATION]
    elif scope != 'publication': raise ValueError('Unknown policy scope')
    result = hashlib.sha256(('policy-v2:' + scope).encode())
    for path in paths:
        result.update(path.name.encode()); result.update(path.read_bytes())
    return result.hexdigest()


@lru_cache(maxsize=None)
def policy(scope='validation'):
    return digest(scope, Path(__file__).resolve().parent)
