# G1 Stage-0 version+Kane binding

**Status:** Measured (attest, scoped), from hosted Actions output on PR #41. No coverage percentage is claimed, and the job isn't a required check on `main`.

- Green: job `G1 Stage-0 version+Kane binding`, [run 40304926335](https://github.com/SyberLabs/attest/actions/runs/40304926335) on `c7349a1`. 21 passed: version 7, kane-run 12, control 2. It was also green on `6e6b2f1` ([run 40304653221](https://github.com/SyberLabs/attest/actions/runs/40304653221)).
- Fails closed: [run 40304782316](https://github.com/SyberLabs/attest/actions/runs/40304782316) on `bffed83`. That commit put `main`'s newest-pack fallback back into `find_pack` and was reverted in `c7349a1`. Five kane-run cases failed and the job failed.
- Scope: only the tests listed below, offline on recorded Kane captures. No live Kane.
- **`observation-build` section** (stacked on #42): **Proposed**. It is added below, and nothing has run on hosted CI yet.

FACTORY §3 lists G1 as Measured (scoped) through #27, but names a gap that #27 does not cover: "app/build version and Kane run-id binding, live Kane". This gate covers the first two. It does not cover live Kane.

## What the gate checks

Job `G1 Stage-0 version+Kane binding` in `.github/workflows/g1-stage0-binding.yml` runs `python scripts/g1-stage0-binding.py`. The script runs `tests/quality/test_g1_stage0_binding.py` with `xfail_strict=true` and passes only when every test case ran and passed. Any skip, xfail, xpass, failure or error fails the job. So does an empty section, or a test whose name doesn't put it in a section.

| Section | Test prefix | Fails closed when |
|---|---|---|
| `version` | `test_version_` | evidence names another build, the receipt or an evidence record names no build, the build changes while Kane is running, or a stored validation's build is swapped or removed before promotion |
| `kane-run` | `test_kane_run_` | evidence names a Kane run the receipt didn't start, or names no run; a pack from another session sits in the evidence store; the run reports no session id; validation reuses an observation cached from an earlier run; a stored validation's run bindings are dropped, nulled or removed before promotion |
| `observation-build` | `test_observation_build_` | a browser observation names no build (Kane redeployed mid-run, a URL doc, or a runner that drops it), or names a build other than the approved one (cached from an earlier build, or swapped), at validation, at promotion (stored evidence's observation build swapped, removed, or the evidence dropped), or in a receipt built from those observations |
| `control` | `test_control_` | a receipt with matching build and run doesn't verify, or a fresh Kane run on the approved build doesn't validate and promote (with each observation naming the approved build), or a receipt carrying those observations' builds doesn't verify. This stops a refuse-everything product from passing |

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

On the workflow path, #41 already catches the realistic cases: a cached replay through `stale_kane_run`, and a mid-run redeploy through the post-run build check. The new check rejects an observation that names the wrong build, or none, even when its Kane run is fresh and pack-bound (`test_observation_build_omitted_or_wrong_fails_validation`). It also gives a receipt a build measured where the page was observed, not one copied from the proposal.

Here the build identity is the existing `proposals.current_build_digest`: git HEAD, status, diff and untracked files, or path plus source digest when the source isn't versioned. The Kane run id is the `session_id` that `test_md_done` reports, which the pack's `result.yaml` repeats.

A build that changes mid-run was already refused at promotion on `main`, but the workflow was still recorded as `validated`. Now it stops at `unresolved`.

## Not covered

- **Live Kane.** Every Kane case replays a recorded event-day capture (`tests/fixtures/kane/`, kane-cli 0.9.0).
- **Pack definition binding.** The pack's `test.md` digest isn't compared with the test file Attest wrote. Recorded captures carry their original `test.md`, so this check would fail every offline replay, including #31 PC-KR-01.
- **Cross-receipt replay.** The same Kane session id appearing on two receipts isn't detected. No store of issued receipts exists yet.
- **`attest.receipt/v0` loop receipts and plain `prove` verdicts.** These don't record a build or Kane run. Only `RunReceipt`/`VerifiedRunReceipt` and the Maintain Docs workflow bind them.
- **An app/build that changes and changes back during one run.** The build is checked only before and after the run. This holds for the observation stamp too.
- **What Kane's browser actually loaded.** The observation build is the doc's worktree identity measured around the run, the same identity as the proposal. It is not read from the served app (no build header or page probe). A server process left running from an older build would still be stamped with the worktree's current build.
- **Shell evidence.** `Evidence` for shell claims carries no build. Only browser observations do.
- **SyberWork.**

## Interaction with open PRs

- **#26/#30, `KR-DS-03`** (strict xfail: "find_pack falls back to the newest pack"). This branch closes that gap, so the case will XPASS once both are on `main`. Flip it to `active` then. This PR doesn't edit `tests/redteam/`.
- **#27, G1 Stage-0 exit.** `tests/test_identity.py::test_verified_receipt_requires_complete_fresh_evidence` is #27's `[control]` id. It keeps its name and now passes a build digest and a Kane run id. The negative tests in #27's `test_g1_stage0_exit.py` still raise their original messages, because the new checks run after the existing ones. `tests.yml` is untouched.
- **#25/#28/#29** edit `attest/workflow.py` in nearby but different hunks.
- Decisions are recorded here rather than in `NOTES.md`, which many open PRs append to.
