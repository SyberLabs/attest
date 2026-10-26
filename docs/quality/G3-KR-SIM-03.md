# G3 KR-SIM-03: cached Kane replay against a changed app build

**Status:** KR-SIM-03 is `active` (R1, R3, R5). The proof is local, offline, on recorded Kane captures. No coverage figure is claimed and no check is required on `main`.

#40 left KR-SIM-03 as a stub: "needs an app/build version on `BrowserObservation` and the receipt". #41 added the bindings, on the receipt and on the validation record, and #42 made the case active on those. #44 put the build on the observation itself: `BrowserObservation.build_digest`, stamped by `KaneBrowserRunner` with the build it measured before and after its own run, checked by validation and at promote.

#44 also noted that no product code turned an observation into `ReceiptEvidence`, so a receipt's evidence build was whatever its caller wrote. The branch stacked on #44 (`feat/kr-sim-03-active`) adds `BrowserObservation.receipt_evidence`, which copies the observation's build into the receipt evidence and never takes one from the caller. KR-SIM-03 now reads the build off the observations and builds its receipts with that method, so it holds on `BrowserObservation` and the receipt, the binding the #40 stub asked for.

## The case

`tests/redteam/cases/kane_runner/KR-SIM-03.json`. The body is `run_case` in `tests/redteam/_version_binding.py`. The real code paths are `KaneBrowserRunner`, the Maintain Docs workflow (`create_repair_proposal`, `approve_workflow`, `resume_workflow`, `promote_workflow`), `current_build_digest`, `BrowserObservation.receipt_evidence` and `attest/receipts.py`. Two things are stand-ins: kane-cli is replaced by the recorded `witness_stale` capture, and the compiler model is deterministic.

1. Kane observes the doc's two browser steps on build A, and the runner caches them. Both observations name build A. `c07` is a Kane step replayed from cache.
2. The app is redeployed (build B), and a repair is approved on B. Validation gets A's cached observations and starts no Kane run. It is `rejected` with `c07`/`c08:stale_kane_run` and `c07`/`c08:observation_build_mismatch`. The stored evidence still names build A. Promotion is refused and the doc is unchanged.
3. The same evidence as a receipt does not verify. The real receipt is built from the stored observations with `receipt_evidence`, bound to the run that made them. It names build A in a receipt for B, and both `RunReceipt` and `VerifiedRunReceipt` reject it as a different app/build version. Then each variant differs from a receipt that would verify in one binding:
   - the observation names build A
   - the observation has no build
   - the receipt build is stripped
   - the evidence names the earlier Kane run, and this validation started none
   - the run id is stripped

   To isolate the run binding, the test restamps a copy of the observation with build B. The product never produces that copy from a cached run. A plain `RunReceipt` also rejects the wrong build and the wrong run.
4. A redeploy lands while Kane runs: `unresolved`, no promotion. The runner saw the build change, so its observations name no build, and a `VerifiedRunReceipt` made from them is refused even for the run that made them.
5. Control: a fresh Kane run on the approved build validates. Its stored observations name the approved build, and the `VerifiedRunReceipt` built from them verifies. A refuse-everything product fails here.
6. That validated record is refused at promote after a further redeploy. Back on the approved build, it promotes, which shows the refusal came from the build binding.

## Proof (local)

Runs on this branch's tip (#44 + one product/case commit), Python 3.13 and 3.11:

- `pytest tests/redteam` gives 1 passed, 10 of 10 runs, about 0.5 s each.
- The full suite gives 309 passed, 3 skipped on both versions. The #44 tip alone gives 308 passed, 3 skipped; the extra test is the `receipt_evidence` unit test in `tests/test_models.py`.
- `scripts/g1-stage0-binding.py` exits 0 (33 passed, unchanged).

**Without the product change.** I swapped only `attest/` in a scratch copy (probe not committed):

- On #42's `attest/` (no observation build), the case fails: the observation has no `build_digest`.
- On #44's `attest/` without `receipt_evidence`, the case fails at the first receipt.

**Mutations.** I turned off one product check at a time. Each of these made the case fail:

