# G5 Protected-text diff

**Gate:** FACTORY §3 G5. Blocks merge when a change alters protected requirement text without a linked approved change decision. Rubric R2: protected text stays byte-identical unless an explicit approved change decision references it.

**Check:** `g5-protected-text / G5 Protected-text diff` (`.github/workflows/g5-protected-text.yml`). It runs `tests/quality/test_g5_protected_text.py`, then `scripts/g5-protected-text.py --base <base> --head HEAD`. On a PR the base is the first parent of the merge commit; on a push to `main` it is the previous `main` tip.

**Status:** **Measured (attest, scoped to the two hooks below)** on PR #33. On `6112bc5`, hosted [run 40297703999](https://github.com/SyberLabs/attest/actions/runs/40297703999) passed 24 control tests and compared 3 protected units, with 0 changed. Negative control: on `0cdc93a`, which weakened the `demo/counter.md` promise, [run 40297561925](https://github.com/SyberLabs/attest/actions/runs/40297561925) failed in the diff step, while `pytest` stayed green. Not yet a required check. Update this line only from real run output.

## What is protected

The checker reads protected text from hooks already on `main`, at both revisions:

| Source | Protected unit |
|---|---|
| Maintain Docs `attest.policy.yaml`, `match:` rule | Every paragraph of a `*.md` file in the same directory that contains the match (case- and whitespace-folded, as in `Policy.decide`). The rule is protected too: removing it, or the policy file, removes the paragraph's protection and fails the gate. |
| Reconcile Spec fixtures, `tests/fixtures/reconcile_spec/*.json` | The `text` of every claim with `"protected": true`. Flipping `protected` to `false` fails the gate. |

On `main` today that is three units: the `demo/counter.md` "increments by one" paragraph, `req-tls-001` and `req-mfa-001`.

A base unit passes only if the head has the same source, path, rule/claim key and bytes. Adding new protected text passes. Editing anything else passes. If the base has no protected text at all, or a policy or fixture cannot be parsed, the gate exits 2 and fails.

## Changing protected text

1. Land an approved change decision in `docs/change-decisions/<id>.json` on `main` first, through its own reviewed PR.
2. Then open the PR that changes the text.

A decision added in the same PR as the edit doesn't count, so a PR can't approve its own change. The decision must bind the exact bytes:

```json
{
  "schema": "attest.change-decision/v0",
  "decision_id": "CD-0001",
  "status": "approved",
  "approved_by": "protected-requirement owner",
  "path": "demo/counter.md",
  "before_sha256": "<sha256 of the protected paragraph or claim text at base>",
  "after_sha256": "<sha256 of the replacement at head, or null to allow removal>",
  "reason": "Why the requirement changed."
}
```

A decision whose status isn't `approved`, or that is missing a field, is listed under "ignored" in the step summary and approves nothing.

## Not covered

- `claim:` policy rules. They name compiler claim ids and can't be resolved without compiling the doc.
- Protected requirement text outside these two hooks, including SyberWork.
- Who may approve. The gate checks that a decision is on `main` and binds the digests. Review of the decision PR is the approval control. Making this check required on `main` needs a repo admin.
