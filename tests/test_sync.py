import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from product_sync.cache import ProductCache
from product_sync.shopify import ShopifyClient
from product_sync.sync import sync_products


def product(id, status="ACTIVE", title="First"):
    return {"id": id, "status": status, "title": title, "descriptionHtml": "<p>Details</p>", "tags": ["tag"], "updatedAt": "2026-10-01T00:00:00Z"}


class FakeClient:
    def __init__(self, items, fail=False):
        self.items, self.fail, self.search = items, fail, None

    def products(self, search):
        self.search = search
        yield from self.items
        if self.fail:
            raise RuntimeError("page failed")


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = SimpleNamespace(domain="test.myshopify.com", token="x", api_version="2026-07", db_path=Path(self.tmp.name) / "cache.db")

    def read(self):
        cache = ProductCache(self.config.db_path)
        result = cache.list_products(self.config.domain)
        cache.close()
        return result

    def test_incremental_dedup_update_remove_and_full_rebuild(self):
        client = FakeClient([product("1"), product("2")])
        sync_products(self.config, client=client)
        self.assertEqual(client.search, "status:active")
        client = FakeClient([product("1", title="Updated"), product("2", status="DRAFT"), product("3")])
        sync_products(self.config, client=client)
        self.assertIn("updated_at:>=", client.search)
        data = self.read()
        self.assertEqual([(x["id"], x["title"]) for x in data], [("1", "Updated"), ("3", "First")])
        self.assertEqual(data[0]["tags"], ["tag"])
        sync_products(self.config, full=True, client=FakeClient([product("1")]))
        self.assertEqual([x["id"] for x in self.read()], ["1"])

    def test_failed_full_sync_rolls_back_data_and_progress(self):
        sync_products(self.config, client=FakeClient([product("1")]))
        cache = ProductCache(self.config.db_path)
        before = cache.watermark(self.config.domain)
        cache.close()
        with self.assertRaises(RuntimeError):
            sync_products(self.config, full=True, client=FakeClient([product("2")], fail=True))
        self.assertEqual([x["id"] for x in self.read()], ["1"])
        cache = ProductCache(self.config.db_path)
        self.assertEqual(cache.watermark(self.config.domain), before)
        cache.close()

    def test_client_paginates_and_retries_throttle(self):
        def response(data):
            r = SimpleNamespace(status_code=200, json=lambda: data, raise_for_status=lambda: None)
            return r
        first = {"data": {"products": {"nodes": [product("1")], "pageInfo": {"hasNextPage": True, "endCursor": "cursor1"}}}}
        second = {"data": {"products": {"nodes": [product("2")], "pageInfo": {"hasNextPage": False, "endCursor": "cursor2"}}}}
        throttle = {"errors": [{"message": "throttled", "extensions": {"code": "THROTTLED"}}]}
        class Session:
            def __init__(self):
                self.calls = []
            def post(self, url, **kwargs):
                self.calls.append(kwargs["json"]["variables"])
                return response([throttle, first, second][len(self.calls)-1])
        session = Session()
        with patch("product_sync.shopify.time.sleep"):
            result = list(ShopifyClient("test.myshopify.com", "secret", "2026-07", session).products("status:active"))
        self.assertEqual([x["id"] for x in result], ["1", "2"])
        self.assertEqual(session.calls[-1]["cursor"], "cursor1")


if __name__ == "__main__":
    unittest.main()
