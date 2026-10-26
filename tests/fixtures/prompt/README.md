# Prompt observation fixtures

One `PromptObservation` record per prompt verdict path in docs/CLI-RUNNER-SPEC.md, in the shape
`--prompt-runner fake --prompt-observations <file>` reads: a JSON object mapping claim ids to
observations, like the Kane fixtures in `tests/fixtures/kane/`.

These are hand-written, not recorded: `runvil-docs-host` does not exist yet (spec milestone 5),
so `host_version` says so. When the Host lands, re-record each file from a real run of the
matching example and replace the `host_version` with the Runvil commit.

| File | Claim it is judged with | Verdict |
| --- | --- | --- |
| `completed_match.json` | expects `hello` | proven |
| `completed_no_expects.json` | no result fence | proven |
| `completed_mismatch.json` | expects `hello` | stale |
| `parse_error.json` | success promised | broken |
| `failed.json` | success promised | broken |
| `needs_script.json` | any | unverifiable |
| `refused.json` | any | unverifiable |
| `expected_error_kind_matches.json` | `expects_error`, expects `UnclosedFence` | proven |
| `expected_error_message_matches.json` | `expects_error`, expects `fence is not closed` | proven |
| `expected_error_wrong_kind.json` | `expects_error`, expects `UnclosedFence` | stale |
| `expected_error_but_completed.json` | `expects_error` | broken |

Through the fake runner every one of these ends `unverifiable`: the fake replays a recording and
is simulation only. `tests/test_prompt_runner.py` judges the raw records for the verdict column.
