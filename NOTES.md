# NOTES

Decisions and deviations from docs/ARCHITECTURE.md, each with a reason. Newest at the bottom.

## Environment (2026-10-23)

- Python 3.12.6 is what the Lenovo has; pyproject requires >=3.11. No pipx installed, so the dev loop uses `.venv` and `pip install -e .`. `pipx install .` is still the documented install path.
- No `codex` or `claude` binary on PATH in this shell. The loop takes the agent command from `ATTEST_AGENT_CMD` (default `claude -p`) and tests use a fake agent script.
- `OPENAI_API_KEY` is set in the environment. `GITHITS_API_TOKEN` is not; GitHits calls go through `npx githits@latest` unauthenticated and are cached.
- GitHub repo `SyberLabs/attest` does not exist yet. Created at M11 with `gh repo create` under the org account (sykosyber), not Mateo's personal account.

## M1 Models

- `Evidence.kind` is `shell | browser`, not `shell | kane`. Reason: the pre-event build must not know Kane; a browser runner of any kind produces a `BrowserObservation`.
- Dropped Kane-specific Evidence fields (`execution_id`, `pack_path`, `step_dir`, `remark`). `BrowserObservation.note` is the neutral slot for a runner's free-text remark; `screenshot` is a path. Saturday maps pack contents into these.
- Added `Evidence.command`, `Evidence.timed_out`, `Evidence.detected_url` for the shell runner. Reason: the drawer shows the command; timeouts need to be broken without an exit code; the detected URL is what later browser claims open.
- `skipped` verdicts carry the evidence of the dependency that failed, with its original `claim_id`. Reason: the doc's invariant says only `unverifiable` may lack evidence, and a skipped badge should open on what broke upstream. The `evidence.claim_id == claim_id` check is relaxed for `skipped` only.
- `Doc` gained `fences` and `urls`. Reason: the compiler's verbatim check and the ingest acceptance test need fenced block ranges and URL lists; the doc lists them as ingest outputs without a home.
- Added `LedgerRow` so the JSONL ledger has a schema. The doc describes it as Verdict rows plus `turn`; the row also records `kind`, `evidence_kind`, `doc_sha256` and `looped` so the strip can be a plain group-by.
- Claim ids must match `c\d{2,}`. Reason: cheap guard against the LLM inventing ids.
- `rules.yaml` omits `kane_max_steps` (Kane-specific), left as a SATURDAY comment.

## M2 to M4

- `demo/*.md` are executable adaptations, not byte copies, of the upstream docs. Reason: the upstream Flask quickstart shows `hello.py` as a Python block and Vite shows `$`-prompted, per-package-manager code groups; neither can run as written. Each demo file names its upstream path and pinned commit in its first paragraph, and `targets.yaml` records the same commit.
- The shell runner prefers Git Bash by its install path on Windows, since `bash` on PATH resolves to Cygwin here. Override with `ATTEST_SHELL`. It records the working directory after each block in a side file so `cd` carries across blocks.
- URL detection also accepts `http://0.0.0.0:<port>` and maps it to 127.0.0.1, because `python -m http.server` and several dev servers print that form.
- Compiler: the model wraps commands in fence markers and copies the gutter indentation about a third of the time, so fence matching is whitespace- and marker-lenient while the stored command is always the fence text from ingest. The span-text check is a containment test because the model quotes whole sentences that wrap across lines.
- Compiler calls use `reasoning.effort=low` on gpt-5 models: 18 to 20 s per doc instead of 40 to 70 s, same output. Model name comes from `ATTEST_OPENAI_MODEL` (default gpt-5-mini).
- Dependencies are chained deterministically (shell after previous shell; browser after previous shell and previous browser) instead of trusting the model's `depends_on`.
- `pipeline.py` is an extra module not in the layout: it holds the prove orchestration so `cli.py` stays thin and `loop.py` can call the same functions.

## M5 to M7

- `label_match` stays at 0.80: it is the only sweep value with zero misses on the corpus (stale tops out at 0.778, the nearest proven case is 0.848). See tests/fixtures/README.md.
- Ledger counts exclude prose rows, so "N claims" and the status counts add up.
- Assert claims (output-only fences) are judged against the previous shell claim's stdout. The doc did not say how assert claims are verified; this is the smallest rule that makes them checkable.
- BLOCKER (not code): GitHits needs `githits login` or `GITHITS_API_TOKEN`; without it every call returns `AUTH_REQUIRED`. The wrapper caches that as a grounding line so briefs still build. The `--json` payload shape is unverified (`# VERIFY:` in grounding.py); the parser walks the payload for any hit-like object. Mateo must log in once on Friday night and run `attest prove demo/vite.md --loop` to warm `cache/githits/`.
- cache/help: four of five card pages cached. SFMTA returns 403 to scripted fetches; save `sf-parking-citation.md` by hand from the browser (first line `<!-- source: URL -->`).

## M8 to M11

