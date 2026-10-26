# Factory-path CI gates

**Status:** G1 and G8 are Measured in `.github/workflows/tests.yml`, each within the scope stated in [Gate status](#gate-status-g1g8). G2–G7 are Proposed. Present repository facts are separated from release policy. No coverage percentages are proposed. A green test command only demonstrates the exercised inputs and code paths; it does not establish universal truth or production behavior.

## Measured repository facts

These are source/configuration observations, not claims that the proposed gates are already running in hosted CI:

- `pyproject.toml` declares `pytest` and `flask` in the optional `test` extra, configures `testpaths = ["tests"]`, and sets `addopts = "-q"`.
- Existing focused tests cover Pydantic model invariants, compilation behavior, deterministic verdict rules, and a local hand-labelled fixture corpus (`tests/test_models.py`, `tests/test_compiler.py`, `tests/test_verdict.py`).
- `attest/kane.py` implements `KaneBrowserRunner`; the CLI defaults to Kane, while `--runner fake` is explicit. `pipeline.prove()` still falls back to `FakeBrowserRunner` when called without a runner. `tests/test_kane.py` exercises NDJSON and evidence-pack parsing/mapping using captured event-day fixtures in `tests/fixtures/kane/`; these are offline contract tests, not a live browser run.
- `.github/workflows/tests.yml` runs on every pull request and on pushes to `main`. Job `pytest` is G8; job `G1 Stage-0 exit` is G1. Whether either is a *required* check is a branch-protection setting this document cannot observe.
- `attest/reconcile_spec.py` now exists, but `tests/quality/test_redteam_contracts.py::test_reconcile_spec_refuses_protected_requirement_weakening` is still an unconditional skip stub. That skip is a visible gap, not coverage.

## Gate status (G1–G8)

Gate ids and wording come from the FACTORY red-team and rubrics draft (2026-10-24, section 3). **Proposed** means no CI job runs the gate. **Measured** means a hosted CI run observed the job passing; the run is linked. A Measured gate covers only the tests it names. It is not a coverage percentage and none is claimed.

| Gate | CI job | What runs | Status |
|---|---|---|---|
| G1 Stage 0 exit | `tests / G1 Stage-0 exit` | `scripts/run-gate.py` over the node ids in `tests/quality/g1_stage0_exit.txt`, in sections `simulated`, `omitted`, `wrong-version`, `incomplete` and a positive `control`. Fails if any id is missing, skipped, xfailed, xpassed or failing, or if a section is empty. | **Measured** for the listed ids only: [run 40293049793](https://github.com/SyberLabs/attest/actions/runs/40293049793) on `cf2c749` passed, with 32 ids / 37 cases, 0 skipped |
| G2 Quota floor | – | – | Proposed |
| G3 Adversarial pass | – | – | Proposed |
| G4 Positive controls | – | – | Proposed. G1 carries one receipt-level control only; the end-to-end Maintain Docs repair and matching reconciliation controls are not gated. |
| G5 Protected-text diff | – | – | Proposed |
| G6 Bridge mapping lint | – | – | Proposed |
| G7 Kane resource | – | – | Proposed |
| G8 Existing tests (attest) | `tests / pytest` | `python -m pytest -rsx` over `tests/` | **Measured** (attest suite only): [run 40293049793](https://github.com/SyberLabs/attest/actions/runs/40293049793) on `cf2c749` gave 281 passed, 3 skipped |

G1 scope limits. These paths are **not** covered by the G1 job:

- Wrong app/build version or wrong Kane run id. `RunReceipt` does not bind these fields yet (Gate D below).
- Live Kane runs. The Kane cases replay captured fixtures offline.
- Any SyberWork path. G8 here covers the attest suite only; the SyberWork suite is not run by this repository's CI.

Skips that G8 tolerates (pytest exits 0 with them, and the job prints their reasons with `-rsx`): the live compiler test (needs `ATTEST_LIVE` and network), the Flask end-to-end test (needs a local `.venv`), and the Reconcile Spec weakening stub above. None of them is in the G1 manifest.

Making a gate block merge also needs a repository admin to mark its check as required in branch protection. This PR does not change, and could not read, branch protection.

## Proposed required gates

### Gate A — repository baseline

**Command:** `python -m pytest -q`

Require the existing suite and `tests/quality/` tests to complete successfully on every PR/release candidate. Do not exclude security tests by directory or marker. Record the command and actual exit/result in the PR. This is the regression baseline, not a substitute for the component gates below.

### Gate B — quality contract tests

**Command:** `python -m pytest -q tests/quality`

Require all applicable assertions to pass against real component boundaries. A normal release gate must not accept an `XFAIL`, `XPASS`, or `SKIP` for a security case required by that component's release. A currently expected failure or skip is a visible gap until it is converted to a passing test by an implementation change; it must not be counted toward the red-team quota.

The quality tests in this slice exercise currently implemented Attest model/judge behavior. The simulation-versus-verification test now passes as a plain test (fake observations are typed `SimulationBrowserObservation` and judge `unverifiable`); it is no longer an expected failure. The protected-requirement weakening test is still an unconditional skip; the skip is a stub, not exercised coverage. Receipts bind source version and content digest; they do not bind app/build version or Kane run ids, so those scenarios cannot be reported as exercised.

### Gate C — per-component release quota

**Command:** `python -m pytest -q tests/quality/<component-suite>` (suite names to be established with each component implementation)

For each component being released, require the minimum per-category adversarial scenarios from [the N5 red-team quota](FACTORY-REDTEAM-QUOTA-2026-10-24.md). Assert the safety outcome for each case. Fail the release if a required category is untested, skipped, xfailed, or covered only by a fake that bypasses the boundary under test. For missing components, this gate is pending—not green.

### Gate D — exact version and evidence integrity

For any release that emits verified evidence or reconciles an external effect, require deterministic tests that tamper independently with repair/case identity, source digest, app/build version, Kane pack/run identifiers, proposal/approval binding, claim/step mapping, and destination/case binding. Each mismatch must fail closed, and a fresh valid evidence set must be required before success. Until a `RunReceipt` binds those fields and code enforces them, this gate is blocked for verified/reconciled releases.

### Gate E — simulation and incomplete work

Require tests showing that fake/echo, dry-run, cached-only and partial/incomplete executions cannot mint a verified receipt or advance an external effect to reconciled. Require incomplete compilation, missing terminal events, invalid evidence, ambiguous authority and budget exhaustion to remain non-success. The SyberWork observation bridge must initialize `verified: false`; only successful destination reconciliation for the exact case/effect/version may promote it. Do not infer this gate from a passing CLI exit code alone.

### Gate F — static and integration checks

Add type/lint/build or live-service jobs only when the repository adopts those tools/contracts. Do not label an unconfigured tool or unauthenticated mock as a measured gate. Live Kane and destination integration checks must use an explicitly scoped test account/environment; secrets must not be logged. A live-service check that cannot run is reported as not run, not passed. Unit CI may use deterministic fixtures, but must retain the boundary contract tests and cannot claim live verification from those fixtures.

## MVP gate order

Apply gates in the Kane Factory MVP order from issue #10; a later capability cannot bypass an earlier control:

1. **Identity:** distinguish repair/case identity from content digests; a patched document must not reuse the original content identity.
2. **Fail closed:** incomplete compilation and simulated execution cannot issue a proven receipt.
3. **Authority-gated repair:** require an explicit, scoped `AuthorityDecision` and an `Approval` bound to the exact proposal digest; refuse to weaken a protected promise and emit an implementation handoff.
4. **Receipts:** issue a `RunReceipt` binding repair/case identity separately from source digest, app/build identity, Kane pack and run ids, proposal, approval and fresh evidence.
5. **Execution isolation:** constrain shell recipes to an isolated workspace and prevent Kane from running past a failed prerequisite.
6. **SyberWork observation bridge:** consume a live Attest receipt as evidence input; set `verified: false` and keep the effect unknown until destination reconciliation verifies the exact case/effect/version.
7. **Reconcile Spec:** diagnosis and handoff only; no repair authority.

This PR specifies gates for that sequence; it does not implement these capabilities.

## Required gate report

For each candidate, record: commit/release identity; exact commands; pass/fail/skip/xfail results; which component/category scenarios ran; whether those scenarios used real code, deterministic fakes, or live integrations; and unresolved blocks. Never translate a suite exit of zero into claims about untested identity binding, production deployment, external effects, credentials, or UI behavior.