- the runner's observation build stamp (#44)
- the runner's mid-run build change detection (#44)
- validation's `observation_build_*` check (#44)
- `receipt_evidence` dropping the observation's build, or taking one from the caller

Two mutations survive the case:

- #44's promote-time observation-build check. Reaching it needs a stored validation that passed with evidence observed on another build, which takes a tampered store. #44's G1 test `test_observation_build_promotion_refuses_evidence_not_observed_on_the_approved_build` covers it.
- `receipt_evidence` marking every observation complete. The case uses only passed observations. The new unit test in `tests/test_models.py` catches it.

The #41 mutations listed on #42 (stale-run marking, receipt build and run matches, verified-receipt bindings, promote-time build check) still apply: the case keeps those assertions.

**G3 over the full inventory.** I merged this branch into #45's head (`a2e73a2`, the #26 → #30 → #40 → #43 → #45 stack, which commits `scripts/g3-adversarial-pass.py`) in a throwaway worktree. I resolved it as described below. The merge isn't committed anywhere.

- `scripts/g3-adversarial-pass.py --root .` exits 0. kane_runner: 24 active adversarial passed, 0 not passing, 0 xfail, 2 stub. On `a2e73a2` alone: 23 passed, 3 stub.
- The other components are unchanged: maintain_docs 27 + 1 control, reconcile_spec 20, syberwork_bridge 7.
- The full suite gives 420 passed, 5 skipped, 2 xfailed. On `a2e73a2` alone: 374 passed, 6 skipped, 2 xfailed.
- These are counts at that merge, not coverage.

## Merging with the #26/#30/#40 stack

The inventory isn't on #41's base, so this branch ports the harness (from #42):

- `tests/redteam/__init__.py`, `conftest.py` and `_harness.py` are byte-identical to #40's head (#30's version), so git merges them cleanly with that stack.
- `cases/kane_runner/KR-SIM-03.json` is the #40 definition, flipped to `active`.

Two steps are left for when this branch and the stack meet:

1. **Conflicts.**
   - `KR-SIM-03.json` is an add/add conflict. Take this branch's version.
   - `attest/kane.py` `find_pack` conflicts between #40's KR-DS-03 fix and #41. Take #41's version, which is stricter and still passes KR-DS-03.
   - `NOTES.md` conflicts at the end of the file. Keep both sides.

   Checked against #45's head (`a2e73a2`): these were the only conflicts.
2. **The stub.** `test_inventory.py` requires each kane_runner case to be bound once, in `test_kane_runner.py`. Replace the stub `test_cached_replay_is_bound_to_app_version(rt_case)` with `(rt_case, tmp_path, monkeypatch)` calling `run_case`, import `run_case` from `._version_binding`, and delete `test_kane_runner_version_binding.py`.

   If the stub is left in, G3 fails loudly: `KR-SIM-03 (active, R1,R3,R5) did not pass` (checked on the merge above).

Alone, this branch's inventory has one case and no `CONTROL`. #37's gate therefore reports `no active positive control` here, which is correct: a single case can't rule out refuse-everything. The G3 proof is the merged run above. Land this after the #26 → #30 → #40 stack, or resolve as described.

## Gap table (rows touched here)

| Case | Rubrics | Outcome | Why |
|---|---|---|---|
| KR-SIM-03 | R1, R3, R5 | **active** | On `BrowserObservation.build_digest` (#44) and receipts built from it (`receipt_evidence`), plus #41's receipt and validation bindings. See above. |
| KR-ISO-02 | R7 | not on this stack | #43 makes it active on the #40 stack (0 kane_runner xfail at #45's head). This branch doesn't touch it. |
| KR-PI-03 | R6 | still stub | Needs live Kane against a hostile page to see Kane's tool calls. A recorded capture shows only the actions Kane took, not what a page steered it to try, so it can't show the invariant offline. |
| KR-ISO-04 | R7 | still stub | Needs live Kane in a disposable environment with filesystem and network probes. Recorded captures contain no probe results. |

G3 over every §1 case is still **not met**: KR-PI-03 and KR-ISO-04 remain, and both need live Kane.
