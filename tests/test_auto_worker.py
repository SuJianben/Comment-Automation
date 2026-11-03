import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from product_sync.judgeme import JudgeMeOutcomeUnknown, JudgeMeRateLimitError, JudgeMeResult
from product_sync.worker import run_once
from product_sync.worker_store import WorkerStore
from product_sync.workflow_store import WorkflowStore, timestamp


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 3, 3, 0, tzinfo=timezone.utc)
        self.sleeps = []
    def __call__(self):
        return self.now
    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)


class Client:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []
    def create_review(self, **kwargs):
        self.calls.append(kwargs['id'])
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = SimpleNamespace(domain='test.myshopify.com', token='x', api_version='2026-07', db_path=Path(self.tmp.name)/'db.sqlite', timezone='Asia/Shanghai')
        self.clock = Clock()
        self.env = patch.dict(os.environ, {'LLM_API_KEY':'', 'LLM_MODEL':'', 'JUDGEME_PRIVATE_API_TOKEN':'test'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def seed(self, count=3):
        store = WorkflowStore(self.config.db_path)
        for i in range(count):
            product = {'store': self.config.domain, 'product_id':f'gid://shopify/Product/{i+1}', 'title':'Item', 'polled_at':timestamp(self.clock())}
            id = store.reserve_draft(product, self.clock())
            store.finish_draft(id, {'reviewer_name':'Sample reviewer', 'rating':5, 'review_title':'Title', 'review_body':'Body'}, 'sample@example.invalid', self.clock())
            store.approve(id, 'Customer', 'real@example.com', 'customer record', 'operator', self.clock(), rating=5, review_title='Title', review_body='Confirmed')
        store.close()

    def run_worker(self, client):
        return run_once(self.config, 'example.invalid', clock=self.clock, sleeper=self.clock.sleep,
                        shopify_sync=lambda config: 0, client_factory=lambda domain, token: client)

    def test_429_backoff_daily_limit_restart_and_spacing(self):
        self.seed()
        rate = JudgeMeRateLimitError('limited', JudgeMeResult(429, {}, '90'))
        client = Client([rate, JudgeMeResult(201, {'accepted': True}), JudgeMeResult(200, {'accepted': True})])
        result = self.run_worker(client)
        self.assertEqual(result['submitted'], 2)
        self.assertEqual(client.calls, ['1', '1', '2'])
        self.assertEqual(self.clock.sleeps, [90.0, 60.0])
        store = WorkflowStore(self.config.db_path)
        self.assertEqual([store.get(i)['status'] for i in (1,2,3)], ['submitted','submitted','approved'])
        store.close()
        self.assertEqual(self.run_worker(Client([]))['submitted'], 0)

    def test_unknown_result_not_retried_or_reclaimed(self):
        self.seed(1)
        client = Client([JudgeMeOutcomeUnknown('timeout')])
        self.assertEqual(self.run_worker(client)['submission_failed'], 1)
        self.assertEqual(client.calls, ['1'])
        self.assertEqual(self.run_worker(Client([]))['submitted'], 0)
        store = WorkflowStore(self.config.db_path)
        self.assertEqual(store.get(1)['status'], 'failed')
        store.close()

    def test_unauthorized_or_missing_provenance_is_never_claimed(self):
        self.seed(1)
        store = WorkflowStore(self.config.db_path)
        with store.db:
            store.db.execute("UPDATE review_drafts SET authorization_reference='' WHERE id=1")
        store.close()
        self.assertEqual(self.run_worker(Client([]))['submitted'], 0)
        store = WorkflowStore(self.config.db_path)
        self.assertEqual(store.get(1)['status'], 'approved')
        store.close()

    def test_crash_during_request_leaves_sending_and_no_repeat(self):
        self.seed(1)
        client = Client([RuntimeError('unexpected crash')])
        with self.assertRaises(RuntimeError):
            self.run_worker(client)
        self.assertEqual(self.run_worker(Client([]))['submitted'], 0)
        store = WorkflowStore(self.config.db_path)
        self.assertEqual(store.get(1)['status'], 'sending')
        store.close()

    def test_pipeline_uses_real_store_to_poll_generate_and_send_only_approved(self):
        from product_sync.cache import ProductCache
        from product_sync.workflow import ReviewWorkflow
        self.seed(1)
        def poll(config):
            cache = ProductCache(config.db_path)
            item = {'id':'gid://shopify/Product/99','title':'New item','descriptionHtml':'<p>Product</p>',
                    'tags':[], 'status':'ACTIVE','updatedAt':'2026-11-03T00:00:00Z'}
            cache.apply(config.domain, item)
            cache.record_poll(config.domain, item, timestamp(self.clock()))
            cache.db.commit()
            cache.close()
            return 1
        def factory(config, domain, clock):
            def generate(title, description, features):
                return {'reviewer_name':'Sample reviewer','rating':4,'review_title':'Draft','review_body':'Draft body'}
            return ReviewWorkflow(config, domain, clock=clock, generator=generate)
        client = Client([JudgeMeResult(201, {'accepted': True})])
        with patch.dict(os.environ, {'LLM_API_KEY':'test','LLM_MODEL':'test'}):
            summary = run_once(self.config, 'example.invalid', clock=self.clock, sleeper=self.clock.sleep,
                               shopify_sync=poll, client_factory=lambda domain, token: client, workflow_factory=factory)
        self.assertEqual((summary['polled'], summary['generated'], summary['submitted']), (1,1,1))
        store = WorkflowStore(self.config.db_path)
        self.assertEqual(store.get(1)['status'], 'submitted')
        self.assertEqual(store.get(2)['status'], 'pending_review')
        self.assertEqual(store.get(2)['is_authorized'], 0)
        store.close()
        self.assertEqual(client.calls, ['1'])

    def test_second_worker_cannot_acquire_same_database_lock(self):
        from product_sync.worker_lock import worker_lock
        with worker_lock(self.config.db_path):
            with self.assertRaises(RuntimeError):
                with worker_lock(self.config.db_path):
                    pass

    def test_daily_boundary_does_not_send_previous_days_claim(self):
        self.clock.now = datetime(2026, 11, 3, 15, 59, 30, tzinfo=timezone.utc)
        self.seed(1)
        store = WorkflowStore(self.config.db_path)
        ledger = WorkerStore(store)
        ledger.record_request(self.config.domain, self.clock())
        store.close()
        client = Client([])
        self.assertEqual(self.run_worker(client)['submission_failed'], 1)
        self.assertEqual(client.calls, [])
        store = WorkflowStore(self.config.db_path)
        self.assertEqual(store.get(1)['last_error'], 'ScheduleCrossedDayBoundary')
        store.close()

    def test_generation_caps_at_two_without_submitting_new_drafts(self):
        from product_sync.cache import ProductCache
        from product_sync.workflow import ReviewWorkflow
        cache = ProductCache(self.config.db_path)
        for n in range(4):
            item = {'id': f'gid://shopify/Product/{n+1}', 'title':f'Item {n}', 'descriptionHtml':'', 'tags':[], 'status':'ACTIVE', 'updatedAt':'2026-11-03T00:00:00Z'}
            cache.apply(self.config.domain, item)
            cache.record_poll(self.config.domain, item, timestamp(self.clock()))
        cache.db.commit()
        cache.close()
        count = []
        def generator(title, description, features):
            count.append(title)
            return {'reviewer_name':'Sample reviewer','rating':5,'review_title':'Title','review_body':'Body'}
        flow = ReviewWorkflow(self.config, 'example.invalid', generator=generator, clock=self.clock)
        self.addCleanup(flow.close)
        result = flow.generate_for_day(max_attempts=2)
        self.assertEqual(result['generated'], 2)
        self.assertEqual(len(count), 2)
        self.assertTrue(all(d['status']=='pending_review' for d in flow.store.list_by_status(self.config.domain, ['pending_review'])))

    def test_two_older_approved_submissions_use_same_daily_budget_as_generation(self):
        self.clock.now -= timedelta(days=1)
        self.seed(2)
        self.clock.now += timedelta(days=1)
        client = Client([JudgeMeResult(201, {}), JudgeMeResult(201, {})])
        with patch.dict(os.environ, {'LLM_API_KEY':'key','LLM_MODEL':'model'}), patch('product_sync.workflow.ReviewWorkflow.generate_for_day') as gen:
            result = self.run_worker(client)
            self.assertEqual(result['submitted'], 2)
            gen.assert_not_called()
        self.assertEqual(client.calls, ['1', '2'])

    def test_generation_limit_applies_to_ui_created_drafts(self):
        self.seed(2)
        store = WorkflowStore(self.config.db_path)
        ledger = WorkerStore(store)
        self.assertEqual(ledger.generated_today(self.config.domain, self.clock(), self.config.timezone), 2)
        store.close()
        with patch.dict(os.environ, {'LLM_API_KEY':'key','LLM_MODEL':'model'}), patch('product_sync.workflow.ReviewWorkflow.generate_for_day') as gen:
            self.run_worker(Client([JudgeMeResult(201, {}), JudgeMeResult(201, {})]))
            gen.assert_not_called()


if __name__ == '__main__':
    unittest.main()
