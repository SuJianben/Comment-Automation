import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from product_sync.judgeme import JudgeMeResult
from product_sync.workflow_store import WorkflowStore, utc_now, timestamp


class ReviewUITests(unittest.TestCase):
    def test_approval_submission_and_history(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "SHOPIFY_STORE_DOMAIN": "test.myshopify.com", "SYNC_DB_PATH": str(Path(directory) / "data.db"),
            "DEFAULT_EMAIL_DOMAIN": "example.invalid", "JUDGEME_PRIVATE_API_TOKEN": "test-token"}):
            store = WorkflowStore(Path(directory) / "data.db")
            product = {"store": "test.myshopify.com", "product_id": "gid://shopify/Product/123", "title": "Bottle", "polled_at": timestamp(utc_now())}
            one = store.reserve_draft(product, utc_now())
            store.finish_draft(one, {"reviewer_name": "Sample reviewer", "rating": 5, "review_title": "Nice", "review_body": "Looks useful"}, "sample-reviewer@example.invalid", utc_now())
            product['product_id'] = 'gid://shopify/Product/456'
            two = store.reserve_draft(product, utc_now())
            store.finish_draft(two, {"reviewer_name": "Sample reviewer", "rating": 4, "review_title": "Good", "review_body": "Good design"}, "sample-reviewer@example.invalid", utc_now())
            store.close()
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app_ui.py"), default_timeout=10).run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.text_area), 2)
            # Newest draft is first; do not touch the other record.
            app.text_input[0].set_value("Customer Name")
            app.text_input[1].set_value("customer@example.com")
            app.text_input[2].set_value("Reviewer")
            app.text_input[3].set_value("Consent #1")
            app.checkbox[0].check()
            with patch("product_sync.judgeme.JudgeMeClient.create_review", return_value=JudgeMeResult(200, {"accepted": True})) as send:
                next(button for button in app.button if button.label == "确认并提交").click().run()
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(send.call_count, 1)
                self.assertEqual(send.call_args.kwargs['id'], '456')
            store = WorkflowStore(Path(directory) / "data.db")
            self.assertEqual(store.get(two)['status'], 'submitted')
            self.assertEqual(store.get(one)['status'], 'pending_review')
            self.assertIn('accepted', store.get(two)['response_payload'])
            store.close()
            self.assertTrue(any("已提交" in e.label for e in app.expander))


    def test_large_queue_shows_validation_and_submits_selected_draft(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "SHOPIFY_STORE_DOMAIN": "test.myshopify.com", "SYNC_DB_PATH": str(Path(directory) / "data.db"),
            "DEFAULT_EMAIL_DOMAIN": "example.invalid", "JUDGEME_PRIVATE_API_TOKEN": "test-token"}):
            store = WorkflowStore(Path(directory) / "data.db")
            for n in range(100):
                item = {"store": "test.myshopify.com", "product_id": f"gid://shopify/Product/{n+1}", "title": f"Product {n+1}", "polled_at": timestamp(utc_now())}
                draft_id = store.reserve_draft(item, utc_now())
                store.finish_draft(draft_id, {"reviewer_name": "Sample reviewer", "rating": 5, "review_title": "Nice", "review_body": "Looks useful"}, "sample-reviewer@example.invalid", utc_now())
            store.close()
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app_ui.py"), default_timeout=30).run()
            self.assertEqual(len(app.exception), 0)
            next(button for button in app.button if button.label == "确认并提交").click().run(timeout=30)
            self.assertTrue(any("未提交" in message.value for message in app.error))
            app.text_input[0].set_value("Real Customer")
            app.text_input[1].set_value("real@example.com")
            app.text_input[2].set_value("Reviewer")
            app.text_input[3].set_value("Consent #1")
            app.checkbox[0].check()
            with patch("product_sync.judgeme.JudgeMeClient.create_review", return_value=JudgeMeResult(200, {"accepted": True})) as send:
                next(button for button in app.button if button.label == "确认并提交").click().run(timeout=30)
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(send.call_count, 1)
                self.assertEqual(send.call_args.kwargs['id'], '100')
            store = WorkflowStore(Path(directory) / "data.db")
            self.assertEqual(store.get(100)['status'], 'submitted')
            self.assertEqual(store.get(99)['status'], 'pending_review')
            store.close()

    def test_product_navigation_sync_search_and_return(self):
        from product_sync.cache import ProductCache
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "SHOPIFY_STORE_DOMAIN": "test.myshopify.com", "SHOPIFY_API_TOKEN": "test-token",
            "SYNC_DB_PATH": str(Path(directory) / "data.db"), "DEFAULT_EMAIL_DOMAIN": "example.invalid"}):
            cache = ProductCache(Path(directory) / "data.db")
            for n in range(25):
                cache.apply("test.myshopify.com", {"id": f"gid://shopify/Product/{n+1}", "title": f"Kit {n:02}",
                    "descriptionHtml": "<p>Comfortable <b>cotton</b></p>", "tags": ["jersey"], "status": "ACTIVE", "updatedAt": "2026-11-01T00:00:00Z"})
            cache.db.commit()
            cache.close()
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app_ui.py"), default_timeout=15).run()
            with patch("product_sync.product_ui.sync_products", return_value=0) as sync:
                app.sidebar.radio[0].set_value("店铺商品").run()
                self.assertEqual(sync.call_count, 1)
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(len(app.expander), 20)
                app.text_input[0].set_value("Kit 24").run()
                self.assertEqual(len(app.expander), 1)
                self.assertEqual(app.expander[0].label, "Kit 24")
                self.assertEqual(sync.call_count, 1)
                next(button for button in app.button if button.label == "刷新商品").click().run()
                self.assertEqual(sync.call_count, 2)
                app.sidebar.radio[0].set_value("评价审核").run()
                self.assertTrue(any(tab.label == "待审核" for tab in app.tabs))
                app.sidebar.radio[0].set_value("店铺商品").run()
                self.assertEqual(sync.call_count, 3)

    def test_product_sync_error_retains_cached_view(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "SHOPIFY_STORE_DOMAIN": "test.myshopify.com", "SHOPIFY_API_TOKEN": "test-token",
            "SYNC_DB_PATH": str(Path(directory) / "data.db"), "DEFAULT_EMAIL_DOMAIN": "example.invalid"}):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app_ui.py"), default_timeout=15).run()
            with patch("product_sync.product_ui.sync_products", side_effect=RuntimeError("connection failed")):
                app.sidebar.radio[0].set_value("店铺商品").run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(any("商品同步失败" in error.value for error in app.error))

    def test_sidebar_preview_and_workflow(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "SHOPIFY_STORE_DOMAIN": "test.myshopify.com", "SHOPIFY_API_TOKEN": "shop-token",
            "SYNC_DB_PATH": str(Path(directory) / "data.db"), "DEFAULT_EMAIL_DOMAIN": "example.invalid",
            "JUDGEME_PRIVATE_API_TOKEN": "judge-token", "LLM_API_KEY": "llm-token", "LLM_MODEL": "model"}):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app_ui.py"), default_timeout=10).run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(any("LLM API Key：已配置" in text.value for text in app.sidebar.markdown))
            next(button for button in app.sidebar.button if button.label == "插入 2 条模拟测试数据").click().run()
            self.assertEqual(len(app.exception), 0)
            store = WorkflowStore(Path(directory) / "data.db")
            drafts = store.list_by_status("test.myshopify.com", ["pending_review"])
            self.assertEqual(len(drafts), 2)
            self.assertTrue(all(d['is_test'] == 1 for d in drafts))
            with self.assertRaises(ValueError):
                store.approve(drafts[0]['id'], "Real", "real@example.com", "consent", "operator", utc_now(), rating=5, review_title="title", review_body="body")
            store.close()
            self.assertTrue(all(b.disabled for b in app.button if b.label == "确认并提交"))
            with patch("product_sync.review_ui.run_daily_workflow", return_value={"generated": 2, "skipped": 1, "failed": 0}) as run:
                next(button for button in app.sidebar.button if button.label == "从 Shopify 拉取并生成新草稿").click().run()
                self.assertEqual(run.call_count, 1)
                self.assertTrue(any("新增 2 条" in x.value for x in app.info))
            next(button for button in app.sidebar.button if button.label == "刷新页面").click().run()
            self.assertEqual(len(app.exception), 0)

    def test_missing_llm_does_not_start_workflow(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "SHOPIFY_STORE_DOMAIN": "test.myshopify.com", "SYNC_DB_PATH": str(Path(directory) / "data.db"),
            "DEFAULT_EMAIL_DOMAIN": "example.invalid", "LLM_API_KEY": "", "LLM_MODEL": ""}):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app_ui.py"), default_timeout=10).run()
            with patch("product_sync.review_ui.run_daily_workflow") as run:
                next(button for button in app.sidebar.button if button.label == "从 Shopify 拉取并生成新草稿").click().run()
                run.assert_not_called()
            self.assertTrue(any("LLM_API_KEY" in x.value for x in app.error))

if __name__ == '__main__':
    unittest.main()

