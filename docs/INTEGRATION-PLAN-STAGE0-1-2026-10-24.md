# Stage 0 + Stage 1 integration plan — 2026-10-24

## Status and evidence boundary

The live GitHub snapshots inspected for PRs [#1 Identity](https://github.com/SyberLabs/attest/pull/1), [#2 Sim/compile gates](https://github.com/SyberLabs/attest/pull/2), [#3 Review rendering](https://github.com/SyberLabs/attest/pull/3), [#4 Authority/promotion](https://github.com/SyberLabs/attest/pull/4), and [#5 Kane execution/isolation](https://github.com/SyberLabs/attest/pull/5) were all open drafts against `feat/maintain-docs-mvp`, whose inspected tip was `0567834` (also the inspected `main` tip). The PR head snapshots were respectively `f79c16f`, `8d2c490`, `94c4ba4`, `6fbbd77`, and `0e01d06`. GitHub reported each slice individually mergeable against that common base; that does not establish that they merge cleanly with one another. No check runs were listed in those snapshots.

This branch currently carries the conflict-free Identity → Sim subset (#1 → #2), followed by this plan and its path-overlap checker. It is intentionally not represented as a completed Maintain Docs MVP: Authority (#4), rendering (#3), and Kane (#5) are not stacked here. The plan follows the attached implementation brief and the actual PR patches. `docs/ROADMAP_STATUS.md` and `docs/RECONCILIATION.md`, which the uploaded roadmap references, were not present in this checkout.

## Recommended landing order

1. **Identity and receipts — PR #1.** Establish immutable source/claim/proposal identities, separate patched-content digests, and receipt invariants first. The actual diff adds `attest/contracts.py`, `attest/snapshots.py`, and `attest/receipts.py`, and changes the loop's patched document identity. This is the foundation Authority and the review page consume.
2. **Simulation and compilation gates — PR #2.** Keep fake browser observations explicitly simulated/unverifiable, make incomplete or invalid compilation fail closed, and require exact normalized browser-label matches on the proof path. It is independent of #1 at the changed-path level and merged cleanly after #1 in a disposable worktree rehearsal.
3. **Maintain Docs authority and promotion — PR #4.** Land the scoped authority policy, proposal/approval bindings, durable SQLite workflow, fresh whole-document validation, and digest-checked promotion after reconciling its loop changes with #1. In the requested sequence (#1 → #2 → #4), a merge rehearsal stopped on content conflicts in `attest/loop.py` and `tests/test_loop.py`; do not treat this slice as stackable until those are resolved and its tests pass on the combined tree.
4. **Review/evidence rendering — PR #3.** Integrate after the final workflow and receipt/status contracts are known. The diff hardens Markdown/link rendering, content-addresses evidence images, contains evidence paths, adds restrictive response headers, and displays unresolved authority and before/after identities. A separate #4 → #3 rehearsal conflicted in `tests/test_render.py`; reconcile the tests and ensure the UI's status reflects the final workflow rather than an inferred green verdict.
5. **Kane execution/isolation — PR #5, last.** Reconcile the real Kane runner, evidence-pack parsing, per-claim gating, process supervision, recipe-only shell execution, and cleanup against the finalized `cli.py`, `loop.py`, `models.py`, `pipeline.py`, `verdict.py`, and shell interfaces. A #4 → #5 rehearsal conflicted in `attest/cli.py`, `attest/loop.py`, `attest/pipeline.py`, `tests/test_loop.py`, and `tests/test_render.py`. Only after those conflicts are resolved should the event-day path be tested with a real Kane run and exact evidence bound to the final content digest.

The intended slice order is Identity → Sim/compile → Authority/promotion → Review/evidence rendering → Kane. PRs remain independent drafts until their authorship, reviews, and required gates are handled; this integration branch is a staging/review surface, not authorization to merge them or push to `main`.

## Changed-path ownership and overlaps

Each PR's exclusive paths remain with its feature slice. The intersections below come from `git diff --name-only` between each live head and their common base. An intersection means both slices edit that path; it does **not** by itself mean Git reports a content conflict.

- **#1 × #4:** `attest/loop.py`, `tests/test_loop.py`.
- **#1 × #5:** `attest/loop.py`, `attest/models.py`, `tests/test_loop.py`.
- **#2 × #3:** `tests/test_render.py`.
- **#2 × #4:** `tests/test_render.py`.
- **#2 × #5:** `attest/verdict.py`, `tests/test_render.py`, `tests/test_verdict.py`.
- **#3 × #4:** `attest/cli.py`, `attest/pipeline.py`, `tests/test_render.py`.
- **#3 × #5:** `attest/cli.py`, `attest/pipeline.py`, `tests/test_render.py`.
- **#4 × #5:** `attest/cli.py`, `attest/loop.py`, `attest/pipeline.py`, `tests/test_loop.py`, `tests/test_render.py`.

The reproducible path-only check is [`scripts/check-pr-path-overlaps.py`](../scripts/check-pr-path-overlaps.py). Fetch the base and PR head refs, then run:

```bash
git fetch origin \
  refs/heads/feat/maintain-docs-mvp:refs/remotes/origin/feat/maintain-docs-mvp \
  refs/pull/1/head:refs/remotes/origin/pr/1 \
  refs/pull/2/head:refs/remotes/origin/pr/2 \
  refs/pull/3/head:refs/remotes/origin/pr/3 \
  refs/pull/4/head:refs/remotes/origin/pr/4 \
  refs/pull/5/head:refs/remotes/origin/pr/5
python scripts/check-pr-path-overlaps.py \
  --base refs/remotes/origin/feat/maintain-docs-mvp \
  --pr 1=refs/remotes/origin/pr/1 \
  --pr 2=refs/remotes/origin/pr/2 \
  --pr 3=refs/remotes/origin/pr/3 \
  --pr 4=refs/remotes/origin/pr/4 \
  --pr 5=refs/remotes/origin/pr/5
```

The checker reports changed-path intersections only. Rehearse ordered merges as well: path overlap is conservative and clean merges can still change behavior. Re-run both checks if a PR head moves. The observed rehearsals were: #1 → #2 clean; adding #4 after #1 → #2 conflicted in `attest/loop.py` and `tests/test_loop.py`; #2 → #3 clean; #2 → #5 clean; #4 → #3 conflicted in `tests/test_render.py`; and #4 → #5 conflicted in the five paths listed above.

## Rebase versus merge

Use a dedicated integration branch based on `feat/maintain-docs-mvp` and merge the PR heads in the order above. Do not rebase or rewrite the five submitted PR heads: they are independent drafts with a shared base, and rebase would obscure their separate review history or require rewriting published heads. Resolve each content conflict once on the integration branch, preserve the upstream commit ancestry, and rerun the complete gates against the combined tree. Do not force-push. The staging branch and PR must target `feat/maintain-docs-mvp`, never `main`.

## Maintain Docs event-day smoke-test checklist

- [ ] Start from a pinned repository snapshot and one explicit `Maintain Docs` authority policy; record the original source/content digest.
- [ ] Exercise a no-repair path with an explicit real browser runner. Confirm a fake/simulated run is labeled simulation and cannot produce a verified receipt.
- [ ] Create one descriptive-doc mismatch with an allowed, bounded doc-only proposal. Inspect the exact patch and before/after content identities; they must differ when content differs.
- [ ] Confirm the proposal persists in SQLite at `awaiting_approval`, survives process restart, and does not modify the source before explicit approval.
- [ ] Approve the exact proposal, plan, source, build, candidate, and policy digests. Change one bound input before resume and verify promotion is refused.
- [ ] Resume with fresh Kane evidence; recompile the whole document, validate from a clean state, and confirm the receipt/evidence is bound to the exact candidate digest and correct run/version.
- [ ] Confirm successful validation plus digest match is required for atomic promotion; failed, simulated, or incomplete validation leaves the source unchanged.
- [ ] Attempt one unauthorized repair and one protected-promise / quantified-requirement weakening. Both must be explicitly refused with no write.
- [ ] Inject omitted, incomplete, malformed, wrong-run, wrong-version, and digest-mismatched evidence; each case must fail closed with no verified receipt or promotion.
- [ ] Exercise unresolved required claims, compilation/cache errors, missing expected browser text, and exact/numeric mismatch; no fuzzy similarity may prove them.
- [ ] Exercise hostile Markdown URLs/attributes and evidence paths outside the artifact root; rendering/serving must not expose scriptable URLs or escaped files.
- [ ] Exercise independent shell/Kane deadlines, cleanup after success/failure/exception, and service readiness distinct from command exit. Confirm cleanup targets only owned process trees, not unrelated port owners.
- [ ] Verify the review page shows the actual unresolved/approved authority state, coverage gaps, exact patch, evidence, and distinct before/after identities.

## Fail-closed release gates from the implementation brief

- **Compilation:** incomplete compilation, invalid spans/fences/cache, missing required browser claims, unresolved required claims, or missing expected text cannot be treated as proof.
- **Execution and simulation:** simulation is a distinct state, never verification. Fake, omitted, failed, wrong-run, wrong-version, malformed, incomplete, or digest-mismatched evidence cannot create a verified receipt.
- **Authority:** protected requirements remain binding until explicitly superseded; only an explicit approved change can authorize its specific description change. Runtime observations and generated proposals are not authority. Missing/conflicting authority stops repair.
- **Repair and promotion:** proposals are untrusted; repairs are bounded to the permitted descriptive document/claim scope and reviewed as an exact patch. Approval must remain durable and bind the identities used. Recompile the whole document and validate with fresh evidence from a clean state. Failed validation never auto-applies; promotion requires a digest match and must preserve actual patched-content identity.
- **Proof semantics:** exact/numeric predicates cannot be proven by fuzzy similarity (suggestion only). Orthogonal execution/comparison/authority/evidence/repair states must not collapse into a misleading green verdict.
- **Security and cleanup:** render bare URLs safely and contain evidence paths; use independent deadlines and `finally` cleanup; never kill an unrelated process because it owns a port; prefer disposable execution for the event fixture.
- **Exit criterion:** no known path may emit a verified receipt from fake, omitted, wrong-version, or incomplete evidence.

## Blockers waiting on Kane PR #5

- The inspected PR #5 head `0e01d06` is still an open draft (two commits; head may change). Its PR description says the actual Kane CLI/browser was unavailable and its runner verification used fixture packs and fake process results. A real Kane CLI/browser event-day run, including run-identity matching and the evidence pack/screenshot round trip, is therefore still unverified.
- The PR description also says Windows-specific process-tree cleanup was not run. Verify that platform-specific path if Windows is a supported event-day execution target.
- PR #5 is the last integration slice and has the five confirmed content-conflict paths with #4 above. It must be refreshed against the resolved integration APIs and merged without force-pushing its feature branch.
- The full event-day Maintain Docs flow is not complete until #4's durable approval/promotion workflow and #3's review page are integrated and the final candidate is revalidated with fresh Kane evidence. The current #1 + #2 draft subset does not satisfy that end-to-end gate.

Tags: `attest`, `n1`, `integration`, `stage0`.