- Loop: the agent's diff is applied in a throwaway git checkout holding only the doc, with `git apply --check --unidiff-zero`; a diff naming any other path is rejected before git sees it. By default the patched doc is rendered and recorded but the source file is left alone; `--apply` writes it back. Reason: the demo should show the diff, not silently rewrite `demo/*.md` mid-run.
- Loop: other claims' spans are shifted by the net line change above them (difflib opcodes) so a patch that adds a line does not misplace later badges.
- Loop: the fenced-diff extractor requires the closing fence at column 0, because the doc's own fences show up as diff context lines starting with a space.
- Asker: answers are saved verbatim; one answer contains an em dash, so the em-dash check skips `demo/answers/`.
- README: the self-check clones the public repo first because the shell runner starts in an empty scratch directory. The install step is `python -m pip install .` since pipx is not installed on the laptop.
- Repo: created as a public repository under the SyberLabs org with the org account; the README's clone step depends on it being public.

## Event day (2026-10-24)

### 10:00 smoke test

- Machine: Python 3.11.4 (Anaconda), Node 22.17.0, Git Bash. No Friday prep on this machine: kane-cli was not installed, never logged in, no Chrome profile, no `OPENAI_API_KEY`, no `codex`/`claude` on PATH (a `claude.exe` ships inside the VS Code extension). Repo cloned fresh at `pre-event` (0567834); `pytest` 124 passed, 1 skipped.
- `npm install -g @testmuai/kane-cli` installed 0.9.0. `kane-cli --version`: 0.9.0. `kane-cli whoami`: authenticated, OAuth, user `syberlabssoftware`, profile default, env prod. `kane-cli balance`: 1200 credits at 10:12; 1061.35 at 10:27 after the smoke runs below.
- Credit cost seen: authoring a one-step test 11 to 14 credits; a replay 0; a step that fights its own assertion 21 to 44. With a 500 floor the day has about 560 credits left after the smoke test.
- Captures in `scratch/` (gitignored), sanitized copies in `tests/fixtures/kane/`.

### Answers to the open questions, from what Kane actually did

1. **Is localhost reachable from Kane's Chrome?** Yes. `kane-cli run "Go to http://localhost:5000 ..." --agent` against `flask --app hello run` passed in 25 s and stored `page_text = "Hello, World!"`. No `--url` or `--cdp-endpoint` workaround needed.
2. **Does final_state appear only in run_end, or per step?** There is no per-step final_state. In `testmd run`, every `## ` step is its own Kane run and emits its own `run_end`; the file ends with `test_md_summary` and `test_md_done`. Two traps: (a) on an authored run `final_state` is a summary map whose keys the model chooses (`greeting_text` instead of the `c07_label` we asked for); (b) on a replayed run `final_state` is absent. The exact `store as` key is always in `run_end.context.variables[<key>].value` (also `variables_out` on replay). Attest reads `context.variables` first, then `variables_out`, then `final_state`.
3. **How do step directories in the pack map to `## ` steps?** Not by ordinal. Pack step directories are Kane's action steps (navigate, analyze, assert, wait), several per `## ` step, named `steps/<ordinal>-<run>-<n>/`. `ordinal` counts across the whole file; the `<run>` prefix of the step id is the 0-based index of the `## ` step (`0-*` for the first `## `, `1-*` for the second; healed sub-runs look like `1-b35b0074-1`). Attest maps by that prefix. Screenshots are `screenshot.jpg`; `annotated.png` did not appear on navigate/analyze/assert steps, so the reader takes it when present and falls back to the last screenshot of the claim's steps.
4. **What does a step remark look like when Kane re-authored or healed a step?** Not observed. Replaying a cached witness step after the page text changed (`Hello, World!` to `Hello, Kane!`) did not heal: the cached extraction re-read the element and stored the new text, `adaptive_heal.triggered: false`, zero credits. The only re-author signals seen are structured, not remarks: `test_md_summary.steps.author_decisions`, `run_end.reason` (`"replay completed"` vs `"Objective completed"`) and `test_md_summary.adaptive_heal.triggered`. Decision: no healing verdict rule today (see 12:00 notes); drift shows up as stale through `label_match`, which is what the doc wants anyway.

### Step phrasing (decides stale vs broken)

- "Check that the page shows X and store ... as 'k'" makes X an assertion. When the page says something else, Kane does not store and report; it waits for the page to change, gets stuck (`stuck.ap_stuck`), and the step fails: broken, never stale. Adding "verify that element is visible" fails the same way; an "if X is absent, store the closest ..." clause makes Kane spawn conditional branches and wait.
- What works: `Store the exact visible text of the heading, label, button or message on the page that is closest to "X" as 'k'. Verify the page has finished loading.` The step passes and stores what the page says, so the verdict rules decide. Action claims keep their action text in front ("Click the count button once."). The doc's "ends in a check" is honoured by the load check, which cannot fail on a label mismatch.
- Failures carry a rich record: root `failure.yaml` points at `tests/<id>/steps/<dir>/failure.yaml`, which has `error.message`, `title`, `triage.rca.root_cause` and `triage.suggested_fix.summary`. The brief uses all four. The failure record is not always on the last failed step, so the reader follows the root pointer.

