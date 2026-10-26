# G7 Kane resource gate

FACTORY-REDTEAM-AND-RUBRICS §3 G7: block merge when "a deadline, cleanup, or isolation test fails, or port-kill code is present". Rubric R7 (bounded resources, cleanup observed). Kane themes from §1: a process exceeds its deadline, cleanup runs in `finally` on crash, nothing kills "whatever owns the port", the run stays in its disposable environment, retries and steps are bounded.

**Check:** `g7-kane-resource / G7 Kane resource` (`.github/workflows/g7-kane-resource.yml`). It is a separate workflow, so it doesn't touch `tests.yml` (G8/G1) or the G5 workflow.

**Status:** Proposed. Update this line only from hosted run output.

## What fails the job

| Step | Command | Fails when |
|---|---|---|
| Port-kill scan | `python scripts/g7-kane-resource.py port-kill` | Any line of a `.py/.sh/.bash/.ps1/.cmd/.bat/.js/.mjs/.cjs/.ts` file under `attest/` or `scripts/` matches a pattern in `tests/quality/g7_port_kill_patterns.txt`. Comments are removed before matching; string literals and docstrings are scanned, because argv lists are strings. The step also fails if a scan root is missing or no file was read. |
| Deadline, cleanup and isolation tests | `python scripts/g7-kane-resource.py tests` | Any node id in `tests/quality/g7_kane_resource.txt` is missing, deselected, skipped, xfailed, xpassed, errored or failed, or any required section (`deadline`, `cleanup`, `isolation`, `port-kill`, `control`) is empty. Runs with `xfail_strict=true`. |

The tests step runs even when the scan fails, so one run reports both halves. Both steps write a table of counts to the job summary. These are counts of listed ids, not coverage figures.

## Port-kill patterns

These live in `tests/quality/g7_port_kill_patterns.txt`, outside the scanned paths, so the scanner never matches its own list.

`netstat`, `taskkill`, `fuser`, `lsof`, `killall`, `pkill`, `kill-port`, `ss` with a `-p` flag, psutil `connections(` / `net_connections(`, `Get-NetTCPConnection`, `Stop-Process`.

Owned cleanup goes through `attest/execution/supervisor.py` (`terminate_process_tree`: `os.killpg` on the group attest created on POSIX, a Toolhelp walk of the owned PID's descendants on Windows). The scan doesn't flag that path. It also doesn't prove the absence of every possible port-to-PID lookup, for example a raw read of `/proc/net/tcp` followed by `os.kill`. The patterns cover the common helpers.

## Positive controls

`[control]` in the manifest:

- `test_well_behaved_kane_process_finishes_under_its_deadline`: a stand-in kane-cli replays the captured `author_pass` NDJSON and exits 0 under `KaneBrowserRunner._execute`. The run parses with the same session id and steps as the fixture.
- `tests/test_kane.py::test_runner_runs_a_group_once_and_maps_each_claim`
- `tests/test_shell.py::test_echo_exit_zero`

A gate that kills or refuses everything can't go green.

## Not covered (Kane gaps, kept out of the manifest because they fail today)

These are recorded as strict xfails in open PRs #26 and #30. G7 doesn't list them, because a listed xfail fails the gate. Once each fix lands, add its test to the manifest.

- **KR-COST-01 (#26):** `KaneBrowserRunner._execute` checks its deadline only when a line of output arrives. A Kane process that goes silent outlives its deadline. G7 covers only the chatty case (`test_kane_process_that_keeps_writing_is_killed_at_its_deadline`).
- **KR-ISO-06 (#30):** `_execute` starts kane-cli without a new process group and kills only the leader, so a child kane-cli spawned can keep running. G7 checks that the leader itself is reaped.
- **KR-ISO-04 (#26, stub):** a live Kane run escaping its disposable browser profile or network scope. This needs a live Kane run with filesystem and network probes, so it isn't offline-testable.
- `extract_zip_safely` / `safe_pack_path` (symlink, size cap, traversal) are tested here, but `run_group` still unpacks with `kane.unpack`, which has a zip-slip guard but no size cap.
- Platform: the job runs on `ubuntu-latest`. Two cleanup tests read `/proc` and skip on Windows, and a skip fails the gate, so the gate is Linux-only by design.
