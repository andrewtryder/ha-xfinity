# Contributing to ha-xfinity

Thank you for contributing to the Home Assistant Xfinity integration!

## Prerequisites

Development requires Python 3.14.2 or newer, Git, uv, and actionlint 1.7.12 (available from the [actionlint releases](https://github.com/rhysd/actionlint/releases)). The integration uses a locked `uv` project environment. To set up the environment and install hooks:

```bash
uv sync --locked
uv run --locked pre-commit install
```

To update or add dependencies, update `pyproject.toml` and regenerate `uv.lock` with:

```bash
uv lock
```

## Local Validation Contract

Before submitting a pull request, run the portfolio standard validation interface:

```bash
./scripts/validate-local.sh
```

This automated script executes the complete local hygiene and quality gate:
1. Toolchain & Python 3.14 runtime verification
2. Dependency lockfile freshness check (`uv lock --check`)
3. Pre-commit hooks (`uv run --locked pre-commit run --all-files`)
4. Ruff linting and formatting verification (`uv run --locked ruff check .`, `uv run --locked ruff format --check .`)
5. Actionlint workflow linting
6. Pytest suite with component statement coverage enforcement (`--cov-fail-under=90`, achieving >=90.3%)
7. Detection of changes made to tracked files, the index, or untracked files

Ruff lint checks include `custom_components/xfinity`. Formatting currently excludes that directory: `__init__.py`, `api.py`, `binary_sensor.py`, `config_flow.py`, `const.py`, `coordinator.py`, `diagnostics.py`, `entity.py`, `login.py`, and `sensor.py` need formatting. In a separate runtime-source formatting follow-up, review and format those files, rerun all tests, then remove the formatter exclusions from the hook and local script.

## Pull Request Guidelines

- All commits and PR titles must follow [Conventional Commits](https://www.conventionalcommits.org/).
- All tests must use synthetic fixtures or local mocks. Never make live network requests or commit real account credentials.
- Do not edit version numbers manually; releases are managed by Release Please.
