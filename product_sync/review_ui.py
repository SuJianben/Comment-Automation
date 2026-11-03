"""Local Streamlit review queue; all state changes use ReviewWorkflow."""
import os
import re
from pathlib import Path
from types import SimpleNamespace

import streamlit as st

from .config import Config, DEFAULT_SHOPIFY_STORE_DOMAIN
from .judgeme import JudgeMeClient
from .product_ui import show_products
from .sidebar_style import apply_sidebar_style, render_store_identity
from .workflow import ReviewWorkflow, run_daily_workflow
from .workflow_store import utc_now


STATUS = {"approved": "已审核 · 待发送", "submitted": "已提交 · 待核实", "synced": "已核实生效", "failed": "失败 · 需核查", "sending": "发送中 · 需核查"}


def _config():
    domain = os.getenv("SHOPIFY_STORE_DOMAIN", DEFAULT_SHOPIFY_STORE_DOMAIN).strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", domain):
        raise ValueError("请设置 SHOPIFY_STORE_DOMAIN，例如 example.myshopify.com。")
    placeholder_domain = os.getenv("DEFAULT_EMAIL_DOMAIN", "").strip()
    if not placeholder_domain:
        raise ValueError("请设置 DEFAULT_EMAIL_DOMAIN，与生成草稿时的设置一致。")
    config = SimpleNamespace(domain=domain, db_path=Path(os.getenv("SYNC_DB_PATH", "data/products.sqlite3")),
                             timezone=os.getenv("SYNC_TIMEZONE", "Asia/Shanghai"))
    return config, placeholder_domain


def _client(domain):
    token = os.getenv("JUDGEME_PRIVATE_API_TOKEN", "").strip()
    if not token:
        raise ValueError("请先设置 JUDGEME_PRIVATE_API_TOKEN。")
    return JudgeMeClient(domain, token)


def _send(flow, draft_id, client):
    result = flow.submit_approved(client, draft_id=draft_id)
    if result["submitted"]:
        return "已提交 Judge.me，等待核实实际创建结果。"
    if result["failed"]:
        return "提交失败，请查看历史记录；结果不明时请先核实，避免重复提交。"
    return "该草稿已被其他操作处理，请刷新页面。"


def _notice(message):
    st.session_state["review_notice"] = message
    st.rerun()


def _draft_form(flow, draft):
    draft_id = draft['id']
    st.subheader(draft['product_title'])
    if draft['is_test']:
        st.info("模拟测试数据，仅供预览，不会提交到 Judge.me。")
    st.caption(f"草稿 #{draft_id} · 商品 {draft['product_id']} · 创建时间 {draft['created_at']}")
    feedback = st.empty()
    with st.form(f"draft-{draft_id}"):
        left, right = st.columns(2)
        with left:
            name = st.text_input("客户姓名 / 昵称", value=draft['reviewer_name'] or "")
            email = st.text_input("真实客户邮箱", value="", placeholder="请填写真实邮箱")
            operator = st.text_input("审核人")
        with right:
            rating = st.selectbox("星级", options=[1, 2, 3, 4, 5], index=max(0, min(4, (draft['rating'] or 5) - 1)))
            authorization_reference = st.text_input("客户授权记录", placeholder="例如客户确认记录编号")
        title = st.text_input("评价标题", value=draft['review_title'] or "")
        body = st.text_area("评价正文", value=draft['review_body'] or "", height=150)
        authorized = st.checkbox("我已核实客户身份、内容和授权，允许提交这条评价")
        submitted = st.form_submit_button("确认并提交", type="primary", disabled=bool(draft['is_test']))
    if submitted:
        try:
            client = _client(flow.config.domain)
            flow.approve(draft_id, reviewer_name=name, reviewer_email=email, is_authorized=authorized,
                         authorization_reference=authorization_reference, operator=operator, rating=rating,
                         review_title=title, review_body=body)
            message = _send(flow, draft_id, client)
        except ValueError as exc:
            feedback.error(f"未提交：{exc}。请检查这条草稿的填写内容和授权确认。")
            st.toast("这条草稿未提交，请查看卡片顶部的提示。", icon="⚠️")
        else:
            _notice(message)
    st.divider()


def _approved_item(flow, draft):
    st.markdown(f"**{draft['product_title']}** · 草稿 #{draft['id']}")
    st.caption("该记录已审核，但尚未发送。")
    if st.button("提交已审核记录", key=f"send-{draft['id']}"):
        try:
            client = _client(flow.config.domain)
            message = _send(flow, draft['id'], client)
        except ValueError as exc:
            st.error(str(exc))
        else:
            _notice(message)


