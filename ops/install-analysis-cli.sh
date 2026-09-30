#!/bin/sh
# Keep the project-local client compatible with the pinned news model set.
set -eu
project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
npm install --prefix "$project_root/.runtime/codex" --save-exact @openai/codex@0.159.2
"$project_root/.runtime/codex/node_modules/.bin/codex" --version
"$project_root/.venv/bin/python" "$project_root/model_access.py" --refresh
