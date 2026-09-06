---
name: project-testing
description: "Mandatory knowledge and workflow when running the check suite, claiming validation is complete, or live-testing on the Stream Deck. Use before committing or claiming tests pass."
---
# Project Testing

## Purpose

Prevents committing broken code, claiming validation without evidence, and skipping the live-testing workflow on the real Stream Deck.

## Scope

Use this skill when:

- running the check suite (pytest, coverage, black, isort, flake8, mypy);
- committing changes to `src/megingjord/` or `tests/`;
- live-testing the Home Assistant, PulseAudio, or BUSY Bar integrations on the real deck;
- claiming validation is complete in a SOW.

Do not use this skill for:

- design discussions or read-only investigation;
- rendering-specific preview work (see `project-rendering`).

## Mandatory Knowledge

- Canonical commands (from `AGENTS.md`):
  - Tests: `env/bin/python -m pytest tests/`
  - Coverage: `env/bin/coverage run --source=src/megingjord -m pytest tests/ && env/bin/coverage combine && env/bin/coverage report -m`
  - Formatting: `env/bin/black src/megingjord/ tests/`
  - Import sorting: `env/bin/isort src/megingjord/ tests/`
  - Lint: `env/bin/flake8 src/megingjord/ tests/`
  - Type check: `env/bin/mypy src/megingjord/`
- Coverage expectations: `render.py` at 100%, `ha.py` at 99% (one pre-existing branch).
- The pre-commit hook runs black, isort, flake8, trim trailing whitespace, fix end of files, check toml, check yaml. The end-of-file-fixer modifies files; re-add them before committing.
- Live testing uses test scripts, excluded from git via `.git/info/exclude`, against the real Home Assistant endpoint.
- The BUSY Bar integration needs an API token from its web UI.
- Run pylint on the whole codebase at once (`env/bin/pylint src/megingjord/`), never on a subset of changed files; subset runs degrade inference and produce different results as the file set changes (pylint pre-commit documentation). Pylint is not in the pre-commit hook; it is a manual/CI check.

## Best Practices

- Run the full check suite before committing.
- Re-add files after the pre-commit hook modifies them, then commit again.
- The user tests interaction and visual changes on the real deck before asking to commit; do not commit such changes without that live test.
- Verify each step of a chained install or setup command independently; a failed `&&` chain silently skips the remaining steps.

## Bad Practices

- Committing without running the full check suite.
- Claiming validation is complete without real-use evidence when a runnable path exists.
- Assuming a chained command ran every step after one step failed.

## Workflow Checklist

1. Run `env/bin/python -m pytest tests/`.
2. Run coverage and confirm `render.py` 100% and `ha.py` 99%.
3. Run `black --check`, `isort --check-only`, `flake8`, `mypy`.
4. Stage the intended files only; never `git add -A`.
5. Commit; if the pre-commit hook modified files, re-add them and commit again.
6. For interaction or visual changes, have the user live-test on the real deck before committing.

## Validation Checklist

Before claiming done:

- Full check suite passes (pytest, coverage, black, isort, flake8, mypy).
- Real-use evidence exists for interaction or visual changes (user live test on the deck).
- Same-failure scan: search for the same pattern elsewhere in the codebase.
- Sensitive data gate: no raw tokens or personal data in durable artifacts.

## Evidence

- `AGENTS.md`: canonical commands and live-testing overrides.

## Update Rules

Update this skill when:

- a new command or workflow becomes canonical;
- the user corrects the testing or commit workflow;
- tests or review find a missed failure mode.
