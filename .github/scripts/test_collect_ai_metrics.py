import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("collect_ai_metrics.py")
SPEC = importlib.util.spec_from_file_location("collect_ai_metrics", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class CollectAiMetricsTest(unittest.TestCase):
    def _write_report(self, root: Path) -> None:
        report = {
            "schema_version": 1,
            "generated_at": "2026-09-03T10:05:00+00:00",
            "github": {
                "repository": "pagopa/qe_scope",
                "run_id": 123,
                "run_attempt": 1,
                "run_url": "https://github.example/actions/runs/123",
                "workflow": "Copilot implement issue",
                "issue_number": "38",
                "started_at": "2026-09-03T10:00:00Z",
                "finished_at": "2026-09-03T10:04:30Z",
            },
            "issue": {
                "jira_key": "QA-17085",
                "title": "[QA-17085] Test",
                "url": "https://github.example/issues/38",
            },
            "copilot": {
                "resolved_models": ["claude-sonnet-5"],
                "input_tokens": 100,
                "output_tokens": 20,
                "ai_units": 1.5,
            },
        }
        destination = root / "artifact"
        destination.mkdir()
        (destination / "copilot-technical-report.json").write_text(
            json.dumps(report)
        )

    def test_collects_and_enriches_a_report(self):
        with tempfile.TemporaryDirectory() as directory:
            reports = Path(directory)
            self._write_report(reports)
            runs = [
                {
                    "id": 123,
                    "run_attempt": 1,
                    "name": "Copilot implement issue",
                    "event": "issues",
                    "status": "completed",
                    "conclusion": "success",
                    "html_url": "https://github.example/actions/runs/123",
                    "created_at": "2026-09-03T09:59:00Z",
                    "run_started_at": "2026-09-03T10:00:00Z",
                    "updated_at": "2026-09-03T10:05:00Z",
                }
            ]
            prs = [
                {
                    "number": 39,
                    "url": "https://github.example/pull/39",
                    "state": "MERGED",
                    "isDraft": False,
                    "headRefName": "copilot/issue-38-123",
                    "createdAt": "2026-09-03T10:05:00Z",
                    "updatedAt": "2026-09-03T11:00:00Z",
                    "closedAt": "2026-09-03T11:00:00Z",
                    "mergedAt": "2026-09-03T11:00:00Z",
                    "reviewDecision": "APPROVED",
                    "additions": 8,
                    "deletions": 2,
                    "changedFiles": 1,
                }
            ]

            result = MODULE.collect_dataset(
                reports, runs, prs, now="2026-09-03T12:00:00+00:00"
            )

        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(len(result["executions"]), 1)
        execution = result["executions"][0]
        self.assertEqual(execution["key"], "123:1")
        self.assertEqual(execution["issue"]["jira_key"], "QA-17085")
        self.assertEqual(execution["run"]["copilot_duration_seconds"], 270.0)
        self.assertEqual(execution["pull_request"]["state"], "MERGED")
        self.assertEqual(execution["copilot"]["input_tokens"], 100)

    def test_rerun_is_idempotent_and_refreshes_pr_state(self):
        with tempfile.TemporaryDirectory() as directory:
            reports = Path(directory)
            self._write_report(reports)
            first = MODULE.collect_dataset(
                reports, [], [], now="2026-09-03T12:00:00+00:00"
            )
            prs = [
                {
                    "number": 39,
                    "headRefName": "copilot/issue-38-123",
                    "state": "MERGED",
                    "mergedAt": "2026-09-03T13:00:00Z",
                }
            ]
            second = MODULE.collect_dataset(
                reports,
                [],
                prs,
                existing=first,
                now="2026-09-03T14:00:00+00:00",
            )
            third = MODULE.collect_dataset(
                reports,
                [],
                prs,
                existing=second,
                now="2026-09-03T15:00:00+00:00",
            )

        self.assertEqual(len(second["executions"]), 1)
        self.assertEqual(
            second["executions"][0]["ingested_at"],
            "2026-09-03T12:00:00+00:00",
        )
        self.assertEqual(second["executions"][0]["pull_request"]["state"], "MERGED")
        self.assertEqual(third, second)


if __name__ == "__main__":
    unittest.main()
