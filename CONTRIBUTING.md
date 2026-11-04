# Contributing to ha-xfinity

Thank you for contributing to the Home Assistant Xfinity integration!

## Prerequisites

Development requires:
- Python 3.14
- Git
- Virtual environment with test dependencies (`requirements_test.txt`)

## Local Setup

Set up a virtual environment and pre-commit hooks:

```bash
python3 -m venv .venv-test
source .venv-test/bin/activate
pip install -r requirements_test.txt
pre-commit install
```

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
6. Clean git working tree verification

## Pull Request Guidelines

- All commits and PR titles must follow [Conventional Commits](https://www.conventionalcommits.org/).
- All tests must use synthetic fixtures or local mocks. Never make live network requests or commit real account credentials.
- Do not edit version numbers manually; releases are managed by Release Please.
