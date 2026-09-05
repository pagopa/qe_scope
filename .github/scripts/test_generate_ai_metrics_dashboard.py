import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("generate_ai_metrics_dashboard.py")
SPEC = importlib.util.spec_from_file_location("generate_ai_metrics_dashboard", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class GenerateAiMetricsDashboardTest(unittest.TestCase):
    def test_renders_kpis_rows_and_safe_content(self):
        dataset = {
            "updated_at": "2026-09-05T04:42:46Z",
            "executions": [
                {
                    "key": "123:1",
                    "run": {
                        "conclusion": "success",
                        "created_at": "2026-09-05T10:00:00Z",
                        "copilot_duration_seconds": 61,
                        "url": "https://github.example/runs/123",
                    },
                    "issue": {
                        "number": 8,
                        "jira_key": "QA-42",
                        "title": "[QA-42] Correggi <script>alert(1)</script>",
                        "url": "https://github.example/issues/8",
                    },
                    "pull_request": {
                        "number": 9,
                        "url": "https://github.example/pull/9",
                        "state": "MERGED",
                        "created_at": "2026-09-05T10:02:00Z",
                        "merged_at": "2026-09-05T11:02:00Z",
                        "additions": 8,
                        "deletions": 2,
                    },
                    "copilot": {
                        "telemetry_status": "complete",
                        "resolved_models": ["claude-sonnet-5"],
                        "input_tokens": 1000,
                        "output_tokens": 50,
                        "cache_read_input_tokens": 700,
                        "ai_units": 1.25,
                    },
                },
                {
                    "key": "124:1",
                    "run": {"conclusion": "failure"},
                    "issue": {"jira_key": "QA-43", "title": "Fallimento"},
                    "pull_request": None,
                    "copilot": {
                        "telemetry_status": "unavailable",
                        "requested_models": ["gpt-5"],
                        "input_tokens": 200,
                        "output_tokens": 10,
                    },
                },
            ],
        }

        result = MODULE.render_dashboard(dataset)

        self.assertIn("AI Coding Pilot", result)
        self.assertIn("1 su 2 esecuzioni", result)
        self.assertIn("1 su 1 PR prodotte", result)
        self.assertIn("1.260", result)
        self.assertIn("claude-sonnet-5", result)
        self.assertIn("Esecuzione fallita", result)
        self.assertIn("Correggi &lt;script&gt;alert(1)&lt;/script&gt;", result)
        self.assertNotIn("Correggi <script>alert(1)</script>", result)
        self.assertNotIn("<script src=", result)
        self.assertNotIn("<link rel=", result)

    def test_renders_empty_dataset(self):
        result = MODULE.render_dashboard({"executions": []})

        self.assertIn("Nessuna esecuzione raccolta", result)
        self.assertIn("<div class=\"kpi-value\">n/d</div>", result)
        self.assertIn('Risultati: <span id="visible-count">0</span>', result)


if __name__ == "__main__":
    unittest.main()
