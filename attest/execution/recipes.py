"""Exact-match allow-list for shell recipes that can run without a shell."""

from __future__ import annotations

import hashlib
import json
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path

import yaml


class RecipeConfigError(ValueError):
    """The trusted recipe file is missing, malformed, or ambiguous."""


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

    def __len__(self) -> int:
        return len(self._recipes)

    @property
    def digest(self) -> str:
        """Order-independent identity of the allow-list, for validation receipts."""
        payload = json.dumps(
            {key: list(args) for key, args in self._recipes.items()},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def resolve(self, command: str) -> list[str]:
        key = command.strip()
        try:
            return list(self._recipes[key])
        except KeyError as exc:
            raise PermissionError(f"no approved recipe for shell claim: {key!r}") from exc


class _StrictLoader(yaml.SafeLoader):
    """Safe YAML (and so JSON) that refuses duplicate keys instead of keeping the last."""


def _no_duplicate_keys(loader: _StrictLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    mapping: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise RecipeConfigError(f"duplicate key {key!r} at line {key_node.start_mark.line + 1}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicate_keys)


def load_recipe_config(path: Path) -> ApprovedRecipeExecutor:
    """Load an operator-supplied allow-list: ``recipes: {<exact command>: [argv...]}``.

    The file is trusted configuration, never document text. Anything ambiguous
    (a symlink, a duplicate command, a non-string argv part, an unknown key)
    is refused rather than guessed at, so a typo cannot widen the allow-list.
    """
    path = Path(path)
    try:
        info = path.lstat()
    except OSError as exc:
        raise RecipeConfigError(f"cannot read recipe config {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RecipeConfigError(f"recipe config {path} must be a regular file, not a symlink")
    try:
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=_StrictLoader)
    except RecipeConfigError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise RecipeConfigError(f"cannot parse recipe config {path}: {exc}") from exc
    if not isinstance(data, dict) or set(data) != {"recipes"}:
        raise RecipeConfigError("recipe config must be a mapping with exactly one key: recipes")
    recipes = data["recipes"] or {}
    if not isinstance(recipes, dict):
        raise RecipeConfigError("recipes must map each exact command to an argv list")
    for command, argv in recipes.items():
        if not isinstance(command, str):
            raise RecipeConfigError(f"recipe command must be a string, got {command!r}")
        if not isinstance(argv, list) or not argv or not all(isinstance(part, str) for part in argv):
            raise RecipeConfigError(f"recipe {command.strip()!r} needs a non-empty list of string argv parts")
        if any("\0" in part for part in argv):
            raise RecipeConfigError(f"recipe {command.strip()!r} has a NUL byte in its argv")
    try:
        return ApprovedRecipeExecutor(recipes)
    except ValueError as exc:
        raise RecipeConfigError(str(exc)) from exc
