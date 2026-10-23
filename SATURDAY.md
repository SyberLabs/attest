# SATURDAY

What remains for event day, Saturday October 24 2026. Everything below is Kane
integration or depends on it, and was deliberately left out of the pre-event build
(see the rule in docs/ARCHITECTURE.md, "Pre-build requirements").

Before 10:00: show the organizers this repo at tag `pre-event`. Run `kane-cli whoami`
and `kane-cli balance`. Confirm `githits login` was done on Friday (grounding returns
AUTH_REQUIRED lines otherwise; the pipeline still works).

## 1. attest/kane.py: KaneBrowserRunner

Replace the stub so it satisfies `attest.runner.BrowserRunner` (`name`, `observe(claim,
base_url) -> BrowserObservation`, `close()`). In order:

- Group consecutive browser claims that share a base URL into one Kane test file in
  Kane's `_test.md` format, one step per claim, each step storing the exact text it
  read under the claim's `store_as` key. A shell claim between two browser claims
  splits the file. Frontmatter from the architecture doc: mode testing, max_steps 15,
  url from the shell runner's detected URL.
- Run the file with `kane-cli testmd run --agent` and read its NDJSON stream.
- Locate the evidence pack under `.testmuai/evidence/`, unzip it, and read per step:
  pass or fail, stored values (`final_state` / `store as`), `screenshot.png` or
  `annotated.png`, `step.json`, `failure.yaml`, the remark.
- `kane_max_steps: 15` goes into `rules.yaml` and `attest.verdict.Rules`.
- `pipeline.execute` already hands each browser claim to `runner.observe` one at a
  time. If one test file per claim is too slow, add a `batch(claims)` method on the
  runner and call it from `execute` for runs of consecutive browser claims. Gate 1 in
  the build-day plan: if round-tripping fails by 12:00, one `kane-cli run` per claim.

## 2. Map Kane output to BrowserObservation

- status: step passed -> `passed`; step failed or `run_end.status == failed` -> `failed`;
  page unreachable -> `unreachable`.
- observed: the `store_as` values from the run's final state, keyed by claim.
- screenshot: the extracted PNG path, copied next to the rendered page
  (`.attest/<hash>/shots/<claim>.png`) so `attest serve` can show it.
- note: Kane's step remark, plus the failure record text when there is one.
- Add Kane-shaped fixtures under `tests/fixtures/kane/` (one real pack from the Friday
  dry run plus hand-edited variants) and tests that map them through the runner.

## 3. Healing detection

`attest/verdict.py` has a `# SATURDAY` marker in `_judge_browser`. If the remark shows
Kane re-authored the step to get past a mismatch, the claim is at best `stale`. Decide
from the real remark text whether the signal is reliable; if not, drop the invariant and
rely on `label_match` alone (open question in the architecture doc).

## 4. Wire the loop trigger to Kane verdicts

The loop (`attest/loop.py`) already triggers on any stale or broken shell or browser
verdict and re-runs a browser claim through `runner.observe` with the base URL from the
shell dependency. What remains: make sure a re-run through Kane replays unchanged steps
from cache and only authors the patched one, and that the brief's "What the runner saw"
section carries the failure record and the annotated screenshot path (both arrive via
`BrowserObservation.note` and `.screenshot`).

Agent command: set `ATTEST_AGENT_CMD` to `codex exec --full-auto` or `claude -p`
depending on which is installed on the laptop. Neither was on PATH during the pre-build;
tests use a fake agent script.

## 5. Ledger and render additions

- Ledger rows for browser steps should record authored vs replayed and credits spent
  (`kane-cli balance` before and after). `ledger.session_summary` has the placeholder.
- The strip template has a `SATURDAY` comment where "K Kane steps, C authored, R replayed"
  goes. Keep the rule: numbers come from the ledger, never computed in the template.
- The drawer's screenshot slot already renders `evidence.screenshot` when set.

## 6. Prove Attest's own README with Kane

`attest prove README.md --runner kane`. The README's steps: clone, install, prove the
Flask demo, serve, open localhost:4848 and see "Ledger". Make `--runner kane` the
default in `cli.py` once it works, and leave `--runner fake` for offline runs.

## 7. Demo prep that is not code

- Pre-run each Procedural Truth card by hand in the signed-in throwaway Chrome profile;
  drop any card that hits a CAPTCHA. The GitHub secret-scanning answer in
  `demo/answers/` says "Security & analysis" and "Secret scanning", the older wording,
  which is the expected catch against today's "Code security" and "Secret Protection".
- Save `cache/help/sf-parking-citation.md` by hand from the browser (the site blocks
  scripted fetches); first line `<!-- source: URL -->`.
- Warm npm and pip caches: run `demo/vite.md` and `demo/flask.md` once each.
- Record the backup run.

## Not Kane, but open

- The compiler sometimes labels the "open this URL" step with the URL itself as the
  expected label. Harmless (Kane stores whatever it reads) but a one-line prompt tweak
  would tighten it.
- `pipx` is not installed on the laptop; the README's install step uses
  `python -m pip install .` and mentions pipx as an alternative.
