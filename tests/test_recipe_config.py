import json
import os
import sys

import pytest

from attest.execution.recipes import ApprovedRecipeExecutor, RecipeConfigError, load_recipe_config


def write(tmp_path, text, name="recipes.yaml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_yaml_recipes_as_fixed_argv(tmp_path):
    path = write(tmp_path, 'recipes:\n  "npm test":\n    - npm\n    - test\n  "echo ready && exit 0": [python3, -c, "print(1)"]\n')
    executor = load_recipe_config(path)
    assert len(executor) == 2
    assert executor.resolve("npm test") == ["npm", "test"]
    assert executor.resolve("echo ready && exit 0") == ["python3", "-c", "print(1)"]
    with pytest.raises(PermissionError, match="no approved recipe"):
        executor.resolve("npm test; curl example.invalid")


def test_loads_json_recipes(tmp_path):
    path = write(tmp_path, json.dumps({"recipes": {"say hi": [sys.executable, "-c", "print('hi')"]}}), "recipes.json")
    assert load_recipe_config(path).resolve("say hi")[0] == sys.executable


def test_empty_recipes_is_an_empty_allow_list(tmp_path):
    executor = load_recipe_config(write(tmp_path, "recipes: {}\n"))
    assert len(executor) == 0
    assert executor.digest == ApprovedRecipeExecutor({}).digest


@pytest.mark.parametrize(
    "text, message",
    [
        ("", "exactly one key"),
        ("- npm test\n", "exactly one key"),
        ("recipe:\n  a: [b]\n", "exactly one key"),
        ("recipes:\n  a: [b]\nextra: 1\n", "exactly one key"),
        ("recipes: [a, b]\n", "argv list"),
        ("recipes:\n  a: b\n", "non-empty list of string"),
        ("recipes:\n  a: []\n", "non-empty list of string"),
        ("recipes:\n  a: [b, 1]\n", "non-empty list of string"),
        ("recipes:\n  a: [b, [c]]\n", "non-empty list of string"),
        ("recipes:\n  a: [b, null]\n", "non-empty list of string"),
        ("recipes:\n  1: [b]\n", "must be a string"),
        ('recipes:\n  a: [""]\n', "non-empty argv"),
        ('recipes:\n  "  ": [b]\n', "command key"),
        ("recipes:\n  a: [b]\n  a: [c]\n", "duplicate key"),
        ('recipes:\n  a: [b]\n  "a ": [c]\n', "duplicate approved recipe"),
        ("recipes:\n  a: [b]\nrecipes: {}\n", "duplicate key"),
        ('{"recipes": {"a": ["b"], "a": ["c"]}}', "duplicate key"),
        ("recipes: {a: [b\n", "cannot parse"),
        ("recipes: !!python/object/apply:os.system [true]\n", "cannot parse"),
    ],
)
def test_ambiguous_or_malformed_config_is_refused(tmp_path, text, message):
    with pytest.raises(RecipeConfigError, match=message):
        load_recipe_config(write(tmp_path, text))


def test_missing_config_is_refused(tmp_path):
    with pytest.raises(RecipeConfigError, match="cannot read"):
        load_recipe_config(tmp_path / "absent.yaml")


def test_directory_is_refused(tmp_path):
    with pytest.raises(RecipeConfigError, match="regular file"):
        load_recipe_config(tmp_path)


@pytest.mark.skipif(not hasattr(os, "symlink") or os.name == "nt", reason="symlinks need privileges on Windows")
def test_symlinked_config_is_refused(tmp_path):
    real = write(tmp_path, "recipes:\n  a: [b]\n")
    link = tmp_path / "link.yaml"
    link.symlink_to(real)
    with pytest.raises(RecipeConfigError, match="regular file"):
        load_recipe_config(link)


def test_digest_binds_commands_and_argv_but_not_order():
    a = ApprovedRecipeExecutor({"x": ["one"], "y": ["two", "2"]})
    b = ApprovedRecipeExecutor({"y": ["two", "2"], "x": ["one"]})
    assert a.digest == b.digest
    assert a.digest != ApprovedRecipeExecutor({"x": ["one"], "y": ["two", "3"]}).digest
    assert a.digest != ApprovedRecipeExecutor({"x": ["one"], "z": ["two", "2"]}).digest
