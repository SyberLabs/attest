# Factory-path pass rubrics

**Status:** Proposed release acceptance rubric. It describes the minimum meaning of a pass; it does not assert that the absent factory components are implemented. The code-specific observations below refer to the Attest checkout reviewed for this change.

## Verdict dimensions stay separate

Do not compress these decisions into one green/red flag. A run must report, or otherwise preserve, the independent dimensions relevant to its component:

- **Execution:** did the authorized runner actually complete the requested work, or was it simulated, skipped, interrupted, timed out or unreachable?
- **Comparison:** does the observed result satisfy the exact compiled claim/contract?
- **Authority:** is the proposed change or external action permitted by an explicit, applicable approval/policy?
- **Evidence:** is the evidence complete, authentic to the relevant run, claim, source version and destination?
- **Repair/effect:** was a repair merely proposed, explicitly approved, applied, or destination-verified?

A positive result in one dimension must not upgrade another. In particular, a successful execution does not establish authority; a successful comparison does not establish evidence authenticity; a proposal or approval does not establish application; and application does not establish destination reconciliation.

## Required pass/fail decisions

| Condition | Pass | Fail / non-pass |
| --- | --- | --- |
| **Compilation completeness** | The full required input compiles against the versioned schema; all required claims/requirements are represented, spans are valid, and compilation completion is explicit. | Any parse/schema error, omitted or unresolved required item, incomplete response, fallback that cannot establish completeness, or ambiguous claim mapping. Do not issue a verified result or promote a repair. |
| **Simulation versus verification** | Only a real, authorized execution with the required provenance can enter `verified`. Simulation is explicitly labeled and cannot mint or be replayed as verified evidence. | Fake, echo, cached-only, dry-run or simulated evidence is presented as live verification, or the system has no way to distinguish it. Treat as a release-blocking defect. |
| **Authority** | The specific protected requirement, scope, actor, version and approval are explicit and applicable to the proposed change/action. | Missing, conflicting, expired, wrong-scope or ambiguous authority; runtime behavior alone is not authority. Reconcile Spec diagnoses and hands off; it does not weaken or apply protected requirements. |
| **Evidence validity** | Evidence is schema-valid and bound to the exact claim/step, source/spec digest, runner execution, and (for external effects) destination/case. Required artifacts are present and readable; freshness and integrity checks pass. | Missing/partial/malformed evidence; wrong kind, claim, run, digest, version, destination or scope; unbound screenshot/observation; or digest verification unavailable. Do not call it proven, verified or reconciled. |
| **Comparison** | The component's deterministic comparator evaluates the exact promised predicate against the observed values; all required predicates pass. | Fuzzy/ambiguous equivalence, missing expected value, unresolved disagreement, or an untrusted model's assertion standing in for comparison. Report stale/ambiguous/unverifiable as appropriate. |
| **Repair** | Maintain Docs may propose a narrowly scoped descriptive-doc repair under its own explicit policy, then requires approval when policy requires it, exact before/after identity, whole-document recompile, and fresh validation evidence before promotion. | An unapproved patch, changed scope, stale evidence, failed validation, altered source identity, or a patch to protected requirements. Never auto-apply a Reconcile Spec candidate. |
| **External effect reconciliation** | SyberWork records an effect as reconciled only after destination-verified evidence is bound to the exact case/effect/version and the reconciliation check passes. | Attest text, a local success, an attempted write, a callback alone, or a simulation is treated as proof of external state. Keep effect outcome `unknown` pending destination verification. |
| **Failure handling** | Timeout, cancellation, malformed output, dependency failure, missing artifacts, unavailable auth, and budget exhaustion produce explicit non-success states and cleanup. | Any such failure becomes a success by default, stale cached evidence, partial state, an implicit retry, or an unhandled exception interpreted as success. |

## Component-specific application

### Attest Maintain Docs

The current source has typed `Claim`, `Evidence`, `Verdict` and `BrowserObservation` models. `Verdict` rejects non-`unverifiable` statuses without evidence, and `judge()` rejects cross-runner evidence kinds for shell/browser claims. These are useful local invariants, not a complete verification receipt: `Evidence` does not bind a document digest, source commit or execution ID, and the fake runner can produce an observation that the deterministic judge calls `proven`. The CLI now defaults to Kane, but `--runner fake` and the library-level no-runner fallback still produce ordinary judge results; simulation is not structurally excluded from `proven`.

A Maintain Docs pass requires a complete compilation, exact approved descriptive scope, a fresh real execution where applicable, evidence bound to the before/after document identity, successful whole-document validation, and no automatic application after failed validation. A change to protected behavior is outside this repair permission.

### Attest Reconcile Spec

There is no Reconcile Spec implementation in this checkout. Its pass is a complete diagnosis and attributable handoff, not a repair. It must identify the conflict, protected text, observed evidence and its provenance, candidate interpretations, ambiguity, and recommended owner. If evidence or authority is incomplete, the handoff must say so. No status may imply the protected requirement was changed, approved, or reconciled.

### Kane browser-runner boundary

`KaneBrowserRunner` now writes grouped `_test.md` files, invokes Kane, parses NDJSON, reads sealed packs and maps steps to `BrowserObservation`; the CLI defaults to this runner. `tests/test_kane.py` uses captured event-day fixtures as offline truth for parser/mapping behavior, not as proof of a live browser run. For a release pass, `passed` from Kane alone is insufficient: terminal/run identity, claim-to-step mapping, exact observed values, required evidence artifacts and failure events must validate. The current parser ignores malformed JSON lines and unknown event types; pack selection can fall back to the newest fresh pack when there is no session id; and `Evidence` does not bind run/source digests. Those are implementation gaps against this rubric. Any healing/re-authored step that changes the compiled action cannot count as a pass for the original claim; the current judge does not independently enforce that rule.

### SyberWork bridge

SyberWork is not attached here. For a bridge release, an Attest `RunReceipt`, observation, or handoff is evidence input only; it does not authorize admission, execute an external change, or establish destination state. Admit, observe, approve/execute and reconcile must remain separate transitions with explicit contract checks. Any uncertain external outcome remains `unknown` until destination verification.

## Review checklist

A reviewer should be able to answer yes to all of these for the exact release candidate:

1. Is compilation complete, and can every checked claim be traced to exact source lines/version?
2. Is simulation visibly distinct from live verification, and is simulated evidence rejected at the promotion boundary?
3. Is authority explicit, scoped, current and separate from observed behavior or model output?
4. Are evidence type, claim/step, source digest, runner identity and destination (when relevant) checked together?
5. Does any failed, ambiguous, incomplete, unauthorized, malformed or over-budget path remain non-success?
6. Do required red-team cases run against the release code rather than only fixtures, mocks or documentation?

Any "no" blocks a verified factory-path release. An unimplemented boundary, expected-failure security test, or skipped required scenario is an open release gap—not a pass.
