# Contributing to ha-xfinity

Thank you for contributing to the Home Assistant Xfinity integration!

## Prerequisites

Development requires Python 3.14.2 or newer, Git, uv, and actionlint 1.7.12 (available from the [actionlint releases](https://github.com/rhysd/actionlint/releases)). The validation script installs pinned Python dependencies into a temporary environment. To install hooks in a persistent environment:

```bash
python3.14 -m venv .venv-test
uv pip sync --python .venv-test/bin/python requirements-dev.lock
.venv-test/bin/pre-commit install
```

Regenerate the lock after changing requirements with `uv pip compile --python-version 3.14.2 --universal requirements-dev.txt -o requirements-dev.lock`. This retains the existing requirements-based Python setup.

## Local Validation Contract

Before submitting a pull request, run the portfolio standard validation interface:

```bash
./scripts/validate-local.sh
```

This automated script executes the complete local hygiene and quality gate:
1. Toolchain & Python 3.14 runtime verification
2. Pre-commit hooks (`pre-commit run --all-files`)
3. Ruff linting and formatting verification (`ruff check .`, `ruff format --check .`)
4. Actionlint workflow linting
5. Pytest suite with component statement coverage enforcement (`--cov-fail-under=90`, currently achieving >90.3%)
6. Detection of changes made to tracked files, the index, or untracked files

Ruff lint checks include `custom_components/xfinity`. Formatting currently excludes that directory: `__init__.py`, `api.py`, `binary_sensor.py`, `config_flow.py`, `const.py`, `coordinator.py`, `diagnostics.py`, `entity.py`, `login.py`, and `sensor.py` need formatting. In a separate runtime-source formatting follow-up, review and format those files, rerun all tests, then remove the formatter exclusions from the hook and local script.

## Pull Request Guidelines

- All commits and PR titles must follow [Conventional Commits](https://www.conventionalcommits.org/).
- All tests must use synthetic fixtures or local mocks. Never make live network requests or commit real account credentials.
- Do not edit version numbers manually; releases are managed by Release Please.
