from pathlib import Path


def test_server_resolves_database_path_before_starting_services():
    source = Path('server_bootstrap.py').read_text()
    assert 'path = str(Path(path).resolve())' in source
    assert 'prepare_database(path, rebuild_articles, joined_articles, CLASSIFICATION_VERSION).close()' in source
    assert 'connect = open_db' in source
