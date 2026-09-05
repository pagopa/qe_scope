#!/usr/bin/env python3
"""Aggregate per-run Copilot reports into a durable, idempotent dataset."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _load_json(path: Path | None, default: Any) -> Any:
    if path is None or not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _value(item: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in item:
            return item[name]
    return None


def _duration_seconds(started_at: Any, finished_at: Any) -> float | None:
    if not isinstance(started_at, str) or not isinstance(finished_at, str):
        return None
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        finish = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, round((finish - start).total_seconds(), 3))


def _record_key(run_id: Any, run_attempt: Any) -> str | None:
    normalized_id = _integer(run_id)
    normalized_attempt = _integer(run_attempt) or 1
    if normalized_id is None:
        return None
    return f"{normalized_id}:{normalized_attempt}"


def _runs_by_key(runs: list[Any]) -> dict[str, dict[str, Any]]:
    result = {}
    for item in runs:
        if not isinstance(item, dict):
            continue
        key = _record_key(
            _value(item, "id", "databaseId"),
            _value(item, "run_attempt", "attempt"),
        )
        if key:
            result[key] = item
    return result


def _prs_by_branch(prs: list[Any]) -> dict[str, dict[str, Any]]:
    result = {}
    for item in prs:
        if not isinstance(item, dict):
            continue
        branch = _value(item, "headRefName", "head_ref_name")
        if branch:
            result[str(branch)] = item
    return result


def _run_data(item: dict[str, Any], report_github: dict[str, Any]) -> dict[str, Any]:
    started_at = _value(item, "run_started_at", "startedAt") or report_github.get(
        "started_at"
    )
    finished_at = report_github.get("finished_at")
    return {
        "id": _integer(_value(item, "id", "databaseId"))
        or _integer(report_github.get("run_id")),
        "attempt": _integer(_value(item, "run_attempt", "attempt"))
        or _integer(report_github.get("run_attempt"))
        or 1,
        "workflow": _value(item, "name", "workflowName")
        or report_github.get("workflow"),
        "event": item.get("event"),
        "status": item.get("status"),
        "conclusion": item.get("conclusion"),
        "url": _value(item, "html_url", "url") or report_github.get("run_url"),
        "created_at": _value(item, "created_at", "createdAt"),
        "started_at": started_at,
        "updated_at": _value(item, "updated_at", "updatedAt"),
        "copilot_duration_seconds": _duration_seconds(started_at, finished_at),
    }


def _pr_data(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if item is None:
        return None
    return {
        "number": _integer(item.get("number")),
        "url": item.get("url"),
        "state": item.get("state"),
        "is_draft": _value(item, "isDraft", "is_draft"),
        "head_ref_name": _value(item, "headRefName", "head_ref_name"),
        "created_at": _value(item, "createdAt", "created_at"),
        "updated_at": _value(item, "updatedAt", "updated_at"),
        "closed_at": _value(item, "closedAt", "closed_at"),
        "merged_at": _value(item, "mergedAt", "merged_at"),
        "review_decision": _value(item, "reviewDecision", "review_decision"),
        "additions": _integer(item.get("additions")),
        "deletions": _integer(item.get("deletions")),
        "changed_files": _integer(_value(item, "changedFiles", "changed_files")),
    }


def _report_files(reports_dir: Path) -> list[Path]:
    if not reports_dir.exists():
        return []
    return sorted(reports_dir.rglob("copilot-technical-report.json"))


def _reports_by_key(reports_dir: Path) -> dict[str, dict[str, Any]]:
    result = {}
    for path in _report_files(reports_dir):
        report = _load_json(path, {})
        if not isinstance(report, dict):
            continue
        github = report.get("github") or {}
        key = _record_key(github.get("run_id"), github.get("run_attempt"))
        if not key:
            continue
        previous = result.get(key)
        if previous is None or str(report.get("generated_at") or "") >= str(
            previous.get("generated_at") or ""
        ):
            result[key] = report
    return result


def _new_record(
    key: str,
    report: dict[str, Any],
    run: dict[str, Any],
    pr: dict[str, Any] | None,
    ingested_at: str,
) -> dict[str, Any]:
    github = report.get("github") or {}
    issue = report.get("issue") or {}
    copilot = report.get("copilot") or {}
    issue_number = _integer(github.get("issue_number"))
    return {
        "key": key,
        "ingested_at": ingested_at,
        "report_generated_at": report.get("generated_at"),
        "repository": github.get("repository"),
        "run": _run_data(run, github),
        "issue": {
            "number": issue_number,
            "jira_key": issue.get("jira_key"),
            "title": issue.get("title"),
            "url": issue.get("url"),
        },
        "pull_request": _pr_data(pr),
        "copilot": copilot,
    }


def collect_dataset(
    reports_dir: Path,
    runs: list[Any],
    prs: list[Any],
    existing: dict[str, Any] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    timestamp = now or datetime.now(timezone.utc).isoformat()
    existing = existing if isinstance(existing, dict) else {}
    records = {
        item["key"]: item
        for item in existing.get("executions", [])
        if isinstance(item, dict) and isinstance(item.get("key"), str)
    }
    reports = _reports_by_key(reports_dir)
    runs_index = _runs_by_key(runs)
    prs_index = _prs_by_branch(prs)

    for key, report in reports.items():
        github = report.get("github") or {}
        issue_number = _integer(github.get("issue_number"))
        run_id = _integer(github.get("run_id"))
        branch = (
            f"copilot/issue-{issue_number}-{run_id}"
            if issue_number is not None and run_id is not None
            else None
        )
        old = records.get(key, {})
        records[key] = _new_record(
            key,
            report,
            runs_index.get(key, {}),
            prs_index.get(branch) if branch else None,
            old.get("ingested_at") or timestamp,
        )

    # Refresh mutable GitHub state even after the source artifact has expired.
    for key, old in list(records.items()):
        run = runs_index.get(key)
        issue_number = _integer((old.get("issue") or {}).get("number"))
        run_id = _integer((old.get("run") or {}).get("id"))
        branch = (
            f"copilot/issue-{issue_number}-{run_id}"
            if issue_number is not None and run_id is not None
            else None
        )
        if run:
            report_github = {
                "run_id": run_id,
                "run_attempt": (old.get("run") or {}).get("attempt"),
                "run_url": (old.get("run") or {}).get("url"),
                "workflow": (old.get("run") or {}).get("workflow"),
                "started_at": (old.get("run") or {}).get("started_at"),
            }
            refreshed = _run_data(run, report_github)
            refreshed["copilot_duration_seconds"] = (old.get("run") or {}).get(
                "copilot_duration_seconds"
            )
            old["run"] = refreshed
        if branch and branch in prs_index:
            old["pull_request"] = _pr_data(prs_index[branch])

    executions = sorted(
        records.values(),
        key=lambda item: (
            str((item.get("run") or {}).get("created_at") or ""), item["key"]
        ),
    )
    previous_executions = existing.get("executions", [])
    updated_at = existing.get("updated_at")
    if executions != previous_executions or not updated_at:
        updated_at = timestamp
    return {
        "schema_version": 1,
        "updated_at": updated_at,
        "executions": executions,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports-dir", type=Path, required=True)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--prs", type=Path, required=True)
    parser.add_argument("--existing", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    dataset = collect_dataset(
        reports_dir=args.reports_dir,
        runs=_load_json(args.runs, []),
        prs=_load_json(args.prs, []),
        existing=_load_json(args.existing, {}),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dataset, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
