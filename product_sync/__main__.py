import argparse
import json
import logging
from .cache import ProductCache
from .config import Config
from .sync import sync_products


def main():
    parser = argparse.ArgumentParser(description="Shopify 商品同步")
    parser.add_argument("command", choices=["sync", "schedule", "list"])
    parser.add_argument("--full", action="store_true", help="全量重建当前店铺的 active 商品缓存（仅 sync 使用）")
    args = parser.parse_args()
    if args.full and args.command != "sync":
        parser.error("--full 只能用于 sync")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config = Config.from_env()
    if args.command == "sync":
        sync_products(config, full=args.full)
    elif args.command == "list":
        cache = ProductCache(config.db_path)
        try:
            print(json.dumps(cache.list_products(config.domain), ensure_ascii=False, indent=2))
        finally:
            cache.close()
    else:
        from apscheduler.schedulers.blocking import BlockingScheduler
        scheduler = BlockingScheduler(timezone=config.timezone)
        scheduler.add_job(sync_products, "cron", hour=config.hour, minute=config.minute,
                          args=[config], id="shopify_product_sync", max_instances=1, coalesce=True)
        logging.info("每天 %02d:%02d (%s) 同步，进程需持续运行", config.hour, config.minute, config.timezone)
        scheduler.start()


if __name__ == "__main__":
    main()
