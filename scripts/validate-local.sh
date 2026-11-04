#!/usr/bin/env bash
set -euo pipefail

# Portfolio Universal Local Validation Contract
# Repository: ha-xfinity
# Gate: 90% component statement coverage

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ -d "$REPO_ROOT/.venv-test/bin" ]]; then
    export PATH="$REPO_ROOT/.venv-test/bin:$PATH"
fi

echo "==> [1/6] Verifying toolchain and interpreter"
python --version

echo "==> [2/6] Running pre-commit hooks on all files"
pre-commit run --all-files

echo "==> [3/6] Running Ruff lint and formatting checks"
ruff check .
ruff format --check .

echo "==> [4/6] Running actionlint workflow validation"
if command -v actionlint >/dev/null 2>&1; then
    actionlint .github/workflows/*.yml
else
    echo "actionlint not found in PATH, skipping workflow linting"
fi

echo "==> [5/6] Running Python test suite with coverage enforcement (>=90% gate)"
pytest

echo "==> [6/6] Verifying clean git working tree"
git diff --exit-code

echo "==> Local validation passed successfully!"
