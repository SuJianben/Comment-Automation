# Shopify 商品同步（独立 Python 模块）

使用 Shopify Admin GraphQL API 和 SQLite，首次全量获取 active 商品，之后按更新时间增量更新。商品 ID 为 Shopify GraphQL ID，描述保留 HTML，标签保存为数组。缓存按店铺域名和商品 ID 去重；非 active 商品会在增量同步时移除。只读取商品，不修改店铺。

## 安装和配置

在本目录执行（Python 3.10+）：

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
$env:SHOPIFY_STORE_DOMAIN = "rvfiyy-r2.myshopify.com"  # 默认已配置；仅切换店铺时需设置
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

## 生成客户体验反馈草稿

草稿由模型生成，使用固定的 `Sample reviewer` 标识，不能作为真实客户评价发布；须由真实客户核实并授权后才能使用其真实体验。商品资料会发送至配置的外部模型服务。接口需要支持 OpenAI 兼容的 `/chat/completions` 和 `json_object` 输出模式。

```powershell
$env:LLM_API_KEY = "你的模型服务 API 密钥"
$env:LLM_BASE_URL = "https://api.openai.com/v1"
$env:LLM_MODEL = "你的模型名称"
```

```python
from product_sync.feedback import generate_feedback

draft = generate_feedback(
    product_title="Insulated Water Bottle",
    description="A stainless steel bottle with a leak-resistant lid.",
    features=["Stainless steel", "Leak-resistant lid"],
)
print(draft)  # 字典包含 reviewer_name、rating、review_title、review_body
```

函数不存储或发布结果。网络错误、限流、服务端错误、响应格式错误和无效 JSON 会重试；其他 HTTP 错误直接抛出。重试可能产生多次外部 API 调用和费用。所有尝试失败会抛出 `FeedbackGenerationError`。

## 同步真实客户评论到 Judge.me

