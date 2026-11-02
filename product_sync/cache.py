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

    def list_products(self, store):
        rows = self.db.execute("SELECT id,title,description_html,tags_json,updated_at FROM products WHERE store=? ORDER BY id", (store,))
        return [{"id": row[0], "title": row[1], "description_html": row[2], "tags": json.loads(row[3]), "updated_at": row[4]} for row in rows]

    def close(self):
        self.db.close()
