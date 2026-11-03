"""Durable daily submission ledger and request timestamps for the worker."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .workflow_store import timestamp


class WorkerStore:
    def __init__(self, workflow_store):
        self.store = workflow_store
        self.db = workflow_store.db
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS auto_worker_submissions (
          draft_id INTEGER PRIMARY KEY, store TEXT NOT NULL, local_day TEXT NOT NULL, claimed_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_worker_daily ON auto_worker_submissions(store, local_day);
        CREATE TABLE IF NOT EXISTS auto_worker_requests (
          store TEXT PRIMARY KEY, last_attempt_at TEXT NOT NULL
        );
        """)

    def _day_bounds(self, now, timezone_name):
        zone = ZoneInfo(timezone_name)
        day = now.astimezone(zone).date()
        return day.isoformat(), timestamp(datetime.combine(day, time.min, tzinfo=zone)), timestamp(datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone))

    def used_today(self, store, now, timezone_name):
        day, start, end = self._day_bounds(now, timezone_name)
        return self.db.execute("""SELECT COUNT(*) FROM (
            SELECT id FROM review_drafts WHERE store=? AND is_test=0 AND created_at>=? AND created_at<?
            UNION SELECT draft_id AS id FROM auto_worker_submissions WHERE store=? AND local_day=?
        )""", (store, start, end, store, day)).fetchone()[0]

    def generated_today(self, store, now, timezone_name):
        _, start, end = self._day_bounds(now, timezone_name)
        return self.db.execute("SELECT COUNT(*) FROM review_drafts WHERE store=? AND is_test=0 AND created_at>=? AND created_at<?", (store, start, end)).fetchone()[0]

    def claim_next(self, store, now, timezone_name, limit):
        day, start, end = self._day_bounds(now, timezone_name)
        try:
            self.db.execute("BEGIN IMMEDIATE")
            used = self.used_today(store, now, timezone_name)
            submitted = self.db.execute("SELECT COUNT(*) FROM auto_worker_submissions WHERE store=? AND local_day=?", (store,day)).fetchone()[0]
            if submitted >= limit:
                self.db.rollback()
                return None
            row = self.db.execute("""SELECT * FROM review_drafts WHERE store=? AND status='approved'
                AND is_authorized=1 AND is_test=0 AND length(trim(coalesce(authorization_reference,'')))>0
                AND length(trim(coalesce(approved_by,'')))>0 AND approved_at IS NOT NULL
                AND length(trim(coalesce(reviewer_name,'')))>0
                AND lower(trim(reviewer_name))!='sample reviewer' AND instr(trim(coalesce(reviewer_email,'')), '@')>1
                AND lower(trim(reviewer_email))!=lower(trim(coalesce(placeholder_email,'')))
                AND NOT EXISTS(SELECT 1 FROM auto_worker_submissions a WHERE a.draft_id=review_drafts.id)
                AND (? OR (created_at>=? AND created_at<?))
                ORDER BY id LIMIT 1""", (store, used < limit, start, end)).fetchone()
            if row is None:
                self.db.rollback()
                return None
            updated = self.db.execute("UPDATE review_drafts SET status='sending',updated_at=? WHERE id=? AND status='approved'", (timestamp(now),row['id']))
            if updated.rowcount != 1:
                self.db.rollback()
                return None
            self.db.execute("INSERT INTO auto_worker_submissions VALUES (?,?,?,?)", (row['id'],store,day,timestamp(now)))
            self.db.commit()
            result = dict(row)
            result['worker_local_day'] = day
            return result
        except Exception:
            self.db.rollback()
            raise

    def last_request(self, store):
        row = self.db.execute("SELECT last_attempt_at FROM auto_worker_requests WHERE store=?", (store,)).fetchone()
        return datetime.fromisoformat(row[0]) if row else None

    def record_request(self, store, now):
        with self.db:
            self.db.execute("""INSERT INTO auto_worker_requests VALUES (?,?)
                ON CONFLICT(store) DO UPDATE SET last_attempt_at=excluded.last_attempt_at""", (store,timestamp(now)))
