# Reconcile Spec

Reconcile Spec is Attest's diagnosis and handoff mode for a reported disagreement between a protected requirement and application behavior. It is deliberately separate from the existing doc-repair loop: that path may update descriptive documentation after evidence, while Reconcile Spec never edits or promotes protected contract text.

## Authority rules

- Protected requirement text remains binding until an authorized human decision explicitly supersedes it.
- A runtime observation is evidence about the application, not authority to change the requirement.
- Candidate sides (`doc_wrong`, `app_wrong`, `both`, or `ambiguous`) are hypotheses for routing, not verdicts.
- Missing or conflicting authority, missing evidence pointers, or an ambiguous candidate stops automatic repair and routes the conflict to an owner.
- Proposed protected text that differs from the protected text is recorded as refused. Reconcile Spec has no protected-text apply or promotion capability, even when authority and evidence pointers are present.
- Evidence pointers are recorded as supplied. Attest does not fetch, verify, or treat their contents as instructions.
- Submitted protected text is checked against the document that states it. Text that is already weaker than that document (a dropped scope, a softened modal, part of a section) is not the protected requirement, and the handoff is blocked.
- A document that says it, or the requirement's section, is superseded, draft or otherwise not in force is not authority for the text it states, even when the text matches. The handoff is blocked.
- A Kane/Attest receipt may be recorded as an observation pointer; it is not authority and cannot mint a `reconciled` outcome.

The handoff records the orthogonal execution, comparison, authority, evidence, and repair states, plus a `handoff_state` of `ready` or `blocked`. Execution is `not_run` unless the input identifies an external runtime observation or receipt; either remains evidence and does not change authority. Repair is only `handoff` or `refused`, never applied, promoted, or `reconciled`.

## Fail-closed handoff

A handoff is `ready` only when authority is present, every conflict carries at least one evidence pointer, every protected text was matched in a document that was read and doesn't say it no longer governs (`source_state=matched`, with that document's sha256 in `source_digests` and no `source_not_governing` lines on any conflict), and `blocking_reasons` is empty. `ReconcileHandoff` refuses to load a JSON handoff that says `ready` otherwise, or `blocked` without a reason. No evidence pointer is fetched.

| Reason | When |
|---|---|
| `request:authority_missing`, `request:authority_conflicting` | The request says so. |
| `<claim>:authority_reference_missing` | The reference is absent or blank. Authority becomes `missing`. |
| `<claim>:authority_reference_mismatch` | The reference has a `#fragment` that isn't the requirement id (case-insensitive, exact). Authority becomes `conflicting`. |
| `<requirement>:protected_text_conflict` | Two conflicts give the same requirement different protected text. Authority becomes `conflicting`. |
| `<requirement>:authority_reference_conflict` | Two conflicts give the same requirement different references. Authority becomes `conflicting`. |
| `<claim>:evidence_missing` | The conflict has no evidence pointer. |
| `<claim>:protected_text_not_in_source` | The checked document labels the requirement with different text. The handoff shows that text as `source_protected_text`. Authority becomes `conflicting`. |
| `<claim>:requirement_ambiguous_in_source` | The checked document labels the requirement more than once, with different texts. Authority becomes `conflicting`. |
| `<claim>:requirement_not_in_source` | The checked document never labels the requirement. Authority becomes `missing`. |
| `<claim>:source_unreadable:<why>` | The checked document can't be read: `not_found`, `outside_root` (absolute, `..`, a URL, or a symlink out of the root), `not_a_file`, `too_large` (over 2 MiB) or `not_utf8`. Authority becomes `missing`. |
| `request:source_not_checked`, `<claim>:source_not_checked` | An API caller passed no documents, or not the one this conflict names. Authority becomes `missing`. |
| `<claim>:source_not_governing:<why>` | The text matched, but the checked document says the requirement doesn't govern: `status`, `superseded` or `section` (see "Governing check"). The handoff lists the lines as `source_not_governing` and `source_state` is `not_governing`. Authority becomes `conflicting`. |
| `<claim>:supersession_without_change_decision` | The observed text, the authority reference, or an evidence pointer or description says "superseded" (or "superceded"). Reconcile Spec takes no change decision as input, so such a claim is never backed by an approved one here. |

### Source check

Each conflict's protected text is checked against one document: the path part of its `authority_reference` (`contracts/security.md` in `contracts/security.md#SEC-4`), or `source_doc` when the reference is only a fragment or blank. The CLI reads that path under `--source-root` (default: the current directory). Nothing outside the root is read, and nothing is fetched. `ReconcileSpec.diagnose` reads no file; it takes the documents `read_sources` read and blocks any conflict whose document it wasn't given.

