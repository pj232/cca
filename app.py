# -*- coding: utf-8 -*-
"""LogiChantier — logistique de chantier : stocks, approvisionnement, réceptions qualité, engins.

Lancement :  streamlit run app.py
"""
import time

import streamlit as st

from lc import config, db, security, seed, services as sv, ui
from lc.views import admin, analyses, appro, dashboard, engins, messagerie, receptions, stocks

st.set_page_config(page_title=config.APP_NAME, page_icon="🧱", layout="wide")


@st.cache_resource
def _boot() -> bool:
    seed.bootstrap()
    return True


def _login() -> None:
    left, right = st.columns([1.15, 1])
    with left:
        st.markdown(
            f'<div class="hero"><h1>🧱 {config.APP_NAME}</h1>'
            "<p>Pilotez la logistique de vos chantiers : <b>matériaux, commandes, livraisons et engins</b>.</p>"
            "<p>Évitez les ruptures, détectez les surconsommations, tracez chaque décision.</p>"
            '<span class="pill">Stocks en temps réel</span><span class="pill">Validation des commandes</span>'
            '<span class="pill">Contrôle qualité</span><span class="pill">Maintenance des engins</span></div>',
            unsafe_allow_html=True)
    with right:
        st.markdown("### Connexion")
        with st.form("login"):
            username = st.text_input("Identifiant")
            password = st.text_input("Mot de passe", type="password")
            ok = st.form_submit_button("Se connecter")
        if ok:
            try:
                user = security.authenticate(username, password)
            except security.LockedError as exc:
                st.error(f"Trop de tentatives. Réessayez dans {exc.wait_seconds // 60 + 1} min.")
                return
            if user:
                st.session_state["user"] = user
                st.session_state["last_seen"] = time.time()
                st.rerun()
            st.error("Identifiant ou mot de passe incorrect.")
        if config.DEMO:
            with st.expander("Comptes de démonstration"):
                st.markdown(
                    "| Profil | Identifiant | Mot de passe |\n|---|---|---|\n"
                    "| Maître d'ouvrage | `maitre` | `Maitre!2026` |\n"
                    "| Chef de chantier | `chantier1` / `chantier2` | `Chantier1!2026` / `Chantier2!2026` |\n"
                    "| Ingénieur | `ingenieur` | `Ingenieur!2026` |\n"
                    "| Administrateur | `administrateur` | `Admin!2026` |")


def _current_user():
    u = st.session_state.get("user")
    if not u:
        return None
    if time.time() - st.session_state.get("last_seen", 0) > config.SESSION_INACTIVITE_S:
        st.session_state.clear()
        st.warning("Session expirée par inactivité : reconnectez-vous.")
        return None
    st.session_state["last_seen"] = time.time()
    row = db.one("SELECT id, username, full_name, role, active, must_change FROM users WHERE id=?", (u["id"],))
    if not row or not row["active"]:
        st.session_state.clear()
        return None
    st.session_state["user"] = row
    return row


def _menu(user):
    uid = int(user["id"])
    ids = sv.user_chantier_ids(user)
    badge_msg = sv.non_lus(user)
    badge_appro = (len(sv.commandes_table(ids, ["Demandée"])) if (sv.has(user, "commande_approuver")
                   or sv.has(user, "commande_approuver_tout")) and ids else 0)
    badge_rec = len(sv.receptions_table(ids, ["À contrôler"])) if sv.has(user, "reception_controler") and ids else 0
    b = lambda n: f" ({n})" if n else ""
    entries = [
        ("dashboard", "📊 Tableau de bord", dashboard.render, sv.has(user, "dashboard")),
        ("stocks", "📦 Stocks", stocks.render, sv.has(user, "stocks_lire")),
        ("appro", "🚚 Approvisionnement" + b(badge_appro), appro.render, sv.has(user, "commandes_lire")),
        ("receptions", "✅ Réceptions & qualité" + b(badge_rec), receptions.render, sv.has(user, "receptions_lire")),
        ("engins", "🚜 Engins & maintenance", engins.render, sv.has(user, "engins_lire")),
        ("analyses", "📈 Analyses", analyses.render, sv.has(user, "analyses")),
        ("messagerie", "💬 Messagerie" + b(badge_msg), messagerie.render, sv.has(user, "messagerie")),
        ("chantiers", "🏗️ Chantiers", admin.chantiers,
         sv.has(user, "chantier_creer") or sv.has(user, "chantier_affecter") or sv.has(user, "avancement_maj")),
        ("utilisateurs", "👥 Utilisateurs", admin.utilisateurs, sv.has(user, "users_gerer")),
        ("audit", "🧾 Journal d'audit", admin.audit, sv.has(user, "audit_lire")),
        ("compte", "👤 Mon compte", admin.compte, True),
    ]
    return [(k, lbl, fn) for k, lbl, fn, allowed in entries if allowed]


def main() -> None:
    ui.inject_css()
    _boot()
    user = _current_user()
    if not user:
        _login()
        return

    flash = st.session_state.pop("_flash", None)
    if flash:
        st.toast(flash[0], icon=flash[1])

    forcer = bool(user["must_change"]) and not config.DEMO
    menu = _menu(user)
    if forcer:
        menu = [m for m in menu if m[0] == "compte"]
    keys = [m[0] for m in menu]
    labels = {k: lbl for k, lbl, _ in menu}
    pages = {k: fn for k, _, fn in menu}
    st.session_state["_menu_keys"] = keys
    if st.session_state.get("nav") not in keys:
        st.session_state["nav"] = keys[0]

    with st.sidebar:
        st.markdown(f"### 🧱 {config.APP_NAME}")
        st.write(f"{config.ROLE_ICONS[user['role']]} **{user['full_name']}**")
        st.caption(config.ROLES[user["role"]])
        if user["must_change"]:
            st.warning("Mot de passe temporaire : changez-le dans « Mon compte ».")
        st.radio("Navigation", keys, format_func=labels.get, key="nav", label_visibility="collapsed")
        if st.button("Se déconnecter"):
            st.session_state.clear()
            st.rerun()

    if forcer:
        st.error("Pour continuer, vous devez remplacer votre mot de passe temporaire.")
    pages[st.session_state["nav"]](user, sv.chantiers_visibles(user))


main()

