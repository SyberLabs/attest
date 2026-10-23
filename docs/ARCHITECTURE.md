# Docs That Can't Lie: architecture and pre-build requirements

Oct 22, 2026 · @mateo

## Context

Attest (working name) is the Saturday entry for [Kane CLI Hack Day](https://luma.com/kane-cli-hack-day), Oct 24 2026, San Francisco, hosted by TestMu AI and supported by OpenAI. It targets the special prize, "Most Innovative Use of Kane CLI," while still clearing the two hard rules: Kane must verify the app, and the app must run and survive a live demo. Submissions lock at the deadline; demos run after, with no fixes allowed.

What the event rewards, from the Luma page and earlier Kane hack days:

| Dimension | What judges look for | How Attest answers it |
| --- | --- | --- |
| Ships | A running app at demo time | `npx attest <repo>` opens a local page in one command |
| Verified | Kane runs against the app, ideally inside the agent loop | Kane proves every browser step of a README; Kane also proves Attest's own README |
| Closed loop | Agent reads Kane output and keeps fixing | A broken step becomes a brief; the agent patches the doc; Kane re-proves that one step |
| Craft | Something a developer would install tonight | Verdicts keyed to doc line spans, sealed evidence per step, a ledger |
| Special prize | Something they didn't see coming | Kane tests documentation, not apps |

Event rules that shape the plan: teams of up to four, solo allowed; a pre-existing project is allowed but the Kane integration must happen on the day, and judges weight the new work; every builder gets $250 in OpenAI credits and 3 months of KaneAI Pro; keep a recorded run as backup. Mateo is likely solo on an old Lenovo laptop that runs one coding agent at a time, so the build is sized for one person and one machine.

## Idea and pitch

**One line:** "Point Attest at a README. Kane runs every step in a real browser and the doc comes back with each step marked proven, stale or broken, with a screenshot behind every badge."

**Thesis:** Documentation is the test suite nobody runs. Every getting-started guide is a sequence of claims: run this, open that, click here, see this. Kane can execute those claims. A doc whose claims have been executed can't lie; a doc whose claims haven't is a rumor.

**Why it is the special-prize entry:** Kane is sold as the thing that verifies the app your agent wrote. Attest points it the other way, at the words that describe the app. The same evidence packs, the same NDJSON, the same closed loop, on a surface the organizers have not shown.

**Why it matters beyond the room:** AI coding agents learn how to use a library from its docs. GitHits indexes those docs and hands them to agents as context. A stale doc step becomes a hallucinated agent step. Attest gives that chain a trust signal it does not have today: not "this doc exists" but "this doc still works."

**What the judges see in three minutes:** a real open-source README goes in; a browser opens and walks the quickstart; badges appear on the doc line by line; one step comes back stale because the UI changed; the agent patches the sentence with a GitHits citation; Kane re-proves that one step; the doc is green. Then: Attest's own README, proven by Kane, so the tool is verified by the rule it enforces.

**The climax: Procedural Truth.** Docs are the first liar Attest catches. The second is an AI's how-to answer. A judge picks a question, GPT answers it, and Kane follows the answer step by step in a real browser, badging each step. Same engine, different input; see the Procedural Truth section.

## Architecture

Attest is a Python CLI with five layers and one loop. Kane is the only part that touches a browser; everything else is deterministic code or one structured LLM call.

&#91;embedded content: Attest pipeline · 9 parts, 1 loop\]

The doc is read at a fixed commit. One LLM call turns it into typed claims, each pinned to a line span. Shell claims run in a subprocess; browser claims become a Kane `_test.md` and run with `testmd run --agent`. Pure Python rules turn exit codes and Kane's evidence pack into a verdict per claim. Proven claims render; stale or broken ones become a brief, grounded by GitHits, which an agent turns into a doc patch. Only the patched step re-proves.

**Layers**

1. **Ingest.** Fetch the README or guide at a commit, hash it, split into numbered lines. Source: GitHub raw, or `githits read github:owner/repo README.md`.
2. **Compile.** One OpenAI structured-output call (the $250 credits) emits a `ClaimSet` against a strict JSON schema. Every claim carries the line span it came from; a claim with no span is rejected by the schema.
3. **Execute.** Shell claims run in order in a scratch directory with a timeout; a browser claim pauses the shell runner, writes the Kane test, runs it, and collects the pack.
4. **Verdict.** No model. Rules compare what Kane observed (via `store as`) with what the doc promised.
5. **Render.** A single HTML page: the doc, a badge per claim, a screenshot behind each badge, and a ledger.

**The loop.** A brief (doc line, Kane failure record, screenshot, observed label) plus GitHits grounding (`search --source docs`, `grep`, `pkg changelog`) goes to `codex exec` or `claude -p`. The agent edits the doc, not the app. Attest re-compiles only the affected claim and re-runs that one step. One attempt, then a human.

**Self-check.** Attest's own README is the last target. Kane proves it, which satisfies "Kane must verify your app" without a separate test suite.

## Components

Nine modules, one package (`attest/`), one entry point (`attest prove <repo-or-path> [--doc README.md]`). Only `kane` and `loop` are written on Saturday; the rest is pre-build.

| Module | Responsibility | Model or tool | Built |
| --- | --- | --- | --- |
| ingest | Fetch doc at a commit, hash it, number lines, detect fenced code blocks and URLs | `httpx`, or `githits read` | Thu |
| asker | Send a card's question to GPT with a fixed "numbered browser steps" prompt; save the answer as a doc with model and timestamp | OpenAI API | Fri |
| compiler | Doc lines to `ClaimSet` with line spans and kinds (shell, browser, assert, prose) | OpenAI structured outputs, strict JSON schema | Thu |
| shell | Run shell claims in a scratch dir with timeout; capture exit code, stdout, the port a server opens | `subprocess`, `asyncio` | Thu |
| kane | Write `_test.md` from browser claims, run `kane-cli testmd run --agent`, parse NDJSON, locate and unzip the evidence pack | kane-cli 0.8+ | Sat |
| verdict | Pure rules: proven, stale, broken, unverifiable; compare observed labels with doc text | Python, pytest-covered | Thu (rules), Sat (Kane inputs) |
| grounding | Pull the doc section, repo grep hits and changelog lines for a failed claim; for AI answers, the cached official help page | `githits search`, `grep`, `pkg changelog`; cached help pages | Fri |
| loop | Build a brief, call the coding agent once, re-prove the affected claim | `codex exec` or `claude -p` | Sat |
| render | Badged doc page, screenshot per badge, ledger; serve on localhost | Jinja2, one HTML file | Fri shell, Sat wiring |

Supporting files: `targets.yaml` (the demo repos and the commit to pin), `rules.yaml` (thresholds such as label match ratio), `demo/` (three prepared docs with a known stale step).

## Data model

Six Pydantic models. The invariant that holds the whole thing together: no `Claim` without a `Span`, and no `Verdict` without an `Evidence` pointer.

```python
class Span(BaseModel):
    start_line: int          # 1-based, inclusive
    end_line: int
    text: str                # exact doc text, for rendering and label match

class Claim(BaseModel):
    id: str                  # c01, c02 ... in doc order
    kind: Literal["shell", "browser", "assert", "prose"]
    span: Span
    command: str | None      # shell: the exact command from the fenced block
    objective: str | None    # browser: the Kane step body, ends in a check
    expects: str | None      # the UI text or output the doc promises ("Vite + React")
    store_as: str | None     # browser: variable Kane fills with what it saw
    depends_on: list[str]    # claims that must be proven first (server up before page open)

class Doc(BaseModel):
    source: str              # URL or path
    commit: str | None
    sha256: str
    lines: list[str]
    claims: list[Claim]

class Evidence(BaseModel):
    claim_id: str
    kind: Literal["shell", "kane"]
    exit_code: int | None
    stdout_tail: str | None
    execution_id: str | None # Kane pack id
    pack_path: str | None    # .testmuai/evidence/<id>.evidence
    step_dir: str | None     # tests/<id>/steps/<n>-<step>/
    screenshot: str | None   # extracted screenshot.png path
    observed: dict[str, str] # final_state from run_end (store_as values)
    remark: str | None       # Kane's step remark, used in briefs

class Verdict(BaseModel):
    claim_id: str
    status: Literal["proven", "stale", "broken", "unverifiable", "skipped"]
    reason: str              # one sentence, rule name included
    evidence: Evidence | None
    label_match: float | None  # 0-1 similarity between expects and observed

class Brief(BaseModel):
    claim_id: str
    verdict: Verdict
    doc_excerpt: str         # the span plus 3 lines each side
    grounding: list[str]     # GitHits lines with source refs
    patch: str | None        # the agent's unified diff on the doc
    attempts: int
```

The `ClaimSet` the compiler returns is just `list[Claim]`, validated against the JSON schema generated from `Claim`. The ledger is append-only JSONL of `Verdict` rows plus a `turn` field, so the page's counters are a `GROUP BY` over one file.

## Step compiler

The compiler is one LLM call with a strict schema, not an agent. It reads the numbered doc and returns `list[Claim]`. Three rules make its output checkable:

1. **Fenced code blocks are shell claims by construction.** The compiler does not get to paraphrase them; `command` must equal the block text verbatim (the schema is validated post-hoc against the doc). A block that contains only output, or starts with `#`, becomes `assert`.
2. **Every browser claim ends in a check and stores what it saw.** The objective is rewritten in Kane's house style: "Go to {{url}}, click the button labelled 'Create project', and store the exact text of the button you clicked as 'c07\_label'." The `store as` pattern is what turns Kane from a yes/no oracle into a witness.
3. **Prose that promises nothing observable is `prose`.** It renders without a badge. Attest never invents a check the doc did not make.

Kane's own `context ingest` → `context extract` → `design tests` chain is the stretch version of this compiler, and the better story for the special prize since it uses Kane's requirements graph on a README. Thursday's dry run decides: if `context extract --mode agent` on a README yields use-cases whose citations map cleanly to lines, swap it in for browser claims and keep the OpenAI compiler for shell claims. If not, the OpenAI compiler stays the trunk and the Kane chain is mentioned as next work.

**Browser claims to `_test.md`.** Consecutive browser claims that share a page become one test file; a shell claim in between splits the file. Frontmatter: `mode: testing`, `max_steps: 15`, `url` from the last shell claim that opened a port (detected from stdout, e.g. `Local: http://localhost:5173`). Each claim is one `## c07` step, so Kane's step index maps back to a claim id and a line span.

**Confidence.** The compiler returns `kind` only; there is no soft score to tune. The one tunable is `label_match` in the verdict rules.

## Verdict rules

No model decides a verdict. The rules below run in order; the first that matches wins. They are pytest-covered against fixture packs before Saturday.

| Status | Rule | Example |
| --- | --- | --- |
| skipped | A claim it depends on is not proven | Page open skipped because `npm run dev` failed |
| broken | Shell: non-zero exit, or timeout. Browser: Kane step `failed`, or `run_end.status` is `failed` | "Click Create project" and no such control exists |
| stale | Browser step passed but `label_match(expects, observed) < 0.8`, or the doc's quoted text is absent from the page while the step still completed | Doc says "Create project", button now says "New project" |
| proven | Shell: exit 0 and, if `expects` is set, it appears in stdout. Browser: step passed and `label_match ≥ 0.8` | `Local: http://localhost:5173` printed; page title contains "Vite + React" |
| unverifiable | `kind == prose`, or a browser claim with no reachable URL | "This works on most Linux distributions" |

`label_match` is a normalized token-set ratio (`rapidfuzz.fuzz.token_set_ratio / 100`), case-folded, punctuation stripped. The 0.8 threshold is set on Thursday by running the rules over the 40-claim fixture corpus and picking the value that separates the hand-labelled stale cases from the proven ones.

**Invariants that cannot be overridden:**

- A verdict other than `unverifiable` always carries an `Evidence` pointer. A badge with nothing behind it is a bug.
- A browser claim is never `proven` from the shell runner, and a shell claim is never `proven` from Kane.
- Healing counts against the doc: if Kane's remark shows it re-authored the step to get past a mismatch, the claim is at best `stale`.
- The loop never edits the target app. It only edits the doc.

**Thresholds file (`rules.yaml`):** `label_match: 0.8`, `shell_timeout_s: 180`, `kane_max_steps: 15`, `loop_attempts: 1`.

## Closed loop

The loop fixes the doc, not the app. That keeps it safe to run on a repo you don't own and keeps the demo honest: the thing Kane tested is the thing that changes.

**Trigger.** Any `stale` or `broken` verdict on a claim with `kind in (browser, shell)`.

**Brief.** One Markdown message, under 60 lines:

1. The doc excerpt: the claim's span plus three lines each side, line-numbered.
2. The verdict and rule name.
3. What Kane saw: `observed` values, the step remark, the failure record from `steps/<n>/failure.yaml`, and the path to `annotated.png`.
4. Grounding from GitHits, as lines with source refs:
   - `githits search "<expects text>" --in github:owner/repo --source docs --json` to find where the current docs describe the same control
   - `githits grep -F "<expects text>" github:owner/repo --corpus all` to find the string in source and docs at the pinned ref
   - `githits pkg changelog <registry>:<pkg> --changes important --limit 5` when the doc names a package version
5. The instruction: "Edit only `<doc path>`. Change the smallest span that makes the claim true. Return a unified diff."

**Agent call.** `codex exec --full-auto` or `claude -p` with the brief on stdin, working directory set to the scratch checkout. One attempt (`loop_attempts: 1`). The diff is applied with `git apply --check` first; a diff that touches any file other than the doc is rejected.

**Re-prove.** Attest re-runs the compiler on the changed span only, regenerates the affected `_test.md`, and runs that one test. Kane replays unchanged steps from cache, so a one-step fix costs one authored step. The ledger records both runs under the same `turn`.

**Stop.** If the second verdict is still not `proven`, the badge shows the attempt and the diff, and the loop stops. A human decides. This is where the demo shows the brief on screen even if the agent misses.

## Procedural Truth

Procedural Truth is the climax beat: Kane follows an AI's how-to answer in a real browser and returns it badged, step by step. Same engine as Attest, different input. A README, an AI answer and a help page are all a list of claimed steps; the compiler, verdict rules, loop and render page carry over unchanged.

**Why it has gravity.** People ask an AI "how do I do X?" and follow the steps without checking them. A wrong fact is embarrassing; a wrong procedure sends someone to a renamed button, the wrong form, or a setting their plan doesn't include. Everyone else at the event uses Kane to check code an AI wrote. Procedural Truth uses Kane to check instructions an AI wrote. Stage line: "Kane runs on OpenAI models. Today, OpenAI checks OpenAI."

**Input adapter.** One new module, `asker`, sends the judge's question to the OpenAI API (the $250 credits) with a fixed system prompt ("answer as numbered steps a person can follow in a browser"), and saves the answer as `demo/answers/<card>.md` with the model name and timestamp. From there it is just a doc: `attest prove demo/answers/<card>.md`. The loop's grounding for AI answers adds one source: the official help page for the product, fetched and cached Friday.

**The card set.** Five questions printed on a card; a judge picks one. Each is pre-run Friday with its outcome recorded, so a live miss still has a known shape.

| Card | Login | Why it is on the card | Expected catch |
| --- | --- | --- | --- |
| How do I enable free secret scanning on my GitHub repo? | Throwaway GitHub account | Mateo hit this himself. The repo setting is now labelled "Secret Protection"; older write-ups say "secret scanning". Free only on public repos | Stale label; missing public-repo condition |
| How do I appeal a parking citation in San Francisco online? | None | Public city site, multi-step, real stakes | To be confirmed in Friday's pre-run |
| Where do I download Census population data for my county? | None | Public federal data site with deep navigation | To be confirmed in Friday's pre-run |
| How do I make a GitHub repository public? | Throwaway account | Short, reversible, a likely clean pass | Probably proven: shows the tool doesn't cry wolf |
| How do I add a collaborator to a GitHub repo? | Throwaway account | Reversible, settings path has moved over time | To be confirmed in Friday's pre-run |

Keep the cards whose Friday outcome is interesting either way: at least one clean pass and at least one catch. Replace any card that hits a CAPTCHA.

**Logged-in cards.** Kane reuses a signed-in Chrome profile (`kane-cli config chrome-profile`), so sign in to the throwaway GitHub account once by hand in that profile, 2FA included, on Friday. Rules for these cards:

- Throwaway account and a public test repo only, never Mateo's real account
- Only reversible actions: toggling a setting on a test repo is fine; payment, deletion, invitations to real people are out
- The compiled Kane test stops before any irreversible button unless that click is the point of the card, and the card says so
- No fake data typed into real government forms; public-site cards stop at the form

**What the judges see.** The question, GPT's answer appearing as a doc, Kane walking it in Chrome, badges filling in. On the GitHub card: the "secret scanning" step goes amber with a screenshot showing "Secret Protection"; the loop rewrites the step citing GitHub's current docs; Kane re-proves it green. Story line: "I couldn't do this myself last week, so I built the thing that checks the instructions."

## Rendered output

One HTML file at `.attest/<doc-hash>/index.html`, served on `localhost:4848` by `attest serve`. No framework; Jinja2 plus 200 lines of CSS. It has to look like the doc, not like a dashboard.

**The doc view.** The original Markdown rendered as-is. Each claim's span gets a left-gutter badge: proven (green), stale (amber), broken (red), unverifiable (grey, no fill). Prose has no badge. Hovering a badge shows the reason sentence; clicking opens the evidence drawer.

**The evidence drawer.** For a browser claim: the annotated screenshot from the pack, the Kane step remark, the `observed` value next to the doc's `expects`, and a link to `kane-cli evidence serve` for the full pack. For a shell claim: the command, exit code, and the last 20 lines of stdout. For a looped claim: the brief, the diff, and both verdicts side by side.

**The ledger strip.** Fixed at the top: `N claims · P proven · S stale · B broken · U unverifiable · K Kane steps · C authored, R replayed`. The session view adds today's turns, loops closed, and credits spent (from `kane-cli balance` before and after). These numbers are read from `ledger.jsonl`, never computed in the template.

**Export.** `attest badge` writes a `docs-proven.svg` (shields-style: `docs | 11/12 proven`) and a `PROOF.md` summary table, so a maintainer can put the result in their README. This is the GitHits hook: the badge is the trust signal an index could read.

## Target projects

The demo needs docs whose steps end in a browser, start in under a minute on the Lenovo, and have at least one step that is stale today. Thursday's job is to run the candidates and keep three. Pin each to a commit in `targets.yaml` so Saturday's run matches Thursday's.

| Candidate | Why it fits | Shell cost | Risk |
| --- | --- | --- | --- |
| Vite "Getting Started" (`npm create vite@latest`) | Scaffold, `npm run dev`, open `localhost:5173`, see the "Vite + React" page. Pure Node, fast | \~40 s with warm npm cache | Scaffold prompt is interactive; pass template flags |
| Streamlit (`pip install streamlit`, `streamlit hello`) | One command opens a multi-page web app with named sidebar entries. Rich UI to click | \~60 s, Python only | Telemetry prompt on first run; set `STREAMLIT_BROWSER_GATHER_USAGE_STATS=false` |
| Flask quickstart | `flask --app hello run`, open `localhost:5000`, see "Hello, World!" | \~10 s | Too small to show a stale step; keep as the safe opener |
| Gradio quickstart | `pip install gradio`, run a 6-line script, open `localhost:7860`, type in a box, see output | \~60 s | Version drift in the snippet is common, which is a feature here |
| Attest's own README | The self-check. `pipx install .`, `attest prove demo/flask.md`, open `localhost:4848`, see badges | \~20 s | Must be proven last, after everything else is frozen |

Selection rule: keep the fastest clean target (opener), one target with a real stale step today (the loop beat), and Attest itself (the close). If no candidate is stale on Thursday, pin a target to an older doc commit whose UI text has since changed; say so on stage. Fabricating a stale step in a live repo is out.

Every target also needs a `demo/<name>.md` copy of the doc at the pinned commit, so the demo does not depend on GitHub being reachable.

## Demo script

Three minutes, four beats. The browser is visible the whole time; Kane's Chrome window is the show.

| Time | Beat | What judges see | Say |
| --- | --- | --- | --- |
| 0:00 | Frame | Cover slide: a README with grey badges | "Every getting-started guide is a list of claims nobody runs. Kane can run them." |
| 0:15 | Beat 1: prove a doc | `attest prove demo/flask.md`. Shell steps run; Chrome opens; badges turn green line by line | "Kane walked the quickstart in a real browser. Every badge has a screenshot behind it." Click one badge |
| 0:45 | Beat 2: catch and fix | `attest prove demo/<stale target>.md`. One badge turns amber; press `f`; brief with GitHits citation; one-line diff; Kane re-proves that step green | "The doc still works, but it lies. One attempt, one step re-proved." |
| 1:30 | Beat 3: Procedural Truth | A judge picks a card. GPT answers live; the answer appears as a doc; Kane walks it in Chrome; badges fill in. On the GitHub card, "secret scanning" goes amber against "Secret Protection" and the loop fixes it | "Kane runs on OpenAI models. Today, OpenAI checks OpenAI." |
| 2:30 | Beat 4: the mirror | Attest's own README, already proven, badges green; ledger strip shows the day's numbers | "Attest's own README is proven by Kane." |
| 2:45 | Close | Final slide: `npx attest`, badge SVG | "Docs were the first liar we caught." Stop before 3:00 |

**Prerequisites before demo time:**

- Three targets proven at least twice on the laptop, with warm npm and pip caches
- `demo/` docs are local copies; nothing in the demo fetches GitHub
- `loop` rehearsed on the stale target with a fix under 60 seconds; if slower, start the loop during beat 2's narration
- A recorded run of all four beats with its evidence packs, on the laptop and on the phone
- Kane balance checked before and after the rehearsal, numbers written into the ledger slide
- Chrome window pre-sized so terminal and browser share the screen

* Every Procedural Truth card pre-run twice, outcome written on the back of the card; the GitHub cards run in the throwaway account's Chrome profile, already signed in
* GPT answers for all five cards saved in `demo/answers/` as a fallback if the live API call is slow

## Pre-build requirements

The event allows a pre-existing project but the Kane integration must happen on the day, and judges weight new work. So the line is: everything that is not Kane is fair game before Saturday; `kane.py`, `loop.py` and the Kane inputs to the verdict rules are written on the day. Running one Kane flow beforehand is explicitly asked for by the organizers.

**Thursday, Oct 22: admin and corpus**

- [ ] Confirm the seat: Luma shows registration closed, so check the confirmation email and the cheat sheet it links; contact the host if nothing arrived
- [ ] Install kane-cli (`npm install -g @testmuai/kane-cli`), `kane-cli login`, run one flow with `--agent`, read the NDJSON and find the pack under `.testmuai/evidence/`
- [ ] Unzip that pack; confirm `steps/<n>/screenshot.png`, `annotated.png`, `step.json`, `failure.yaml` and `logs/<n>-network.har` are where evidence.md says
- [ ] Claim the $250 OpenAI credits; set `OPENAI_API_KEY`
- [ ] Install GitHits (`npx githits@latest init`), run `search`, `grep` and `pkg changelog` once each with `--json`
- [ ] Install the coding agent for the loop (Codex CLI or Claude Code) and run `codex exec` or `claude -p` headless once
- [ ] Run the five candidate docs by hand on the Lenovo; record start-up time and any interactive prompts; pick three targets and pin commits in `targets.yaml`
- [ ] Try `kane-cli context ingest README.md && kane-cli context extract --mode agent` on one target; decide trunk vs stretch for the compiler
- [ ] Create repo `SyberLabs/attest` with the Pydantic models, folder layout, `rules.yaml`, `targets.yaml`, and an empty `kane.py` and `loop.py`

**Friday, Oct 23: pipeline without Kane**

- [ ] Write `ingest.py` and `compiler.py` with the strict JSON schema; run on all three targets; hand-check that every fenced block became a shell claim verbatim
- [ ] Write the 40-claim fixture corpus (`tests/fixtures/`): 25 clean, 8 stale by label, 4 broken, 3 prose; each with hand labels
- [ ] Write `verdict.py` and its pytest suite against the fixtures; set `label_match` in `rules.yaml`
- [ ] Write `shell.py`: scratch dir, timeout, port detection from stdout, stdout tail capture
- [ ] Write `grounding.py` against GitHits `--json` output; cache responses to disk for the demo
- [ ] Write `render.py` and the template; render a fake ledger and three fake verdicts; check it on the laptop screen at demo resolution
- [ ] Write `demo/<name>.md` copies at the pinned commits
- [ ] Warm npm and pip caches for all targets; snapshot the scratch environments
- [ ] Draft the four cover and close slides plus README; leave the ledger numbers blank
- [ ] Pack: laptop charger, phone with the pitch deck, transit times to the venue for a 10:00 start

**Friday additions for Procedural Truth**

- [ ] Create a throwaway GitHub account with 2FA and a public test repo; sign in once in a dedicated Chrome profile and point `kane-cli config chrome-profile` at it
- [ ] Write `asker.py`; generate answers for the five cards; save to `demo/answers/`
- [ ] Pre-run each card by hand: confirm no CAPTCHA, note the expected catch, drop cards that fail for reasons other than the instructions
- [ ] Fetch and cache the official help page for each card (GitHub's secret scanning docs first) for the loop's grounding
- [ ] Print the card set; write each card's Friday outcome on the back

**Saturday 10:00 to 10:30, before writing Kane code:** show the organizers what existed at 10 AM (the repo as committed Friday night), so the new-work weighting is clean.

## Build-day plan

Sized for one person on one laptop, with a second and third slot if teammates appear. Submissions lock at 18:00 per the event page; freeze at 17:00.

| Start | Work | Owner | Checkpoint |
| --- | --- | --- | --- |
| 10:00 | Show the Friday repo to organizers; `kane-cli whoami`, `balance`; run one target by hand | Mateo | 10:30 · Kane runs on this venue network |
| 10:30 | `kane.py`: claims to `_test.md`, `testmd run --agent`, NDJSON parse, pack locate and unzip, screenshot paths | Mateo | 12:00 · Flask target end to end, badges green |
| 12:00 | Verdict rules take Kane inputs: `observed` vs `expects`, healing detection from remarks; run the fixture suite plus one real pack | Mateo | 13:00 · stale target shows amber on the right line |
| 13:00 | `loop.py`: brief, GitHits grounding, agent call, diff guard, single-step re-prove | Mateo | 15:00 · loop closes on the stale target |
| 15:00 | Wire render to the real ledger; `attest serve`; prove Attest's own README | Mateo (or teammate 2 from 10:30) | 16:00 · self-check green |
| 16:00 | Rehearse the four beats twice; record the backup run; fill ledger numbers into slides | Mateo | 16:45 · rehearsal under 3:00 |
| 16:45 | README, badge export, submit, freeze | Mateo | 17:00 · submitted |

**If a teammate joins:** teammate 2 owns render and slides from 10:30; teammate 3 owns the loop from 13:00 while Mateo hardens `kane.py` and the verdict rules.

**Cut order if late:** badge SVG export, the evidence drawer's HAR view, the Kane `context extract` stretch, the session ledger (keep the per-doc strip). **Never cut:** one real Kane catch on a real doc, the self-check, the recorded backup.

**Two gates:**

1. 12:00: if `kane.py` is not round-tripping on Flask, drop multi-step test files and run one `kane-cli run` per browser claim. Slower, but it demos.
2. 15:00: if the loop does not close, demo the brief on screen and apply the diff by hand; say so. The catch is the story; the loop is the craft.

## Risks and fallbacks

| Risk | Early sign | Fallback |
| --- | --- | --- |
| Kane cannot reach `localhost` servers started by the shell runner | Thursday: Flask target fails at the first browser step | Run the server outside the scratch dir, pass `--url`; last resort, `--cdp-endpoint` to a Chrome Attest launches |
| `store as` values come back empty or paraphrased | `final_state` missing the label key | Ask for "exact text" in the objective; else mark `unverifiable` and say so |
| No candidate doc is stale today | Thursday run: all three targets green | Pin one target to an older doc commit whose UI changed; disclose it |
| The LLM compiler mangles a fenced block | Post-hoc check fails on verbatim match | Reject the claim set and retry once with the failing line quoted; else treat all fenced blocks as shell claims by regex and let the LLM only do browser claims |
| Old Lenovo cannot run Chrome, a dev server and an agent at once | Thursday timing over 90 s per target | Drop the agent to `claude -p` with a small model; run the loop while nothing else runs; keep Streamlit out |
| A judge's card hits a CAPTCHA or a login prompt on stage | Friday pre-run | Drop that card from the set; if it happens live, show the recorded run for that card |
| GPT's live answer is correct and nothing gets caught | Friday pre-run shows a clean pass | That is a result: "proven" badges show the tool doesn't cry wolf; then run the GitHub card |
| The throwaway GitHub session expires before the demo | Saturday morning check | Re-sign in by hand at 16:00; keep 2FA device on hand |
| Venue network drops | It just happens | Local `demo/` docs and saved answers, cached GitHits JSON, recorded run on phone |
| Kane credits run short on the day | `kane-cli balance` below 500 after rehearsal | Replay from cache; author only the stale steps; 3 months of KaneAI Pro starts on event day |
| Judges ask "where is the app?" | A doc checker feels thin | Point at the served page, the live card run, the self-check, and the badge export |
| Behind schedule | 13:00 checkpoint missed | Gate 1 and the cut order above |

## Open questions

- [ ] Did the confirmation email arrive? Registration shows closed on Luma; the cheat sheet and the OpenAI credit claim are in that email
- [ ] Does `kane-cli testmd run --agent` emit per-step `final_state`, or only at `run_end`? Decides whether one test file can hold many claims or each claim needs its own run
- [ ] Does `context extract --mode agent` accept a README as a source and cite line numbers usable as spans? Decides the compiler trunk
- [ ] Does Kane's remark text reliably reveal healing (re-authoring after a mismatch)? If not, drop the healing invariant and rely on `label_match` alone
- [ ] Does Kane keep a Chrome profile's GitHub session between runs, and does GitHub ask for re-authentication on Settings pages (sudo mode)? Decides whether the GitHub cards are live or recorded
- [ ] Which three non-GitHub cards actually produce a catch? Friday pre-run answers it
- [ ] Can the Lenovo run Chrome, a Vite dev server and `claude -p` together within memory?
- [ ] Name: "Attest" is a placeholder. Check npm for collisions before printing it on a slide
- [ ] Team: solo, or post a pitch in the event channel for one person on render and slides?

## Sources

- [Kane CLI Hack Day, supported by OpenAI (Luma)](https://luma.com/kane-cli-hack-day): rules, tracks, prizes, pre-existing project policy, credits
- [Kane CLI README](https://github.com/LambdaTest/kane-cli): commands, `--agent` NDJSON, `run_end` schema, exit codes, `store as` pattern, Chrome profile config
- [Kane CLI agent guide](https://www.testmuai.com/kane-cli/agents.md): skill install, what leaves the machine, headless behaviour
- [Assurance lifecycle](https://github.com/LambdaTest/kane-cli/blob/main/docs/user-guide/assurance/overview.md): `context ingest`, `extract`, `review`, `design tests`, `cover`, trust and freshness vocabulary
- [Evidence packs](https://github.com/LambdaTest/kane-cli/blob/main/docs/user-guide/evidence.md): zip layout, `steps/<n>/screenshot.png`, `failure.yaml`, HAR per run, project store path
- [Writing test.md files](https://github.com/LambdaTest/kane-cli/blob/main/docs/user-guide/testmd/overview.md): frontmatter keys, step shape, `optional`, variables
- [GitHits CLI command reference](https://docs.githits.com/cli/commands): `search`, `grep`, `read`, `pkg changelog`, `pkg upgrade-review`, `--json`
- [Enable secret scanning (GitHub Docs)](https://docs.github.com/en/code-security/how-tos/secure-your-secrets/detect-secret-leaks/enable-secret-scanning): current "Secret Protection" label; free only on public repos
- [GitHub rolls out free secret scanning for public repos (BleepingComputer, 2023)](https://www.bleepingcomputer.com/news/security/github-rolls-out-free-secret-scanning-for-all-public-repositories/amp/): the older "secret scanning" wording the card's stale step is checked against
- Internal: KaneImpact deck (SyberLabs, Oct 2026) for the credit model, checkpoint style and cut-list discipline reused here
