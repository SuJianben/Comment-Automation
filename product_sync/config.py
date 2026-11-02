import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    domain: str
    token: str
    api_version: str
    db_path: Path
    hour: int
    minute: int
    timezone: str

    @classmethod
    def from_env(cls):
        domain = os.environ.get("SHOPIFY_STORE_DOMAIN", "").strip().lower()
        token = os.environ.get("SHOPIFY_API_TOKEN", "").strip()
        version = os.environ.get("SHOPIFY_API_VERSION", "2026-07").strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", domain):
            raise ValueError("SHOPIFY_STORE_DOMAIN 必须是店铺的 myshopify.com 域名")
        if not token:
            raise ValueError("缺少 SHOPIFY_API_TOKEN")
        if not re.fullmatch(r"20\d\d-(01|04|07|10)", version):
            raise ValueError("SHOPIFY_API_VERSION 格式错误")
        hour = int(os.environ.get("SYNC_HOUR", "3"))
        minute = int(os.environ.get("SYNC_MINUTE", "0"))
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("定时小时或分钟超出范围")
        return cls(domain, token, version, Path(os.environ.get("SYNC_DB_PATH", "data/products.sqlite3")), hour, minute, os.environ.get("SYNC_TIMEZONE", "Asia/Shanghai"))
