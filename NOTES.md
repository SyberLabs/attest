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
