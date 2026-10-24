# Attest

Docs that can't lie. Point Attest at a README, a quickstart or an AI's how-to answer.
It executes every claimed step and hands the document back with each step badged
proven, stale, broken, unverifiable or skipped, with evidence behind every badge.

A closed loop sends a failed step to a coding agent, which patches the doc (never the
app), and only that step runs again. The same pipeline fed with a GPT answer to a
judge's question is "Procedural Truth".

This README is itself a document Attest can prove. Every code block below is a step
that runs as written, and the last step names an exact label on the rendered page.

## Install

Clone the repository:

```bash
git clone https://github.com/SyberLabs/attest.git
```

Install it. Python 3.11 or newer is required. `pipx install .` works too if you have pipx.

```bash
cd attest && python -m pip install .
```

## Prove a document

Prove the bundled Flask quickstart. Shell steps run in a scratch directory, the dev server
it starts is kept alive for the browser steps, and the result is rendered as one HTML page.
The default browser runner is Kane CLI, which runs `kane-cli testmd run --agent`; sign in once
with `kane-cli login`. Use `--runner fake` for offline runs. `--headless` runs Chrome without
a window. Procedural Truth cards can stop before a claim with `--stop-before <claim>`.

```bash
cd attest && attest prove demo/flask.md
```

The command ends by printing a line that starts with `page:` giving the path of the rendered page.

## Look at the result

Serve the most recent page on localhost:

```bash
cd attest && attest serve
```

Open http://localhost:4848/ in your browser. The dark strip fixed across the top of the page
is headed "Ledger" and shows the claim counts for this document.

Each claim has a badge in the left gutter next to the lines it came from. Hover a badge to read
the reason sentence. Click a badge to open the evidence drawer: the command and output tail for a
shell step, the expected label next to the observed label for a browser step, and the brief and
diff for a step the loop repaired.

## Other commands

`attest badge` writes `docs-proven.svg` and `PROOF.md` for the last run, so a maintainer can put
the result in their own README.

`attest ask "<question>" --card <name>` sends a how-to question to GPT, saves the numbered
answer as `demo/answers/<name>.md`, and from there it is just another document to prove.

`attest prove <doc> --loop` sends every stale or broken step to the coding agent and re-proves
only the patched step. The default agent is `codex exec -s read-only --skip-git-repo-check`;
set `ATTEST_AGENT_CMD` to override it. Add `--report` to file each Kane run with the event's
`kanehack report`. `attest prove` exits 1 when a claim ends stale or broken.

## How it works

1. Ingest: read the doc at a path or URL, hash it, number the lines, find fenced blocks and URLs.
2. Compile: one structured-output call to OpenAI turns the numbered doc into typed claims, each
   pinned to a line span. Fenced blocks become shell claims verbatim; browser instructions
   become plain-English steps that name the exact label they expect; the rest is prose.
3. Execute: shell claims run in order in a scratch directory with a timeout. Browser claims go
   to a browser runner, which returns what it observed.
4. Verdict: pure rules, no model. Shell claims use the exit code and the promised output.
   Browser claims compare the expected label with the observed one using a token-set ratio with
   a tuned threshold.
5. Render: the original Markdown with a badge per claim, an evidence drawer, and a ledger strip
   whose numbers come from an append-only ledger.

Secrets come from the environment only: `OPENAI_API_KEY` for the compiler and asker,
`GITHITS_API_TOKEN` optionally for grounding. Nothing is written to disk.

## Built on event day

Attest is a SyberLabs entry for Kane CLI Hack Day, San Francisco, October 24 2026. The day
added the Kane test file writer, NDJSON and evidence pack reader, witness phrasing, and recordings
per document version. The loop now follows Kane verdicts. It also closed the Vite stale catch:
the starter showed "Get started" while the doc expected "Vite + React", and the loop patched it.
