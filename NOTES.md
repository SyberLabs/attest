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