### 10:30 to 12:00 kane.py

- Gate 12:00 met at 10:35 without the fallback: `attest prove demo/flask.md --runner kane` proved all 4 claims with a real screenshot behind c07 (14.3 credits authored, then 0 on replay).
- One test file per run of consecutive browser claims, at a stable path `.attest/kane/tests/<doc>-<first claim>_test.md`, run with `cwd=.attest/kane` so packs land in `.attest/kane/.testmuai/evidence/` and Kane's replay cache (`output-<stem>/`) survives between proves.
- Screenshots: the pack path stays in the evidence (briefs name it); render copies it to `.attest/<hash>/shots/<claim>.<ext>` for the page.
- `kane_max_steps: 15` is in `rules.yaml`; a file holds at most 12 claims (`FILE_MAX_CLAIMS`).

### 12:00 to 13:00 verdicts on Kane input

- Healing rule left out. Reason: Kane never healed today, so there is no remark pattern to trust, and the one drift experiment showed a cached step re-reading the new text for free. A drifted UI already lands as stale through `label_match`. The runner still appends "adaptive heal triggered" to the note if Kane reports it, for the brief.
- Stale target is Vite, now `role: stale` in targets.yaml. Real Kane run: create-vite 9.3.1's React starter shows "Get started"; the doc (pinned 7e221fc) says "Vite + React". c07 on line 37 is stale, label_match 0.48, screenshot shows the new page. Kane also read c08 as "Count is 0" and c09 as "Count is 1", both of which match the doc once case is folded; Attest marks them skipped because they depend on the stale c07.
- `demo/vite.md` had an adaptation bug of ours, not upstream: line 32 said `cd my-react-app && npm run dev` after line 28 had already changed into the directory, so the shell runner (which carries `cd` across blocks) failed with exit 1. Fixed to `npm run dev`. With no OpenAI key on this machine yet, the compiler cache entry was derived from the old one by replacing c05's command with the new fence text (fenced blocks are shell claims verbatim by construction); `meta.mode` says `derived`.
- Credits: Vite authored 3 steps for 48.5. Balance after: about 997.

### 13:00 to 15:00 loop wired to Kane (done at 11:06)

- Gate 15:00 met at 11:06: `attest prove demo/vite.md --runner kane --loop` closed on the stale target. The agent (`claude.exe -p` from the VS Code extension, via `ATTEST_AGENT_CMD`) patched line 37 to "Get started", the no-key re-compile followed the new quote, Kane re-authored the file (28.9 credits) and c07 went green; c08 and c09 were judged from that same run (`after loop on c07`). 6 of 6 proven, both verdicts for c07 under one turn.
- First loop attempt failed for a reason outside Kane: an orphaned Vite dev server from the 10:40 run held port 5173, the new server moved to 5174, and Kane opened the doc's 5173 and saw a 404. Fixes: the shell session also kills whatever listens on the announced port at close, and steps point local doc URLs at the URL the server printed. Port 5173 was still held once after the successful loop run; killed by hand, to fix.
- Kane keeps one recording per test file, so a doc that alternates between original and patched wording would re-author every time. The runner snapshots `output-<stem>/` per file-text hash after a passing run and restores it when that text comes back. Both Vite versions are now recorded.

### Event platform (11:08)

- Ran the event installer as published (`install.ps1 -Yes`, 0.1.20): Codex CLI installed, Kane CLI upgraded to 0.10.0-rc.3 with its skill, `kanehack` 0.1.20, hack-os and hack-gtm skills, Codex connected to the `kane-hack-event` MCP server (config.toml backed up). Flask re-proved on rc.3 by replay, 0 credits, unchanged output.
- Account steps (password, register or sign in, team, claim, pair, Codex login) are Mateo's, in his own terminal.

### 11:10 to 13:00 Codex does the coding, cards, self-check

