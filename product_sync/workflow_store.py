"""SQLite persistence for review drafts and state transitions."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone


def utc_now():
    return datetime.now(timezone.utc)


def timestamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


class WorkflowStore:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        from .cache import ProductCache
        cache = ProductCache(path)
        cache.close()
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS review_drafts (
          id INTEGER PRIMARY KEY AUTOINCREMENT, store TEXT NOT NULL, product_id TEXT NOT NULL,
          product_title TEXT NOT NULL, source_polled_at TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          reviewer_name TEXT, reviewer_email TEXT, placeholder_email TEXT,
          is_authorized INTEGER NOT NULL DEFAULT 0 CHECK(is_authorized IN (0,1)),
          authorization_reference TEXT, approved_by TEXT, approved_at TEXT,
          rating INTEGER, review_title TEXT, review_body TEXT,
          status TEXT NOT NULL CHECK(status IN ('generating','pending_review','approved','sending','submitted','synced','failed')),
          response_payload TEXT, response_status INTEGER, last_error TEXT,
          submitted_at TEXT, verified_at TEXT, verified_by TEXT, verification_reference TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_drafts_product_time ON review_drafts(store, product_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_drafts_status ON review_drafts(store, status, id);
        """)
        # Serialize migrations when multiple local UI sessions open the database.
        try:
            self.db.execute("BEGIN IMMEDIATE")
            columns = {row['name'] for row in self.db.execute("PRAGMA table_info(review_drafts)")}
            if 'is_test' not in columns:
                self.db.execute("ALTER TABLE review_drafts ADD COLUMN is_test INTEGER NOT NULL DEFAULT 0 CHECK(is_test IN (0,1))")
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def insert_test_drafts(self, store, now):
        """Insert local preview records, permanently excluded from review submission."""
        from uuid import uuid4
        stamp = timestamp(now)
        samples = [
            ("示例商品 · 随行水杯", 5, "A handy bottle", "The compact design looks easy to take along."),
            ("示例商品 · 棉质帆布袋", 4, "Simple everyday bag", "The simple style looks practical for daily use."),
        ]
        with self.db:
            for title, rating, review_title, body in samples:
                self.db.execute("""INSERT INTO review_drafts
                    (store, product_id, product_title, source_polled_at, created_at, updated_at,
                     reviewer_name, reviewer_email, placeholder_email, rating, review_title, review_body, status, is_test)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_review', 1)""",
                    (store, 'test://' + uuid4().hex, title, stamp, stamp, stamp, 'Sample reviewer',
                     'sample@example.invalid', 'sample@example.invalid', rating, review_title, body))
        return len(samples)

    def products_polled_between(self, store, start, end):
        return self.db.execute("""SELECT p.* FROM polled_products p
            JOIN products current ON current.store=p.store AND current.id=p.product_id
            WHERE p.store=? AND p.polled_at>=? AND p.polled_at<?
              AND p.polled_at=(SELECT MAX(q.polled_at) FROM polled_products q
                  WHERE q.store=p.store AND q.product_id=p.product_id AND q.polled_at>=? AND q.polled_at<?)
            ORDER BY p.polled_at,p.product_id""", (store, start, end, start, end)).fetchall()

    def reserve_draft(self, product, now):
        stamp = timestamp(now)
        since = timestamp(now - timedelta(hours=24))
        try:
            self.db.execute("BEGIN IMMEDIATE")
            found = self.db.execute("SELECT id FROM review_drafts WHERE store=? AND product_id=? AND created_at>? AND (status != 'failed' OR reviewer_name IS NOT NULL) LIMIT 1", (product['store'], product['product_id'], since)).fetchone()
            if found:
                self.db.rollback()
                return None
            cursor = self.db.execute("""INSERT INTO review_drafts
                (store,product_id,product_title,source_polled_at,created_at,updated_at,status)
                VALUES (?,?,?,?,?,?,'generating')""", (product['store'], product['product_id'], product['title'], product['polled_at'], stamp, stamp))
            self.db.commit()
            return cursor.lastrowid
        except Exception:
            self.db.rollback()
            raise

    def finish_draft(self, draft_id, draft, email, now):
        with self.db:
            cursor = self.db.execute("""UPDATE review_drafts SET reviewer_name=?, reviewer_email=?, placeholder_email=?,
                rating=?, review_title=?, review_body=?, status='pending_review',updated_at=?
                WHERE id=? AND status='generating'""", (draft['reviewer_name'],email,email,draft['rating'],draft['review_title'],draft['review_body'],timestamp(now),draft_id))
            if cursor.rowcount != 1:
                raise ValueError("草稿不处于生成状态")

    def fail_generation(self, draft_id, message, now):
        with self.db:
            self.db.execute("UPDATE review_drafts SET status='failed',last_error=?,updated_at=? WHERE id=? AND status='generating'", (message,timestamp(now),draft_id))

    def approve(self, draft_id, name, email, authorization_reference, operator, now, *, rating, review_title, review_body):
        if not all(isinstance(x,str) and x.strip() for x in (name,email,authorization_reference,operator)) or '@' not in email:
            raise ValueError("需要真实客户姓名、邮箱、授权记录和审核人")
        if type(rating) is not int or rating not in range(1, 6) or not all(isinstance(x, str) and x.strip() for x in (review_title, review_body)):
            raise ValueError("需要客户确认的评分、标题和正文")
        if name.strip().casefold() == 'sample reviewer':
            raise ValueError("请替换示例姓名")
        with self.db:
            cursor = self.db.execute("""UPDATE review_drafts SET reviewer_name=?,reviewer_email=?,is_authorized=1,
              authorization_reference=?,approved_by=?,approved_at=?,updated_at=?,rating=?,review_title=?,review_body=?,status='approved'
              WHERE id=? AND status='pending_review' AND is_test=0 AND lower(?) != lower(placeholder_email)""",
              (name.strip(),email.strip(),authorization_reference.strip(),operator.strip(),timestamp(now),timestamp(now),rating,review_title.strip(),review_body.strip(),draft_id,email.strip()))
            if cursor.rowcount != 1:
                raise ValueError("草稿状态无效或邮箱仍为占位邮箱")

    def ids_for_submission(self, store):
        return [row['id'] for row in self.db.execute("SELECT id FROM review_drafts WHERE store=? AND status='approved' AND is_authorized=1 AND is_test=0 ORDER BY id", (store,))]

    def claim_submission(self, draft_id, store, now):
        with self.db:
            cursor = self.db.execute("UPDATE review_drafts SET status='sending',updated_at=? WHERE id=? AND store=? AND status='approved' AND is_authorized=1 AND is_test=0", (timestamp(now),draft_id,store))
            if cursor.rowcount != 1:
                return None
            return dict(self.db.execute("SELECT * FROM review_drafts WHERE id=?", (draft_id,)).fetchone())

    def submission_result(self, draft_id, status, now, result=None, error=None):
        if status not in ('submitted','failed'):
            raise ValueError("无效提交状态")
        payload = json.dumps(result.data, ensure_ascii=False, default=str) if result is not None else None
        with self.db:
            cursor = self.db.execute("""UPDATE review_drafts SET status=?,updated_at=?,response_status=?,response_payload=?,last_error=?,submitted_at=?
                WHERE id=? AND status='sending'""", (status,timestamp(now),result.status_code if result else None,payload,error,timestamp(now) if status=='submitted' else None,draft_id))
            if cursor.rowcount != 1:
                raise ValueError("草稿不处于发送状态")

    def confirm_synced(self, draft_id, operator, verification_reference, now):
        if not all(isinstance(x,str) and x.strip() for x in (operator,verification_reference)):
            raise ValueError("需要核实人和实际创建的核验依据")
        with self.db:
            cursor = self.db.execute("""UPDATE review_drafts SET status='synced',updated_at=?,verified_at=?,verified_by=?,verification_reference=?
                WHERE id=? AND status='submitted'""", (timestamp(now),timestamp(now),operator.strip(),verification_reference.strip(),draft_id))
            if cursor.rowcount != 1:
                raise ValueError("只能确认已提交的草稿")

    def list_by_status(self, store, statuses):
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        rows = self.db.execute(
            f"SELECT * FROM review_drafts WHERE store=? AND status IN ({placeholders}) ORDER BY id DESC",
            (store, *statuses),
        )
        return [dict(row) for row in rows]

    def get(self, draft_id):
        row = self.db.execute("SELECT * FROM review_drafts WHERE id=?", (draft_id,)).fetchone()
        return dict(row) if row else None

    def close(self):
        self.db.close()
