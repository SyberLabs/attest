# G3 KR-SIM-03: cached Kane replay against a changed app build

**Status:** KR-SIM-03 is `active` (R1, R3, R5). The proof is local, offline, on recorded Kane captures. No coverage figure is claimed and no check is required on `main`.

#40 left KR-SIM-03 as a stub: "needs an app/build version on `BrowserObservation` and the receipt". #41 added the bindings, on the receipt and on the validation record. `BrowserObservation` itself still has no build field. The case tests the bindings where #41 put them. (The branch stacked on #42, `feat/browser-observation-build-binding`, adds `BrowserObservation.build_digest`. With it, step 2 also fails with `<id>:observation_build_mismatch`. The case asserts only that its listed failures are present, so it passes unchanged.)

## The case

`tests/redteam/cases/kane_runner/KR-SIM-03.json`. The body is `run_case` in `tests/redteam/_version_binding.py`. The real code paths are `KaneBrowserRunner`, the Maintain Docs workflow (`create_repair_proposal`, `approve_workflow`, `resume_workflow`, `promote_workflow`), `current_build_digest` and `attest/receipts.py`. Two things are stand-ins: kane-cli is replaced by the recorded `witness_stale` capture, and the compiler model is deterministic.

1. Kane observes the doc's two browser steps on build A, and the runner caches them. `c07` is a Kane step replayed from cache.
2. The app is redeployed (build B), and a repair is approved on B. Validation gets A's cached observations and starts no Kane run. It is `rejected` with `c07:stale_kane_run` and `c08:stale_kane_run`. Promotion is refused and the doc is unchanged.
3. The same evidence as a receipt does not verify. Each variant differs from a receipt that would verify in one binding:
   - evidence names build A
   - the evidence build is stripped
   - the receipt build is stripped
   - the evidence names the earlier Kane run, and this validation started none
   - the run id is stripped

   A plain `RunReceipt` also rejects the wrong build and the wrong run.
4. A redeploy lands while Kane runs: `unresolved`, no promotion.
5. Control: a fresh Kane run on the approved build validates, and its `VerifiedRunReceipt` verifies. A refuse-everything product fails here.
6. That validated record is refused at promote after a further redeploy. Back on the approved build, it promotes, which shows the refusal came from the build binding.

## Proof (local, Python 3.13)

- **On this branch:** `pytest tests/redteam` gives 1 passed (10 of 10 runs, about 0.2 s each). The full suite gives 295 passed, 3 skipped. The #41 tip alone gives 294 passed, 3 skipped. The G1 gate script exits 0.
- **On `main`'s product code:** the step 1–2 scenario ends `promoted` and the doc is rewritten, with 0 Kane runs during validation. With #41's product code it ends `rejected`. (I swapped only `attest/` in a scratch copy for this probe; the probe isn't committed.)
- **Mutations:** I turned off one #41 check at a time. Each mutation made the case fail:
  - stale-run marking
  - post-run build check
  - receipt build match
  - receipt run match
  - verified-receipt build, evidence build and browser run id
  - promote-time build check

  A refuse-everything validation also failed the control.
  - The receipt-build-required check is caught only by its message. A receipt with no build always also has evidence with no build, so that check can't be isolated.
  - The case does not exercise #41's validation-vs-proposal `build_digest` and `kane_runs` checks at promote. Those need a tampered store, and #41's G1 cases cover them.
- **G3 over the full inventory:** this branch merged into #40's head (`8434de7`), with #37's `scripts/g3-adversarial-pass.py` (`fd9c0ed`) copied in unchanged and not committed:
  - Exit 0. kane_runner: 23 active adversarial passed (was 22), 0 not passing, 1 xfail, 2 stub (was 3).
  - The other components are unchanged: maintain_docs 27 + 1 control, reconcile_spec 20, syberwork_bridge 7.
  - The full suite gives 384 passed, 5 skipped, 3 xfailed, 0 xpassed.
  - #36's G2 script exits 0 with the same counts (kane_runner 20/20).
  - These are counts at that merge, not coverage.

## Merging with the #26/#30/#40 stack

The inventory isn't on #41's base, so this branch ports the harness:

- `tests/redteam/__init__.py`, `conftest.py` and `_harness.py` are byte-identical to #40's head (#30's version), so git merges them cleanly with that stack.
- `cases/kane_runner/KR-SIM-03.json` is the #40 definition, flipped to `active`.

Two steps are left for when this branch and the stack meet:

1. **Conflicts.**
   - `KR-SIM-03.json` is an add/add conflict. Take this branch's version.
   - `attest/kane.py` `find_pack` conflicts between #40's KR-DS-03 fix and #41. Take #41's version, which is stricter and still passes KR-DS-03.
2. **The stub.** `test_inventory.py` requires each kane_runner case to be bound once, in `test_kane_runner.py`. Replace the stub `test_cached_replay_is_bound_to_app_version(rt_case)` with `(rt_case, tmp_path, monkeypatch)` calling `run_case`, import `run_case` from `._version_binding`, and delete `test_kane_runner_version_binding.py`.

   If the stub is left in, G3 fails loudly: `KR-SIM-03 (active, R1,R3,R5) did not pass` (checked on the merge above).

Alone, this branch's inventory has one case and no `CONTROL`. #37's gate therefore reports `no active positive control` here, which is correct: a single case can't rule out refuse-everything. The G3 proof is the merged run above. Land this after the #26 → #30 → #40 stack, or resolve as described.

## Gap table (rows touched here)

| Case | Rubrics | Outcome | Why |
|---|---|---|---|
| KR-SIM-03 | R1, R3, R5 | **promoted** | Executable on #41's receipt and validation bindings. See above. |
| KR-ISO-02 | R7 | still xfail (on #40) | This tip still has `ShellSession._kill_listener` (`attest/shell.py`). #34 (`b15868b`) removes it. The case flips to `active` in whichever of #34 and the #40 stack merges second. I didn't copy #34's product change here. |
| KR-PI-03 | R6 | still stub | Needs live Kane against a hostile page to see Kane's tool calls. A recorded capture shows only the actions Kane took, not what a page steered it to try, so it can't show the invariant offline. |
| KR-ISO-04 | R7 | still stub | Needs live Kane in a disposable environment with filesystem and network probes. Recorded captures contain no probe results. |

G3 over every §1 case is still **not met**: three gaps remain.