- Activation finished on the event side at about 11:50 (`kanehack claim`: "Plan activated."); the Kane balance went from about 890 to 100,885 credits. `kanehack login` (TestMu key) still rejects the key; help ticket H-0008. Reporting works without it: `attest prove ... --report` wraps each Kane run in `kanehack report` (KR-7 Flask, KR-8 Vite stale catch, KR-9 Vite re-prove after the loop, all VERIFIED on https://kaneclihack.sh/t/attest/proof).
- Codex CLI runs on Mateo's ChatGPT sign-in. Its Windows sandbox is disabled in this build (`windows_sandbox_service`: under development), so `-s workspace-write` silently becomes read-only and every shell command is blocked. With Mateo's OK, coding tasks run with `--dangerously-bypass-approvals-and-sandbox` and narrow briefs; every diff is reviewed, tested and committed by hand. Codex wrote: ledger Kane accounting, `--stop-before`, the click/URL step phrasing, the README update and kane default, the four review fixes, the demo beat scripts and `serve --doc`. Codex is also the loop's default agent, in the read-only sandbox (it returns a diff, it cannot edit).
- `codex exec` waits for more input on an open stdin, like `testrun run`: always end it with `< /dev/null`.
- A GPT-6 Astra review (Mateo, via the API credits) found three real bugs, each confirmed before fixing: `label_match("Enabled", "Not enabled")` was 1.0 (now a negation guard caps it at 0.5; corpus still clean at 0.80), unsafe link schemes and unescaped quotes in the report, and a prefix-based zip-slip check. Its other findings are known limitations (below).
- Self-check: `attest prove README.md --runner kane` is 6 of 6 proven. It first caught a real README bug: the repeated `cd attest &&` sent the third block into the package directory. Fixed the README, not the code. Run with a throwaway venv first on PATH so the README's `pip install .` does not touch `.venv` or Anaconda base.
- Cards: SF parking dropped (venue network has no IPv6 route; sfmta.org is IPv6-only here). Census ran 14 steps and failed at c15 ("type and select B01003"); too long for the demo. GitHub cards wait on the throwaway Chrome profile.

### Known limitations

- Shell claims run on the host with the user's environment, files and network. The scratch directory is not a sandbox; only prove docs you would run by hand.
- Kane runs a whole test file before Attest judges the first claim, so a later step's side effect can happen even when an earlier claim ends stale. Cards use `--stop-before` for irreversible or real-form steps.
- The negation guard is token-based; contractions lose their apostrophe in normalization ("can't" becomes "can t") and are not caught.
- Kane keeps one recording per test file; Attest snapshots `output-<stem>/` per file version to replay both a doc and its patch. If Kane changes its cache layout, the first run after re-authors.
- `git remote` still points at the pre-transfer `sykosyber/attest` URL, which redirects to `SyberLabs/attest`.

### Numbers for the slides (14:10, from `.attest/ledger.jsonl` and `kane-runs.jsonl`)

- Docs proven today: 9 (Flask, Vite, Attest's README, 6 Procedural Truth answers or versions), 23 prove turns.
- Final verdicts across those turns: 72 proven, 7 stale, 6 broken, 25 unverifiable, 43 skipped. 31 browser claims proven by Kane with a screenshot behind each.
- Stale caught: 12 first verdicts. The real ones that matter on stage: Vite "Vite + React" is now "Get started"; GitHub "Security & analysis" is now "Advanced Security".
- Loops: 9 agent calls, 5 closed with the patched claim proven, 10 dependent claims proven from the same Kane run afterwards. Agents: `claude -p` in the morning, Codex (read-only sandbox) since 12:00.
- Kane steps since ledger accounting (about 12:00): 55 authored, 22 replayed, 746.7 credits. A replayed re-prove costs 0 credits; the full Vite catch-and-fix loop runs for 0 once both doc versions are recorded.
- Credits: 1200 at 10:12; 891.58 at 11:08 (308.42 spent before plan activation); 100,885.42 after activation at about 11:50; 99,928.89 at 14:08 (956.53 spent since). About 1,265 credits spent today in total.
- Kane run reports on the event proof page: https://kaneclihack.sh/t/attest/proof (KR-7 onwards, all with commit SHAs).
- Tests: 179 passed, 1 skipped.

### 14:40 to 16:30 the Kane Factory

- Direction change (Mateo): "The Kane Factory, powered by SyberWork". Modular loop: Spec -> Kane -> SyberWork -> worker -> Kane (loop if needed) -> Outcome. SyberWork is the vendor-neutral middle; companies plug in their own agents. OpenAI is the default worker. Codex CLI is no longer used anywhere in the loop.
- Presentation: one live tab (`attest serve --live`, `prove --live`) with the terminal beside the doc, plain-English narration, a redesigned report (score header, caught-and-fixed and repair-refused drawers). Built by a Claude Opus subagent; reviewed fixes: banner timing, live screenshots, before/after screenshot files, the live state after a loop, browser caching (`Cache-Control: no-store`), Windows `os.replace` on files the server is reading.
- Authority gate (`attest.policy.yaml`): protected promises are never repaired in the doc; the loop issues an implementation handoff. Receipts (`attest.receipt/v0`) for every loop case. Live: counter app adds two while the doc promises "increments by one" -> refused, receipt `handoff_implementation`.
- Number guard in `label_match`: "count is 1" vs "count is 2" scored 0.90 and would have passed the counter bug; different numbers now cap at 0.5. Tuning corpus still clean at 0.80.
- Doc agent is the OpenAI API (`ATTEST_AGENT_CMD=openai`). It answered in the `*** Begin Patch` format until the instructions showed the exact unified diff format.
- Spec mode (`--app`): a failing spec claim becomes an `attest.ticket/v0`; the worker (`ATTEST_WORKER_CMD`, default the OpenAI API) implements it in a git-initialised candidate copy of the app; Kane re-checks. The built-in worker uses structured output (whole files) and Attest builds the diff with difflib: hand-written hunks failed because the model copied JSON-escaped quotes into context lines and padded seed data to make the spec pass.
- Live result (Acme CRM, `demo/spec.md` + `demo/app`): Kane found "Mark as won" missing, the OpenAI worker implemented it, Kane re-checked: proven (`c05_label = "Won"`), receipt `0ca486124ac8-c05-33`, disposition `implemented`. The original `demo/app` stays unchanged so the demo repeats.
- Kane phrasing fixes found on the way: a click whose promised text is an outcome is read after the click ("Mark as won" contains "won"); only labels right after a click verb count as the clicked control (typed text is not a button).
- Self-check after the README rewrite: 6/6. It caught a real bug first: the shell runner took a URL in Attest's own Kane log line for a server announcing itself.
- SyberWork (Reason fleet): #89 receipt bridge accepts today's real receipts, including the spec-mode one (e94552d); #90 dispatch worker CLI in progress. Coordination: SyberLabs/attest #10 (overview), #11 (contract), #12 (rebase order for #13-#20, reviewed), SyberLabs/SyberWork #90, #91.
- Freeze on `main` lifted (Mateo). The submission pins to the tag `event-day`.

## CLI runner (2026-10-25, branch cli-runner)

Decisions and deviations from docs/CLI-RUNNER-SPEC.md, each with a reason. Milestones M1 to M4 of the spec; M5 and M6 are not in this branch.

### Baseline

- Clean `main` (e9a81ec) on this laptop: 12 failed, 249 passed, 2 skipped with `OPENAI_API_KEY` unset. CI on ubuntu is green for the same commit. All 12 are Windows-local: nine because `git apply` in `%TEMP%` honours the home directory's git repo (`C:\Users\MATEO` is itself a repo with the system `core.autocrlf=true`) and rewrites patched text to CRLF (`proposals.py`), one because the venv launcher hides a grandchild from the Windows process-tree kill (`execution/supervisor.py`), one stale assertion in `test_flask_quickstart_end_to_end` that CI never runs (it skips without `.venv`), plus two loop tests that fail downstream of the CRLF case. Not fixed here: out of scope for the CLI runner and each touches code the open draft PRs own.
- With `OPENAI_API_KEY` exported (it is, in this shell), three tests that expect the no-model path call the real API and write new `cache/compiler/v2-*.json` files into the repo; the failure count then drifts between 13 and 15. Every suite run on this branch uses `env -u OPENAI_API_KEY -u OPENAI_MODEL`, and the three generated cache files were deleted before the first commit.

### M1 Models and verdicts

- `PromptObservation` lives in `models.py` as the spec writes it; the simulation marker `SimulationPromptObservation` and the `PromptRunner` protocol live in `attest/prompt_runner.py`, mirroring `runner.py` for browsers. Reason: `models.py` stays tool-neutral.
- `Evidence` gains `prompt`, and for the sidecar shell rules `ready_url`, `ready_status` and `substitute`. Prompt evidence may carry none of the shell fields and no browser observation; shell and browser evidence may carry no prompt observation. Reason: the spec's new invariant (a claim is never proven from another runner's evidence) is cheapest to hold at the model.
- `expects_error` is accepted only on prompt claims, and a prose claim may not carry it. Reason: it is a promise about a run; on any other kind it would be a silent no-op.
- Result normalization strips trailing spaces and tabs at line ends (the spec says "trailing spaces"). Reason: a tab at a line end is invisible in a fence, and the reason text still names the three normalizations.
- Verdict reasons for proven and stale prompt claims end with "scripted run", as the spec's risk note asks. Shell verdicts for a service name the readiness URL and the timeout from `rules.yaml`; `judge` passes `rules` to the shell judge for that.
- A substitute that passes is capped at `stale` and the reason names both commands; a substitute that fails stays `broken`. A substitute that starts a service is capped the same way.
- The loop (`ProveResult.failing`, briefs, grounding, receipts) is untouched: no milestone in M1 to M4 covers it, so prompt claims are not yet sent to the agent. `ReceiptEvidence.kind` in `receipts.py` stays `shell | browser` for the same reason.

### M2 Compiler by rule

- `plan_fences` gives every fence a role (shell, assert, prompt, result, prose) and is the one place that decides which fences are claims. `fences_by_regex`, the draft checks, cache validation and `recompile_claim` all read the plan. Reason: four functions each walking `doc.fences` with their own classification would drift.
- Counts differ from the spec. The spec's "75 prompt examples, 60 with a result fence" counts every `markdown` fence in `language/03-blocks-and-prose.md`; by the spec's own rule 1 (frontmatter with a `runvil:` key) the chapter holds 68 complete prompt files, 7 fragments (a lone section such as `## Setup` with a `lua` fence), and 58 result pairs, because two of the 60 `text` fences follow a fragment. The tests pin 68 and 58. Fragments compile to prose, as rule 3 says for Lua fragments; proving fragments is a stated non-goal.
- A paired prompt claim's span runs from the prompt fence's first content line to the result fence's last, so the badge and the stale reason cover the result the doc shows. The claim's `command` is the prompt fence text byte for byte and `expects` the result fence text byte for byte; the span text is the doc's text in between, as `_validate_compiled_claims` requires.
- An unpaired `text` fence keeps today's classification (an `assert` claim judged against the previous shell step's output). In a Language chapter there is no shell step before it, so it ends unverifiable. Reason: rule 2 pairs only with blank lines between, and the Gateway guide needs output fences to stay assert claims.
- Unpaired prompt claims compile with `expects` empty and `expects_error` false (success promised). The spec's model pass that would set `expects_error` or move an example to prose is not in M2 ("by rule, no model call") and is left open; a fence-error example therefore ends broken until that pass exists.
- `Doc.browser_claims` (default True) switches off the browser-instruction heuristic when False. Reason: 18 prose lines of chapter 3 match the heuristic ("the text shows", "a page", "label") and a no-model compile fails closed on them; a flag the sidecar sets (`browser_claims: false`, M3) is explicit where a looser heuristic would be silent. The compile cache revalidates under the flag, so a cache written for a prompt-only doc is rejected when the flag is on again.
- Prompt claims depend on the previous shell step only, like assert claims, never on the previous prompt. Reason: each example is its own Host process, so one broken example must not skip the ones after it.
- Drafts the model returns for prompt, result or fragment fences are dropped in `assemble`, not rejected in `check_claims`. The system prompt now tells the model to return those blocks as prose. Reason: the model should never be the authority on a block the rules place, and a retry over its mislabel would waste a call.
- `README.md` is untouched: `test_readme.py` pins the README's hash to a legacy cache entry, so documenting the new flags there would break an existing test. The flags are documented in docs/CLI-RUNNER-SPEC.md and the `--help` text.

