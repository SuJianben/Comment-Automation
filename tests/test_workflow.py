import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace

from product_sync.judgeme import JudgeMeOutcomeUnknown, JudgeMeResult
from product_sync.workflow import ReviewWorkflow, run_daily_workflow
from product_sync.workflow_store import timestamp


class Shopify:
    def products(self, search):
        yield {"id": "gid://shopify/Product/123", "title": "Bottle", "descriptionHtml": "Steel", "tags": ["sale"], "status": "ACTIVE", "updatedAt": "2026-11-02T00:00:00Z"}


def generator(title, description, features):
    assert features == []
    return {"reviewer_name": "Sample reviewer", "rating": 5, "review_title": "Nice", "review_body": "Looks useful"}


class Judge:
    shop_domain = "test.myshopify.com"
    def __init__(self, fail=False, status=200):
        self.calls = []
        self.fail = fail
        self.status = status
    def create_review(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise JudgeMeOutcomeUnknown("timeout")
        return JudgeMeResult(self.status, {"accepted": True})


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = SimpleNamespace(domain="test.myshopify.com", token="x", api_version="2026-07", db_path=Path(self.tmp.name)/"db.sqlite", timezone="Asia/Shanghai")
        self.assertEqual(run_daily_workflow(self.config, "example.invalid", shopify_client=Shopify(), generator=generator)['generated'], 1)
        self.flow = ReviewWorkflow(self.config, "example.invalid", generator=generator)
        self.addCleanup(self.flow.close)

    def approve(self):
        self.flow.approve(1, reviewer_name="Real Customer", reviewer_email="real@example.com", is_authorized=True,
            authorization_reference="customer approval 123", operator="operator", rating=4,
            review_title="Customer title", review_body="Customer confirmed text")

    def test_complete_flow_and_duplicate_skip(self):
        self.assertEqual(self.flow.store.get(1)['status'], 'pending_review')
        self.assertEqual(self.flow.generate_for_day()['skipped'], 1)
        with self.assertRaises(ValueError):
            self.flow.approve(1, reviewer_name="Real", reviewer_email="sample-reviewer@example.invalid", is_authorized=True,
                authorization_reference="yes", operator="operator", rating=4, review_title="Title", review_body="Body")
        with self.assertRaises(ValueError):
            self.flow.approve(1, reviewer_name="Real", reviewer_email="real@example.com", is_authorized=False,
                authorization_reference="yes", operator="operator", rating=4, review_title="Title", review_body="Body")
        self.approve()
        client = Judge()
        self.assertEqual(self.flow.submit_approved(client)['submitted'], 1)
        self.assertEqual(client.calls[0]['id'], '123')
        self.assertEqual(self.flow.store.get(1)['status'], 'submitted')
        self.assertIn('accepted', self.flow.store.get(1)['response_payload'])
        self.assertEqual(self.flow.submit_approved(client)['submitted'], 0)
        self.flow.confirm_synced(1, operator="operator", verification_reference="Judge.me review 123")
        self.assertEqual(self.flow.store.get(1)['status'], 'synced')

    def test_http_201_records_submitted_without_resending(self):
        self.approve()
        client = Judge(status=201)
        self.assertEqual(self.flow.submit_approved(client)['submitted'], 1)
        record = self.flow.store.get(1)
        self.assertEqual(record['status'], 'submitted')
        self.assertEqual(record['response_status'], 201)
        self.assertIsNone(record['last_error'])
        self.assertEqual(self.flow.submit_approved(client)['submitted'], 0)
        self.assertEqual(len(client.calls), 1)

    def test_unexpected_2xx_remains_failed_for_review(self):
        self.approve()
        client = Judge(status=204)
        self.assertEqual(self.flow.submit_approved(client)['failed'], 1)
        self.assertEqual(self.flow.store.get(1)['status'], 'failed')

    def test_uncertain_failure_not_automatically_retried(self):
        self.approve()
        client = Judge(fail=True)
        self.assertEqual(self.flow.submit_approved(client)['failed'], 1)
        self.assertEqual(self.flow.store.get(1)['status'], 'failed')
        self.flow.submit_approved(client)
        self.assertEqual(len(client.calls), 1)

    def test_clock_rolling_24_hours(self):
        row = self.flow.store.get(1)
        created = datetime.fromisoformat(row['created_at'])
        # Explicit date selects the original polling date even after advancing clock.
        from zoneinfo import ZoneInfo
        poll_day = datetime.fromisoformat(row['source_polled_at']).astimezone(ZoneInfo('Asia/Shanghai')).date()
        self.flow.clock = lambda: created + timedelta(hours=23)
        self.assertEqual(self.flow.generate_for_day(poll_day)['skipped'], 1)
        self.flow.clock = lambda: created + timedelta(hours=25)
        self.assertEqual(self.flow.generate_for_day(poll_day)['generated'], 1)


if __name__ == '__main__':
    unittest.main()
