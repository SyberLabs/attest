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