### M3 Shell services and fixtures

- The sidecar loader (`attest/cli_config.py`) fails closed: an unknown key, a fixture path that is absolute or climbs out of the scratch directory, a missing source file or a readiness URL off loopback is an error. Reason: the sidecar makes Attest write files and poll URLs on the reader's behalf; it should never do so on a typo.
- Readiness URLs are loopback only (`localhost`, `127.0.0.1`). Reason: a service claim runs on the host, so its health endpoint is local by construction; polling anything else would make the sidecar a web client.
- A service claim's clock is its readiness timeout (the service's `ready_timeout_s`, else `rules.yaml`), not `shell_timeout_s`, and readiness is one GET returning 200, polled every 0.25 s; a printed URL is not consulted for a service. A service that exits before 200 ends broken by its exit code; one that never answers 200 ends broken with the URL, the timeout and the last status seen; on Windows the port is killed after a timeout as the close step already did, because the venv launcher hides a grandchild from the tree kill.
- A substitute matches the whole command after `executable_command` (prompts stripped), compared stripped. Reason: the spec's example is a whole command; a prefix match could rewrite a command the author never meant.
- A substitute-capped `stale` still satisfies dependents (`verdict.satisfies_dependents`, used by the skip rule and by `execute`). Reason: the spec says the claims after a service run against it, and under `--substitute` the whole point is to reach them; the chain rule as written would have skipped every step after the capped install. Any other stale or broken verdict still skips its dependents.
- `--substitute` without any substitute in the sidecar is an error. Reason: the flag changes what runs; silently doing nothing would mislead.
- `Claim.setup` is filled by rule in `prove`: a shell command that names a fixture's path (or its basename) gets that fixture. `check_setup` refuses to run anything when a claim names a fixture the sidecar does not define. Fixtures are written before the first claim of any kind, and the list (name, scratch path, sha256) goes to `ProveResult.fixtures` and `fixtures.json` beside the page; the receipt keeps its shape until the loop learns prompt claims.
- The Gateway chapter (`tests/fixtures/runvil/gateway-01-install-and-run.md`) against the fake gateway: `cargo install gateway` ends broken (exit 101 from a fake cargo that answers like crates.io for an unpublished crate) and every later step is skipped; under `--substitute` the install is stale, the version, service and three curl steps are proven. The untagged systemd snippet (`ExecStart=...`, `Restart=on-failure`, `RestartSec=5`) runs as shell and ends **proven**, not broken as the spec expects: each line is a valid shell assignment and the block exits 0. A false proven is worse than the false broken the spec lists under risks, so the upstream fence-tagging PR matters more than the spec says. The test pins today's behaviour.
- The sidecar beside the fixture chapters sets `browser_claims: false`; the Gateway chapter trips the browser heuristic on one system-tray sentence and the Language chapter on 18.
- `rerun_claim` in the loop builds its own `ShellSession` without the sidecar, so a looped service claim would not poll readiness. Left for the loop milestone.

