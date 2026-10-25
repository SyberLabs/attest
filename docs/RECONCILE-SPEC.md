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

The handoff records the orthogonal execution, comparison, authority, evidence, and repair states. Execution is `not_run` unless the input identifies an external runtime observation or receipt; either remains evidence and does not change authority. Repair is only `handoff` or `refused`, never applied, promoted, or `reconciled`.

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

The command writes `reconcile-spec.md` and `reconcile-spec.json` side by side. Passing `--apply` writes the refusal handoff and exits non-zero; protected text is never changed. Review the artifact with the protected-requirement owner and the recommended application or documentation owner.

## Non-goals

- No automatic weakening or rewriting of requirements.
- No application patching, protected-contract apply, or promotion.
- No second browser assurance graph and no SyberWork admission or execution wiring.
- No claim that supplied evidence was independently verified.

Maintain Docs remains the descriptive-doc repair path. A descriptive explanation may be updated there when the protected requirement is unchanged; any disagreement about that requirement belongs in this handoff mode. Later SyberWork factory wiring can consume the JSON handoff as a case event, while preserving its evidence as observation rather than proof of an external write or authority to change a contract.
