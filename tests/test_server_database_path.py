from pathlib import Path


def test_server_resolves_database_path_before_starting_services():
    source = Path('app.py').read_text()
    assert 'path = str(Path(path).resolve())' in source
    assert 'connect(path).close()' in source
