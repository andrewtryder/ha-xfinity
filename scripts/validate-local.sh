#!/usr/bin/env bash
set -euo pipefail

# Portfolio Universal Local Validation Contract
# Repository: ha-xfinity
# Gate: 90% component statement coverage

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ -n $(git ls-files --others --exclude-standard) ]]; then
    echo "Error: move or commit untracked files before validation" >&2
    exit 1
fi

# Compare both index and working-tree changes, including untracked paths.
snapshot_dir=$(mktemp -d)
snapshot() {
    git status --porcelain=v1 --untracked-files=all > "$1/status"
    git diff --binary > "$1/worktree"
    git diff --cached --binary > "$1/index"
}
mkdir "$snapshot_dir/before" "$snapshot_dir/after"
snapshot "$snapshot_dir/before"
check_changes() {
    snapshot "$snapshot_dir/after"
    if ! diff -qr "$snapshot_dir/before" "$snapshot_dir/after" >/dev/null; then
        echo "Error: validation changed the working tree or index:" >&2
        git status --short >&2
        return 1
    fi
}
finish() {
    local result=$?
    check_changes || result=1
    rm -rf "$snapshot_dir"
    return "$result"
}
trap finish EXIT

echo "==> [1/7] Verifying toolchain and interpreter"
command -v uv >/dev/null 2>&1 || { echo "Error: uv is required but not installed" >&2; exit 1; }
command -v actionlint >/dev/null 2>&1 || { echo "Error: actionlint is required; see CONTRIBUTING.md" >&2; exit 1; }
uv sync --locked
uv run --locked python -c 'import sys; assert sys.version_info >= (3, 14, 2), "Python 3.14.2 or newer is required"; print(sys.version)'

echo "==> [2/7] Verifying dependency lockfile freshness"
uv lock --check

echo "==> [3/7] Running pre-commit hooks on all files"
uv run --locked pre-commit run --all-files

echo "==> [4/7] Running Ruff lint and formatting checks"
uv run --locked ruff check .
uv run --locked ruff format --check .

echo "==> [5/7] Running actionlint workflow validation"
actionlint .github/workflows/*.yml

echo "==> [6/7] Running Python test suite with coverage enforcement (>=90% gate)"
uv run --locked pytest --disable-socket --allow-unix-socket

echo "==> [7/7] Checking for changes made by validation"
check_changes

echo "==> Local validation passed successfully!"
