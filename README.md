# The Kane Factory, powered by SyberWork

> **Hack Day submission (team Attest, receipt S-RKCK):** the final event-day commit is
> [`f8c6b3f`](https://github.com/SyberLabs/attest/commit/f8c6b3f3353e20a84174f5b6b240fb42e88e2b32)
> on the `event-day` branch, committed before the 18:00 PDT deadline. The receipt lists an earlier commit on
> the same branch (`28edeb5`) because the form's commit field was not updated; correction
> requested in help ticket H-0014.

Docs and specs make promises. The Kane Factory checks every promise against the running app in
a real browser, sends what fails to an agent, and closes a case only when Kane has proven the fix.
Attest is the factory's first station: point it at a README, a quickstart, an AI's how-to answer
or a product spec, and it hands the document back with every step badged proven, stale, broken,
unverifiable or skipped, with evidence behind every badge.

The loop is modular: `Spec -> Kane -> SyberWork -> worker -> Kane (loop if needed) -> Outcome`.

- **Kane** (TestMu's browser agent) checks the doc or spec against the running app in a real
  browser and leaves evidence: what it saw, plus a screenshot.
- **SyberWork** is the vendor-neutral middle. It admits the case, applies the company's authority
  rules and dispatches to whatever agent or harness the company already uses. OpenAI is the
  default worker.
- **Kane again** re-checks the result. Only a Kane-proven outcome closes the case, and every case
  leaves a receipt.

This README is itself a document Attest can prove. Every code block below is a step that runs as
written, and the last step names an exact label on the rendered page.

## Four modes

1. **Prove a doc.** The bundled Flask quickstart: every step proven, and the browser step carries
   Kane's screenshot.
2. **Doc drift, repaired.** The Vite quickstart says "Vite + React"; today's starter page says
   "Get started". That is a descriptive claim, so the factory may fix the doc: the OpenAI API
   writes a one-line patch to the doc (never the app) and Kane re-proves it.
3. **Protected promise, refused.** The counter doc promises the button "increments by one"; the
   app adds two. `demo/attest.policy.yaml` protects that promise, so the doc repair is refused and
   an implementation handoff is issued instead. The factory will not weaken a promise to make a
   test pass.
4. **Spec mode (new today).** `attest prove demo/spec.md --app demo/app --loop` treats a workflow
   spec as the contract for the Acme CRM demo app. The target flow: Kane finds that the "Mark as
   won" capability is missing, the factory issues a ticket, a worker implements it in a candidate
   copy of the app (the original is never modified), and Kane re-checks the copy. The doc is never
   patched in this mode.

## Install

Clone the repository:

```bash
git clone https://github.com/SyberLabs/attest.git
```

Install it from inside the clone; the steps below run from there too. Python 3.11 or newer is required. `pipx install .` works too if you have pipx.

```bash
cd attest && python -m pip install .
```

## Prove a document

Prove the bundled Flask quickstart. Shell steps run in a scratch directory, the dev server they
start stays alive for the browser steps, and the result is rendered as one HTML page. The default
browser runner is Kane CLI (`kane-cli testmd run --agent`); sign in once with `kane-cli login`.

```bash
attest prove demo/flask.md
```

The command ends with a summary of the verdicts and the path of the rendered page.

## Look at the result

Serve the most recent page on localhost:

```bash
attest serve
```

Open http://localhost:4848/ in your browser. The header at the top of the page shows the score
for this document and, when every claim held, the label "all proven".

Each claim has a badge in the left gutter next to the lines it came from. Hover a badge for the
reason; click it to open the evidence drawer: the command and output tail for a shell step, the
expected label beside the observed label (and Kane's screenshot) for a browser step, and the
brief, diff, authority decision and receipt for a step the loop handled.

## Receipts and SyberWork

Every loop case writes an `attest.receipt/v0` JSON file under `.attest/receipts/`. A receipt
records the doc hashes before and after, the claim, the authority decision and the rule behind
it, the agent's proposal and patch hash, the evidence before and after, and the disposition
(repaired, unresolved or handed off). Receipts are written with `verified: false`. SyberWork's
bridge (SyberLabs/SyberWork PR #89) reads them as unverified observations; Kane's proven re-check
is the destination verification.

## How it works

1. Ingest: read the doc at a path or URL, hash it, number the lines, find fenced blocks and URLs.
2. Compile: one structured-output call to OpenAI turns the doc into typed claims pinned to line
   spans. Fenced blocks become shell claims verbatim; browser instructions become plain-English
   steps that name the exact label they expect.
3. Execute with Kane: shell claims run in order in a scratch directory with a timeout. Browser
   claims become a Kane test file; Kane drives a real Chrome and returns what it observed, with
   screenshots.
4. Verdict: pure rules, no model. Shell claims use the exit code and the promised output; browser
   claims compare the expected label with the observed one using a token-set ratio with a tuned
   threshold.
5. Loop with an authority gate: each stale or broken claim is checked against the policy. A
   descriptive claim goes to the agent, whose doc-only diff is applied in a throwaway checkout and
   re-proven by Kane. A protected promise is refused and handed off for implementation. In spec
   mode every failing claim becomes a ticket for the worker, applied to a candidate copy of the app.
6. Receipts: every loop case writes `attest.receipt/v0`, and an append-only ledger keeps the counts.
7. Render: the original Markdown with a badge per claim, an evidence drawer and the ledger strip.
   With `--live` the page updates while claims run and shows the terminal beside the doc.

Secrets come from the environment only: `OPENAI_API_KEY` for the compiler, the asker and the
default agent and worker, `GITHITS_API_TOKEN` optionally for grounding. Nothing is written to disk.

## Options

`attest prove` takes `--runner kane` (the default) or `--runner fake` for offline runs, and
`--headless` to run Chrome without a window. `--live`, together with `attest serve --live` in a
second terminal, gives a page that updates as claims run. `--loop` sends stale or broken steps
to the agent and re-proves them; `--policy <file>` names the authority policy (by default
`attest.policy.yaml` next to the doc); `--apply` writes an accepted doc patch back to the source.
`--app <dir>` switches to spec mode and implies `--loop`. `--stop-before <claim>` and
`--skip <claim>` keep Kane away from irreversible or inapplicable steps. `--cdp-endpoint` attaches
Kane to an existing Chrome, for example one that is already signed in. `--report` files each Kane
run with the event's `kanehack report`. `attest prove` exits 1 when a claim ends stale or broken,
so CI can gate on it.

`ATTEST_AGENT_CMD` picks the doc-repair agent: the default `openai` calls the OpenAI API directly,
and any other value is a shell command that reads the brief on stdin and prints a unified diff.
`ATTEST_WORKER_CMD` does the same for the spec-mode worker, which defaults to OpenAI. This is the
seam where a company plugs in its own agent.

Other commands: `attest badge` writes `docs-proven.svg` and `PROOF.md` for the last run.
`attest ask "<question>" --card <name>` sends a how-to question to GPT and saves the answer as
`demo/answers/<name>.md`, ready to prove like any other doc (Procedural Truth).

## Demo

One command runs the demo beats in one live browser tab, with a pause between beats for clicking
badges: `powershell -File demo/record.ps1`. The beats are the Flask proof, the Kane Factory on
the Acme CRM spec (mode 4), the Vite catch and repair, the refused counter repair, and this README
proving itself; add `-WithGitHub` for a Procedural Truth answer on GitHub (signed in).

## Known limitations

- Shell claims run on the host with your environment, files and network. The scratch directory
  is not a sandbox; only prove docs you would run by hand.
- Kane runs a whole test file before Attest judges the first claim, so a later step's side effect
  can happen even when an earlier claim ends stale. Use `--stop-before` for irreversible steps.
- How a step is phrased for Kane decides stale versus broken; the phrasing is a tuned heuristic.
- Label matching (token-set ratio with negation and number guards) suggests a verdict; it does
  not prove intent. Contractions can slip past the negation guard.
- Cards that need a logged-in site need a signed-in Chrome reached over CDP.

## Built on event day

Attest is a SyberLabs entry for Kane CLI Hack Day, San Francisco, October 24 2026. The day added
the Kane test file writer, the NDJSON and evidence pack reader, step phrasing that lets the
verdict rules decide, recordings per document version, the authority gate, receipts and spec
mode. By mid-afternoon Attest had proven 9 documents across 23 turns, with 31 browser claims
proven by Kane, each with a screenshot. Real drift it caught: Vite's "Vite + React" is now
"Get started", and GitHub's "Security & analysis" is now "Advanced Security".

Links: the event proof page with Attest's Kane run reports is https://kaneclihack.sh/t/attest/proof
and the factory plan is https://github.com/SyberLabs/attest/issues/10.