### M4 Fake prompt runner

- `FakePromptRunner` (`attest/prompt_runner.py`) replays a JSON file mapping claim ids to `PromptObservation` records, the shape `FakeBrowserRunner.from_file` reads. Every replay is a `SimulationPromptObservation` with a `simulation-only:` message, and `judge` turns it into unverifiable on its own, before `execute(simulation=True)` does. A claim with no recording ends `needs_script`, never a guess.
- `prove` refuses a fake prompt runner without `simulation=True`, as it does the fake browser runner, and refuses a document with prompt claims when no prompt runner was passed. The CLI builds `RunvilPromptRunner` by default and `prove` calls its `check()` only when the compiled claims include a prompt, so a shell-only doc never needs the Host and a prompt doc fails before any claim runs when the Host is missing.
- `tests/fixtures/prompt/` holds one record per verdict path. They are hand-written, not recorded: `runvil-docs-host` does not exist yet (M5), and `host_version` says so. The test judges each raw record for its verdict and shows the same record is unverifiable through the fake.
- The Python side of the Host protocol (`attest/runvil_runner.py`) is in this milestone although the spec lists it under the Rust Host, because the `--prompt-runner runvil` default needs something to build. `seed` is the first 8 bytes of sha256(claim id); `started_at` is 2026-01-01T00:00:00Z plus that number modulo one year, so both are fixed per claim id. `args` and `script` are always empty: the spec does not say where either comes from, so no model round is ever answered and such a prompt ends `needs_script`. Open question for the spec.
- A Host that exceeds `prompt_timeout_s` yields a `refused` observation ("did not answer within Ns"), so the claim is unverifiable with the reason on the badge; a Host that exits non-zero or prints something other than a `PromptObservation` raises `PromptRunnerError`, which the CLI reports as "proof failed closed". Reason: a hung run is not evidence either way, while a Host speaking garbage is a setup bug a reader must see.
- The page's drawer shows a prompt claim's prompt file, the doc's expected result or error, and the Host's status, result, error kind, line, message and version. The terminal narrates the result or the error kind. Shell drawers also show the readiness URL and a substitute (M3).
- Not done: `attest prove` with several doc paths in one call (spec, CLI surface) is outside M1 to M4 and untouched; `attest.receipt/v0` and the loop do not yet see prompt evidence, as noted under M1.

