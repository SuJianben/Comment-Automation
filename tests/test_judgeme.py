import unittest
from types import SimpleNamespace
import requests

from product_sync.judgeme import (
    JudgeMeAPIError, JudgeMeAuthenticationError, JudgeMeBadRequest, JudgeMeClient,
    JudgeMeOutcomeUnknown, JudgeMeRateLimitError,
)


class Session:
    def __init__(self, status=200, payload=None, error=None):
        self.status, self.payload, self.error = status, payload, error
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return SimpleNamespace(status_code=self.status, headers={"Retry-After": "5"},
                               json=lambda: self.payload, text="")


class JudgeMeTests(unittest.TestCase):
    def client(self, session):
        return JudgeMeClient("store.myshopify.com", "private-key", session=session)

    def submit(self, client):
        return client.create_review(id="1234", name="Name", email="customer@example.com", rating=5, title="Title", body="Body")

    def test_request_and_success(self):
        session = Session(payload={"message": "accepted"})
        result = self.submit(self.client(session))
        self.assertEqual((result.status_code, result.data), (200, {"message": "accepted"}))
        url, kwargs = session.calls[0]
        self.assertEqual(url, "https://judge.me/api/v1/reviews")
        self.assertEqual(kwargs["json"]["id"], 1234)
        self.assertEqual(kwargs["json"]["platform"], "shopify")
        self.assertEqual(kwargs["headers"]["X-Api-Token"], "private-key")
        self.assertNotIn("api_token", kwargs["json"])

    def test_error_responses_and_no_retries(self):
        for status, error in [(400, JudgeMeBadRequest), (422, JudgeMeBadRequest),
                              (401, JudgeMeAuthenticationError), (403, JudgeMeAuthenticationError),
                              (429, JudgeMeRateLimitError), (500, JudgeMeOutcomeUnknown),
                              (404, JudgeMeAPIError)]:
            with self.subTest(status=status):
                session = Session(status, {"error": "test"})
                with self.assertRaises(error) as caught:
                    self.submit(self.client(session))
                self.assertEqual(caught.exception.result.status_code, status)
                self.assertEqual(caught.exception.result.retry_after, "5")
                self.assertEqual(len(session.calls), 1)

    def test_timeout_is_unknown_and_not_retried(self):
        session = Session(error=requests.Timeout())
        with self.assertRaises(JudgeMeOutcomeUnknown):
            self.submit(self.client(session))
        self.assertEqual(len(session.calls), 1)

    def test_graphql_gid_is_rejected(self):
        with self.assertRaises(ValueError):
            self.client(Session()).create_review("gid://shopify/Product/123", "Name", "a@b.com", 5, "Title", "Body")


if __name__ == "__main__":
    unittest.main()
