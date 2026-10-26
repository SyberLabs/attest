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

## Oct 25: SyberWork dispatch works end to end

- Root cause of the "intermittent" dispatch failure: under dispatch, `python -m attest.worker` inherits `ATTEST_WORKER_CMD` (the dispatch command line), and the built-in worker parsed its model out of `worker_identity()`, so `C:\Users\...` became the model and OpenAI answered `model_not_found`. Every dispatched ticket failed; direct runs passed only because the variable was unset. Fixed in #22 (`builtin_model()`), which also keeps an external worker's stderr in `<case>.worker.log`.
- Full loop through SyberWork dispatch (SyberWork #92 head): Acme CRM, line 15 broken, ticket admitted and routed to `python -m attest.worker` (gpt-6-sol), Kane re-check 4/4 proven, receipt `0ca486124ac8-c05-46`. Kane credits: 54.4 (first pass, authored fresh) + 17.1 (re-check).
- `demo/beat_factory.ps1` now routes through SyberWork by default when `.attest\syberwork` holds the checkout; `-Direct` calls the worker without it.
- Windows: `main` fails 12 tests locally that pass on the Ubuntu CI (line endings in the proposal and workflow code); issue #23.

## Oct 26: workflow validation is recipes-only

- Leftover from #18: "workflow validation ignores --recipes-only". `resume_workflow` called `execute()` without `recipes_only`, so an approved candidate's shell steps (model-authored text) ran through the compatibility shell with the full host environment. Validation now always passes `recipes_only=True`; the caller can supply an `ApprovedRecipeExecutor`, and an unapproved shell step gets exit 126 and the workflow is rejected. The receipt records `shell_mode: recipes_only`.
- `attest resume` has no recipe configuration yet, so from the CLI every shell claim fails closed. Browser-only workflows are unaffected.

## Oct 26: `attest resume --recipes`

- Follow-up to the recipes-only validation gate (#25). `attest resume <id> --recipes FILE` loads a trusted allow-list, `recipes: {<exact command>: [argv...]}` in YAML or JSON, into an `ApprovedRecipeExecutor`. A shell claim runs only when its whole command is a key, and then as that fixed argv with no shell and the curated recipes-only environment. Without `--recipes`, or for any command not listed, the claim gets exit 126 and the workflow is rejected, as before.
- The file is explicit operator input only; nothing is loaded from `--home` or from beside the document, so a proposal cannot supply its own allow-list. The loader refuses rather than guesses: a symlink or non-file, duplicate keys (YAML and JSON both keep the last one silently, so a strict loader is used), unknown top-level keys, non-string argv parts. It loads before the runner starts, so a bad file exits 2 and the workflow stays `validating`.
- The validation receipt records `recipe_digest` (sha256 of the canonical allow-list; the empty list when none was given), so a promotion shows which allow-list proved its shell steps.
- `attest prove --recipes-only` still has no way to supply recipes; out of scope here.

## Oct 26: Spec mode promotion is approval-gated

- Health leftover: spec mode had no approval path at all. Its receipts recorded `approval.actor = policy:default` for an app change no human approved, and nothing durable stood between the candidate and the original `--app` directory.
- Candidate edits still need no approval. After the loop, a changed candidate becomes a `spec_app` workflow in `workflow.sqlite3` (`attest/app_promotion.py`), in `awaiting_approval`, with the same states and `attest approve` / `attest resume --apply` commands as Maintain Docs (#15). The approval binds the original app's tree digest as copied, the candidate baseline and final tree digests, the plan, the build identity and a spec-mode policy digest.
- `resume` re-runs the stored spec claims against the approved candidate materialised from content-addressed artifacts (not the mutable candidate directory) with fresh Kane evidence only; fake or simulated runners are refused. `--apply` then rechecks every digest (app, build, spec, candidate, change set), refuses symlinked or out-of-root paths, stages each changed file next to its target, rechecks the app tree, swaps, and rolls back on any failure. Only files the candidate changed are written, so untouched CRLF files keep their bytes; a changed file is written with the candidate's LF endings.
- Spec-mode receipts now say `approval: {actor: null, state: not_approved}` and carry `promotion.workflow_id`. `prove --app` exits 3 when a promotion proposal is waiting, as a Maintain Docs proposal does.

## Oct 26: G3 Adversarial pass gate

- FACTORY §3 G3 is a separate workflow, `.github/workflows/g3-adversarial-pass.yml` (check `G3 Adversarial pass`), so `tests.yml` (G8/G1) and the G5/G7 workflows stay untouched. Details: `docs/quality/G3-ADVERSARIAL-PASS.md`.
- `scripts/g3-adversarial-pass.py` reads `tests/redteam/cases/*/*.json` (#26 layout), runs `pytest -m redteam tests/redteam` in-process with a small plugin that maps each test to its case through the `redteam(case_id=..., status=...)` marker, and fails on any active case that doesn't pass (skip and xfail included), any active case with no test, any xfail/stub case whose test no longer behaves as its status says, a malformed case file, or an inventory with no active adversarial case or no active `CONTROL` case.
- Only `active` cases are enforced. `xfail` and `stub` cases are printed as open gaps and are never counted as passes, so a green job is not "G3 over every §1 case".
- `main` at b0989ac has no `tests/redteam/`, so the job runs with `--allow-missing-inventory` and reports INACTIVE (exit 0, nothing ran, not a pass) until #26 merges. The flag has no effect once the directory exists; drop it after #26 merges.
- G3 doesn't count floors (that's G2) and doesn't run #31's `tests/test_positive_controls.py` (G8 runs it).

## Oct 26: G7 Kane resource gate

- FACTORY §3 G7 is a separate workflow, `.github/workflows/g7-kane-resource.yml` (check `G7 Kane resource`), so `tests.yml` (G8/G1, #27) and the G5 workflow (#33) stay untouched. Details and scope limits: `docs/quality/G7-KANE-RESOURCE.md`.
- Port-kill scan: `python scripts/g7-kane-resource.py port-kill` reads every `.py/.sh/.bash/.ps1/.cmd/.bat/.js/.mjs/.cjs/.ts` file under `attest/` and `scripts/` with comments removed, and fails on any case-insensitive match from `tests/quality/g7_port_kill_patterns.txt`: `\bnetstat\b`, `\btaskkill\b`, `\bfuser\b`, `\blsof\b`, `\bkillall\b`, `\bpkill\b`, `\bkill-port\b`, `["'\s]ss["']?\s*,?\s*["']?-[a-z]*p`, `\b(?:net_)?connections\s*\(`, `\bGet-NetTCPConnection\b`, `\bStop-Process\b`. It also fails when it reads no files. The list sits outside the scanned paths so the scanner doesn't match itself.
- Tests: `python scripts/g7-kane-resource.py tests` runs `tests/quality/g7_kane_resource.txt` (sections deadline, cleanup, isolation, port-kill, control, gate). A missing, skipped, xfailed or xpassed id fails the gate.
- On `main` at b0989ac the scan failed on `ShellSession._kill_listener` (`attest/shell.py`, Windows only), which ran the Windows port-listing tool and then force-killed every PID listening on the server's port, owned or not.
- Removed `_kill_listener`. `close()` now kills only the trees `ShellSession` started (`terminate_process_tree`: process group on POSIX, owned-PID descendant walk on Windows). Risk on Windows: a dev server whose parent chain to the bash leader is already broken at close time (an intermediate `npm.cmd`/`cmd.exe` exited) is not reached by the descendant walk and can keep its port; the next run then sees a busy port (Vite moves to 5174, which `kane.retarget` already follows). That's the trade FACTORY §1 asks for: a leftover owned server is visible, killing someone else's process is not. Not tested on Windows in this change. Draft #24 (cli-runner) adds a new `_kill_listener` call and has to drop it on rebase; G7 flags it if it doesn't.

## Oct 26: G3 KR-SIM-03 promoted on #41's version+Kane binding

- KR-SIM-03 (cached Kane replay against a changed app build) is `active`: `tests/redteam/_version_binding.py`, decisions and proof in `docs/quality/G3-KR-SIM-03.md`. On `main`'s product code the same scenario promotes; on #41 it is rejected.
- Still open: KR-ISO-02 (xfail until #34's port-kill removal meets the #40 stack), KR-PI-03 and KR-ISO-04 (stubs, need live Kane).
- Merging with #26/#30/#40: take this KR-SIM-03.json, and move `run_case` into `test_kane_runner.py`'s stub (steps in the doc).

## Oct 26: browser observations name the build Kane saw (stacked on #42)

- `BrowserObservation.build_digest`: `KaneBrowserRunner` measures `current_build_digest` of the prepared doc before and after each kane-cli run. A change during the run, a URL doc, or a failed measurement gives `None`.
- Validation fails `<id>:observation_build_missing` / `<id>:observation_build_mismatch`. Promotion refuses stored evidence whose observation build isn't the approved one. Receipts filled from the observation build fail #41's checks when it is `None` or different.
- G1 binding gate: new `observation-build` section (`test_observation_build_*`), Measured (scoped) on #44. Green run 40312676697, fails closed on the negative control `89237c4` (run 40312713440), green on the revert (run 40312768841). Details are in `docs/quality/G1-STAGE0-BINDING.md`.
- Not covered: the build is the worktree identity, not read from the served app. No product code turns observations into `ReceiptEvidence`. Shell evidence has no build.

## Oct 26: KR-SIM-03 on the observation build (stacked on #44)

- `BrowserObservation.receipt_evidence` turns an observation into browser `ReceiptEvidence`. The build comes from the observation, never the caller. Only a passed observation is complete. This closes #44's "no product code turns observations into `ReceiptEvidence`" for whoever calls it. Nothing in the product builds receipts yet, so no runtime path calls it today.
- KR-SIM-03 reads the build off the observations: cached observations name build A, validation on B also fails `<id>:observation_build_mismatch`, and receipts made from the observations with `receipt_evidence` are refused. On #42's or #44's `attest/` without this change the case fails. Proof, mutations and the G3 run over the #45 stack are in `docs/quality/G3-KR-SIM-03.md`.
- Still open: KR-PI-03 and KR-ISO-04 (stubs, need live Kane). The build is still the worktree identity, not read from the served app.

## Oct 26: receipt evidence keeps the observation's origin (stacked on #46)

- #46's `BrowserObservation.receipt_evidence` marked every passed observation complete and not simulated, and `FakeBrowserRunner` kept a scripted `build_digest`. A fake observation with a build, plus a run id from the caller, therefore verified a `VerifiedRunReceipt`. That held live and after the store round trip. Reproduced on #46's tip.
- Fix: `receipt_evidence` marks simulation-only observations (the subclass or the note prefix) simulated, and simulation-only or `unbound:` observations incomplete. The fake runner drops a scripted build.
- New G1 `observation-build` cases cover simulated, stored-simulated and unbound receipt evidence, plus a build deployed only for the length of the Kane run, which validation's own build checks miss. A new `control` case covers `receipt_evidence` from a validated run. Hosted on PR #47: green [run 40316904818](https://github.com/SyberLabs/attest/actions/runs/40316904818); fails closed on the negative control `39d5cc2` ([run 40317583048](https://github.com/SyberLabs/attest/actions/runs/40317583048)); green on the revert ([run 40317922106](https://github.com/SyberLabs/attest/actions/runs/40317922106)). Details are in `docs/quality/G1-STAGE0-BINDING.md`.
- Still open: a plain `RunReceipt` accepts evidence that names no build. `receipt_evidence`'s Kane run id still comes from the caller. The runner cache still serves observations from another build (they are refused downstream). The build is still the worktree identity. KR-PI-03 and KR-ISO-04 are still stubs and need live Kane.

## Oct 26: G3 gap closure (stacked on #30)

- Of the 10 G3 gaps open on #30's head (#37/#38), 6 are now `active`. Each one comes from a product fix in its own commit, so a reviewer can drop any of them. KR-DS-03: `find_pack` no longer falls back to another run's pack when Kane named a session. KR-PI-04: `step_body` folds line breaks, so doc text can't forge a `## <id>` step. KR-COST-01 and KR-ISO-06: `KaneBrowserRunner._execute` runs a deadline watchdog beside the read and kills kane-cli's own process group with `terminate_process_tree`. RS-PI-02: `_inline_code` shows line breaks as JSON escapes. BR-AB-03: `attest.worker` refuses top-level keys outside `attest.ticket/v0`.
- Still open: KR-ISO-02 stays a strict xfail. #34 removes `_kill_listener`, and this PR doesn't duplicate that product change. Whichever of #34 and this stack merges second flips KR-ISO-02 to `active`. KR-PI-03 and KR-ISO-04 are stubs that need a live Kane run. KR-SIM-03 is a stub that needs an app/build version on `BrowserObservation` (the G1 "not covered" item).
- `_execute` now sets `start_new_session` (POSIX) or `CREATE_NEW_PROCESS_GROUP` (Windows). Ctrl-C therefore no longer reaches kane-cli directly. The interrupt reaches attest instead, and attest terminates the group in its exception path. Not tested on Windows here.

## Oct 26: KR-ISO-02 active (stacked on #40)

- `attest/shell.py` takes #34's `b15868b` hunk byte for byte: `_kill_listener` is gone, and `close()` kills only the trees `ShellSession` started. The NOTES text, G7 workflow, `scripts/g7-kane-resource.py` and `tests/quality/` stay on #34. Once #34 merges, its copy of the same hunk is a no-op, and git should take the identical change without a conflict.
- KR-ISO-02 is `active`. The case is also stricter now. The scan adds `\b_kill_listener\b` and `\btaskkill\b`; the old `taskkill ... /IM` pattern missed the `/PID` form `_kill_listener` used. A behavior check was added too. A server the session started announces a port that another process owns. `close()` runs with `sys.platform` forced to `win32` and `subprocess.run` recorded. It must stop the server, leave the port owner running, and make no `subprocess.run` call. On the old `shell.py`, each half fails on its own: the scan reports 4 lines, and the behavior check records `['netstat', '-ano']`.
- Still open on this stack: KR-PI-03 and KR-ISO-04 (stubs, need a live Kane run). KR-SIM-03 is handled on #41, not here.

## Oct 26: G3 script in-tree on the #26 stack (stacked on #43)

- #40 and #43 ran #37's `scripts/g3-adversarial-pass.py` from a temp copy because the script lived only on #37's branch. It is now committed here byte for byte from `fd9c0ed` (same blob, mode 755), together with #37's `tests/quality/test_g3_adversarial_pass.py` (21 synthetic-inventory tests, also unchanged) and `docs/quality/G3-ADVERSARIAL-PASS.md`. No case file or product code changes, and no status promotions.
- New workflow `.github/workflows/g3-adversarial-pass.yml` (check `G3 Adversarial pass`). It is #37's workflow with one step changed: no `--allow-missing-inventory`, because the inventory is in this branch's history. A ref without `tests/redteam/cases` fails here instead of reporting INACTIVE. `tests.yml` is untouched, and so are the G2/G5/G7 workflows on #34/#36.
- Local on `42fe329` + this commit: the script exits 0. pytest gives 78 passed, 3 skipped. kane_runner 23, maintain_docs 27 + MD-CTRL-01, reconcile_spec 20, syberwork_bridge 7, 0 active not passing, 0 xfail. Open gaps: KR-ISO-04, KR-PI-03, KR-SIM-03 (stubs). These are counts of cases at this commit, not coverage. Fails closed locally: KR-PI-03 set to `active` gives exit 1 (`KR-PI-03 (active, R6) did not pass`), and the inventory moved away gives exit 1 (`No case inventory`). Both were reverted. Full suite: 374 passed, 6 skipped, 2 xfailed (#43 plus the 21 gate tests).
- Hosted on PR #45 at `ce42494`: G3 [run 40313033256](https://github.com/SyberLabs/attest/actions/runs/40313033256) passed with the same counts and gaps. G8 [run 40313033231](https://github.com/SyberLabs/attest/actions/runs/40313033231) gave 374 passed, 6 skipped, 2 xfailed.
- Merging with #37: the script and gate tests are identical, so git takes them without a conflict. The workflow and the G3 doc are add/add conflicts. Keep this branch's versions: no flag, plus the stacked status line. NOTES.md is the usual append/append conflict.

## Oct 26: G4 positive controls gate

- FACTORY §3 G4 is a separate workflow, `.github/workflows/g4-positive-controls.yml` (check `G4 Positive controls`), so `tests.yml` (G8/G1) and the other gate workflows stay untouched. Details and scope limits: `docs/quality/G4-POSITIVE-CONTROLS.md`.
- Stacked on #31 (`feat/g2-positive-controls`), which adds `tests/test_positive_controls.py`; that file is not changed here. Without it the gate fails closed, so this must not reach `main` before #31.
- `python scripts/g4-positive-controls.py` runs `tests/quality/g4_positive_controls.txt`: control, twin and refuse-everything mutant sections for PC-MD-01, PC-KR-01 and PC-RS-01, plus the gate's own tests. A missing file or a missing, skipped, xfailed or xpassed id fails it. Mutants are listed per parameter.
- The runner mirrors the G7 runner in #34 (manifest sections, junit outcomes) but is self-contained, since #34 isn't on `main`. Fold the two together once both land.
- Not covered: the SyberWork "matching destination lookup reconciles" control, Reconcile Spec promotion (diagnosis only on `main`), live Kane.

## Oct 26: G2 quota floor in CI

- New workflow `g2-quota-floor` (job `G2 Quota floor`), additive and not a required check. It counts `tests/redteam/cases` against the FACTORY §1 floors and fails closed. Details and counts: `docs/quality/G2-QUOTA-FLOOR.md`.
- Stacked on #30 (which is stacked on #26): `main` has no red-team inventory, so on `main` alone every cell is 0 and the gate would fail. Enforcement on `main` activates when #26 and #30 merge. The merge-base is clean (#26 and #30 are linear on `b0989ac`).
- Counting rule, locked by firstmate (2026-10-26, about 2:51 AM PT): G2 counts `active` + `xfail`; `stub` never counts. A case also counts only if its bound test ran and ended as its status says. This matches the #26 harness and FACTORY's G2/G3 split: an xfail is an executable observed gap, so it counts for G2 and must fail G3 once G3 is enforced.
- Known doc conflict, left unresolved here: `docs/quality/CI-GATES.md` (Gate B/C) and `docs/quality/FACTORY-REDTEAM-QUOTA-2026-10-24.md` on `main` say xfail must not count toward a quota. This session doesn't rewrite them. `--count active` stays the stricter opt-in; on this tip it leaves Reconcile Spec PI and Kane PI/DS/ISO one short each.
- SyberWork bridge DS/SIM/SCH/COST/REC are reported but not enforced (SyberWork side, N3/N4). The exemption fails the gate once it is met, so it has to be removed then.