### M5 runvil-docs-host: build check only

- `cppalliance/runvil` is cloned next to this repo at `D:\syberlabs\runvil`, commit `c27899e7` (master, 2026-10-24): 60 MB of history, 45 MB of tree. The spec's references check out: the gateway crate is `gateway` with the binary `runvil-gateway` and `publish = false`; `crates/harness-internal/runner/tests/it/scripted.rs` defines `ScriptedBroker` (a second copy lives in `crates/harness/tests/suite/broker.rs`); `crates/runvil/public-api.txt` has 1500 lines; the five flags the Gateway chapter uses are in `crates/gateway/app/src/cli.rs`; `docs-claims.mjs` is at `crates/workshop/ui/test/docs-claims.mjs`.
- `cargo build --release -p gateway --no-default-features -j 4` does not link on this laptop, on either installed toolchain, so no Rust was written. The default toolchain is `stable-x86_64-pc-windows-gnu` (rustc 1.98.1) whose `x86_64-w64-mingw32-gcc` is the llvm-mingw clang shim, and it fails on the first build scripts with `lld: unable to find library -lgcc_eh` and `-lgcc` (45 s, 601 KB in `target/`). The repo's own `.cargo/config.toml` targets `x86_64-pc-windows-msvc` with `rust-lld`; that toolchain is installed too, but Visual Studio 2022 is present without the C++ build tools or a Windows SDK (`Windows Kits/10/Lib` is absent), so it fails on the same build scripts with `could not open 'userenv.lib'`, `ws2_32.lib`, `dbghelp.lib` (12 s, 1.6 MB). The cargo registry holds 536 MB after fetching the dependency set.
- Fix on this machine is one of: install the "Desktop development with C++" workload (MSVC tools plus the Windows 10/11 SDK) and build with `cargo +stable-x86_64-pc-windows-msvc`, or install a real MinGW-w64 GCC and point the GNU target's linker at it. Both are machine changes, not repo changes, so they are left to Mateo. CI on ubuntu is unaffected.
- Nothing in M5 beyond this check was started: no Host crate, no CI (M6).

### First real run: Gateway chapter 1 against runvil-gateway (2026-10-26)

