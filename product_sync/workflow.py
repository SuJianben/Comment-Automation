"""Orchestrate polling, review drafts, human approval and Judge.me submission."""
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .feedback import generate_feedback
from .judgeme import JudgeMeError
from .sync import sync_products
from .workflow_store import WorkflowStore, timestamp, utc_now


class ReviewWorkflow:
    def __init__(self, config, email_domain, *, generator=generate_feedback, clock=utc_now):
        if not isinstance(email_domain, str) or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", email_domain) or '.' not in email_domain:
            raise ValueError("DEFAULT_EMAIL_DOMAIN 必须是有效的占位邮箱域名")
        self.config, self.email_domain, self.generator, self.clock = config, email_domain.lower(), generator, clock
        self.store = WorkflowStore(config.db_path)

    def close(self):
        self.store.close()

    def generate_for_day(self, day=None, *, max_attempts=None):
        zone = ZoneInfo(self.config.timezone)
        day = day or self.clock().astimezone(zone).date()
        if not isinstance(day, date) or isinstance(day, datetime):
            raise ValueError("day 必须是日期")
        start = timestamp(datetime.combine(day, time.min, tzinfo=zone))
        end = timestamp(datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone))
        products = self.store.products_polled_between(self.config.domain, start, end)
        result = {"generated": 0, "skipped": 0, "failed": 0}
        for product in products:
            if max_attempts is not None and result['generated'] + result['failed'] >= max_attempts:
                break
            draft_id = self.store.reserve_draft(product, self.clock())
            if draft_id is None:
                result['skipped'] += 1
                continue
            try:
                # Shopify tags are not guaranteed to be product features.
                draft = self.generator(product['title'], product['description_html'], [])
                slug = re.sub(r"[^a-z0-9]+", "-", draft['reviewer_name'].lower()).strip('-') or 'sample-reviewer'
                email = f"{slug[:48]}@{self.email_domain}"
                self.store.finish_draft(draft_id, draft, email, self.clock())
                result['generated'] += 1
            except Exception as exc:
                self.store.fail_generation(draft_id, type(exc).__name__, self.clock())
                result['failed'] += 1
        return result

    def approve(self, draft_id, *, reviewer_name, reviewer_email, is_authorized,
                authorization_reference, operator, rating, review_title, review_body):
        if is_authorized is not True:
            raise ValueError("必须确认客户授权")
        draft = self.store.get(draft_id)
        if not draft or draft['store'] != self.config.domain:
            raise ValueError("找不到当前店铺的草稿")
        self.store.approve(draft_id, reviewer_name, reviewer_email, authorization_reference, operator,
                           self.clock(), rating=rating, review_title=review_title, review_body=review_body)

    def submit_approved(self, client, *, draft_id=None):
        if client.shop_domain.lower() != self.config.domain.lower():
            raise ValueError("Judge.me 店铺与草稿店铺不一致")
        summary = {"submitted": 0, "failed": 0, "skipped": 0}
        ids = self.store.ids_for_submission(self.config.domain) if draft_id is None else [draft_id]
        for draft_id in ids:
            draft = self.store.claim_submission(draft_id, self.config.domain, self.clock())
            if draft is None:
                summary['skipped'] += 1
                continue
            try:
                gid = draft['product_id']
                prefix = 'gid://shopify/Product/'
                if not gid.startswith(prefix) or not gid[len(prefix):].isdigit():
                    raise ValueError("无效的 Shopify 商品 ID")
                result = client.create_review(id=gid[len(prefix):], name=draft['reviewer_name'],
                    email=draft['reviewer_email'], rating=draft['rating'], title=draft['review_title'], body=draft['review_body'])
                if result.status_code not in (200, 201):
                    self.store.submission_result(draft_id, 'failed', self.clock(), result=result, error="非预期的成功响应，需核实")
                    summary['failed'] += 1
                else:
                    self.store.submission_result(draft_id, 'submitted', self.clock(), result=result)
                    summary['submitted'] += 1
            except JudgeMeError as exc:
                self.store.submission_result(draft_id, 'failed', self.clock(), result=exc.result, error=type(exc).__name__)
                summary['failed'] += 1
            except ValueError as exc:
                self.store.submission_result(draft_id, 'failed', self.clock(), error=type(exc).__name__)
                summary['failed'] += 1
        return summary

    def confirm_synced(self, draft_id, *, operator, verification_reference):
        self.store.confirm_synced(draft_id, operator, verification_reference, self.clock())


def run_daily_workflow(config, email_domain, *, shopify_client=None, generator=generate_feedback):
    """Poll Shopify and generate today's drafts; never automatically submit."""
    sync_products(config, client=shopify_client)
    workflow = ReviewWorkflow(config, email_domain, generator=generator)
    try:
        return workflow.generate_for_day()
    finally:
        workflow.close()
