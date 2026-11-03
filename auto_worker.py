"""Run the local authorized-review worker once or on a daily schedule."""
import argparse
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from product_sync.config import Config
from product_sync.worker import run_once


def main():
    parser = argparse.ArgumentParser(description="Shopify 商品与已授权评价后台任务")
    parser.add_argument("--once", action="store_true", help="立即运行一次并退出")
    parser.add_argument("--run-now", action="store_true", help="启动定时任务前立即运行一次")
    args = parser.parse_args()
    if args.once and args.run_now:
        parser.error("--once 和 --run-now 不能同时使用")
    log_path = Path(__file__).resolve().parent / 'auto_worker.log'
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s',
        handlers=[RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3, encoding='utf-8'), logging.StreamHandler()])
    config = Config.from_env()
    email_domain = os.environ.get('DEFAULT_EMAIL_DOMAIN', '')
    limit = int(os.environ.get('WORKER_DAILY_LIMIT', '2'))
    interval = int(os.environ.get('WORKER_INTERVAL_SECONDS', '60'))
    if not 1 <= limit <= 2 or interval < 60:
        parser.error('WORKER_DAILY_LIMIT 必须是 1 或 2，WORKER_INTERVAL_SECONDS 必须至少为 60')

    def job():
        try:
            run_once(config, email_domain, daily_limit=limit, interval=interval)
        except Exception as exc:
            # Avoid writing tokens, customer data, response bodies or prompts to log.
            logging.error('任务失败 error_type=%s', type(exc).__name__)
            if args.once:
                raise

    if args.once:
        job()
        return
    scheduler = BlockingScheduler(timezone=config.timezone)
    scheduler.add_job(job, 'cron', hour=config.hour, minute=config.minute,
                      id='authorized_review_worker', max_instances=1, coalesce=True)
    logging.info('计划时间 %02d:%02d timezone=%s', config.hour, config.minute, config.timezone)
    if args.run_now:
        job()
    scheduler.start()


if __name__ == '__main__':
    main()
