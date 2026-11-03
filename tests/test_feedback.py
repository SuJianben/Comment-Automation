import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import requests
from product_sync.feedback import FeedbackGenerationError, generate_feedback


def response(content, status=200):
    return SimpleNamespace(status_code=status, json=lambda: {"choices": [{"message": {"content": content}}]})


class Session:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return result


class FeedbackTests(unittest.TestCase):
    def call(self, session, **kwargs):
        return generate_feedback("Bottle", "Steel bottle", ["Steel"], api_key="secret", model="test-model", session=session, **kwargs)

    @patch("product_sync.feedback.time.sleep")
    def test_network_and_invalid_json_retried_then_success(self, sleep):
        good = {"reviewer_name": "Jane", "rating": 5, "review_title": "Looks handy", "review_body": "The steel design looks nice."}
        session = Session([requests.Timeout(), response("not json"), response(json.dumps(good))])
        result = self.call(session)
        self.assertEqual(result["reviewer_name"], "Sample reviewer")
        self.assertEqual(result["rating"], 5)
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(session.calls[0][0], "https://api.openai.com/v1/chat/completions")
        self.assertEqual(session.calls[0][1]["json"]["response_format"], {"type": "json_object"})
        self.assertEqual(sleep.call_count, 2)

    @patch("product_sync.feedback.time.sleep")
    def test_invalid_schema_exhausts_retries(self, sleep):
        bad = {"reviewer_name": "Name", "rating": True, "review_title": "Title", "review_body": "Body", "extra": "x"}
        with self.assertRaises(FeedbackGenerationError):
            self.call(Session([response(json.dumps(bad))] * 2), max_attempts=2)

    def test_auth_failure_is_not_retried(self):
        session = Session([response("", 401)])
        with self.assertRaisesRegex(ValueError, "401"):
            self.call(session)
        self.assertEqual(len(session.calls), 1)

    def test_invalid_base_url_rejected(self):
        with self.assertRaises(ValueError):
            self.call(Session([]), base_url="http://example.com/v1")


if __name__ == "__main__":
    unittest.main()
