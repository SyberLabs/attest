"""Kane CLI adapter with one-claim judgment barriers and exact pack identity."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import zipfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .execution.sandbox import extract_zip_safely, safe_pack_path
from .execution.supervisor import ProcessResult, ProcessSupervisor
from .models import BrowserObservation, Claim

ProcessRunner = Callable[..., ProcessResult]
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_RUN_ID_KEYS = ("run_id", "execution_id", "pack_id", "evidence_id", "runId", "executionId")
_SUCCESS = {"passed", "pass", "success", "succeeded", "completed", "complete", "ok"}
_FAILURE = {"failed", "failure", "fail", "error", "aborted", "cancelled", "canceled"}
_UNREACHABLE = {"unreachable", "not_reachable", "page_unreachable"}


def _default_process_runner(
    argv: Sequence[str], *, cwd: Path, env: Mapping[str, str], timeout_s: float
) -> ProcessResult:
    return ProcessSupervisor(timeout_s=timeout_s).run(argv, cwd=cwd, env=env)


def find_pack(evidence_root: Path | str, run_id: str) -> Path:
    """Find only a pack whose filename encodes this exact run identity.

    There is deliberately no newest-pack or most-recent-modification fallback:
    an absent or ambiguous pack is evidence failure, never a successful run.
    """
    if not _RUN_ID_RE.fullmatch(run_id) or run_id in {".", ".."}:
        raise ValueError("invalid Kane run identity")
    root = Path(evidence_root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Kane evidence directory not found for run {run_id}")
    names = {run_id, f"{run_id}.evidence", f"{run_id}.zip", f"{run_id}.evidence.zip"}
    matches: list[Path] = []
    for candidate in root.rglob("*"):
        if candidate.name not in names or candidate.is_symlink():
            continue
        try:
            candidate.resolve().relative_to(root)
        except ValueError:
            continue
        if candidate.is_file() or candidate.is_dir():
            matches.append(candidate)
    if not matches:
        raise FileNotFoundError(f"no evidence pack matches Kane run {run_id}")
    if len(matches) != 1:
        raise ValueError(f"multiple evidence packs match Kane run {run_id}")
    return matches[0]


def _event_name(event: Mapping[str, Any]) -> str:
    for key in ("event", "type", "name", "event_type"):
        value = event.get(key)
        if isinstance(value, str):
            return value.casefold().replace("-", "_")
    return ""


def _nested_objects(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _nested_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _nested_objects(child)


def _run_identity(events: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    end_events = [event for event in events if _event_name(event) in {"run_end", "runend"}]
    if len(end_events) != 1:
        raise ValueError("Kane output must contain exactly one run_end event")
    end = end_events[0]
    identities: set[str] = set()
    for obj in _nested_objects(end):
        for key in _RUN_ID_KEYS:
            value = obj.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                identities.add(str(value).strip())
    if len(identities) != 1:
        raise ValueError("Kane run_end event has no unambiguous run identity")
    return identities.pop(), end


def _parse_events(output: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(output.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Kane emitted malformed NDJSON on line {line_number}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"Kane emitted a non-object event on line {line_number}")
        events.append(value)
    return events


def _field_text(objects: Sequence[Mapping[str, Any]], *keys: str) -> str | None:
    result: str | None = None
    for obj in objects:
        for item in _nested_objects(dict(obj)):
            for key in keys:
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    result = value.strip()
    return result


def _status_value(value: Any) -> str | None:
    if isinstance(value, str):
        normalized = value.strip().casefold().replace("-", "_")
        if normalized in _SUCCESS | _FAILURE | _UNREACHABLE:
            return normalized
    return None


def _status_candidates(value: Any) -> list[str]:
    statuses: list[str] = []
    if isinstance(value, dict):
        for key in ("status", "result", "state"):
            status = _status_value(value.get(key))
            if status:
                statuses.append(status)
        for key in ("passed", "success", "is_passed"):
            flag = value.get(key)
            if isinstance(flag, bool):
                statuses.append("passed" if flag else "failed")
        for key, child in value.items():
            if key not in {"final_state", "observed", "values", "variables"}:
                statuses.extend(_status_candidates(child))
    elif isinstance(value, list):
        for child in value:
            statuses.extend(_status_candidates(child))
    return statuses


def _step_status(events: list[dict[str, Any]], end_event: Mapping[str, Any], step: Mapping[str, Any]) -> str:
    packed_statuses = _status_candidates(dict(step))
    if not packed_statuses:
        return "failed"  # a valid packed per-step result is mandatory
    event_statuses: list[str] = []
    for event in events:
        name = _event_name(event)
        if "step" not in name and not any(k in event for k in ("step_index", "step_number", "step_id")):
            continue
        event_statuses.extend(_status_candidates(event))
    run_statuses = _status_candidates(dict(end_event))
    if any(status in _UNREACHABLE for status in packed_statuses + event_statuses):
        return "unreachable"
    if any(status in _FAILURE for status in packed_statuses + event_statuses + run_statuses):
        return "failed"
    if any(status in _SUCCESS for status in packed_statuses) and any(status in _SUCCESS for status in run_statuses):
        return "passed"
    return "failed"  # incomplete or ambiguous evidence is never treated as success


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Kane pack has a missing or malformed step.json") from exc
    if not isinstance(value, dict):
        raise ValueError("Kane pack step.json must contain an object")
    return value


def _find_values(value: Any, key: str) -> list[Any]:
    values: list[Any] = []
    for obj in _nested_objects(value):
        if key in obj:
            values.append(obj[key])
    return values


def _as_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        for key in ("text", "value", "label"):
            if key in value:
                return _as_text(value[key])
    return None


def _pack_step_dir(root: Path, number: int = 1) -> Path:
    for candidate in (f"steps/{number}", f"steps/{number:02d}", "steps/0"):
        path = safe_pack_path(root, candidate)
        if path.is_dir():
            return path
    return safe_pack_path(root, f"steps/{number}")


def _copy_screenshot(root: Path, step_dir: Path, screenshot_root: Path, step: Mapping[str, Any]) -> str | None:
    screenshot: Path | None = None
    for obj in _nested_objects(dict(step)):
        for key in ("screenshot", "screenshot_path", "annotated", "annotated_path"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                try:
                    candidate = safe_pack_path(root, value.strip())
                except ValueError:
                    continue
                if candidate.is_file():
                    screenshot = candidate
                    break
        if screenshot:
            break
    if screenshot is None:
        for name in ("annotated.png", "screenshot.png"):
            candidate = step_dir / name
            if candidate.is_file():
                screenshot = candidate
                break
    if screenshot is None:
        return None
    payload = screenshot.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    destination = screenshot_root.resolve() / f"{digest}.png"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.is_file() or hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=f".{digest}-", delete=False) as tmp:
            tmp.write(payload)
            tmp_path = Path(tmp.name)
        try:
            os.replace(tmp_path, destination)
        finally:
            tmp_path.unlink(missing_ok=True)
    return str(destination)


def _read_failure(root: Path, step_dir: Path) -> str | None:
    failure = safe_pack_path(root, str(step_dir.relative_to(root) / "failure.yaml"))
    if not failure.is_file():
        return None
    return failure.read_text(encoding="utf-8", errors="replace")[:12000]


class KaneBrowserRunner:
    """Run one claim at a time so each result can be judged before the next action."""

    name = "kane"

    def __init__(
        self,
        cli: str | Sequence[str] = "kane-cli",
        *,
        workdir: Path | str | None = None,
        evidence_root: Path | str | None = None,
        screenshot_root: Path | str | None = None,
        timeout_s: float = 180.0,
        max_steps: int = 15,
        env: Mapping[str, str] | None = None,
        process_runner: ProcessRunner | None = None,
    ) -> None:
        self.cli = [cli] if isinstance(cli, str) else list(cli)
        if not self.cli:
            raise ValueError("Kane CLI command must not be empty")
        if timeout_s <= 0 or max_steps < 1:
            raise ValueError("timeout_s and max_steps must be positive")
        self.workdir = Path(workdir or Path.cwd()).resolve()
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.evidence_root = Path(evidence_root) if evidence_root is not None else self.workdir / ".testmuai" / "evidence"
        self.screenshot_root = Path(screenshot_root) if screenshot_root is not None else self.workdir / ".attest" / "shots"
        if not self.evidence_root.is_absolute():
            self.evidence_root = self.workdir / self.evidence_root
        if not self.screenshot_root.is_absolute():
            self.screenshot_root = self.workdir / self.screenshot_root
        self.timeout_s = timeout_s
        self.max_steps = max_steps
        self.env = dict(os.environ if env is None else env)
        self.process_runner = process_runner or _default_process_runner

    def _test_text(self, claim: Claim, base_url: str) -> str:
        if not claim.store_as or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", claim.store_as):
            raise ValueError(f"browser claim {claim.id} has an invalid store_as key")
        # Flatten untrusted Markdown so an embedded heading cannot create a
        # second Kane step (which would run before this claim is judged).
        objective = " ".join(claim.objective.split())
        return (
            "---\n"
            "mode: testing\n"
            f"max_steps: {self.max_steps}\n"
            f"url: {json.dumps(base_url)}\n"
            "---\n\n"
            f"## {claim.id}\n"
            f"{objective}\n"
            f"Store the exact text you read as `{claim.store_as}`.\n"
        )

    def observe(self, claim: Claim, base_url: str | None) -> BrowserObservation:
        if claim.kind != "browser":
            raise ValueError("KaneBrowserRunner.observe requires a browser claim")
        if not claim.store_as or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", claim.store_as):
            return BrowserObservation(status="failed", note=f"browser claim {claim.id} has an invalid store_as key")
        if not base_url:
            return BrowserObservation(status="unreachable", note="no reachable base URL was provided")

        with tempfile.TemporaryDirectory(prefix="attest-kane-", dir=self.workdir) as temporary:
            run_dir = Path(temporary)
            test_path = run_dir / f"{claim.id}_test.md"
            test_path.write_text(self._test_text(claim, base_url), encoding="utf-8")
            argv = [*self.cli, "testmd", "run", "--agent", str(test_path)]
            try:
                result = self.process_runner(argv, cwd=self.workdir, env=self.env, timeout_s=self.timeout_s)
            except TimeoutError:
                return BrowserObservation(status="failed", note="Kane process exceeded its wall-clock deadline")
            except OSError as exc:
                return BrowserObservation(status="failed", note=f"Kane process could not start: {exc}")
            if result.timed_out:
                return BrowserObservation(status="failed", note="Kane process exceeded its wall-clock deadline")
            try:
                events = _parse_events(result.stdout)
                run_id, end_event = _run_identity(events)
                pack = find_pack(self.evidence_root, run_id)
                with tempfile.TemporaryDirectory(prefix="attest-kane-pack-") as unpack_dir:
                    if pack.is_dir():
                        pack_root = pack.resolve()
                    elif zipfile.is_zipfile(pack):
                        pack_root = extract_zip_safely(pack, Path(unpack_dir))
                    else:
                        raise ValueError("Kane evidence pack is neither a directory nor a zip archive")
                    step_dir = _pack_step_dir(pack_root)
                    step_path = safe_pack_path(pack_root, str(step_dir.relative_to(pack_root) / "step.json"))
                    step = _load_json(step_path)
                    status = _step_status(events, end_event, step)
                    if result.returncode != 0:
                        status = "failed"
                    final_state = None
                    for obj in _nested_objects(step):
                        value = obj.get("final_state")
                        if isinstance(value, dict):
                            final_state = value
                    if final_state is None:
                        for event in events:
                            for obj in _nested_objects(event):
                                value = obj.get("final_state")
                                if isinstance(value, dict):
                                    final_state = value
                    values = _find_values(final_state or {}, claim.store_as)
                    observed_value = _as_text(values[-1]) if values else None
                    observed = {claim.store_as: observed_value} if observed_value is not None else {}
                    note = _field_text([step], "remark", "note", "message")
                    failure_text = _read_failure(pack_root, step_dir)
                    if failure_text:
                        note = f"{note}\n{failure_text}" if note else failure_text
                    screenshot = _copy_screenshot(pack_root, step_dir, self.screenshot_root, step)
                    return BrowserObservation(status=status, observed=observed, screenshot=screenshot, note=note)
            except (FileNotFoundError, ValueError, OSError, zipfile.BadZipFile) as exc:
                return BrowserObservation(status="failed", note=f"Kane evidence rejected: {exc}")

    def close(self) -> None:
        """Process supervision tears down each Kane process group after observe()."""
        return None

    def __enter__(self) -> "KaneBrowserRunner":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
