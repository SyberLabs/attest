"""Exact-match allow-list for shell recipes that can run without a shell."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


class ApprovedRecipeExecutor:
    """Resolve an approved command string to a fixed argv vector.

    Recipe registration is trusted application configuration. The document can
    select only an exact key; it cannot supply argv fragments or shell syntax.
    """

    def __init__(self, recipes: Mapping[str, Sequence[str]]) -> None:
        normalized: dict[str, tuple[str, ...]] = {}
        for command, argv in recipes.items():
            key = command.strip()
            args = tuple(str(part) for part in argv)
            if not key or not args or not args[0]:
                raise ValueError("approved recipes need a command key and non-empty argv")
            if key in normalized:
                raise ValueError(f"duplicate approved recipe: {key}")
            normalized[key] = args
        self._recipes = normalized

    def resolve(self, command: str) -> list[str]:
        key = command.strip()
        try:
            return list(self._recipes[key])
        except KeyError as exc:
            raise PermissionError(f"no approved recipe for shell claim: {key!r}") from exc
