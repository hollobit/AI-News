"""Invoke the project's scheduler from an installed skill."""
from pathlib import Path
import subprocess
import sys

ROOT = Path('/Users/user/git/news')
if __name__ == '__main__':
    raise SystemExit(subprocess.call([str(ROOT / '.venv/bin/python'), str(ROOT / 'scheduled_collection.py'),
                                     *sys.argv[1:]], cwd=ROOT))
