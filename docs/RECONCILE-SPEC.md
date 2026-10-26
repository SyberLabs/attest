# Reconcile Spec

Reconcile Spec is Attest's diagnosis and handoff mode for a reported disagreement between a protected requirement and application behavior. It is deliberately separate from the existing doc-repair loop: that path may update descriptive documentation after evidence, while Reconcile Spec never edits or promotes protected contract text.

## Authority rules

- Protected requirement text remains binding until an authorized human decision explicitly supersedes it.
- A runtime observation is evidence about the application, not authority to change the requirement.
- Candidate sides (`doc_wrong`, `app_wrong`, `both`, or `ambiguous`) are hypotheses for routing, not verdicts.
- Missing or conflicting authority, missing evidence pointers, or an ambiguous candidate stops automatic repair and routes the conflict to an owner.
- Proposed protected text that differs from the protected text is recorded as refused. Reconcile Spec has no protected-text apply or promotion capability, even when authority and evidence pointers are present.
- Evidence pointers are recorded as supplied. Attest does not fetch, verify, or treat their contents as instructions.
- A Kane/Attest receipt may be recorded as an observation pointer; it is not authority and cannot mint a `reconciled` outcome.

The handoff records the orthogonal execution, comparison, authority, evidence, and repair states, plus a `handoff_state` of `ready` or `blocked`. Execution is `not_run` unless the input identifies an external runtime observation or receipt; either remains evidence and does not change authority. Repair is only `handoff` or `refused`, never applied, promoted, or `reconciled`.

## Fail-closed handoff

A handoff is `ready` only when authority is present, every conflict carries at least one evidence pointer, and `blocking_reasons` is empty. `ReconcileHandoff` refuses to load a JSON handoff that says `ready` otherwise, or `blocked` without a reason. Each reason reads only the submitted request; no pointer is fetched.

| Reason | When |
|---|---|
| `request:authority_missing`, `request:authority_conflicting` | The request says so. |
| `<claim>:authority_reference_missing` | The reference is absent or blank. Authority becomes `missing`. |
| `<claim>:authority_reference_mismatch` | The reference has a `#fragment` that isn't the requirement id (case-insensitive, exact). Authority becomes `conflicting`. |
| `<requirement>:protected_text_conflict` | Two conflicts give the same requirement different protected text. Authority becomes `conflicting`. |
| `<requirement>:authority_reference_conflict` | Two conflicts give the same requirement different references. Authority becomes `conflicting`. |
| `<claim>:evidence_missing` | The conflict has no evidence pointer. |
| `<claim>:supersession_without_change_decision` | The observed text, the authority reference, or an evidence pointer or description says "superseded" (or "superceded"). Reconcile Spec takes no change decision as input, so such a claim is never backed by an approved one here. |

The request schema also rejects blank `source_doc`, requirement ids, protected text and pointers, and duplicate claim ids, before anything is written.

After writing, the CLI reads the package back and checks that the JSON and the Markdown carry every conflict, its protected text bytes and every submitted evidence pointer.

Exit codes: `0` ready handoff; `1` the written package doesn't carry the request; `2` invalid input, or refused (`--apply`, or a proposed protected text that differs from the protected text, with or without `--apply`); `3` blocked, stopped for human review. Artifacts are written for `0`, `1`, `2` (refused) and `3`. A refused weakening is still handed to the owner of the behavior: `repair_state` is `refused` and `handoff_state` stays `ready` when nothing else blocks it.

## CLI

The command accepts a JSON diagnosis packet. This keeps the N2 boundary explicit: Attest records a supplied conflict and creates a handoff; it does not infer authority or claim to have independently verified external evidence.

```bash
attest reconcile-spec diagnosis.json --out .attest/reconcile-spec/run-1
```

Example input:

```json
{
  "source_doc": "docs/requirements.md",
  "authority_status": "present",
  "conflicts": [
    {
      "claim_id": "c01",
      "protected_requirement_id": "SEC-4",
      "authority_reference": "contracts/security.md#SEC-4",
      "protected_text": "The application must require multi-factor authentication.",
      "observed_text": "The application accepted a password-only sign-in.",
      "candidate_side": "app_wrong",
      "evidence": [
        {
          "kind": "runtime",
          "pointer": "artifacts/run-17/sign-in.png",
          "description": "Observed sign-in flow"
        }
      ]
    }
  ]
}
```

The command writes `reconcile-spec.md` and `reconcile-spec.json` side by side. Passing `--apply`, or proposing changed protected text, writes the refusal handoff and exits 2. A blocked handoff exits 3. Protected text is never changed. Review the artifact with the protected-requirement owner and the recommended application or documentation owner.

## Non-goals

- No automatic weakening or rewriting of requirements.
- No application patching, protected-contract apply, or promotion.
- No second browser assurance graph and no SyberWork admission or execution wiring.
- No claim that supplied evidence was independently verified.

Maintain Docs remains the descriptive-doc repair path. A descriptive explanation may be updated there when the protected requirement is unchanged; any disagreement about that requirement belongs in this handoff mode. Later SyberWork factory wiring can consume the JSON handoff as a case event, while preserving its evidence as observation rather than proof of an external write or authority to change a contract.
