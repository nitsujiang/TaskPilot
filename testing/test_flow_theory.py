"""
Theory tests for parser flows without calling Gemini or Slack.

These assert the control plane when the API is down (empty dict), on quota
(exception path inside call_gemini), or when short replies use fuzzy urgency.
Run from repo root: python -m unittest testing.test_flow_theory -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestParserFlowTheory(unittest.TestCase):
    @patch("agent.parser.send_slack_message_with_fallback")
    @patch("agent.parser.call_gemini")
    def test_meeting_missing_urgency_triggers_clarification(self, mock_gemini, mock_send):
        """LLM returns structured meeting; parser recomputes missing urgency."""
        mock_gemini.return_value = {
            "task": "meeting",
            "title": "Productivity 1",
            "description": "TaskPilot test",
            "owners": ["<@U08RC56EE14>"],
            "deadline": None,
            "urgency": None,
            "missing_infos": [],
        }
        from agent.parser import process_message

        out = process_message(
            "I want to book a meeting with <@U08RC56EE14> title \"P\" description \"D\"",
            channel="C0TEST",
            timezone="America/New_York",
            thread_ts="1.0",
        )
        self.assertEqual(out.get("task"), "meeting")
        self.assertIn("urgency", out.get("missing_infos", []))
        mock_send.assert_called()
        body = mock_send.call_args[0][1]
        self.assertIn("urgency", body.lower())

    @patch("agent.parser.send_slack_message_with_fallback")
    @patch("agent.parser.call_gemini")
    def test_clarification_empty_llm_fuzzy_hig_completes(self, mock_gemini, mock_send):
        """When merge LLM returns {}, 'hig' still fills urgency; no extra Slack if complete."""
        mock_gemini.return_value = {}
        from agent.parser import process_clarification

        task_data = {
            "task": "meeting",
            "title": "P",
            "description": "D",
            "owners": ["<@U1>"],
            "urgency": None,
            "missing_infos": ["urgency"],
        }
        out = process_clarification("hig", task_data, "C0TEST", "UTC", "1.0")
        self.assertEqual(out.get("urgency"), "high")
        self.assertEqual(out.get("missing_infos"), [])
        mock_send.assert_not_called()

    @patch("agent.parser.send_slack_message_with_fallback")
    @patch(
        "agent.parser.take_last_gemini_user_hint",
        return_value="The AI service hit a usage or quota limit right now. Please try again later.",
    )
    @patch("agent.parser.call_gemini", return_value={})
    def test_extract_quota_single_user_message(self, mock_gemini, mock_take, mock_send):
        """After a failed extract, one quota-style hint is shown; not stacked with repeat copy."""
        from agent.parser import process_message

        out = process_message(
            "I want to book a meeting with <@U1> title \"T\" description \"D\"",
            channel="C0TEST",
            thread_ts="2.0",
        )
        self.assertEqual(out, {})
        mock_send.assert_called_once()
        body = mock_send.call_args[0][1]
        self.assertIn("quota", body.lower())
        self.assertNotIn("didn't catch", body.lower())

    @patch("agent.parser.send_slack_message_with_fallback")
    @patch("agent.parser.call_gemini")
    def test_extract_empty_no_exception_repeat_only(self, mock_gemini, mock_send):
        """Empty model output without transport error → repeat prompt, no quota wording."""
        mock_gemini.return_value = {}
        from agent.parser import process_message

        process_message(
            "something that does not look like urgency shorthand only",
            channel="C0TEST",
            thread_ts="3.0",
        )
        body = mock_send.call_args[0][1]
        self.assertNotIn("quota", body.lower())
        self.assertIn("repeat", body.lower())


if __name__ == "__main__":
    unittest.main()