def _history_item(flow, item):
    label = STATUS[item['status']]
    with st.expander(f"{item['product_title']} · #{item['id']} · {label}"):
        st.write(f"星级：{item['rating'] if item['rating'] is not None else '—'}")
        st.write("标题：", item['review_title'] or "—")
        st.write("正文：", item['review_body'] or "—")
        st.write("最后更新：", item['updated_at'])
        if item['status'] == 'sending':
            st.warning("发送结果未知，请先在 Judge.me 核实，避免重复提交。")
        if item['last_error']:
            st.write("错误：", item['last_error'])
        if item['response_status'] is not None:
            st.write("HTTP 状态：", item['response_status'])
        if item['response_payload'] is not None:
            st.text_area("API 响应", value=item['response_payload'], height=120, disabled=True, key=f"response-{item['id']}")
        if item['status'] == 'submitted':
            st.caption("HTTP 200 或 201 仅表示请求已接收；请在 Judge.me 核实实际创建情况。")
            with st.form(f"verify-{item['id']}"):
                operator = st.text_input("核实人")
                reference = st.text_input("实际创建的核实记录")
                confirm = st.form_submit_button("标记已核实生效")
            if confirm:
                try:
                    # Enforce shop separation on UI action.
                    if item['store'] != flow.config.domain:
                        raise ValueError("记录不属于当前店铺")
                    flow.confirm_synced(item['id'], operator=operator, verification_reference=reference)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    _notice("已记录核实结果。")


def _sidebar(flow, placeholder_domain):
    with st.sidebar:
        st.subheader("配置检查")
        st.caption("当前生效配置")
        for label, key in (("Shopify API Token", "SHOPIFY_API_TOKEN"),
                           ("LLM API Key", "LLM_API_KEY"),
                           ("LLM 模型", "LLM_MODEL"),
                           ("Judge.me Token", "JUDGEME_PRIVATE_API_TOKEN")):
            state = "已配置" if os.getenv(key, "").strip() else "未配置"
            st.write(f"{label}：{state}")
        st.divider()
        st.subheader("操作")
        if st.button("从 Shopify 拉取并生成新草稿", use_container_width=True):
            if not os.getenv('LLM_API_KEY', '').strip() or not os.getenv('LLM_MODEL', '').strip():
                st.error("请先配置 LLM_API_KEY 和 LLM_MODEL，再生成草稿。")
            else:
                try:
                    config = Config.from_env()
                    with st.spinner("正在拉取商品并生成草稿，请稍候…"):
                        summary = run_daily_workflow(config, placeholder_domain)
                except Exception:
                    st.error("执行失败，请检查 Shopify 和模型服务配置后重试。")
                else:
                    _notice(f"生成完成：新增 {summary['generated']} 条，跳过 {summary['skipped']} 条，失败 {summary['failed']} 条。")
        if st.button("插入 2 条模拟测试数据", use_container_width=True):
            count = flow.store.insert_test_drafts(flow.config.domain, utc_now())
            _notice(f"已插入 {count} 条模拟测试数据。")
        if st.button("刷新页面", use_container_width=True):
            st.rerun()


def run():
    st.set_page_config(page_title="评价审核", page_icon="✳", layout="wide")
    apply_sidebar_style()
    st.title("评价审核")
    try:
        config, placeholder_domain = _config()
        flow = ReviewWorkflow(config, placeholder_domain)
    except ValueError as exc:
        st.error(str(exc))
        st.stop()
    try:
        render_store_identity(config.domain)
        page = st.sidebar.radio("导航", ["评价审核", "店铺商品"], key="navigation")
        previous_page = st.session_state.get("previous_navigation")
        st.session_state["previous_navigation"] = page
        _sidebar(flow, placeholder_domain)
        if page == "店铺商品":
            show_products(config, entered=previous_page != page)
            return
        st.caption(f"当前店铺：{config.domain}")
        message = st.session_state.pop("review_notice", None)
        if message:
            st.info(message)
        pending_tab, history_tab = st.tabs(["待审核", "历史记录"])
        with pending_tab:
            pending = flow.store.list_by_status(config.domain, ['pending_review'])
            approved = flow.store.list_by_status(config.domain, ['approved'])
            st.write(f"待审核 {len(pending)} 条 · 已审核待发送 {len(approved)} 条")
            if not pending and not approved:
                st.info("暂无待处理评价。")
            for draft in pending:
                _draft_form(flow, draft)
            for draft in approved:
                _approved_item(flow, draft)
        with history_tab:
            history = flow.store.list_by_status(config.domain, ['submitted', 'synced', 'failed', 'sending'])
            if not history:
                st.info("暂无提交记录。")
            for item in history:
                _history_item(flow, item)
    finally:
        flow.close()
