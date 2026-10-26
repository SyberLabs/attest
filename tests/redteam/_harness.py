"""Shared plumbing for the red-team case inventory.

Every case is one JSON file under ``cases/<component>/<ID>.json`` holding its
tags (component, category, rubric IDs), its fixture inputs and the invariants
it expects. A test function binds to exactly one case with ``@case("ID")``,
which applies the pytest markers from the file. ``test_inventory.py`` checks
that files and tests match one to one, so the G2 count read from the files is
the count pytest runs.

Status semantics:

* ``active``: the test asserts the invariant and passes today.
* ``xfail``: the test asserts the invariant, and it fails today (strict xfail).
  It is an observed gap, never a green security result.
* ``stub``: no executable check yet (skipped). Stubs never count toward a floor.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path

import pytest

CASES_DIR = Path(__file__).parent / "cases"

COMPONENTS = ("maintain_docs", "reconcile_spec", "kane_runner", "syberwork_bridge")
CATEGORIES = ("PI", "AB", "DS", "SIM", "SCH", "COST", "ISO", "REC", "CONTROL")
RUBRICS = tuple(f"R{n}" for n in range(1, 10))
STATUSES = ("active", "xfail", "stub")

# Minimum case counts per component x category, from FACTORY-REDTEAM-AND-RUBRICS §1.
QUOTAS = {
    "maintain_docs": {"PI": 4, "AB": 5, "DS": 4, "SIM": 3, "SCH": 3, "COST": 2},
    "reconcile_spec": {"PI": 4, "AB": 6, "DS": 2, "SIM": 2, "SCH": 3, "COST": 1},
    "kane_runner": {"PI": 4, "AB": 1, "DS": 3, "SIM": 3, "SCH": 2, "COST": 3, "ISO": 4},
    "syberwork_bridge": {"PI": 3, "AB": 3, "DS": 3, "SIM": 2, "SCH": 4, "COST": 1, "REC": 6},
}

# Required case themes from §1; each must have at least one non-stub case.
REQUIRED_THEMES = {
    "maintain_docs": (
        "protected_text_edit",
        "weaken_promise_refused",
        "reused_doc_sha256",
        "fuzzy_numeric_proof",
        "apply_after_failed_validation",
        "unresolved_required_claims",
        "path_escape",
    ),
    "reconcile_spec": (
        "weaken_requirement_refused_handoff",
        "missing_or_conflicting_authority",
        "injected_superseded_claim",
        "handoff_missing_evidence",
        "ambiguous_never_auto_resolved",
    ),
    "kane_runner": (
        "page_content_injection",
        "stale_or_wrong_app_version",
        "deadline_exceeded",
        "cleanup_in_finally",
        "no_port_kill",
        "escape_disposable_environment",
        "unbounded_retries_or_steps",
    ),
    "syberwork_bridge": (
        "receipt_as_reconciliation_proof",
        "caller_success_or_evidence_fields",
        "status_url_redirect_or_cross_origin",
        "digest_or_key_mismatch",
        "404_treated_as_no_write",
        "retry_after_effect_claim",
        "terminal_outcome_races_lookup",
        "budget_released_while_unresolved",
    ),
}


def load_cases() -> dict[str, dict]:
    cases: dict[str, dict] = {}
    for path in sorted(CASES_DIR.glob("*/*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["_path"] = path
        if data.get("id") in cases:
            raise ValueError(f"duplicate red-team case id {data.get('id')!r} in {path}")
        cases[data["id"]] = data
    return cases


CASES = load_cases()


def marker_names(meta: dict) -> list[str]:
    """Plain markers so `pytest -m "rt_maintain_docs and rt_AB"` selects cases."""
    return ["redteam", f"rt_{meta['component']}", f"rt_{meta['category']}", *(f"rt_{r}" for r in meta["rubrics"])]


def case(case_id: str):
    """Bind a test to one case file and apply its markers and status."""
    meta = CASES[case_id]

    def decorate(fn):
        marks = [
            pytest.mark.redteam(
                case_id=case_id,
                component=meta["component"],
                category=meta["category"],
                rubrics=tuple(meta["rubrics"]),
                status=meta["status"],
            ),
            *(getattr(pytest.mark, name) for name in marker_names(meta)[1:]),
        ]
        if meta["status"] == "xfail":
            marks.append(pytest.mark.xfail(strict=True, reason=f"{case_id} observed gap: {meta['gap']}"))
        elif meta["status"] == "stub":
            marks.append(pytest.mark.skip(reason=f"{case_id} stub: {meta['gap']}"))
        for mark in marks:
            fn = mark(fn)
        fn.redteam_case = case_id
        return fn

    return decorate


@pytest.fixture
def rt_case(request) -> dict:
    """The case dict for the running test, read from its redteam marker."""
    marker = request.node.get_closest_marker("redteam")
    assert marker is not None, "rt_case requires @case(...)"
    return CASES[marker.kwargs["case_id"]]


def make_diff(old: str, new: str, filename: str = "guide.md") -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}",
        )
    )


class KaneTestRunner:
    """Workflow runner stand-in for fixtures with shell claims only.

    It carries the runner name the workflow gate expects but can observe
    nothing; any browser claim reaching it fails the test loudly.
    """

    name = "kane"

    def prepare(self, claims, source="doc"):
        self.prepared = [claim.id for claim in claims]

    def observe(self, claim, base_url):
        raise AssertionError("red-team workflow fixtures have no browser claims")

    def close(self):
        pass
