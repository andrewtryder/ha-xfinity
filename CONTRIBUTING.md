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

## Discovering features in the Android app

Contributors with different Xfinity services can help identify useful entities.
On a trusted computer, connect an Android emulator or device with USB debugging,
sign in to the official app, and install Android platform tools (`adb`) and
Python. Check the device with `adb devices`, then run:

```bash
python3 scripts/discover-app-features.py --output xfinity-features.txt
```

Use `--serial SERIAL` if more than one device is connected. Navigate through
the app yourself and enter a name for each screen to record its accessibility
labels. Submit a redacted description in an issue or PR, including which
services you have, what each feature shows, and why it would help in Home
Assistant. The script does not click controls, record screenshots, capture
network traffic, or identify API endpoints. Some screens do not expose labels.
Seeing a feature in the app does not establish how the integration can access it.

The report can contain personal data, account numbers, addresses, device names,
and other sensitive labels. Review and redact it before sharing; never submit
credentials, tokens, cookies, callback URLs, or unreviewed network captures.
Keep live account data out of fixtures and PRs. If an implementation requires
API research, document the request method and path and use synthetic examples;
do not invoke disruptive controls such as modem restart merely to discover them.
