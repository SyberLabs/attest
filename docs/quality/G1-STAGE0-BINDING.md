# G1 Stage-0 version+Kane binding

**Status:** Measured (attest, scoped), from hosted Actions output on PR #41. No coverage percentage is claimed, and the job isn't a required check on `main`.

- Green: job `G1 Stage-0 version+Kane binding`, [run 40304926335](https://github.com/SyberLabs/attest/actions/runs/40304926335) on `c7349a1`. 21 passed: version 7, kane-run 12, control 2. It was also green on `6e6b2f1` ([run 40304653221](https://github.com/SyberLabs/attest/actions/runs/40304653221)).
- Fails closed: [run 40304782316](https://github.com/SyberLabs/attest/actions/runs/40304782316) on `bffed83`. That commit put `main`'s newest-pack fallback back into `find_pack` and was reverted in `c7349a1`. Five kane-run cases failed and the job failed.
- Scope: only the tests listed below, offline on recorded Kane captures. No live Kane.
- **`observation-build` section** (#44, stacked on #42): **Measured (scoped)** from hosted Actions output on PR #44.
  - Green on `2ebef93`: [run 40312676697](https://github.com/SyberLabs/attest/actions/runs/40312676697), 33 passed (version 7, kane-run 12, observation-build 11, control 3). `tests / pytest` gave 308 passed, 3 skipped ([run 40312676852](https://github.com/SyberLabs/attest/actions/runs/40312676852)).
  - Fails closed on `89237c4`: [run 40312713440](https://github.com/SyberLabs/attest/actions/runs/40312713440). That commit made validation and promotion ignore the observation build. Six observation-build cases failed and the job failed; `tests / pytest` gave 6 failed, 302 passed ([run 40312713353](https://github.com/SyberLabs/attest/actions/runs/40312713353)).
  - Green again on the revert `db093ad`: [run 40312768841](https://github.com/SyberLabs/attest/actions/runs/40312768841), 33 passed. `tests / pytest` gave 308 passed, 3 skipped ([run 40312768879](https://github.com/SyberLabs/attest/actions/runs/40312768879)).
- **Receipt evidence from observations** (#47, stacked on #46): **Measured (scoped)** from hosted Actions output on PR #47. The `observation-build` section goes from 11 to 17 cases and `control` from 3 to 4.
  - Green on `4aa9429`: [run 40316904818](https://github.com/SyberLabs/attest/actions/runs/40316904818), 40 passed (version 7, kane-run 12, observation-build 17, control 4). `tests / pytest` gave 317 passed, 3 skipped ([run 40316904857](https://github.com/SyberLabs/attest/actions/runs/40316904857)).
  - Fails closed on `39d5cc2`: [run 40317583048](https://github.com/SyberLabs/attest/actions/runs/40317583048). That commit put `attest/models.py` and `attest/runner.py` back to #46's tip. Four observation-build cases failed (both simulated-receipt cases, unbound-but-passed, and simulated-names-no-build) and the job failed. `tests / pytest` gave 5 failed, 312 passed ([run 40317583015](https://github.com/SyberLabs/attest/actions/runs/40317583015)).
  - Green again on the revert `563972f`: [run 40317922106](https://github.com/SyberLabs/attest/actions/runs/40317922106), 40 passed. `tests / pytest` gave 317 passed, 3 skipped ([run 40317922303](https://github.com/SyberLabs/attest/actions/runs/40317922303)).

- **Kane run on the observation** (#49, PR to `main`): **Measured (scoped)** from hosted Actions output on PR #49. `kane-run` goes from 12 to 24 cases, `version` from 7 to 9, `observation-build` from 17 to 18 and `control` from 4 to 5.
  - Green on `be9bf48` (#49's product commit `5170fd2` merged with `main` at `664f166`): [run 40332835946](https://github.com/SyberLabs/attest/actions/runs/40332835946), 56 passed. `tests / pytest` gave 645 passed, 4 skipped, 2 xfailed ([run 40332835705](https://github.com/SyberLabs/attest/actions/runs/40332835705)).
  - Fails closed on `eedcc45`: [run 40332888887](https://github.com/SyberLabs/attest/actions/runs/40332888887). That commit turned off #49's hooks in `models.py`, `receipts.py`, `runner.py` and `workflow.py`. 15 failed, 41 passed, and the job failed. `tests / pytest` gave 16 failed, 629 passed ([run 40332887171](https://github.com/SyberLabs/attest/actions/runs/40332887171)).
  - Green again on the revert `4f144d6`: [run 40332954244](https://github.com/SyberLabs/attest/actions/runs/40332954244), 56 passed. `tests / pytest` gave 645 passed, 4 skipped, 2 xfailed ([run 40332954197](https://github.com/SyberLabs/attest/actions/runs/40332954197)).
FACTORY §3 lists G1 as Measured (scoped) through #27, but names a gap that #27 does not cover: "app/build version and Kane run-id binding, live Kane". This gate covers the first two. It does not cover live Kane.

## What the gate checks

Job `G1 Stage-0 version+Kane binding` in `.github/workflows/g1-stage0-binding.yml` runs `python scripts/g1-stage0-binding.py`. The script runs `tests/quality/test_g1_stage0_binding.py` with `xfail_strict=true` and passes only when every test case ran and passed. Any skip, xfail, xpass, failure or error fails the job. So does an empty section, or a test whose name doesn't put it in a section.

| Section | Test prefix | Fails closed when |
|---|---|---|
| `version` | `test_version_` | evidence names another build, the receipt or an evidence record names no build, the build changes while Kane is running, or a stored validation's build is swapped or removed before promotion. Also (#49): a plain `RunReceipt` that names a build or Kane runs holds complete browser evidence naming none |
| `kane-run` | `test_kane_run_` | evidence names a Kane run the receipt didn't start, or names no run; a pack from another session sits in the evidence store; the run reports no session id; validation reuses an observation cached from an earlier run; a stored validation's run bindings are dropped, nulled or removed before promotion. Also (#49): the runner doesn't stamp an observation with the run whose pack backs it; an observation names another run, or none, at validation; stored evidence's observation run is swapped or removed, or the claim's run binding is swapped, before promotion; stored evidence is simulation-only, `unbound:` or `failed` at promotion; `receipt_evidence` pairs a cached observation (live or stored) with a fresh caller run id; a fake runner named `kane` validates |
| `observation-build` | `test_observation_build_` | a browser observation names no build (Kane redeployed mid-run, a URL doc, or a runner that drops it), or names a build other than the approved one (cached from an earlier build, or swapped), at validation, at promotion (stored evidence's observation build swapped, removed, or the evidence dropped), or in a receipt built from those observations. Also (#47): a build deployed just before the Kane run and rolled back just after it, which validation's own build checks don't see; a fake runner's scripted build (and, #49, its scripted Kane run); and receipt evidence that `receipt_evidence` builds from a simulation-only or unbound observation, live or as stored |
| `control` | `test_control_` | a receipt with matching build and run doesn't verify, or a fresh Kane run on the approved build doesn't validate and promote (with each observation naming the approved build), or a receipt carrying those observations' builds doesn't verify, or (#47) a receipt whose evidence `receipt_evidence` builds from a validated Kane run's stored observations doesn't verify, or (#49) stored observations that name their bound run don't promote and verify. This stops a refuse-everything product from passing |

`tests/quality/test_g1_stage0_binding_gate.py` checks that the runner fails on each of those conditions.

## Product hooks (narrow fixes on this branch)

On `main`, nothing bound evidence to an app/build version or a Kane run. With the real `KaneBrowserRunner` (only the kane-cli subprocess replaced by a recorded capture), these three cases went all the way to `promoted`:

- an observation cached from an earlier run
- a pack from another session
- a run that reported no session id

The fixes:

- **`attest/receipts.py`**: `ReceiptEvidence` gains `build_digest` and `kane_run_id`. `RunReceipt` gains `build_digest` and `kane_run_ids`. Any receipt rejects evidence from another build, or from a Kane run it didn't start. A `VerifiedRunReceipt` also requires a build on the receipt and on every evidence record, plus a Kane run id on every browser record. The new fields are optional on `RunReceipt`, so unverified records can still describe incomplete runs.
- **`attest/kane.py`**: `find_pack` binds a pack only when its `result.yaml` has `external_id.session_id` equal to the session id the run reported. It used to fall back to the newest fresh pack, and its match was a substring test. Without a session id, no pack is bound. A run with no bound pack gives an observation marked `unbound:` and downgraded from `passed`. `KaneBrowserRunner.run_ids` records which session backs each claim's observation.
- **`attest/verdict.py`**: an `unbound:` observation is `unverifiable`, never `proven`. The constant `UNBOUND_NOTE_PREFIX` sits next to `SIMULATION_NOTE_PREFIX` in `attest/runner.py`.
- **`attest/workflow.py`**: validation records `build_digest` and `kane_runs` (claim id to session id). A browser claim whose evidence isn't backed by a Kane run started during this validation fails as `<id>:stale_kane_run`. The build digest is checked again after the run, and a change makes the result `unresolved`. Promotion refuses a validation whose `build_digest` isn't the approved one, or whose browser claims lack a run binding. Validations recorded before this change carry neither field, so they have to be validated again before they can be promoted.

### Browser observations carry their build (stacked on #42)

#41 bound the receipt and the validation record, but the validation's `build_digest` was copied from the proposal. Nothing on the observation said which build Kane saw. Now:

- **`attest/models.py`**: `BrowserObservation.build_digest` is optional and must be a sha256 hex digest, the same pattern as `ReceiptEvidence.build_digest`. `SimulationBrowserObservation` inherits it.
- **`attest/kane.py`**: `prepare` remembers the doc path, and `KaneBrowserRunner.build_digest()` is `current_build_digest` of that path. `run_group` measures it before and after the kane-cli subprocess, and stamps every observation from the run with it. If the build changed during the run, if the doc is a URL, or if measuring fails, the observation gets `build_digest=None` and a `build: unknown or changed during this Kane run` note. A cached observation keeps the build it was taken on.
- **`attest/workflow.py`**: validation fails a browser claim whose observation names no build (`<id>:observation_build_missing`) or another build (`<id>:observation_build_mismatch`), next to `<id>:stale_kane_run`. Promotion reads each browser claim's stored `evidence[id].observation.build_digest` and refuses unless it is the approved build: "validation browser evidence was not observed on the approved app/build version; --apply refused". Validations stored before this change have no observation build, so they have to be validated again.
- **Receipts**: no product code turns an observation into `ReceiptEvidence` yet. The tests fill `ReceiptEvidence.build_digest` from the observation, and #41's receipt checks reject a `None` (verified receipts) or a different build (any receipt).

### Receipt evidence keeps the observation's origin (#47, stacked on #46)

#46 added `BrowserObservation.receipt_evidence`, which takes the build from the observation. It marked every passed observation complete and not simulated. A `FakeBrowserRunner` script (`--fake-browser` JSON) can carry `build_digest`, and the fake runner passed it through. So a simulation-only observation, with a build and a caller-supplied run id, became browser evidence that `VerifiedRunReceipt` accepted. The same happened after the store round trip, which turns the subclass into a plain `BrowserObservation` with a `simulation-only:` note. I reproduced this on #46's tip before the fix. Now:

- **`attest/models.py`**: `receipt_evidence` applies the verdict's rules. A simulation-only observation (the `SimulationBrowserObservation` subclass, or a `simulation-only:` note) gives `simulated=True, complete=False`. An unbound one (an `unbound:` note) gives `complete=False`, even if its status was put back to `passed`. `RunReceipt` and `VerifiedRunReceipt` already refuse both under `EvidenceState.COMPLETE`. The prefixes are imported inside the method because `attest/runner.py` imports `attest/models.py`.
- **`attest/runner.py`**: `FakeBrowserRunner` drops a scripted `build_digest`, because a simulation observed no build.

New cases in the gate: four `observation-build` tests (seven cases with parameters, counting the build-rollback test below) and one `control` test.

- `test_observation_build_simulated_observation_names_no_build`
- `test_observation_build_receipt_from_a_simulated_observation_does_not_verify` (live and stored). It is still recordable as `EvidenceState.SIMULATED`.
- `test_observation_build_receipt_from_an_unbound_observation_does_not_verify`. One case uses a real pack-less Kane run. The other uses a test-made `passed` copy, which the product never produces.
- `test_observation_build_kane_run_on_a_build_rolled_back_before_validation_ends_is_rejected`. It needs no product change. Build B is deployed just before the Kane run and rolled back right after it, so validation's own `current_build_digest` checks see the approved build at both ends. The run is fresh and pack-bound. Only the runner's per-observation stamp names B. The result is `rejected` with only `c07`/`c08:observation_build_mismatch`, there is no promotion, and the receipt is refused. Of the mutations I ran, the only one that makes it fail is "validation ignores the observation build", and #44's tests catch that one too. So this test shows the scenario holds. It doesn't add mutation coverage.
- `test_control_receipt_evidence_from_a_validated_kane_run_verifies`. A `receipt_evidence` that marked everything simulated fails here.

Local mutations: I turned off one product line at a time and ran the G1 file, `tests/test_models.py` and `tests/redteam`. Each of these made at least one new test fail:

| Mutation | Tests that fail |
|---|---|
| `attest/` reset to #46 | 5 |
| simulation ignored | 3 |
| subclass checked, note not checked | 2 |
| unbound ignored | 2 |
| fake runner keeps the scripted build | 1 |
| `receipt_evidence` always simulated | 6, including KR-SIM-03 and both controls that build receipt evidence |

On the workflow path, #41 already catches the realistic cases: a cached replay through `stale_kane_run`, and a mid-run redeploy through the post-run build check. The new check rejects an observation that names the wrong build, or none, even when its Kane run is fresh and pack-bound (`test_observation_build_omitted_or_wrong_fails_validation`). It also gives a receipt a build measured where the page was observed, not one copied from the proposal.

Here the build identity is the existing `proposals.current_build_digest`: git HEAD, status, diff and untracked files, or path plus source digest when the source isn't versioned. The Kane run id is the `session_id` that `test_md_done` reports, which the pack's `result.yaml` repeats.

A build that changes mid-run was already refused at promotion on `main`, but the workflow was still recorded as `validated`. Now it stops at `unresolved`.

### Observations name their Kane run (#49, on `main` after #47)

#47 left the Kane run as the caller's: `receipt_evidence` copied whatever `kane_run_id` it was given, and promotion only asked that `kane_runs[claim]` be non-empty. On #47's tip:

- a cached observation taken by an earlier Kane run on the *approved* build, plus a fresh run id from the caller, verified a `VerifiedRunReceipt` (the build check can't see it, because the build is right);
- a stored validation whose claim run binding was swapped for another session promoted, and so did one whose stored observation was simulation-only, `unbound:` or `failed`, as long as its build and run fields held;
- a plain `RunReceipt` naming a build and Kane runs accepted complete browser evidence naming neither.

Now:

- **`attest/models.py`**: `BrowserObservation.kane_run_id` (optional, non-empty). `simulation_only` and `unbound` are properties, read from the note prefix too. `receipt_evidence` takes the run from the observation: a caller run that differs raises, and an observation naming no run gives evidence naming none, whatever the caller says.
- **`attest/kane.py`**: `run_group` stamps each pack-bound observation with its run's session id, the same value it records in `run_ids`. An unbound observation names none.
- **`attest/runner.py`**: `FakeBrowserRunner` drops a scripted `kane_run_id`, as it drops a scripted build.
- **`attest/workflow.py`**: validation fails `<id>:observation_run_mismatch` / `<id>:observation_run_missing` when the observation doesn't name the run the runner's record binds. Promotion reads stored browser evidence back as `BrowserObservation` and refuses unless it names the claim's bound run and is passed, not simulation-only and not unbound.
- **`attest/receipts.py`**: `validate_named_bindings`: a plain `RunReceipt` that names a build or Kane runs refuses complete browser evidence that names none. `VerifiedRunReceipt` skips it and keeps its own stricter checks and messages, so KR-SIM-03's expected messages don't change.

New cases: 16 (version 2, kane-run 12, observation-build 1, control 1), plus `tests/test_models.py::test_observation_receipt_evidence_takes_the_observed_kane_run`. One #41 assertion changed: a plain `RunReceipt` with complete browser evidence naming no run now expects the refusal.

Negative control: one commit turns off the `models.py`, `receipts.py`, `runner.py` and `workflow.py` hooks above and leaves the `kane.py` stamp. 15 of the 16 new G1 cases fail, plus the models test. The two that still pass are `test_kane_run_runner_stamps_the_run_whose_pack_backs_each_observation` (the stamp is untouched) and the control case, which must pass.

## Not covered

- **Live Kane.** Every Kane case replays a recorded event-day capture (`tests/fixtures/kane/`, kane-cli 0.9.0).
- **Pack definition binding.** The pack's `test.md` digest isn't compared with the test file Attest wrote. Recorded captures carry their original `test.md`, so this check would fail every offline replay, including #31 PC-KR-01.
- **Cross-receipt replay.** The same Kane session id appearing on two receipts isn't detected. No store of issued receipts exists yet.
- **`attest.receipt/v0` loop receipts and plain `prove` verdicts.** These don't record a build or Kane run. Only `RunReceipt`/`VerifiedRunReceipt` and the Maintain Docs workflow bind them.
- **An app/build that changes and changes back during one run.** The build is checked only before and after the run. This holds for the observation stamp too.
- **What Kane's browser actually loaded.** The observation build is the doc's worktree identity measured around the run, the same identity as the proposal. It is not read from the served app (no build header or page probe). A server process left running from an older build would still be stamped with the worktree's current build.
- **Shell evidence.** `Evidence` for shell claims carries no build. Only browser observations do.
- **A plain `RunReceipt` that names no binding.** Since #49 a plain `RunReceipt` that names a build or Kane runs refuses *complete* browser evidence that names none. One that names neither still accepts any evidence, and incomplete evidence naming none is still accepted, so unverified records can still describe incomplete runs.
- **The stored validation record isn't authenticated.** #49's promotion checks make the record agree with itself (observation run vs `kane_runs`, observation build vs `build_digest`, status and notes). A rewrite that changes every field together isn't caught.
- **Spec mode (`spec_app`).** Spec-mode validation and promotion bind no observation build or Kane run.
- **Stale observations in the runner cache.** `KaneBrowserRunner.observe` still serves a cached observation taken on another build or by an earlier run. Validation, promotion and receipts refuse it, but the runner doesn't run Kane again. Changing that would change KR-SIM-03's step 2.
- **`implement` re-checks.** After an app change, `forget` drops only the target claim's group. Other groups' cached observations, taken before the app changed, stay. `attest.receipt/v0` records no build, and the observation build there is the doc worktree's, not the candidate app's.
- **SyberWork.**

## Interaction with open PRs

- **#26/#30, `KR-DS-03`** (strict xfail: "find_pack falls back to the newest pack"). This branch closes that gap, so the case will XPASS once both are on `main`. Flip it to `active` then. This PR doesn't edit `tests/redteam/`.
- **#27, G1 Stage-0 exit.** `tests/test_identity.py::test_verified_receipt_requires_complete_fresh_evidence` is #27's `[control]` id. It keeps its name and now passes a build digest and a Kane run id. The negative tests in #27's `test_g1_stage0_exit.py` still raise their original messages, because the new checks run after the existing ones. `tests.yml` is untouched.
- **#25/#28/#29** edit `attest/workflow.py` in nearby but different hunks.
- Decisions are recorded here rather than in `NOTES.md`, which many open PRs append to.
