# G4 Positive controls gate

FACTORY-REDTEAM-AND-RUBRICS §3 G4: block merge when "the allowed Maintain Docs repair or the matching reconciliation fails". §2: one allowed Maintain Docs repair succeeds end to end, so a product that refuses everything cannot pass the adversarial gates (G1/G3).

**Check:** `g4-positive-controls / G4 Positive controls` (`.github/workflows/g4-positive-controls.yml`). It is a separate workflow, so it doesn't touch `tests.yml` (G8/G1) or the other gate workflows. It is not a required check; that is for the repo admins.

**Stacking:** the controls themselves (`tests/test_positive_controls.py`) come from PR #31, which is not on `main` yet. This branch stacks on #31's tip and does not change that file. Without it the gate fails closed (`positive-control file is missing`), so the job must not land on `main` before #31.

**Status:** Measured (attest, scoped), from hosted run output only. Each run below is on this PR's branch, stacked on #31 (`a65975b`):

- Green: [run 40303204215](https://github.com/SyberLabs/attest/actions/runs/40303204215) on `c57b037`: 28 cases passed (16 positive-control ids from #31 plus 12 gate cases), every section non-empty.
- Fails closed, product refuses promotion: [run 40303296190](https://github.com/SyberLabs/attest/actions/runs/40303296190) on `83fedca`, a temporary commit that made `promote_workflow` raise for every call. `md-control` and `kane-control` failed (2 failed, 26 passed); twins and mutants still passed.
- Fails closed, controls absent: [run 40303402812](https://github.com/SyberLabs/attest/actions/runs/40303402812) on `b1c2e1a`, which removed `tests/test_positive_controls.py` for one run: `positive-control file is missing`, all 16 control ids `not run`.
- Green again: [run 40303476442](https://github.com/SyberLabs/attest/actions/runs/40303476442) on `c0654e9`, after both temporary changes were reverted (tree identical to `c57b037`): 28 passed.

The scope is the listed ids only. See "Not covered" below. No coverage percentage is claimed.

Overlap with G8: the promotion mutant also failed the existing `pytest` job (5 failed, including existing workflow promotion tests), so for that mutant G4 and G8 both block. What G4 adds is a fixed list that can't be deleted, skipped or xfailed without failing: with the controls file removed, `pytest` failed only because this PR's own `test_manifest_lists_every_collected_positive_control` is in the suite.

## What fails the job

`python scripts/g4-positive-controls.py` runs every node id in `tests/quality/g4_positive_controls.txt` with `xfail_strict=true` and reads the junit report. The job fails when:

- a test file the manifest names is not in the checkout (the controls are absent),
- a listed id is missing, deselected, skipped, errored, failed, xfailed or xpassed,
- a required section is empty: `md-control`, `md-twin`, `md-mutant`, `kane-control`, `kane-twin`, `kane-mutant`, `rs-control`, `rs-twin`, `rs-mutant`, `gate`,
- pytest exits non-zero for any other reason.

Parametrized mutants are listed one by one, so dropping one fails the gate. `test_manifest_lists_every_collected_positive_control` fails when a control is added to or renamed in `tests/test_positive_controls.py` without a manifest update. The job writes a per-section table of listed and passed ids to the job summary. These are counts of listed ids, not coverage figures.

## What is enforced

| Section | Ids | What it shows |
|---|---|---|
| `md-control` | PC-MD-01 | `prove` → `run_loop` → proposal → `approve_workflow` (6 digests bound) → `resume_workflow` (runner `kane`, `c01` proven, receipt digest recomputes) → `promote_workflow` → fresh `prove` with `c01` proven. Source equals `candidate_digest`, only target-span lines changed, the protected line is byte-identical. |
| `md-twin` | 2 | The agent also edits the protected promise → `rejected`, approval refused. Policy protects the target → `handoff_implementation`, no proposal. |
| `md-mutant` | 4 | Loop policy denies all, patch authority denies all, validation never proves, promotion refused: each makes PC-MD-01 fail. |
| `kane-control` | PC-KR-01 | The same workflow with `KaneBrowserRunner` for baseline and validation, only its kane-cli subprocess replaced by the recorded `witness_stale` capture. Stale `c08` is repaired, validated on the evidence pack and promoted. |
| `kane-twin` | 1 | A label Kane did not observe → validation `rejected` with `c08:stale`, source unchanged. |
| `kane-mutant` | 3 | Kane steps always fail, patch authority denies all, promotion refused. |
| `rs-control` | PC-RS-01 | `attest reconcile-spec` without `--apply` exits 0 with every orthogonal state set, `repair_state=handoff`, `handoff_state=ready` with no blocking reasons, `source_state=matched` with the contract's sha256, both evidence pointers in JSON and Markdown, the contract unchanged, and no `reconciled`. |
| `rs-twin` | 7 | `--apply` → exit 2, `refused`. A weakening `proposed_protected_text` → exit 2, `refused`. No evidence pointers → exit 3, `blocked` (`c01:evidence_missing`). The contract states a stronger requirement than the submitted text → exit 3, `blocked` (`c01:protected_text_not_in_source`), the contract's text in the handoff. The contract says it is superseded → exit 3, `blocked` (`c01:source_not_governing:superseded`). The contract's change log says SEC-4 was replaced → exit 3, `blocked` (`c01:source_not_governing:prose`). The reference names a weaker copy while the contract sits under the same root → exit 3, `blocked` (`c01:requirement_differs_elsewhere`), the contract's text in the handoff. |
| `rs-mutant` | 6 | `diagnose` refuses everything. Every request gets a blocking reason. The source check finds no requirement in any document. Every document is flagged not governing. Every sentence reads as withdrawing the requirement. Every requirement is found stated differently elsewhere. |
| `gate` | 10 functions | The runner fails on a missing file, a missing, skipped, failed or errored id, an empty section, an empty manifest, and a manifest that drifts from the control module. |

## Not covered

- **The second §2 control, "one matching destination lookup reconciles"**, belongs to the SyberWork bridge (§0, R8). It isn't in this repo, so G4 here enforces only the Maintain Docs half of the G4 row plus the Kane and Reconcile Spec paths. The bridge control stays Proposed on the SyberWork side.
- **Reconcile Spec promotion:** on `main` Reconcile Spec is diagnosis only (`can_apply_protected_text` and `can_promote_protected_text` are `False`). Its positive outcome is a complete handoff that is not refused, not a promoted repair. Nothing routes an unprotected Reconcile Spec conflict to Maintain Docs yet.
- **Live Kane:** PC-KR-01 runs the real runner code on a recorded kane-cli 0.9.0 capture. It doesn't prove a live Kane run, an app/build version binding or a Kane run id binding.
- **Mutants are in-process monkeypatches** of named product stages, not a mutation-testing run over the product source. They show that the controls catch a blanket refusal at those stages only.
- **Twins** each change one input. They don't replace the §1 adversarial cases, which are G2/G3's scope.
- Platform: `ubuntu-latest`, Python 3.11 only.
