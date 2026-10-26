# Attest CLI Runner: spec

Oct 25, 2026 · @mateo

Attest gets a CLI runner so it can prove docs whose steps are commands and prompt runs, not browser pages. The first target is Runvil's documentation, which has no freshness check today.

## Context

"Kane CLI runner" is read here as the runner for command-line claims that sits beside the Kane browser runner. Kane stays the runner for browser claims; this spec adds what Kane can't see.

**Why Runvil.** The [runvil-docs CONTRIBUTING](https://github.com/cppalliance/runvil-docs/blob/master/CONTRIBUTING.md) says there is no freshness gate and no generator, so chapters are updated by hand when code changes. The docs live in a separate repo from the code, and Runvil's `vibe/` folder holds more than 50 dated plans from October alone, many of them renames and debt removal.

**How much there is to check.** Counted from the docs repo at its current head:

| Book | Chapters | Fences | What they hold |
| --- | --- | --- | --- |
| Gateway guide | 11 | 38, all untagged | 13 commands, 21 TOML config snippets, 4 JSON responses |
| Language guide | 16 | 606 complete prompt files in `markdown` fences | 275 are followed directly by a `text` fence with the run result |
| Workshop guide | 1 | 0 | A stub today |

**One drift already visible.** The Gateway guide's first step is `cargo install gateway`, but [the gateway crate](https://github.com/cppalliance/runvil/blob/master/crates/gateway/app/Cargo.toml) sets `publish = false`, so that command can't install it from crates.io. The flags the same chapter uses (`--config`, `--profile`, `--no-tray`, `--print-url`, `--browser`) all exist in `cli.rs`.

## Goals and non-goals

The runner succeeds when `attest prove` can gate a runvil-docs pull request on yes/no checks, with no model calls for the prompt examples.

**Goals**

1. Prove the Gateway guide's command steps end to end, on Windows (Git Bash) and Linux.
2. Prove the Language guide's prompt examples by running each one through Runvil's Harness, with model replies played from a script.
3. Decide every CLI and prompt verdict by exact checks: exit code, exact output, error kind. No similarity score gates a CLI claim, matching the rule in [Runvil's AGENTS.md](https://github.com/cppalliance/runvil/blob/master/AGENTS.md) to gate on yes/no properties.
4. Run in CI: `attest prove` already exits 1 on a stale or broken claim.
5. Keep receipts, the ledger and the authority gate unchanged in shape, so SyberWork's receipt bridge reads CLI cases like browser ones.

**Non-goals**

- Replacing `docs-claims.mjs`. It guards known stale phrases; Attest finds drift nobody has noticed yet. Both stay.
- Editing Runvil's code. The loop patches docs or hands off to the code owner, as today.
- A real sandbox for shell claims. The scratch directory stays a convenience, as the README's known limitations say.
- The Workshop guide, until it has content.
- Proving Lua fragments that are not complete prompt files.

## Where it sits in Attest

The CLI runner slots in beside the Kane runner at the execute step; ingest, verdicts, the loop and receipts keep their shape.

```text
Doc under test (+ attest.cli.yaml sidecar)
  -> Compiler (fences by rule; a model pass for the rest)
       -> shell claims   -> ShellSession (existing, plus services and fixtures)
       -> browser claims -> Kane runner (Kane CLI in Chrome, unchanged)
       -> prompt claims  -> Prompt runner (NEW: runvil-docs-host on the Harness, scripted model rounds)
  -> Verdict rules (exact checks; fuzzy match for browser only)
       -> stale or broken -> Loop and authority gate (doc patch or code handoff)
  -> Receipt and ledger (attest.receipt/v0)
  -> SyberWork bridge (reads receipts as unverified)
```

`pipeline.execute` already dispatches on claim kind: `shell` goes to `ShellSession.run`, `browser` to `runner.observe`, `assert` reads the previous shell evidence. The spec adds one branch, `prompt` to `prompt_runner.run`, and passes a `PromptRunner` into `execute` and `prove` beside the existing `BrowserRunner`.

## Data model changes

One new claim kind, one new evidence kind and one new observation record; the two invariants in `models.py` stay as written.

| Change | Where | Detail |
| --- | --- | --- |
| `ClaimKind` adds `prompt` | `models.py` | `command` holds the prompt file text verbatim; `expects` holds the result fence verbatim, or is empty |
| `Claim` adds `expects_error: bool` | `models.py` | True when the doc promises the run fails (a fence-error example); the default is False |
| `EvidenceKind` adds `prompt` | `models.py` | Prompt evidence must carry a `PromptObservation` and nothing from the shell runner |
| New `PromptObservation` | `models.py` | What the Host reported for one run (below) |
| `Claim` adds `setup: list[str]` | `models.py` | Names of fixtures from the sidecar file that must exist before the claim runs |

```python
class PromptObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["completed", "failed", "parse_error", "refused", "needs_script"]
    result: str | None = None          # the run result, exactly as the Host returned it
    error_kind: str | None = None      # ParseErrorKind or RunError kind, as Runvil names it
    error_line: int | None = None      # 1-based line in the prompt file, for parse errors
    message: str | None = None         # Runvil's own message, verbatim
    rounds: int = 0                    # model rounds answered from the script
    host_version: str                  # runvil commit the Host was built from
```

**Invariants added**

- A prompt claim is never proven from shell evidence, and a shell claim is never proven from a prompt observation. This mirrors the existing shell and browser rule.
- A `needs_script` observation can only produce `unverifiable`. A run that wanted a model reply the doc never showed proves nothing either way.
- Simulation runs stay unverifiable, as `execute(simulation=True)` already enforces.

## Runners

Command steps reuse the existing `ShellSession` with two additions; prompt examples get a new runner backed by a small Rust Host.

### Shell session: services and fixtures

The Gateway guide assumes things a reader already has: a `gateway.toml`, a profile named `main`, a server on `127.0.0.1:8081`. A sidecar file next to the doc supplies them, so the doc itself stays untouched.

```yaml
# attest.cli.yaml, beside the doc or named with --cli-config
fixtures:
  gateway-config:
    path: gateway.toml          # written into the scratch directory before any claim
    from: fixtures/gateway.toml # a loopback-only config, profile "main", bind 127.0.0.1:8081
services:
  - match: "runvil-gateway --config"   # a claim whose command starts this way is a service
    ready: http://127.0.0.1:8081/health # polled until it answers 200
    ready_timeout_s: 60
substitute:
  "cargo install gateway": "cargo install --path crates/gateway/app"  # only with --substitute, and recorded
```

- **Services.** Today a command that never exits ends as a timeout unless it prints a URL. A matched service claim is proven when its readiness URL answers 200, then keeps running for the claims after it. The session's existing close step stops it and frees the port.
- **Fixtures.** Written before the first claim, listed in the receipt by path and hash. A fixture is never a claim and never gets a badge.
- **Substitutes.** Off by default. With `--substitute`, a matched command runs in its place, and the verdict can reach at most `stale`, with the reason naming the substitute. A step that only works when rewritten is drift, and the badge says so.

### Prompt runner: a Host on the Harness

Runvil's [AGENTS.md](https://github.com/cppalliance/runvil/blob/master/AGENTS.md) says a Host runs prompts only through the Harness. So the runner is a Host: a small binary, `runvil-docs-host`, built on `harness_runner`, with an inference broker that plays model rounds from a script. The Harness suite already has this pattern in `crates/harness-internal/runner/tests/it/scripted.rs` (`ScriptedBroker`).

- **Protocol.** One JSON object in on stdin, one out on stdout, one process per claim. In: `prompt`, `args`, `script` (model replies in round order), `seed`, `started_at`. Out: a `PromptObservation`.
- **Determinism.** A fixed `RunSeed` and `started_at` per claim, both derived from the claim id, so a rerun gives the same events. Runvil's Engine already forbids wall-clock reads and OS entropy in its own tests.
- **Model rounds.** Answered from the script. A round with no scripted reply ends the run as `needs_script`, never as a guess.
- **Tools.** Web fetch and search, MCP and user input are not installed. A prompt that needs one ends as `refused`, with Runvil's own message saying what is missing.
- **Python side.** `RunvilPromptRunner` in `attest/runvil_runner.py` implements a `PromptRunner` protocol beside `BrowserRunner`: `run(claim) -> PromptObservation`, `close()`. It finds the binary from `ATTEST_RUNVIL_HOST` or `PATH`.

### Kane runner

Unchanged. It stays the runner for browser claims, and a doc can mix all three kinds.

## Compiler changes

Most Runvil claims compile by rule from the fences alone; the model call is kept for the few fences a rule can't place.

**By rule, no model call**

1. A `markdown` fence whose text opens with frontmatter carrying a `runvil:` key is a `prompt` claim. `command` is the fence text, byte for byte.
2. A `text` fence that follows it, with only blank lines between, becomes that claim's `expects`. This covers the 275 paired examples.
3. A `lua` fence that is not inside a prompt file is prose: a fragment promises nothing a run can check.
4. Fences tagged `sh`, `bash`, `console` and the rest of `SHELL_LANGS` stay `shell`, as today.

`find_fences` already closes a four-backtick fence only on four or more backticks, so the three-backtick `lua` fences nested inside a prompt stay inside it. A test pins this on a Language guide chapter.

**Untagged fences need a decision.** The Gateway guide's fences carry no language tag, and `classify_fence` treats an empty tag as shell. Run as-is, the 21 TOML snippets and 4 JSON responses would all end `broken`. Two fixes, in order of preference:

1. **Upstream (preferred).** A one-time docs PR tags each Gateway fence `sh`, `toml` or `json`. It is a small change to the house rule ("open every code fence with four backticks") and makes classification exact.
2. **Locally.** Until then, the compiler's model call classifies untagged fences as command, config or output. Config fences become fixtures when a later command names their file; output fences become `assert` claims on the command before them.

**Unpaired prompts.** 331 prompt examples have no result fence. The model call reads the prose around each and sets one of: the doc promises success (`expects` empty, `expects_error` false), a specific failure (`expects_error` true, `expects` holding the quoted message or error name), or nothing checkable (prose). A fence-error example is the main case for `expects_error`.

## Verdict rules

Every CLI and prompt verdict is an exact yes/no comparison; `label_match` stays a browser-only rule. Rules run in the existing order, and the first match wins: skipped, broken, stale, proven, unverifiable.

**Prompt claims**

| Status | Rule |
| --- | --- |
| unverifiable | Observation is `needs_script` or `refused`, or the run is a simulation |
| broken | Success promised, and the run ended `parse_error` or `failed` |
| broken | Failure promised (`expects_error`), and the run `completed` |
| stale | The run ended the way the doc promised, but `result` differs from `expects`, or the error kind or message differs from the one the doc quotes |
| proven | Success promised: `completed`, and `result` equals `expects` when `expects` is set |
| proven | Failure promised: the run failed, and the error kind equals, or the message contains, the quoted text when one is set |

"Equals" means byte equality after three normalizations, each recorded in the verdict's reason: CRLF to LF, trailing spaces removed at line ends, and one final newline. Nothing else is folded.

**Shell claims, additions**

| Status | Rule |
| --- | --- |
| broken | A service claim whose readiness URL did not answer 200 within `ready_timeout_s` |
| stale | A claim that only passed through a `--substitute` command; the reason names both commands |
| proven | A service claim whose readiness URL answered 200; the claims after it run against that service |

The existing shell rules already compare `expects` by exact substring after whitespace collapse, so they need no change. `rules.yaml` gains `prompt_timeout_s: 30` and `ready_timeout_s: 60`.

## Loop, authority gate and receipts

The loop works as it does for browser claims, with grounding from the Runvil source instead of a web search, and a re-prove that costs nothing.

**Brief.** For a shell claim: the command, exit code and output tail. For a prompt claim: the prompt, the doc's `expects`, the actual `result` or error kind, line and message, and a line diff between the two. Both keep the existing doc excerpt and the instruction to edit only the doc.

**Grounding.** A local search of a Runvil checkout pinned to one commit, recorded in the receipt:

- a flag or subcommand in a failed command → `crates/gateway/app/src/cli.rs`
- a crate name in `cargo install` → that crate's `Cargo.toml` (`name`, `publish`, `[[bin]]`)
- an Engine call or type in a prompt → `crates/runvil/public-api.txt`
- an error kind → its definition in the Engine crates

**Re-prove.** Only the patched claim runs again. A prompt run takes one Host process and no model calls, and a shell claim takes the time its command takes.

**Authority.** Runvil's chapters describe shipped behaviour, so a doc repair is the default. The policy protects the promises the docs make on the code's behalf, so a mismatch there becomes a handoff to the code owner instead of a weaker doc:

```yaml
# attest.policy.yaml for runvil-docs; each phrase appears in the guides today
protected:
  - match: "needs no credentials"                 # Gateway: GET /health
  - match: "Deterministic table iteration"        # Language: Lua environment
  - match: "deterministic control flow"           # Language: what a prompt is
```

**Receipts.** `attest.receipt/v0` keeps its shape. It gains `evidence.kind: "prompt"`, the Host's `host_version`, the pinned Runvil commit, and the fixtures used, by path and hash. SyberWork's receipt bridge (`syberlabs/attest_reconcile_map.py`) needs a matching case for the new evidence kind; receipts stay `verified: false` until a re-prove passes.

## CLI surface and CI gate

Three new flags and one new sidecar file; everything else on `attest prove` keeps working as documented.

| Flag | Default | Purpose |
| --- | --- | --- |
| `--prompt-runner runvil\|fake` | `runvil` when the doc has prompt claims | Picks the prompt runner; `fake` is simulation-only, like `--runner fake` |
| `--cli-config <file>` | `attest.cli.yaml` beside the doc | Fixtures, services and substitutes |
| `--substitute` | off | Allows the sidecar's substitutes; their claims cap at `stale` |

`attest prove` also accepts several doc paths in one call, writing one page and one ledger turn per doc, so a whole book runs in one CI step.

**CI workflow, first in a fork of runvil-docs**

```yaml
# .github/workflows/attest.yml (sketch)
on: [pull_request]
jobs:
  prove:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4                 # runvil-docs
      - uses: actions/checkout@v4
        with: { repository: cppalliance/runvil, ref: <pinned commit>, path: runvil }
      - run: cargo build --release -p gateway --no-default-features && cargo build --release -p runvil-docs-host
        working-directory: runvil
      - run: pip install git+https://github.com/SyberLabs/attest
      - run: attest prove src/gateway/*.md src/language/*.md --cli-config attest.cli.yaml
      - uses: actions/upload-artifact@v4
        if: always()
        with: { name: attest-report, path: .attest/ }
```

The job fails when any claim ends stale or broken, and the uploaded `.attest/` folder holds the rendered pages and the receipts. The gateway builds with `--no-default-features`, which skips local inference, speech (native whisper code), web search and the config UI (Node). The guide's checks need none of them, and no GPU is involved at any step.

## First targets

Start with one Gateway chapter for the shell path and three Language chapters whose examples mostly run without a model; chapters that lean on models and web tools come after scripting works.

| Order | Chapter | Claims to expect | Why it goes here |
| --- | --- | --- | --- |
| 1 | `gateway/01-install-and-run.md` | Install, version, start with config and profile, `/health`, wrong-token 401, loopback without a key | Exercises fixtures, a service and the install drift in one short chapter |
| 2 | `language/03-blocks-and-prose.md` | 75 prompt examples, 60 with a result fence | The most pairs of any chapter; blocks and prose rarely call a model |
| 3 | `language/08-jump-and-call.md` | 35 prompt examples, 30 paired | Control flow inside one run, so results are exact |
| 4 | `language/09-the-store.md` | 48 prompt examples, 31 paired | Store reads and writes go through the Harness, which tests file effects |
| Later | `language/14-fanout.md`, `15-tasks.md` | 29 and 33 paired | Fan-out and tasks likely need scripted model rounds |
| Later | `language/13-web-fetch-and-search.md` | 31 prompts, none paired | Needs a stubbed web Pack; ends `refused` until then |

The claim counts are fence counts from the docs repo; the compiler's final numbers can differ where a prose pass moves an unpaired example to prose.

## Milestones, tests and acceptance

Six milestones, each closed by a check a reviewer can rerun; the Python side lands before any Rust is written.

1. **Models and verdicts.** `prompt` kind, `PromptObservation`, `expects_error` and the new rules, with pytest tables covering every row of the verdict tables. Done when the suite passes with the existing tests unchanged.
2. **Compiler by rule.** Prompt and result fences pair without a model call. Done when `language/03-blocks-and-prose.md` compiles to 75 prompt claims, 60 of them with `expects`, and a test pins the four-backtick nesting.
3. **Shell services and fixtures.** `attest.cli.yaml`, readiness polling, substitutes. Done when the Gateway chapter runs against a recorded fake gateway in tests, and the install step ends `broken` (or `stale` under `--substitute`).
4. **Fake prompt runner.** `--prompt-runner fake` reads recorded `PromptObservation` JSON, like the Kane fixtures in `tests/fixtures/kane/`. Done when every prompt verdict path has a fixture.
5. **`runvil-docs-host`.** The Rust Host on `harness_runner` with a scripted broker. Done when it returns `hello` for the guide's first `fences` example, the same bytes on two runs, built in CI.
6. **CI on a fork.** The workflow above on a fork of runvil-docs. Done when targets 1 to 4 run on every pull request and the report is attached, with every stale or broken claim either fixed by a doc PR or listed as an issue.

**Acceptance for the whole spec:** a pull request to runvil-docs that breaks a result fence fails CI with a badge on that line, and the same pull request with the fence corrected passes.

## Risks and open questions

The biggest open question is where the Rust Host lives; the biggest risk is noise from claims the compiler places wrong.

**Risks**

- **False brokens from untagged fences.** Until the Gateway fences are tagged, a misread config or output fence shows as a broken step. Mitigation: the upstream tagging PR first, and the model pass flags its own guesses in the verdict reason.
- **Scripted replies hide real model behaviour.** A proven prompt example shows the Engine and Harness behave as documented for that script, not that a real model would answer that way. The badge reason says "scripted".
- **Harness API churn.** `harness_runner` is internal and Runvil renames often. Pinning one Runvil commit per CI run keeps failures attributable; bumping the pin is its own pull request.
- **Build cost.** The gateway pulls native speech code. CI caches the cargo target folder; local runs use the fake runners.

**Open questions for Vinnie**

- [ ] Does `runvil-docs-host` belong in the runvil repo as test support, or outside it in Attest?
- [ ] Is the scripted broker in the Harness test suite a pattern he wants reused, or is there a supported Host-side way to play model rounds?
- [ ] Would he accept tagging the Gateway guide's fences, given the four-backtick house rule?
- [ ] Is `cargo install --path crates/gateway/app` the intended install, or do the installers replace that step?
- [ ] Which promises should the authority policy protect beyond the three in the example?
