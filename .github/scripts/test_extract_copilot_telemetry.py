import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("extract_copilot_telemetry.py")
SPEC = importlib.util.spec_from_file_location("extract_copilot_telemetry", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def attributes(values):
    result = []
    for key, value in values.items():
        kind = "intValue" if isinstance(value, int) else "stringValue"
        result.append({"key": key, "value": {kind: str(value)}})
    return result


class ExtractCopilotTelemetryTests(unittest.TestCase):
    def test_extracts_top_level_totals_without_double_counting_chat(self):
        root = {
            "attributes": attributes(
                {
                    "gen_ai.operation.name": "invoke_agent",
                    "server.address": "api.githubcopilot.com",
                    "gen_ai.request.model": "auto",
                    "gen_ai.usage.input_tokens": 120,
                    "gen_ai.usage.output_tokens": 30,
                    "gen_ai.usage.cache_read.input_tokens": 80,
                    "github.copilot.turn_count": 2,
                    "github.copilot.aiu": 1,
                }
            )
        }
        chat = {
            "attributes": attributes(
                {
                    "gen_ai.operation.name": "chat",
                    "gen_ai.request.model": "auto",
                    "gen_ai.response.model": "claude-sonnet-4.6",
                    "gen_ai.usage.input_tokens": 70,
                    "gen_ai.usage.output_tokens": 20,
                }
            )
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "telemetry.jsonl"
            path.write_text(json.dumps({"resourceSpans": [{"spans": [root, chat]}]}) + "\n")
            result = MODULE.extract_telemetry(path)

        self.assertEqual(result["telemetry_status"], "complete")
        self.assertEqual(result["requested_models"], ["auto"])
        self.assertEqual(result["resolved_models"], ["claude-sonnet-4.6"])
        self.assertEqual(result["input_tokens"], 120)
        self.assertEqual(result["output_tokens"], 30)
        self.assertEqual(result["cache_read_input_tokens"], 80)
        self.assertEqual(result["turn_count"], 2)
        self.assertEqual(result["ai_units"], 1)

    def test_missing_telemetry_is_reported_without_failure(self):
        result = MODULE.extract_telemetry(Path("does-not-exist.jsonl"))

        self.assertEqual(result["telemetry_status"], "unavailable")
        self.assertIsNone(result["input_tokens"])
        self.assertEqual(result["resolved_models"], [])


if __name__ == "__main__":
    unittest.main()
