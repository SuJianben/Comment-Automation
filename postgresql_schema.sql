-- Reference schema only: the Python implementation currently uses SQLite.
CREATE TABLE IF NOT EXISTS polled_products (
  store TEXT NOT NULL, product_id TEXT NOT NULL, polled_at TIMESTAMPTZ NOT NULL,
  title TEXT NOT NULL, description_html TEXT NOT NULL, tags_json JSONB NOT NULL,
  PRIMARY KEY(store, product_id, polled_at)
);
CREATE INDEX IF NOT EXISTS idx_polled_store_time ON polled_products(store, polled_at);
CREATE TABLE IF NOT EXISTS review_drafts (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  store TEXT NOT NULL, product_id TEXT NOT NULL, product_title TEXT NOT NULL,
  source_polled_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL, reviewer_name TEXT, reviewer_email TEXT,
  placeholder_email TEXT, is_authorized BOOLEAN NOT NULL DEFAULT FALSE, is_test BOOLEAN NOT NULL DEFAULT FALSE,
  authorization_reference TEXT, approved_by TEXT, approved_at TIMESTAMPTZ,
  rating INTEGER, review_title TEXT, review_body TEXT,
  status TEXT NOT NULL CHECK(status IN ('generating','pending_review','approved','sending','submitted','synced','failed')),
  response_payload JSONB, response_status INTEGER, last_error TEXT,
  submitted_at TIMESTAMPTZ, verified_at TIMESTAMPTZ, verified_by TEXT, verification_reference TEXT
);
CREATE INDEX IF NOT EXISTS idx_drafts_product_time ON review_drafts(store,product_id,created_at);
CREATE INDEX IF NOT EXISTS idx_drafts_status ON review_drafts(store,status,id);
-- PostgreSQL requires its own transactional reservation/locking implementation.
