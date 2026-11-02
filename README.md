# Shopify 商品同步（独立 Python 模块）

使用 Shopify Admin GraphQL API 和 SQLite，首次全量获取 active 商品，之后按更新时间增量更新。商品 ID 为 Shopify GraphQL ID，描述保留 HTML，标签保存为数组。缓存按店铺域名和商品 ID 去重；非 active 商品会在增量同步时移除。只读取商品，不修改店铺。

## 安装和配置

在本目录执行（Python 3.10+）：

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
$env:SHOPIFY_STORE_DOMAIN = "your-store.myshopify.com"
$env:SHOPIFY_API_TOKEN = "你的 Admin API 访问令牌"
$env:SHOPIFY_API_VERSION = "2026-07"
```

令牌需要 `read_products` 权限。环境变量仅对当前终端生效；正式部署应通过运行环境的密钥管理配置，不要将令牌写入代码。可选配置：`SYNC_DB_PATH`（默认 `data/products.sqlite3`，相对于当前工作目录）、`SYNC_HOUR`（默认 3）、`SYNC_MINUTE`（默认 0）、`SYNC_TIMEZONE`（默认 `Asia/Shanghai`）。

## 使用

```powershell
# 首次自动全量；后续增量
.venv\Scripts\python -m product_sync sync
# 查看当前店铺的缓存 JSON
.venv\Scripts\python -m product_sync list
# 全量重建缓存，用于核对已删除商品
.venv\Scripts\python -m product_sync sync --full
# 每日定时；此命令需要持续运行，首次启动不会立即执行
.venv\Scripts\python -m product_sync schedule
# 测试
.venv\Scripts\python -m unittest discover -s tests -v
```

定时进程停止或电脑休眠时不会运行，也不会自动补跑；部署为系统服务并在启动时先执行一次 `sync` 可补跑。每次同步只在全部分页成功后提交缓存和进度；失败会回滚并在下次重试。限流、网络异常和服务端错误会有限次数重试。

增量查询包含最近 5 分钟的重叠窗口，可能再次读取这段时间的商品，但不会产生重复记录。索引延迟超过窗口、系统时钟不准，或者 Shopify 商品被删除时，增量结果可能不完整；定期执行 `sync --full` 可重新核对。此命令会重新拉取所有 active 商品。大量商品时分页受 Shopify 25,000 条分页上限限制，需要改用 Bulk Operations；该模块不适用于超过该上限的单次结果。
