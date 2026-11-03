"""Load the local sidebar-only presentation styles."""
from html import escape
from pathlib import Path

import streamlit as st


def apply_sidebar_style():
    css = (Path(__file__).parent / "styles" / "sidebar.css").read_text(encoding="utf-8")
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def render_store_identity(domain):
    """Show the currently configured store without implying switching is available."""
    safe_domain = escape(domain, quote=True)
    st.sidebar.markdown(
        f'<div class="store-identity"><span class="store-identity__mark" aria-hidden="true">S</span>'
        f'<div class="store-identity__body"><span class="store-identity__label">当前店铺</span>'
        f'<span class="store-identity__domain">{safe_domain}</span></div></div>',
        unsafe_allow_html=True,
    )
