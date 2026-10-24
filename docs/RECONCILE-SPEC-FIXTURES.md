# Reconcile Spec golden fixtures

These packs are stable, hand-authored inputs for N2's Reconcile Spec policy work. They describe the boundary between protected promises, observations, and ordinary descriptive documentation; they do not implement or prescribe the policy engine.

## Contents

`tests/fixtures/reconcile_spec/` contains one JSON pack per scenario:

- `protected_requirement_vs_observed_app.json`: protected requirement and observed behavior conflict; the available evidence leaves the diagnosis ambiguous, so the expected repair is a handoff.
- `descriptive_doc_stale_not_protected.json`: a stale descriptive statement is explicitly marked `expected_mode: "maintain_docs"`; it is not a Reconcile Spec case and should be handed to Maintain Docs.
- `promise_weakening_attempt.json`: a proposed change weakens a protected requirement; the expected repair is `refused` and `promotion_allowed` is `false`.

All `fake://` values are inert evidence pointers for tests. They do not identify real documents, runs, or evidence and must not be resolved as URLs.

## Pack contract

Each pack has `schema_version` (currently `1`), a matching `fixture_id`, human-readable `title` and `scenario`, an `expected_mode`, a non-empty `claims` array, and an `expected` annotation. Each claim stub has a unique `claim_id`, `claim_kind`, `text`, boolean `protected` and `descriptive` flags, and one or more fake evidence pointers. Protected requirements set only `protected`; descriptive claims set only `descriptive`; observations and proposals set neither.

`expected` records `diagnosis_side`, `repair` (`handoff` or `refused`), `promotion_allowed`, and a short rationale. The annotations are test expectations, not authority to mutate an application or promote a candidate promise.

## Loading from N2 tests

Load the JSON directly; do not import the unfinished engine just to read fixture inputs. For example:

```python
import json
from pathlib import Path

fixture_dir = Path("tests/fixtures/reconcile_spec")
packs = [json.loads(path.read_text(encoding="utf-8"))
         for path in sorted(fixture_dir.glob("*.json"))]
```

Gate policy evaluation on `expected_mode`: send `reconcile_spec` packs through the Reconcile Spec diagnosis path, and assert that `maintain_docs` packs are routed away to Maintain Docs. For each applicable pack, compare the diagnosis side and repair against `expected.diagnosis_side` and `expected.repair`. Always assert that a false `promotion_allowed` result does not promote a candidate; in particular, the weakening-attempt pack must be refused.

The schema-only test is `python -m pytest tests/test_reconcile_spec_fixtures_schema.py`. It uses only Python's standard library, validates fixture shape and annotations, and deliberately does not import `reconcile_spec` or any other N2 policy module. Once the policy engine exists, add engine behavior tests separately rather than making this fixture-contract check depend on it.
