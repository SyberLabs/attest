# Factory-path red-team quotas (N5)

**Status:** Proposed release policy, grounded in the Attest source reviewed for this change. Quota numbers below are minimum test scenarios to add for each component release; they are not measured coverage, completed test counts, or a claim that the components exist today.

## Scope and counting rule

The factory path under review has four boundaries: Attest Maintain Docs, Attest Reconcile Spec, the Kane browser-runner boundary, and the SyberWork admit/observe/reconcile bridge. For **each component and each release**, require at least **two distinct adversarial scenarios in every applicable category** below. The pair must vary the attack or failure condition, not just repeat the same fixture with another label. Each scenario must assert the security-relevant outcome (reject, refuse, mark invalid/unverified, or stop); logging the input or returning a generic success is not a pass. A scenario may satisfy only one quota cell unless its assertions independently exercise both conditions.

Release sequencing follows the Kane Factory MVP order recorded in issue #10: (1) separate repair/case identity from content digests; (2) fail closed on simulated execution and incomplete compilation; (3) authority-gated repair; (4) bound receipts; (5) execution isolation; (6) SyberWork bridge; (7) Reconcile Spec diagnosis and handoff. Later stages do not waive earlier controls.

| Required category | Maintain Docs | Reconcile Spec | Kane runner | SyberWork bridge |
| --- | ---: | ---: | ---: | ---: |
| Prompt injection into briefs or other untrusted content | 2 | 2 | 2 | 2 |
| Authority bypass / protected-requirement weakening | 2 | 2 | 2 | 2 |
| Digest swap / wrong-version evidence | 2 | 2 | 2 | 2 |
| Cost blowup (including Kane step storms where reachable) | 2 | 2 | 2 | 2 |
| Malformed MCP/schema input | 2 per serialized schema boundary¹ | 2 per serialized schema boundary¹ | 2 per serialized schema boundary¹ | 2 per serialized schema boundary¹ |
| Auth misuse | 2 | 2 | 2 | 2 |
| Failure-mode fail-closed | 2 | 2 | 2 | 2 |

¹ Test the strict schema at every boundary that accepts serialized input (for example, Pydantic models, MCP messages if an MCP adapter is introduced, Kane event streams/packs, and bridge events). **MCP is not present in the Attest checkout reviewed here**; do not report MCP coverage for it. If a component has no serialized input boundary, record that as an explicit architectural non-exposure and require two negative tests at its nearest ingress before release. Do not turn an unimplemented boundary into N/A: define and test its schema before its first release.

These quotas intentionally do not prescribe code coverage percentages. A release report should list the test identifiers and outcomes for each cell, plus any accepted non-applicability with evidence. Missing tests, skipped tests, xfails, or a component absent from this checkout are gaps, not satisfied quotas.

## Scenario intent by component

Each numbered pair is a minimum shape, not a claim that the tests already exist.

### Attest Maintain Docs

The current checkout has `attest/compiler.py`, `attest/pipeline.py`, and `attest/loop.py`; it does not have a Maintain Docs authority/approval/receipt module. Existing `Claim`, `Evidence`, and `Verdict` are in `attest/models.py`. Keep new release tests at the real public boundary when that workflow lands.

- **Prompt injection:** (1) put instructions to alter the application or reveal credentials inside a source brief; (2) put a conflicting instruction in retrieved/grounding text. Both remain untrusted proposal content and cannot widen the doc-only patch scope.
- **Authority / weakening:** (1) propose deleting or weakening a protected requirement; (2) present runtime behavior or an unapproved change as authority to rewrite it. Refuse automatic application in both cases.
- **Digest / version:** (1) pair evidence for one source digest with a patched document digest; (2) replay a valid-looking receipt/evidence record from an earlier commit. Reject the mismatch and require fresh validation of the exact candidate.
- **Cost:** (1) feed a document whose claim count/size drives unbounded compile or execution work; (2) force retries/repair attempts to exhaust configured budgets. Stop at explicit limits; never convert a budget stop to proven.
- **Schema:** (1) omit a required claim/span/evidence field; (2) provide a wrong-typed or unexpected field in serialized claims/evidence. Reject malformed input; no partial promotion. The existing Pydantic models use `extra="forbid"`, but `LLMClaim`/`ClaimSet` do not set that policy explicitly, so test the actual structured-output boundary rather than infer strictness from downstream models.
- **Auth:** (1) make a brief request a credential or environment dump; (2) supply a credential for the wrong service/account/scope. No credential is returned to content or used to authorize a write outside configured scope.
- **Fail closed:** (1) compiler returns an incomplete set or compilation fails after retry/fallback; (2) evidence is missing or validation fails after a patch. No approved repair or verified outcome is emitted.

### Attest Reconcile Spec

No Reconcile Spec implementation or policy type is present in this checkout. Its first release is diagnosis and handoff only, separate from Maintain Docs repair.

