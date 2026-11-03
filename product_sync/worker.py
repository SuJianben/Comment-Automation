"""Scheduled work on drafts that have already been confirmed by a real customer."""
import logging
import os
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

from .judgeme import JudgeMeClient, JudgeMeError, JudgeMeRateLimitError
from .sync import sync_products
from .worker_lock import worker_lock
from .worker_store import WorkerStore
from .workflow import ReviewWorkflow
from .workflow_store import utc_now

log = logging.getLogger(__name__)


def _retry_delay(retry_after, attempt, now):
    delay = 60 * (2 ** attempt)
    if retry_after:
        try:
            delay = max(delay, float(retry_after))
        except ValueError:
            try:
                target = parsedate_to_datetime(retry_after)
                if target.tzinfo is None:
                    target = target.replace(tzinfo=timezone.utc)
                delay = max(delay, (target - now).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    return max(0, delay)


def _wait_for_spacing(ledger, store, interval, clock, sleeper):
    last = ledger.last_request(store)
    if last:
        delay = interval - (clock() - last).total_seconds()
        if delay > 0:
            log.info("限速等待 seconds=%.1f", delay)
            sleeper(delay)


def _send_one(flow, ledger, client, draft, *, interval, clock, sleeper):
    draft_id = draft['id']
    prefix = 'gid://shopify/Product/'
    gid = draft['product_id']
    if not gid.startswith(prefix) or not gid[len(prefix):].isdigit():
        flow.store.submission_result(draft_id, 'failed', clock(), error='InvalidProductID')
        return 'failed'
    for attempt in range(3):
        _wait_for_spacing(ledger, flow.config.domain, interval, clock, sleeper)
        if clock().astimezone(ZoneInfo(flow.config.timezone)).date().isoformat() != draft['worker_local_day']:
            flow.store.submission_result(draft_id, 'failed', clock(), error='ScheduleCrossedDayBoundary')
            log.warning("提交等待跨越日期，需人工核实 draft_id=%s", draft_id)
            return 'failed'
        ledger.record_request(flow.config.domain, clock())
        try:
            result = client.create_review(id=gid[len(prefix):], name=draft['reviewer_name'],
                email=draft['reviewer_email'], rating=draft['rating'], title=draft['review_title'], body=draft['review_body'])
        except JudgeMeRateLimitError as exc:
            log.warning("Judge.me 限流 draft_id=%s attempt=%s", draft_id, attempt+1)
            delay = _retry_delay(exc.result.retry_after if exc.result else None, attempt, clock())
            if attempt < 2 and delay <= 3600:
                sleeper(delay)
                continue
            flow.store.submission_result(draft_id, 'failed', clock(), result=exc.result, error='JudgeMeRateLimitError')
            return 'failed'
        except JudgeMeError as exc:
            # A timeout or 5xx may have been accepted. Never automatically resend.
            flow.store.submission_result(draft_id, 'failed', clock(), result=exc.result, error=type(exc).__name__)
            return 'failed'
        except ValueError:
            flow.store.submission_result(draft_id, 'failed', clock(), error='InvalidReviewData')
            return 'failed'
        if result.status_code not in (200, 201):
            flow.store.submission_result(draft_id, 'failed', clock(), result=result, error='UnexpectedSuccessStatus')
            return 'failed'
        flow.store.submission_result(draft_id, 'submitted', clock(), result=result)
        return 'submitted'
    raise AssertionError("unreachable")


def run_once(config, email_domain, *, daily_limit=2, interval=60, clock=utc_now,
             sleeper=time.sleep, shopify_sync=sync_products, client_factory=JudgeMeClient,
             workflow_factory=ReviewWorkflow):
    if type(daily_limit) is not int or not 1 <= daily_limit <= 2:
        raise ValueError("每日上限必须为 1 或 2")
    if not isinstance(interval, (int, float) ) or isinstance(interval, bool) or interval < 60:
        raise ValueError("请求间隔必须至少 60 秒")
    summary = {'polled': 0, 'generated': 0, 'generation_failed': 0, 'submitted': 0, 'submission_failed': 0}
    with worker_lock(config.db_path):
        log.info("任务开始 store=%s", config.domain)
        summary['polled'] = shopify_sync(config)
        flow = workflow_factory(config, email_domain, clock=clock)
        try:
            ledger = WorkerStore(flow.store)
            # Give already-authorized reviews priority within the shared daily budget.
            token = os.getenv('JUDGEME_PRIVATE_API_TOKEN', '').strip()
            if not token:
                log.warning("Judge.me 配置缺失，跳过提交")
            else:
                client = client_factory(config.domain, token)
                while True:
                    draft = ledger.claim_next(config.domain, clock(), config.timezone, daily_limit)
                    if draft is None:
                        break
                    log.info("开始提交 draft_id=%s", draft['id'])
                    outcome = _send_one(flow, ledger, client, draft, interval=interval, clock=clock, sleeper=sleeper)
                    summary['submitted' if outcome == 'submitted' else 'submission_failed'] += 1
                    log.info("提交结果 draft_id=%s status=%s", draft['id'], outcome)
            remaining = max(0, daily_limit - ledger.used_today(config.domain, clock(), config.timezone))
            if remaining and os.getenv('LLM_API_KEY', '').strip() and os.getenv('LLM_MODEL', '').strip():
                generation = flow.generate_for_day(max_attempts=remaining)
                summary['generated'] = generation['generated']
                summary['generation_failed'] = generation['failed']
            elif remaining:
                log.warning("模型配置缺失，跳过草稿生成")
            log.info("任务结束 store=%s polled=%s generated=%s generation_failed=%s submitted=%s submission_failed=%s", config.domain, summary['polled'], summary['generated'], summary['generation_failed'], summary['submitted'], summary['submission_failed'])
            return summary
        finally:
            flow.close()
