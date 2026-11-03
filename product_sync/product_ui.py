"""Shop product navigation and cached, paginated product display."""
import logging
from html.parser import HTMLParser
from math import ceil

import streamlit as st

from .cache import ProductCache
from .config import Config
from .sync import sync_products

log = logging.getLogger(__name__)
PAGE_SIZE = 20


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        elif tag in ("p", "br", "li", "div", "h1", "h2", "h3"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.hidden:
            self.hidden -= 1
        elif tag in ("p", "li", "div"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _description(html):
    parser = _TextExtractor()
    parser.feed(html or "")
    return "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())


def _refresh():
    try:
        with st.spinner("正在同步店铺商品…"):
            count = sync_products(Config.from_env())
        st.session_state['product_sync_notice'] = ("success", f"商品同步完成，本次读取 {count} 条变更。")
    except Exception:
        log.exception("商品同步失败")
        st.session_state['product_sync_notice'] = ("error", "商品同步失败，当前显示的是本地已有商品。请检查店铺连接后重试。")


def show_products(config, *, entered=False):
    st.header("店铺商品")
    if entered:
        _refresh()
    if st.button("刷新商品", key="refresh_products"):
        _refresh()
    notice = st.session_state.get('product_sync_notice')
    if notice:
        getattr(st, notice[0])(notice[1])
    search = st.text_input("搜索商品名称、标签或 ID", key="product_search")
    cache = ProductCache(config.db_path)
    try:
        count, _ = cache.search_products(config.domain, search, limit=1)
        pages = max(1, ceil(count / PAGE_SIZE))
        # A new search can shrink the result set.
        if st.session_state.get('product_page', 1) > pages:
            st.session_state['product_page'] = 1
        page = st.number_input("页码", min_value=1, max_value=pages, value=1, step=1, key="product_page")
        _, products = cache.search_products(config.domain, search, limit=PAGE_SIZE, offset=(page-1)*PAGE_SIZE)
    finally:
        cache.close()
    st.caption(f"共 {count} 件商品 · 第 {page} / {pages} 页")
    if not products:
        st.info("没有找到商品。")
    for product in products:
        with st.expander(product['title']):
            st.caption(f"商品 ID：{product['id']}")
            st.write("标签：", "、".join(product['tags']) if product['tags'] else "无")
            st.write("描述：")
            st.text(_description(product['description_html']) or "暂无描述")
