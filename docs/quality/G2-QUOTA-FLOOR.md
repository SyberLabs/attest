# G2 Quota floor gate

FACTORY-REDTEAM-AND-RUBRICS §3 G2: block merge when "red-team cases per component × category fall below the §1 minimums". The case inventory is `tests/redteam/cases/<component>/<ID>.json` from #26 and #30. This gate counts it. It does not judge whether the cases pass their rubrics; that is G3.

**Check:** `g2-quota-floor / G2 Quota floor` (`.github/workflows/g2-quota-floor.yml`). It is a separate workflow, so it doesn't touch `tests.yml` (G8/G1) or the G5/G7 workflows. It is not a required check.

**Status:** Measured (attest, scoped), from hosted run output only, on PR #36 (stacked on #30 → #26):

- Green: [run 40300539117](https://github.com/SyberLabs/attest/actions/runs/40300539117) on `7f76dc7` (gate only, case inventory unchanged from #30 `214c12e`). Gate controls 35 passed. Quota floor PASS with the counts below.
- Fails closed: [run 40300679526](https://github.com/SyberLabs/attest/actions/runs/40300679526) on `b7ce6c0`, which demoted MD-PI-04 to `stub`. Quota floor FAIL: `maintain_docs/PI: 3 counted < floor 4`. Reverted in the next commit.

Scope: the enforced cells and themes below. Not required on `main`; on `main` alone (no `tests/redteam/`) the gate fails, so enforcement there starts when #26 and #30 merge. No coverage percentage is claimed.

## What fails the job

| Step | Command | Fails when |
|---|---|---|
| Gate controls | `python -m pytest -rsx tests/quality/test_g2_quota_floor.py` | The counter accepts a stub, an xfail under `--count active`, a case with no bound test, a case whose test didn't end as its status says, a missing theme, a stale exemption, or a lowered harness quota. |
| Quota floor | `python scripts/g2-quota-floor.py` | Any enforced cell is under its §1 floor, any enforced §1 theme has no counted case, `tests/redteam/_harness.py` `QUOTAS`/`REQUIRED_THEMES` differ from the pinned §1 values, a pending exemption is already met, a case file is malformed, collection errors, or pytest exits with an internal/usage error. A tip with no `tests/redteam/` fails: every cell is 0. |

The quota step runs even if the controls fail, so one run reports the real counts. It writes a per-cell table to the job summary.

## Counting rule

A case counts toward its cell only when both hold:

1. Its status is `active` or `xfail`. `stub` never counts. This is the rule in #26's `tests/redteam/_harness.py` and `test_inventory.py`: an xfail case has an executable check that observes a real gap, so it exists for G2 and fails G3.
2. `tests/redteam` ran in the same job, a test bound to the case with `@case(ID)` was collected, and it ended the way the status says: `active` passed, `xfail` xfailed under `xfail_strict=true`. A missing, skipped, errored, failed or XPASS test means the case is not counted.

The floors are pinned in `scripts/g2-quota-floor.py` from §1, and the gate checks that the harness `QUOTAS` equal them. Lowering the harness quota fails G2.

**Conflict to resolve (captain):** `docs/quality/CI-GATES.md` (Gate B/C) and `docs/quality/FACTORY-REDTEAM-QUOTA-2026-10-24.md` on `main` say xfail must not count toward a quota. #26's harness and FACTORY's G2/G3 split count xfail for G2. This gate follows #26. `python scripts/g2-quota-floor.py --count active` applies the stricter rule; the job summary always shows the active-only count beside the counted one. Under active-only, this tip is short in 4 cells (see below).

The repo's `FACTORY-REDTEAM-QUOTA-2026-10-24.md` also proposes a different table (2 per category, 7 categories). G2 enforces the FACTORY-REDTEAM-AND-RUBRICS §1 table, which is the one #26/#30 implement.

## Scope enforced

| Component | Enforced | Not enforced |
|---|---|---|
| Maintain Docs | all 6 categories, all 7 themes | – |
| Reconcile Spec | all 6 categories, all 5 themes | – |
| Kane runner | all 7 categories, all 7 themes | – |
| SyberWork bridge | PI, AB; themes `receipt_as_reconciliation_proof`, `caller_success_or_evidence_fields` | DS, SIM, SCH, COST, REC and the other 6 themes: the SyberWork side is not in this repository (§3 rollout: Bridge quota with N3/N4) |

Pending exemptions live in `PENDING_CELLS` / `PENDING_THEMES` in the script, each with its reason. An exemption that becomes met fails the gate until it is removed, so it cannot outlive the gap.

## Counts on this tip

From run 40300539117 (the local run gave the same table). Branch tip stacked on #30 (`214c12e`) with no change to any case. Counted = active + xfail whose test ended as its status says.

| Component | Cat | Floor | active | xfail | stub | Counted | Active-only | Result |
|---|---|---:|---:|---:|---:|---:|---:|---|
| maintain_docs | PI | 4 | 4 | 0 | 0 | 4 | 4 | met |
| maintain_docs | AB | 5 | 7 | 0 | 0 | 7 | 7 | met |
| maintain_docs | DS | 4 | 5 | 0 | 0 | 5 | 5 | met |
| maintain_docs | SIM | 3 | 4 | 0 | 0 | 4 | 4 | met |
| maintain_docs | SCH | 3 | 5 | 0 | 0 | 5 | 5 | met |
| maintain_docs | COST | 2 | 2 | 0 | 0 | 2 | 2 | met |
| reconcile_spec | PI | 4 | 3 | 1 | 0 | 4 | **3** | met (xfail RS-PI-02) |
| reconcile_spec | AB | 6 | 7 | 0 | 0 | 7 | 7 | met |
| reconcile_spec | DS | 2 | 3 | 0 | 0 | 3 | 3 | met |
| reconcile_spec | SIM | 2 | 2 | 0 | 0 | 2 | 2 | met |
| reconcile_spec | SCH | 3 | 3 | 0 | 0 | 3 | 3 | met |
| reconcile_spec | COST | 1 | 1 | 0 | 0 | 1 | 1 | met |
| kane_runner | PI | 4 | 3 | 1 | 1 | 4 | **3** | met (xfail KR-PI-04; stub KR-PI-03 not counted) |
| kane_runner | AB | 1 | 1 | 0 | 0 | 1 | 1 | met |
| kane_runner | DS | 3 | 2 | 1 | 0 | 3 | **2** | met (xfail KR-DS-03) |
| kane_runner | SIM | 3 | 3 | 0 | 1 | 3 | 3 | met (stub KR-SIM-03 not counted) |
| kane_runner | SCH | 2 | 2 | 0 | 0 | 2 | 2 | met |
| kane_runner | COST | 3 | 4 | 1 | 0 | 5 | 4 | met |
| kane_runner | ISO | 4 | 3 | 2 | 1 | 5 | **3** | met (xfail KR-ISO-02, KR-ISO-06; stub KR-ISO-04 not counted) |
| syberwork_bridge | PI | 3 | 3 | 0 | 0 | 3 | 3 | met |
| syberwork_bridge | AB | 3 | 3 | 1 | 0 | 4 | 3 | met |
| syberwork_bridge | DS | 3 | 0 | 0 | 0 | 0 | 0 | below floor, not enforced |
| syberwork_bridge | SIM | 2 | 0 | 0 | 0 | 0 | 0 | below floor, not enforced |
| syberwork_bridge | SCH | 4 | 0 | 0 | 0 | 0 | 0 | below floor, not enforced |
| syberwork_bridge | COST | 1 | 0 | 0 | 0 | 0 | 0 | below floor, not enforced |
| syberwork_bridge | REC | 6 | 0 | 0 | 0 | 0 | 0 | below floor, not enforced |

Toward §1 min totals (each cell capped at its floor): Maintain Docs 21/21, Reconcile Spec 18/18, Kane runner 20/20, SyberWork bridge 6/22.

These are case counts, not coverage figures.

## Not covered

- **Rubric pass/fail (G3).** Seven counted cases are strict xfails (observed gaps: RS-PI-02, KR-PI-04, KR-DS-03, KR-COST-01, KR-ISO-02, KR-ISO-06, BR-AB-03). G2 counts them; G3 must fail on them.
- **#31 positive controls (G4).** `tests/test_positive_controls.py` is not a red-team case file and has no §1 category, so G2 does not count it. The one `CONTROL` case in the inventory (MD-CTRL-01) is outside the §1 table too.
- **Case quality.** G2 checks that a bound test exists and ends as labelled. It doesn't check that two cases in a cell vary the attack rather than repeat a fixture.
- **SyberWork bridge** beyond PI/AB, as above.
