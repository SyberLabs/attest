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
