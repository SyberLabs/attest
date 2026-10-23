"""Asker (Procedural Truth): turn a judge's question into a doc of browser steps.

The question goes to the OpenAI API with a fixed system prompt that asks for
numbered steps naming exact button and link labels. The answer is saved as
demo/answers/<card>.md with the model name and timestamp in frontmatter. From
there it is just a doc: ``attest prove demo/answers/<card>.md``.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

SYSTEM_PROMPT = (
    "Answer as numbered steps a person can follow in a web browser. "
    "Name each button or link by its exact label."
)
DEFAULT_MODEL = os.environ.get("ATTEST_ASK_MODEL", os.environ.get("ATTEST_OPENAI_MODEL", "gpt-5-mini"))

Asker = Callable[[str, str, str], str]  # (model, system, question) -> answer text


def ask_openai(model: str, system: str, question: str) -> str:
    from openai import OpenAI

    client = OpenAI()
    kwargs = {}
    if model.startswith(("gpt-5", "o")):
        kwargs["reasoning"] = {"effort": "low"}
    resp = client.responses.create(
        model=model,
        input=[{"role": "system", "content": system}, {"role": "user", "content": question}],
        **kwargs,
    )
    return resp.output_text


def slugify(card: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", card.lower()).strip("-")
    if not slug:
        raise ValueError("card name needs at least one letter or digit")
    return slug


def answer_doc(question: str, answer: str, card: str, model: str, asked_at: str) -> str:
    """Frontmatter plus the question as the title and the answer as the body."""
    body = answer.strip() + "\n"
    return (
        "---\n"
        f"card: {card}\n"
        f"model: {model}\n"
        f"asked_at: {asked_at}\n"
        f"system_prompt: {SYSTEM_PROMPT!r}\n"
        "---\n"
        f"# {question.strip()}\n"
        "\n"
        f"{body}"
    )


def ask_and_save(
    question: str,
    card: str,
    out_dir: Path = Path("demo/answers"),
    model: str | None = None,
    asker: Asker = ask_openai,
) -> Path:
    model = model or DEFAULT_MODEL
    slug = slugify(card)
    answer = asker(model, SYSTEM_PROMPT, question)
    asked_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{slug}.md"
    path.write_text(answer_doc(question, answer, slug, model, asked_at), encoding="utf-8", newline="\n")
    return path


CARDS: dict[str, str] = {
    "github-secret-scanning": "How do I enable free secret scanning on my GitHub repo?",
    "sf-parking-citation": "How do I appeal a parking citation in San Francisco online?",
    "census-county-population": "Where do I download Census population data for my county?",
    "github-make-public": "How do I make a GitHub repository public?",
    "github-add-collaborator": "How do I add a collaborator to a GitHub repo?",
}
