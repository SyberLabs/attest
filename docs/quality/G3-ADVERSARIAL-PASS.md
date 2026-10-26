# G3 Adversarial pass gate

FACTORY-REDTEAM-AND-RUBRICS §3 G3: block merge when "any §1 case fails R1–R9". §2: a case passes only if every rubric line it applies holds; there is no partial credit.

**Check:** `g3-adversarial-pass / G3 Adversarial pass` (`.github/workflows/g3-adversarial-pass.yml`). It is a separate workflow, so it doesn't touch `tests.yml` (G8/G1) or the G5/G7 workflows.

**Status:** From hosted run output only:

- **On `main` (this PR, #37): INACTIVE.** [Run 40302187220](https://github.com/SyberLabs/attest/actions/runs/40302187220) reports that no §1 case ran, because `main` has no inventory yet. That is not a pass. G3 on `main` stays Proposed until #26 merges and a run on `main` enforces.
- **Measured (attest, scoped) over the #26+#30 inventory**, through verify-only draft #38 (the #37 G3 commits on top of #30's head `214c12e`):
  - Green: [run 40302198731](https://github.com/SyberLabs/attest/actions/runs/40302198731). 70 active adversarial cases and 1 active control (MD-CTRL-01) passed, with 0 active cases not passing. The 7 xfail and 3 stub cases are listed as open gaps. pytest gave 71 passed, 3 skipped, 7 xfailed.
  - Fails closed: [run 40302405267](https://github.com/SyberLabs/attest/actions/runs/40302405267). KR-DS-03, a real open product gap (`find_pack` falls back to the newest pack for an unknown session id), was marked `active`. The job failed with `KR-DS-03 (active, R3,R5) did not pass`.
  - Green again after the revert: [run 40302533912](https://github.com/SyberLabs/attest/actions/runs/40302533912).

  The scope is the active cases in that inventory at that commit. G3 over *every* §1 case is not met: 10 gaps remain (BR-AB-03, KR-COST-01, KR-DS-03, KR-ISO-02, KR-ISO-06, KR-PI-04, RS-PI-02 xfail; KR-ISO-04, KR-PI-03, KR-SIM-03 stub). No coverage percentage is claimed.

## Enforced scope

The §1 case inventory from PR #26: one JSON file per case under `tests/redteam/cases/<component>/<ID>.json`, tagged `component`, `category`, `rubrics` (R1–R9) and `status`, with a test bound to it by `@case("ID")` (which applies `@pytest.mark.redteam(case_id=..., status=...)`). The gate runs `pytest -m redteam tests/redteam` with `xfail_strict=true` and maps each test back to its case through that marker.

Only cases with `"status": "active"` are enforced. `xfail` (an observed gap, strict xfail) and `stub` (skipped, no executable check) cases are listed in the job summary as open gaps. They never count as G3 passes. G3 over *every* §1 case holds only when no gap is left; until then a green job means "every active case passed", nothing more.

## What fails the job

`python scripts/g3-adversarial-pass.py` exits 1 when:

| Condition | Why |
|---|---|
| An active case fails, errors (including fixture setup or teardown), is skipped, xfails or xpasses | A skip or xfail of an active case is not a pass |
| An active case has no bound test | A case file alone is not a check |
| An `xfail` case does not xfail, or a `stub` case is not skipped | The file's status no longer matches what the test does. Promote a fixed gap to `active`, or demote a broken one, in the same PR |
| A test's `redteam` marker names an unknown case, or its marker status differs from the case file | Outcomes must map to exactly the case the file describes |
| A case file is malformed: id not matching its file name, component not matching its directory, unknown category or status, rubrics empty or outside R1–R9 | Fail closed on input the gate can't read |
| No active adversarial case, or no active positive control (`CONTROL`) | "Refuse everything" and "nothing ran" must not pass (§2 positive controls) |
| pytest exits non-zero (collection error, usage error, no tests) | |

The job writes a per-component table (active passed, controls passed, active not passing, xfail, stub), the rubric tags on passing active cases, and the open gaps to the job summary. These are counts of cases at that commit, not coverage figures.

## Activation on `main`

`main` at `b0989ac` has no `tests/redteam/cases`; the inventory lands with #26 (Maintain Docs, Reconcile Spec, Kane) and #30 (Kane remainder, SyberWork bridge, stacked on #26). Until then the job runs with `--allow-missing-inventory` and reports **INACTIVE**: exit 0, a notice annotation, and a summary saying no §1 case ran. INACTIVE is not a G3 pass and must not be read as one.

The flag only covers a missing directory. As soon as `tests/redteam/cases` exists, every rule above applies. After #26 merges, drop `--allow-missing-inventory` from the workflow so that deleting the inventory fails the job instead of going quiet.

## Relation to other gates

- **G2 Quota floor** (separate job, R18) counts the same case files against the §1 minimums. G3 does not check floors: it checks that the cases that exist and claim `active` pass. A component with zero cases is G2's failure, not G3's.
- **G4 Positive controls**: G3 requires at least one active `CONTROL` case in the inventory to pass (MD-CTRL-01 in #26). The end-to-end controls in #31 (`tests/test_positive_controls.py`) are not part of the inventory and are not run by this job; G8 runs them.
- **G7 Kane resource** removes `ShellSession._kill_listener` (#34). #26's KR-ISO-02 is a strict xfail on that code. If #34 merges first, KR-ISO-02 xpasses and G3 fails with "expected xfailed … update its status" until the case is promoted to `active`. That is intended.
- `tests/quality/test_redteam_contracts.py` holds earlier N5 red-team checks that are not §1 inventory cases. G8 runs them; G3 does not.

## Not covered

- Rubric lines are those each case file lists. §2 says R1 and R9 apply to every adversarial case, but some inventory cases tag only the rubric they target (for example KR-COST-0x list R7 only). G3 checks the assertions in each test, not that the tags are complete.
- Live Kane and live SyberWork destination runs. Inventory cases are offline; the stubs that need a live run stay gaps.
- Branch protection. Making the check required is a repository admin setting; this change does not set it.
