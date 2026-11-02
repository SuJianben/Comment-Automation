import logging
from datetime import datetime, timedelta, timezone
from .cache import ProductCache
from .shopify import ShopifyClient

log = logging.getLogger(__name__)


def sync_products(config, full=False, client=None):
    client = client or ShopifyClient(config.domain, config.token, config.api_version)
    cache = ProductCache(config.db_path)
    try:
        # The write lock serializes runs for this database, including across processes.
        cache.db.execute("BEGIN IMMEDIATE")
        previous = cache.watermark(config.domain)
        full = full or previous is None
        # An overlap helps with indexing delay; the upsert makes rereads harmless.
        start = datetime.now(timezone.utc) - timedelta(minutes=5)
        watermark = start.strftime("%Y-%m-%dT%H:%M:%SZ")
        if previous and watermark < previous:
            raise RuntimeError("系统时间早于上次同步时间，请检查系统时钟")
        search = "status:active" if full else f"status:active,archived,draft,unlisted updated_at:>='{previous}'"
        if full:
            cache.db.execute("DELETE FROM products WHERE store=?", (config.domain,))
        count = 0
        for product in client.products(search):
            cache.apply(config.domain, product)
            count += 1
        cache.db.execute("INSERT INTO sync_state VALUES (?, ?) ON CONFLICT(store) DO UPDATE SET watermark=excluded.watermark", (config.domain, watermark))
        cache.db.commit()
        log.info("商品同步成功 store=%s mode=%s fetched=%d watermark=%s", config.domain, "full" if full else "incremental", count, watermark)
        return count
    except Exception:
        cache.db.rollback()
        log.exception("商品同步失败，缓存和进度已回滚 store=%s", config.domain)
        raise
    finally:
        cache.close()