In that document, a requirement is labelled where its id is the first word of a paragraph, list item or heading, after Markdown markers (`#`, `>`, `-`, `**`, numbering). The id matches case-insensitively, as in the fragment check. The labelled text is:

- for a heading (`## SEC-4`), anything after the id on the heading plus everything up to the next heading, so a section can't be quoted in part;
- for a paragraph or list item (`- **SEC-5:** ...`), the rest of it, including list items nested under it;
- for a bare label (`SEC-4:` alone), the next paragraph.

The submitted text must equal the labelled text after folding whitespace (line breaks and runs of spaces). Nothing else is normalized: case, punctuation and Unicode differences all block. Two different labelled texts for one id block as ambiguous.

### Governing check

When the text matches, the same document is checked for its own statement that the requirement doesn't govern. Attest has no registry of which document governs, so only the document's words count, and each rule errs toward blocking:

- `status`: a declared status whose first word isn't `approved`, `accepted`, `active`, `adopted`, `current`, `effective`, `final` or `ratified`. A status is a `status:` line (front matter included), `**Status:** ...`, `## Status: ...`, a table row `| Status | ... |`, or the first line after a `## Status` heading. `Draft`, `Proposed`, `Deprecated`, `Superseded`, `Replaced by ...`, any other word and an empty value all block. A status anywhere in the document counts, because the check can't tell which requirement a status line belongs to.
- `superseded`: "superseded" (or "superceded") anywhere in the document, the word #50 already blocks on in the request, or a `replaced_by:` key.
- `section`: a heading above the label (its section, any enclosing section, the document title) or a bracketed tag right after the id (`## SEC-4 (withdrawn)`, `- **SEC-4** [Deprecated]:`) says `superseded`, `deprecated`, `withdrawn`, `obsolete`, `rescinded`, `repealed`, `retired`, `archived`, `draft`, `proposed`, `replaced`, `historical` or "no longer". A requirement's own heading title isn't scanned for these words, so `## SEC-6 Reject deprecated TLS versions` doesn't block. A non-governing section that holds only other requirements doesn't block this one, unless it says "superseded", which blocks anywhere.

A document without any of these marks still isn't shown to be governing. The check only refuses one that says it isn't.

The request schema also rejects blank `source_doc`, requirement ids, protected text and pointers, and duplicate claim ids, before anything is written.

After writing, the CLI reads the package back and checks that the JSON and the Markdown carry every conflict, its protected text bytes and every submitted evidence pointer.

Exit codes: `0` ready handoff; `1` the written package doesn't carry the request; `2` invalid input, or refused (`--apply`, or a proposed protected text that differs from the protected text, with or without `--apply`); `3` blocked, stopped for human review. Artifacts are written for `0`, `1`, `2` (refused) and `3`. A refused weakening is still handed to the owner of the behavior: `repair_state` is `refused` and `handoff_state` stays `ready` when nothing else blocks it.

## CLI

The command accepts a JSON diagnosis packet. This keeps the N2 boundary explicit: Attest records a supplied conflict and creates a handoff; it does not infer authority or claim to have independently verified external evidence.

```bash
attest reconcile-spec diagnosis.json --out .attest/reconcile-spec/run-1 --source-root .
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

Here SEC-4's text is checked in `contracts/security.md`, the document its reference names, not in `docs/requirements.md`. The command writes `reconcile-spec.md` and `reconcile-spec.json` side by side. Passing `--apply`, or proposing changed protected text, writes the refusal handoff and exits 2. A blocked handoff exits 3. Protected text is never changed. Review the artifact with the protected-requirement owner and the recommended application or documentation owner.

## Non-goals

- No automatic weakening or rewriting of requirements.
- No application patching, protected-contract apply, or promotion.
- No second browser assurance graph and no SyberWork admission or execution wiring.
- No claim that supplied evidence was independently verified.
- No claim that the checked document is the governing authority. The source check shows the submitted text is what that document says, and the governing check refuses a document that says it doesn't govern. Neither shows that a document without such marks is current, approved or unsuperseded, or that the reference names the right contract.

Maintain Docs remains the descriptive-doc repair path. A descriptive explanation may be updated there when the protected requirement is unchanged; any disagreement about that requirement belongs in this handoff mode. Later SyberWork factory wiring can consume the JSON handoff as a case event, while preserving its evidence as observation rather than proof of an external write or authority to change a contract.