- Toolchain: Visual Studio 2022 Build Tools 17.14 installed with winget to `D:\VS\BuildTools` (MSVC 14.44, download cache `D:\VS\Cache`), 7.7 minutes. The Windows SDK 10.0.26100 goes to `C:\Program Files (x86)\Windows Kits` and the shared components follow the registry's `SharedInstallationPath` on C:, neither of which the installer lets us move. `cargo +stable-x86_64-pc-windows-msvc build --release -p gateway --no-default-features -j 4` then builds `runvil-gateway 0.3.0` in 3 min 32 s; `target/` is 1.0 GB.
- `cargo install gateway` must never run verbatim: crates.io has an unrelated `gateway` 0.0.0 (softprops, 2018, an AWS Lambda library with no binary, built on cpython bindings). The step would download a stranger's package and then fail with "nothing to install". Checked by reading its manifest, without building it. The chapter's install step names the wrong package, which is sharper than the spec's "publish = false".
- `--runner none` added: a document with no browser steps needs no browser tool, and `prove` refuses it if the compiled doc has a browser claim. Reason: Kane CLI is not installed on this laptop and the CLI built a Kane runner unconditionally, which blocked any command-only run.
- Run setup, outside the repo in `D:\syberlabs\attest-runs\gateway-01`: the chapter copy, the real config (`tests/fixtures/runvil/gateway.toml`, now the shape of runvil's own smoke-test config, still read by the fake), a sidecar whose substitute installs Runvil's gateway from the checkout into a throwaway `--root` with the repo's linker flags so the release build is reused, and `HOME`/`USERPROFILE` pointed at a throwaway folder because the gateway roots its state directory (logs, discovery file, instance lock) at the home directory. `CARGO_HOME` and `RUSTUP_HOME` stay real.
- Result, 99 s, no model call: install stale (substitute), version proven, service proven (`/health` answered 200), the three curl steps proven, systemd snippet proven. The gateway answered exactly what the chapter promises: `{"status":"serving"}`, `{"error":{"code":"unauthorized",...}}` for the wrong token, `{"object":"list","data":[]}` for a keyless loopback call. Port 8081 was free afterwards and the state landed only in the throwaway home.
- What the verdicts do not yet show: the three curl steps are proven by curl's exit code alone, because the chapter's promises (401, `unauthorized`, admitted without a key) sit in prose and a no-model compile attaches no `expects`. The systemd snippet is a confirmed false proven: bash prints `--config: command not found` and the block exits 0 on its last assignment line. Both are recorded as next steps, not fixed here.

### M5 runvil-docs-host: built, and chapter 3 proven (2026-10-26)

- The Host lives in this repo at `host/runvil-docs-host/` as a path dependency on the sibling checkout (`../runvil`), so it needed no decision from Vinnie to start; moving it into runvil later is a file move. It builds on the `harness` facade crate, not `harness_runner` as the spec says: the facade is what a real Host like Workshop uses and re-exports everything the runner has (Harness, RunRequest, HostSnapshot, the broker and timer traits, the memory recorder). The build script bakes the runvil commit into `host_version` (`runvil@c27899e7ac45`). First build, release, into runvil's shared target folder: 37 s, 6 MB binary.
- The Host parses the prompt with the Engine's parser before the Harness run, because the Harness's failed outcome folds a parse error into one message and its `parse_failed` event carries no kind or line; AGENTS.md lets a Host use the parser to read prompts. So `parse_error` observations carry Runvil's own `ParseErrorKind` name (`Fence`, `Structure`, `List`, `Lua`, ...) and the line when the parser knows it. A runtime failure's kind is the `RunErrorKind` name the Harness reports (`Lua`, `Completion`, ...); the Harness gives no location for it, so `error_line` stays empty there.
- `seed` and `started_at` in the request are accepted and ignored. The Harness draws the seed from the OS and the start from the wall clock at preparation, before the run exists, and offers no way to pass either in; a Host that built the `Run` itself to set them would bypass the Harness, which AGENTS.md forbids. The spec's "same events on rerun" is not available through the Harness today; the `fences` example still gives identical bytes twice because nothing in it reads the seed. Open question for Vinnie.
- The broker lists one model, `script`, selected through `HostSnapshot`, and answers each round from the request's script in order; an empty script marks the broker starved and refuses the round with `Rejected`, which the Engine does not retry, and the Host reports the failed run as `needs_script`. A prompt declaring a Pack ends as `refused` with the Engine's own notice (`missing required Pack: web`), since the Host installs none.
- Verdict rule changed: a result fence shows what the run leaves behind, its result or, when the doc documents a failure, the failure's message. When a run fails and the doc's result fence equals the error kind or sits inside the Host's message, the claim is proven (reason says so); a result fence the failure does not match stays broken. Reason: the Language guide writes every documented failure as a prompt fence followed by the message in a `text` fence, and the first run of chapter 3 marked all six such examples broken with the exact message the doc shows. `expects_error` stays for unpaired examples the future model pass classifies.
- Chapter 3 against the Host, no model call, 3 s: 83 claims, 60 proven, 3 broken, 20 unverifiable. The three broken (`transform`, `empty_item`, `later_fault`) are documented failures whose message fence sits one prose line after the example ("and this message:"), so rule 2 does not pair them; a runvil-docs convention of putting the message fence directly after the example would prove them. The 20 unverifiable are the 7 fragments, 8 output fences separated from their example by prose, and 5 prompts that call a model and end `needs_script`, which is the honest answer until Attest can supply a script.