- **Prompt injection:** (1) put "accept the new requirement" in the conflict brief; (2) put a model-directed override in application output. Both remain data and cannot alter protected text.
- **Authority / weakening:** (1) candidate patch weakens a protected requirement; (2) observed behavior conflicts with a protected requirement but there is no authorized superseding decision. Return a diagnosis/handoff and refuse promotion in both cases.
- **Digest / version:** (1) use evidence from a different app build or commit; (2) swap the digest of the protected-contract snapshot after diagnosis. Invalidate the comparison/handoff as stale.
- **Cost:** (1) supply an oversized conflict set; (2) repeat ambiguous diagnoses to force retries. Enforce bounded input, work and retry budgets; exhaustion is unresolved, not a decision.
- **Schema:** (1) omit/invalidly type the protected requirement or candidate side; (2) add unknown authority/evidence fields. Reject rather than defaulting authority or side.
- **Auth:** (1) request application of the handoff using read-only or absent credentials; (2) replay an approval for a different spec/version. The handoff path has no write authority and rejects the mismatch.
- **Fail closed:** (1) authority is missing or conflicting; (2) evidence is invalid/incomplete. Emit no promoted repair and no verified resolution.

### Kane browser-runner boundary

`attest/kane.py` implements `KaneBrowserRunner`: it groups browser claims into `_test.md` files, invokes `kane-cli testmd run --agent`, parses NDJSON, reads evidence packs, and maps steps to claims. The CLI selects Kane by default; `--runner fake` remains available for offline use, and the library-level `prove()` fallback is still fake. Captured event-day fixtures under `tests/fixtures/kane/` exercise parser and mapping tests in `tests/test_kane.py`; those offline tests do not establish live-browser behavior or complete evidence integrity.

- **Prompt injection:** (1) page text asks the agent to ignore the compiled step; (2) a Kane remark/evidence field contains instructions for the host. Treat page/runner text as observation only; do not expand the step or execute new host actions.
- **Authority / weakening:** (1) a run attempts a different action than the compiled claim; (2) healing/re-authoring makes a mismatch pass. Preserve the original claim and classify the observation non-proven/stale or refuse it.
- **Digest / version:** (1) attach a valid evidence pack from another execution ID; (2) return a screenshot/value for the wrong step, claim, URL or document revision. Reject mismatched run, step and source bindings. Current `Evidence` has no run/session or source-digest fields, and `find_pack()` can select the newest fresh pack even when it finds no pack matching the reported session id; exact binding remains a proposed gate, not a verified implementation.
- **Cost:** (1) request more than the configured maximum steps; (2) create enough claims/groups to trigger an excessive number of runner calls or retries. The implementation splits at 14 claims per test file, writes `max_steps` (default 15, configurable through `rules.yaml`), and applies per-step and group-size-derived timeouts. These are not an aggregate per-document, retry, or credit budget; test that adversarial claim volume cannot create an unbounded sequence of groups. Never silently truncate into a proven run.
- **Schema:** (1) malformed/truncated NDJSON or missing terminal event; (2) wrong event type/step index or malformed evidence-pack manifest. Reject the run as incomplete/invalid, not passed. Current `parse_events()` ignores malformed JSON lines and unknown event types. Preserve the existing tolerance for untyped progress/noise, but add adversarial cases proving that malformed or missing required run/step records yield an explicit invalid/incomplete outcome rather than silent acceptance.
- **Auth:** (1) attempt to reuse a browser profile/session outside the explicitly selected account; (2) request a privileged or write action without an approved scoped identity. Stop before the action; never surface profile credentials in output.
- **Fail closed:** (1) runner exits/times out before a complete terminal record; (2) required pack/screenshot/value is missing or unreadable. Return invalid/unverifiable, never proven.

### SyberWork admit/observe/reconcile bridge

The SyberWork repository and bridge are not attached to this checkout, so these are required first-release contract tests, not observations about SyberWork behavior.

- **Prompt injection:** (1) put a write instruction inside an Attest observation; (2) put one in an external-effect response. Neither observation is an admission or authorization command.
- **Authority / weakening:** (1) submit an Attest handoff as approval to execute; (2) submit an observation as authority to weaken a SyberWork contract. Reject both; require the bridge's own authorized approval path.
- **Digest / version:** (1) reconcile an effect against evidence for the wrong destination/version; (2) swap the case/contract/evidence digest between admission and reconciliation. Keep outcome `unknown` until destination verification is bound to the exact case and effect.
- **Cost:** (1) replay duplicate admission/observation events; (2) fan one case into an unbounded number of Kane steps or reconciliation retries. Deduplicate and enforce limits; exhaustion remains unresolved.
- **Schema:** (1) send a missing/ill-typed event envelope; (2) send unknown status/evidence fields or a truncated event. Reject without advancing the case state.
- **Auth:** (1) use a valid identity with the wrong tenant/case scope; (2) replay an expired or wrong-audience approval. Reject without creating an external effect.
- **Fail closed:** (1) destination verification times out or is unavailable; (2) the receipt is partial or cannot be bound to the case/effect. Keep outcome `unknown`; never mark reconciled.

## Evidence to record per release

For every scenario record component/version, test ID, attack input or deterministic fixture, expected refusal/state, actual result, and the digests/identifiers necessary to reproduce it. Keep secrets out of fixtures and logs. A green result from a fake/simulated runner is not evidence of a live browser or external-effect boundary. Changes to a shared schema, trust boundary, runner protocol, retry/cost controls, or authority policy rerun the relevant component quota before release.
