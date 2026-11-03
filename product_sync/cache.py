import json
import sqlite3


class ProductCache:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.execute("PRAGMA busy_timeout = 30000")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS products (
          store TEXT NOT NULL, id TEXT NOT NULL, title TEXT NOT NULL,
          description_html TEXT NOT NULL, tags_json TEXT NOT NULL,
          updated_at TEXT NOT NULL, PRIMARY KEY (store, id)
        );
        CREATE TABLE IF NOT EXISTS polled_products (
          store TEXT NOT NULL, product_id TEXT NOT NULL, polled_at TEXT NOT NULL,
          title TEXT NOT NULL, description_html TEXT NOT NULL, tags_json TEXT NOT NULL,
          PRIMARY KEY (store, product_id, polled_at)
        );
        CREATE INDEX IF NOT EXISTS idx_polled_store_time ON polled_products(store, polled_at);
        CREATE TABLE IF NOT EXISTS sync_state (
          store TEXT PRIMARY KEY, watermark TEXT NOT NULL
        );
        """)

    def watermark(self, store):
        row = self.db.execute("SELECT watermark FROM sync_state WHERE store=?", (store,)).fetchone()
        return row[0] if row else None

    def apply(self, store, product):
        if product["status"] != "ACTIVE":
            self.db.execute("DELETE FROM products WHERE store=? AND id=?", (store, product["id"]))
        else:
            self.db.execute("""INSERT INTO products VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(store, id) DO UPDATE SET title=excluded.title,
                description_html=excluded.description_html, tags_json=excluded.tags_json,
                updated_at=excluded.updated_at""", (store, product["id"], product["title"],
                product["descriptionHtml"], json.dumps(product["tags"], ensure_ascii=False), product["updatedAt"]))

    def record_poll(self, store, product, polled_at):
        if product["status"] == "ACTIVE":
            self.db.execute("""INSERT OR REPLACE INTO polled_products
                (store, product_id, polled_at, title, description_html, tags_json)
                VALUES (?, ?, ?, ?, ?, ?)""", (store, product["id"], polled_at,
                product["title"], product["descriptionHtml"],
                json.dumps(product["tags"], ensure_ascii=False)))

    def list_products(self, store):
        rows = self.db.execute("SELECT id,title,description_html,tags_json,updated_at FROM products WHERE store=? ORDER BY id", (store,))
        return [{"id": row[0], "title": row[1], "description_html": row[2], "tags": json.loads(row[3]), "updated_at": row[4]} for row in rows]

    def search_products(self, store, search="", *, limit=20, offset=0):
        # Escape LIKE wildcards so user input is treated as literal text.
        term = search.strip().replace("!", "!!").replace("%", "!%").replace("_", "!_")
        pattern = f"%{term}%"
        where = "store=? AND (title LIKE ? ESCAPE '!' OR id LIKE ? ESCAPE '!' OR tags_json LIKE ? ESCAPE '!')"
        params = (store, pattern, pattern, pattern)
        total = self.db.execute(f"SELECT COUNT(*) FROM products WHERE {where}", params).fetchone()[0]
        rows = self.db.execute(f"SELECT id,title,description_html,tags_json,updated_at FROM products WHERE {where} ORDER BY title COLLATE NOCASE,id LIMIT ? OFFSET ?", (*params, limit, offset))
        return total, [{"id": row[0], "title": row[1], "description_html": row[2], "tags": json.loads(row[3]), "updated_at": row[4]} for row in rows]

    def close(self):
        self.db.close()
