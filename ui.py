# -*- coding: utf-8 -*-
"""Thème à fort contraste et composants d'interface réutilisables."""
from __future__ import annotations

import html
import re

import pandas as pd
import streamlit as st

from . import config, services as sv

TONES = {"ok": "#17794F", "warn": "#E85D04", "danger": "#C1121F", "info": "#0B6EBD", "neutral": "#0B1F33"}
STATUT_COLORS = {"Rupture": "#C1121F", "Sous le seuil": "#E85D04", "À commander": "#F2B705", "OK": "#17794F"}
STATUT_ORDRE = list(STATUT_COLORS)
CONTROLE_TONES = {"Conforme": "ok", "Conforme avec réserves": "warn", "Non conforme": "danger", "À contrôler": "info"}
SEQ = ["#0B3C5D", "#E85D04", "#17794F", "#C1121F", "#6A4C93", "#F2B705", "#00A6A6", "#8D99AE"]

CSS = """
<style>
:root{--ink:#0B1F33;--ink2:#3A4A5C;--bg:#EEF2F6;--card:#FFFFFF;--accent:#C84B00;}
.stApp{background:var(--bg);color:var(--ink);}
.block-container{padding-top:1.2rem;max-width:1400px;}
h1,h2,h3,h4{color:var(--ink)!important;font-weight:800!important;}
[data-testid="stSidebar"]{background:#0B1F33;}
[data-testid="stSidebar"] *{color:#F8FAFC;}
[data-testid="stSidebar"] .stRadio label{padding:7px 10px;border-radius:8px;font-weight:600;}
[data-testid="stSidebar"] .stRadio label:hover{background:#16406B;}
[data-testid="stSidebar"] .stButton>button{background:#F2B705;color:#0B1F33!important;font-weight:800;border:0;}
.stButton>button,.stFormSubmitButton>button{background:var(--accent);color:#fff;font-weight:700;border:0;border-radius:8px;}
.stButton>button:hover,.stFormSubmitButton>button:hover{background:#9E3A00;color:#fff;}
.stDownloadButton>button{background:#0B1F33;color:#fff;font-weight:700;border:0;border-radius:8px;}
.stTabs [data-baseweb="tab"]{font-weight:700;color:var(--ink);}
.stTabs [aria-selected="true"]{border-bottom:4px solid var(--accent)!important;}
[data-testid="stExpander"]{background:var(--card);border-radius:10px;border:1px solid #C9D3DE;}
.ph{background:linear-gradient(90deg,#0B1F33,#16406B);border-radius:12px;padding:16px 22px;margin-bottom:14px;
    border-bottom:5px solid #F2B705;}
.ph .pt{color:#fff;font-size:1.6rem;font-weight:800;line-height:1.2;}
.ph .ps{color:#D6E4F2;font-size:.95rem;margin-top:2px;}
.kpi{background:var(--card);border-radius:10px;padding:12px 14px;border-left:9px solid var(--tone,#0B1F33);
     box-shadow:0 1px 4px rgba(11,31,51,.18);height:100%;}
.kpi .l{font-size:.74rem;text-transform:uppercase;letter-spacing:.06em;color:var(--ink2);font-weight:800;}
.kpi .v{font-size:1.85rem;font-weight:800;color:var(--ink);line-height:1.15;}
.kpi .s{font-size:.82rem;color:var(--ink2);}
.al{border-radius:8px;padding:9px 14px;margin:5px 0;font-weight:600;border-left:8px solid;}
.al.danger{background:#FDE8E8;color:#6B0F1A;border-color:#C1121F;}
.al.warn{background:#FFF1E0;color:#7A3300;border-color:#E85D04;}
.al.info{background:#E3F0FB;color:#08365E;border-color:#0B6EBD;}
.al.ok{background:#E2F5EC;color:#0C4A30;border-color:#17794F;}
.badge{display:inline-block;padding:2px 10px;border-radius:999px;font-weight:800;font-size:.8rem;color:#fff;}
.badge.ok{background:#17794F;}.badge.warn{background:#B54200;}.badge.danger{background:#C1121F;}
.badge.info{background:#0B6EBD;}.badge.neutral{background:#0B1F33;}
.hero{background:linear-gradient(160deg,#0B1F33 0%,#16406B 100%);color:#fff;border-radius:14px;padding:34px 30px;
      border-bottom:6px solid #F2B705;}
.hero h1{color:#fff!important;font-size:2.1rem;margin:0 0 6px 0;}
.hero p{color:#D6E4F2;font-size:1rem;margin:4px 0;}
.hero .pill{display:inline-block;background:#F2B705;color:#0B1F33;font-weight:800;border-radius:999px;
            padding:2px 12px;margin:6px 6px 0 0;font-size:.82rem;}
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


# ----------------------------------------------------------------------------
# Composants HTML
# ----------------------------------------------------------------------------
def _md_bold(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(text))


def header(title: str, subtitle: str = "") -> None:
    st.markdown(f'<div class="ph"><div class="pt">{html.escape(title)}</div>'
                f'<div class="ps">{html.escape(subtitle)}</div></div>', unsafe_allow_html=True)


def kpi_html(label: str, value, sub: str = "", tone: str = "neutral") -> str:
    return (f'<div class="kpi" style="--tone:{TONES.get(tone, TONES["neutral"])}">'
            f'<div class="l">{html.escape(str(label))}</div><div class="v">{html.escape(str(value))}</div>'
            f'<div class="s">{html.escape(str(sub))}</div></div>')


def kpi_row(items: list[tuple]) -> None:
    cols = st.columns(len(items))
    for c, it in zip(cols, items):
        with c:
            st.markdown(kpi_html(*it), unsafe_allow_html=True)


def alert_html(level: str, text: str) -> str:
    return f'<div class="al {level}">{_md_bold(text)}</div>'


def alert(level: str, text: str) -> None:
    st.markdown(alert_html(level, text), unsafe_allow_html=True)


def badge(text: str, tone: str = "neutral") -> str:
    return f'<span class="badge {tone}">{html.escape(text)}</span>'


# ----------------------------------------------------------------------------
# Formats et affichage
# ----------------------------------------------------------------------------
def money(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v:,.0f}".replace(",", " ") + f" {config.DEVISE}"


def money_col(s: pd.Series) -> pd.Series:
    return s.map(money)


def show_df(df: pd.DataFrame, **kw) -> None:
    try:
        st.dataframe(df, hide_index=True, width="stretch", **kw)
    except Exception:
        st.dataframe(df, hide_index=True, use_container_width=True, **kw)


def style_fig(fig, height: int = 380, title: str | None = None):
    fig.update_layout(
        template="plotly_white", height=height, colorway=SEQ,
        font=dict(color="#0B1F33", size=13), margin=dict(l=10, r=10, t=55 if title else 20, b=10),
        legend=dict(orientation="h", y=-0.22), hoverlabel=dict(bgcolor="#0B1F33", font_color="#FFFFFF"),
        title=dict(text=title, font=dict(size=16, color="#0B1F33")) if title else None,
        paper_bgcolor="#FFFFFF", plot_bgcolor="#FFFFFF")
    fig.update_xaxes(gridcolor="#DCE3EA", linecolor="#0B1F33")
    fig.update_yaxes(gridcolor="#DCE3EA", linecolor="#0B1F33")
    return fig


def show_fig(fig, key: str | None = None) -> None:
    try:
        st.plotly_chart(fig, width="stretch", key=key)
    except Exception:
        st.plotly_chart(fig, use_container_width=True, key=key)


def csv_button(label: str, df: pd.DataFrame, filename: str, key: str) -> None:
    st.download_button(label, df.to_csv(index=False).encode("utf-8-sig"), file_name=filename,
                       mime="text/csv", key=key)


# ----------------------------------------------------------------------------
# Sélecteurs, navigation et actions
# ----------------------------------------------------------------------------
def names_of(ch: pd.DataFrame) -> dict[int, str]:
    return {int(i): str(n) for i, n in zip(ch["id"], ch["nom"])}


def pick(ch: pd.DataFrame, key: str, label: str = "Chantiers") -> list[int]:
    names = names_of(ch)
    return st.multiselect(label, list(names), default=list(names), format_func=names.get, key=key)


def pick_one(ch: pd.DataFrame, key: str, label: str = "Chantier") -> int:
    names = names_of(ch)
    if len(names) == 1:
        only = next(iter(names))
        st.caption(f"{label} : **{names[only]}**")
        return only
    return st.selectbox(label, list(names), format_func=names.get, key=key)


def go_to(page: str) -> None:
    if page in st.session_state.get("_menu_keys", []):
        st.session_state["nav"] = page


def flash(message: str, icon: str = "✅") -> None:
    st.session_state["_flash"] = (message, icon)


def act(fn, *args, ok: str = "Enregistré.", rerun: bool = True, **kwargs):
    """Exécute une opération métier : affiche l'erreur lisible ou confirme puis rafraîchit."""
    try:
        res = fn(*args, **kwargs)
    except sv.AccessError as exc:
        st.error(f"⛔ {exc}")
        return None, False
    except ValueError as exc:
        st.error(str(exc))
        return None, False
    flash(ok)
    if rerun:
        st.rerun()
    return res, True