仅传入真实客户提供并已授权使用的评论，不要把模型草稿冒充客户评论提交。根据 [Judge.me 官方文档](https://judge.me/api/docs)，创建请求在后台处理，HTTP 2xx 不代表评论已创建；商店若关闭网页评论，该接口不会创建评论。API 创建的评论不能删除或标记为 verified。

```python
import os
from product_sync.judgeme import JudgeMeClient, JudgeMeError

client = JudgeMeClient(
    shop_domain=os.environ["JUDGEME_SHOP_DOMAIN"],
    api_token=os.environ["JUDGEME_PRIVATE_API_TOKEN"],
)
try:
    result = client.create_review(
        id=123456789,  # Shopify 数字商品 ID，而非 gid://...
        name="真实客户姓名", email="真实客户邮箱", rating=5,
        title="客户提供的标题", body="客户提供的评论正文",
    )
    print(result.status_code, result.data)  # 请求响应，不等于评论已创建
except JudgeMeError as exc:
    print(type(exc).__name__)
    # 根据 exc.result（可能为 None）检查状态和响应；超时后先核实再提交。
```

`api_token` 通过 `X-Api-Token` 请求头发送，不放入 URL；创建端点官方说明无需鉴权，因此不能用成功响应证明密钥有效。`JudgeMeBadRequest` 对应 400/422，`JudgeMeAuthenticationError` 对应 401/403，`JudgeMeRateLimitError` 对应 429，其 `result.retry_after` 保存服务端返回的等待时间。其他 HTTP 错误为 `JudgeMeAPIError`；网络异常、408 或 5xx 为 `JudgeMeOutcomeUnknown`，应先核实评论是否已创建。客户端不会自动重试 POST，避免重复评论。

## 每日轮询与审核工作流

SQLite 自动创建 `polled_products` 和 `review_drafts` 表。运行下列命令会轮询 Shopify 并为当天轮询到、仍为 active 的商品生成草稿；任务不会自动提交评论：

```powershell
$env:DEFAULT_EMAIL_DOMAIN = "example.invalid"
# 同时配置前述 Shopify 和 LLM 环境变量
.venv\Scripts\python -m product_sync workflow-run
.venv\Scripts\python -m product_sync workflow-schedule
```

定时进程需要持续运行，使用 `SYNC_TIMEZONE`、`SYNC_HOUR` 和 `SYNC_MINUTE`。不要同时运行原有 `schedule` 与 `workflow-schedule`，以免重复轮询。每日范围使用配置时区的自然日，同一店铺商品在滚动 24 小时内只生成一条；商品标签不一定是商品特征，当前没有可靠特征字段，故传空列表。轮询快照只从更新后的同步开始记录，不会自动补录历史。

后台审核接口调用示例（需要由你的后台连接真实审核界面）：

```python
from product_sync.config import Config
from product_sync.workflow import ReviewWorkflow
from product_sync.judgeme import JudgeMeClient
import os

config = Config.from_env()
flow = ReviewWorkflow(config, os.environ["DEFAULT_EMAIL_DOMAIN"])
try:
    flow.approve(
        1, reviewer_name="真实客户姓名", reviewer_email="客户真实邮箱",
        is_authorized=True, authorization_reference="客户授权记录编号",
        operator="审核人", rating=5, review_title="客户确认的标题",
        review_body="客户确认的真实体验",
    )
    client = JudgeMeClient(config.domain, os.environ["JUDGEME_PRIVATE_API_TOKEN"])
    print(flow.submit_approved(client))
    # 人工核实 Judge.me 已创建后，记录可追溯的核实依据：
    flow.confirm_synced(1, operator="核实人", verification_reference="Judge.me 评论编号或核实记录")
finally:
    flow.close()
```

待审核草稿的邮箱是占位值，审核时必须替换并记录客户授权依据。状态依次为 `pending_review`、`approved`、`submitted` 和 `synced`；请求失败为 `failed`。内部状态 `generating` 和 `sending` 用于并发防重。若进程在生成或发送时中断，管理员需要人工核实相应记录；特别是发送中断或网络故障时不能直接重新提交。`submitted` 只代表收到 HTTP 200 或 201，`confirm_synced` 依赖操作人员提供的实际创建核实记录，程序不会自动验证该证据。

`postgresql_schema.sql` 仅提供 PostgreSQL 表结构参考；控制器和并发锁目前只支持 SQLite，未测试 PostgreSQL。

## 本机评价管理页面

在项目目录安装依赖，并在同一个 PowerShell 窗口设置环境变量：

```powershell
.venv\Scripts\python -m pip install -r requirements.txt
$env:SHOPIFY_STORE_DOMAIN = "rvfiyy-r2.myshopify.com"  # 默认已配置；仅切换店铺时需设置
$env:DEFAULT_EMAIL_DOMAIN = "example.invalid"
$env:JUDGEME_PRIVATE_API_TOKEN = "你的 Judge.me API token"
# 若生成草稿时使用自定义数据库，设置完全相同的路径：
# $env:SYNC_DB_PATH = "data/products.sqlite3"
.venv\Scripts\python -m streamlit run app_ui.py --server.address 127.0.0.1
```

浏览器访问终端显示的本机地址。该页面没有登录系统，且显示客户信息和 API 响应；仅供本机使用，不能直接对公网或局域网开放。它不会自动轮询或生成草稿，需先运行上面的工作流命令。

在待审核页修改内容，填写客户真实姓名、邮箱、审核人及授权记录，勾选已核实后点击“确认并提交”。每次只提交当前记录；若记录已审核但未发送，可从待审核页继续发送。历史页区分已提交、已生效、失败和发送中，并可记录实际创建的核实依据。HTTP 200 或 201 并不代表已创建；结果不明时请先到 Judge.me 核实，避免重复提交。

项目默认店铺为 `rvfiyy-r2.myshopify.com`。如设置 `SHOPIFY_STORE_DOMAIN`，环境变量中的店铺会覆盖默认值；店铺域名不包含访问令牌。

本机可将 `SHOPIFY_API_TOKEN`、`JUDGEME_PRIVATE_API_TOKEN` 和 `DEFAULT_EMAIL_DOMAIN` 放在项目根目录的 `.env`；该文件已排除在 Git 之外。运行环境已有的同名变量优先。请勿分享 `.env` 文件。

### 页面侧边栏操作

侧边栏显示当前生效的店铺、Shopify、LLM 和 Judge.me 配置状态，不显示令牌。修改 `.env` 后需重启页面服务。点击“从 Shopify 拉取并生成新草稿”会运行原有每日工作流，先要求配置 `LLM_API_KEY` 和 `LLM_MODEL`；它不会自动提交评论。可点击“插入 2 条模拟测试数据”预览卡片；这些数据保存于本地 SQLite，标记为测试数据，不允许审核或提交。再次点击会新增两条。

### 查看店铺商品

在左侧“导航”选择“店铺商品”。每次从其他页面进入时会自动同步 Shopify 商品，也可以点击“刷新商品”；搜索和翻页只使用本地缓存，不会调用模型。可按商品名称、标签或 ID 搜索，每页显示 20 件，展开查看描述与标签。同步失败时保留并展示已有缓存。

增量同步无法感知已删除商品；如需核对删除情况，使用前述 `sync --full` 重新同步。

## 后台自动任务

`auto_worker.py` 定时同步商品，按当天已生成草稿数量控制模型调用，并只处理已人工核实客户身份、评价内容和授权的 `approved` 草稿；不会自动填写授权或把新生成草稿直接发布。默认每天 `SYNC_TIMEZONE` 时区的 `SYNC_HOUR:SYNC_MINUTE`（默认北京时间 03:00），首次启动不会立即执行。

```powershell
cd "D:\CODEX项目\评论agent\shopify_product_sync"
# 常驻运行（关闭终端或电脑休眠会停止执行）
.venv\Scripts\python auto_worker.py
# 立即运行一次并退出；可能向 Judge.me 提交已授权的 approved 评论
.venv\Scripts\python auto_worker.py --once
# 立即运行一次，然后按计划常驻
.venv\Scripts\python auto_worker.py --run-now
```

可在 `.env` 设置 `WORKER_DAILY_LIMIT=2`（只接受 1 或 2）和 `WORKER_INTERVAL_SECONDS=60`（至少 60）。后台任务按店铺和配置时区的自然日，合计最多处理该数量的不同草稿：优先提交已授权的评价，再用剩余额度生成新草稿；当天在页面中已生成的真实草稿也计入额度。同一条当天生成并提交的草稿只计一次；此额度只约束本后台任务，页面手动提交和其他任务不会受此额度或发送间隔限制。不要同时运行多个生成调度器。

只对 Judge.me 明确返回的 429 最多尝试三次，遵守 `Retry-After` 且每次等待至少60秒；如果建议等待超过一小时，停止自动重试。提交时遇到网络超时、5xx 或进程中断，结果可能未知：不会自动重发，应先在 Judge.me 核实。成功响应进入 `submitted`，仍须核实实际创建后才标记 `synced`。

后台任务使用 SQLite 记录当天领取的提交及最近一次请求时间，并通过本机锁防止同一数据库的后台任务并发；不支持跨机器共享锁。日志写入项目目录 `auto_worker.log` 并自动轮转，日志和备份已被 Git 忽略。常驻进程需电脑保持运行，未设置为系统服务或开机自动启动；遗漏的计划不会自动补跑。
