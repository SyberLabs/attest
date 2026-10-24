# Factory-path CI gates

**Status:** Proposed gates, with present repository facts separated from release policy. No coverage percentages are proposed. A green test command only demonstrates the exercised inputs and code paths; it does not establish universal truth or production behavior.

## Measured repository facts

These are source/configuration observations, not claims that the proposed gates are already running in hosted CI:

- `pyproject.toml` declares `pytest` and `flask` in the optional `test` extra, configures `testpaths = ["tests"]`, and sets `addopts = "-q"`.
- Existing focused tests cover Pydantic model invariants, compilation behavior, deterministic verdict rules, and a local hand-labelled fixture corpus (`tests/test_models.py`, `tests/test_compiler.py`, `tests/test_verdict.py`).
- `attest/kane.py` is an unimplemented stub; `attest/runner.py` includes `FakeBrowserRunner`; there is no Reconcile Spec policy or receipt type in this checkout.
- No tracked `.github/workflows/*` CI workflow was present in the inspected checkout. This document proposes gates; it does not claim a workflow was configured or run.
- The current execution environment did not have pytest installed when first checked (`python -m pytest --version` reported `No module named pytest`). A successful local run must be reported only after the declared test dependency is available and the command actually completes.

## Proposed required gates

### Gate A — repository baseline

**Command:** `python -m pytest -q`

Require the existing suite and `tests/quality/` tests to complete successfully on every PR/release candidate. Do not exclude security tests by directory or marker. Record the command and actual exit/result in the PR. This is the regression baseline, not a substitute for the component gates below.

### Gate B — quality contract tests

**Command:** `python -m pytest -q tests/quality`

Require all applicable assertions to pass against real component boundaries. A normal release gate must not accept an `XFAIL`, `XPASS`, or `SKIP` for a security case required by that component's release. A currently expected failure or skip is a visible gap until it is converted to a passing test by an implementation change; it must not be counted toward the red-team quota.

Initial tests in this slice exercise only currently implemented Attest model/judge behavior. The simulation-versus-verification test is marked strict expected-failure because the fake runner can currently produce a `proven` browser verdict. It is evidence of a known gap, not a passing security gate. The protected-requirement weakening test is explicitly skipped because Reconcile Spec has no policy/module here; the skip is a stub, not exercised coverage. Digest-binding scenarios are not implemented here and cannot be reported as exercised.

### Gate C — per-component release quota

**Command:** `python -m pytest -q tests/quality/<component-suite>` (suite names to be established with each component implementation)

For each component being released, require the minimum per-category adversarial scenarios from [the N5 red-team quota](FACTORY-REDTEAM-QUOTA-2026-10-24.md). Assert the safety outcome for each case. Fail the release if a required category is untested, skipped, xfailed, or covered only by a fake that bypasses the boundary under test. For missing components, this gate is pending—not green.

### Gate D — exact version and evidence integrity

For any release that emits verified evidence or reconciles an external effect, require deterministic tests that tamper independently with source digest, app/spec version, execution/run identifier, claim/step mapping, and destination/case binding. Each mismatch must fail closed, and a fresh valid evidence set must be required before success. Until the corresponding identity fields exist and are enforced in code, this gate is blocked for verified/reconciled releases.

### Gate E — simulation and incomplete work

Require tests showing that fake/echo, dry-run, cached-only and partial/incomplete executions cannot mint a verified receipt or advance an external effect to reconciled. Require incomplete compilation, missing terminal events, invalid evidence, ambiguous authority and budget exhaustion to remain non-success. Do not infer this gate from a passing CLI exit code alone.

### Gate F — static and integration checks

Add type/lint/build or live-service jobs only when the repository adopts those tools/contracts. Do not label an unconfigured tool or unauthenticated mock as a measured gate. Live Kane and destination integration checks must use an explicitly scoped test account/environment; secrets must not be logged. A live-service check that cannot run is reported as not run, not passed. Unit CI may use deterministic fixtures, but must retain the boundary contract tests and cannot claim live verification from those fixtures.

## Required gate report

For each candidate, record: commit/release identity; exact commands; pass/fail/skip/xfail results; which component/category scenarios ran; whether those scenarios used real code, deterministic fakes, or live integrations; and unresolved blocks. Never translate a suite exit of zero into claims about untested identity binding, production deployment, external effects, credentials, or UI behavior.
