"""KR-SIM-03 on the #41 tip, ahead of the full red-team inventory (#26/#30/#40).

The inventory's ``test_kane_runner.py`` holds KR-SIM-03 as a stub, and its
``test_inventory.py`` requires each kane_runner case to be bound there exactly once.
When this branch and that stack meet, replace the stub's body with
``run_case(rt_case, tmp_path, monkeypatch)`` (adding the two fixtures to its
signature) and delete this file. Until then, leaving the stub in place fails G3
loudly: an ``active`` case whose test raises.
"""

from __future__ import annotations

from ._harness import case
from ._version_binding import run_case


@case("KR-SIM-03")
def test_cached_replay_is_bound_to_app_version(rt_case, tmp_path, monkeypatch):
    run_case(rt_case, tmp_path, monkeypatch)
