# -*- coding: utf-8 -*-
from datetime import date, timedelta

import streamlit as st

from .. import config, db, services as sv, ui


def chantiers(user, ch):
    ui.header("Chantiers", "Portefeuille, avancement et équipes affectées")
    if not ch.empty:
        d = ch[["nom", "localisation", "type_ouvrage", "date_debut", "date_fin_prevue", "avancement_pct", "statut"]]
        ui.show_df(d.rename(columns={"avancement_pct": "avancement (%)", "date_fin_prevue": "fin prévue"}),
                   column_config={"avancement (%)": st.column_config.ProgressColumn(
                       "avancement (%)", min_value=0, max_value=100, format="%.0f %%")})
    if sv.has(user, "avancement_maj") and not ch.empty:
        with st.expander("📏 Mettre à jour l'avancement physique"):
            cid = ui.pick_one(ch, "av_ch")
            actuel = float(ch[ch["id"] == cid].iloc[0]["avancement_pct"])
            pct = st.slider("Avancement (%)", 0, 100, int(round(actuel)), key=f"av_{cid}")
            st.caption("Cet avancement sert de référence pour détecter les surconsommations de matériaux.")
            if st.button("Enregistrer l'avancement", key="av_btn"):
                ui.act(sv.maj_avancement, user, int(cid), float(pct), ok="Avancement mis à jour.")
    if sv.has(user, "chantier_creer"):
        with st.expander("➕ Créer un chantier"):
            with st.form("f_chantier", clear_on_submit=True):
                nom = st.text_input("Nom du chantier")
                c1, c2 = st.columns(2)
                loc = c1.text_input("Localisation")
                typ = c2.selectbox("Type d'ouvrage", config.TYPES_OUVRAGE)
                c3, c4 = st.columns(2)
                debut = c3.date_input("Début", value=date.today())
                fin = c4.date_input("Fin prévue", value=date.today() + timedelta(days=365))
                ok = st.form_submit_button("Créer")
            if ok:
                ui.act(sv.creer_chantier, user, nom, loc, typ, debut, fin, ok="Chantier créé.")
    if sv.has(user, "chantier_affecter") and not ch.empty:
        with st.expander("👥 Équipe et statut"):
            cid = ui.pick_one(ch, "aff_ch")
            membres = db.df("SELECT id, full_name, role FROM users WHERE active=1 AND role<>'admin' ORDER BY full_name")
            opts = {int(r.id): f"{r.full_name} — {config.ROLES[r.role]}" for r in membres.itertuples()}
            actuels = [int(x) for x in db.col("SELECT user_id FROM affectations WHERE chantier_id=?", (int(cid),))]
            choisis = st.multiselect("Équipe", list(opts), default=[a for a in actuels if a in opts],
                                     format_func=opts.get, key=f"aff_{cid}")
            actuel = ch[ch["id"] == cid].iloc[0]["statut"]
            statut = st.selectbox("Statut", config.STATUTS_CHANTIER, index=config.STATUTS_CHANTIER.index(actuel))
            if st.button("Enregistrer l'équipe", key="aff_btn"):
                ui.act(sv.affecter, user, int(cid), choisis, statut, ok="Équipe et statut mis à jour.")


def utilisateurs(user, ch):
    ui.header("Utilisateurs", "Comptes, profils et affectations")
    u = db.df("SELECT id, username, full_name, role, active, must_change, created_at FROM users ORDER BY role, full_name")
    show = u.assign(role=u["role"].map(config.ROLES), active=u["active"].map({1: "Actif", 0: "Désactivé"}),
                    must_change=u["must_change"].map({1: "Oui", 0: "Non"}))
    ui.show_df(show.drop(columns=["id"]).rename(columns={"username": "identifiant", "full_name": "nom", "role": "profil",
                                                         "active": "état", "must_change": "mdp à changer",
                                                         "created_at": "créé le"}))
    with st.expander("➕ Créer un compte"):
        chs = db.df("SELECT id, nom FROM chantiers ORDER BY nom")
        cn = {int(r.id): r.nom for r in chs.itertuples()}
        with st.form("f_user", clear_on_submit=True):
            c1, c2 = st.columns(2)
            ident = c1.text_input("Identifiant")
            nom = c2.text_input("Nom complet")
            role = st.selectbox("Profil", list(config.ROLES), format_func=config.ROLES.get)
            pwd = st.text_input("Mot de passe temporaire (8+ caractères, lettres et chiffres)", type="password")
            aff = st.multiselect("Chantiers affectés", list(cn), format_func=cn.get)
            ok = st.form_submit_button("Créer le compte")
        if ok:
            ui.act(sv.creer_utilisateur, user, ident, nom, role, pwd, aff,
                   ok="Compte créé : changement de mot de passe exigé à la première connexion.")
    with st.expander("✏️ Modifier un compte"):
        opts = {int(r.id): f"{r.username} — {r.full_name}" for r in u.itertuples()}
        uid = st.selectbox("Compte", list(opts), format_func=opts.get, key="ad_uid")
        c1, c2 = st.columns(2)
        nouveau = c1.text_input("Nouveau mot de passe temporaire", type="password", key="ad_pwd")
        if c1.button("Réinitialiser le mot de passe", key="ad_reset"):
            ui.act(sv.reinitialiser_mot_de_passe, user, int(uid), nouveau, ok="Mot de passe réinitialisé.")
        actif = bool(u[u["id"] == uid].iloc[0]["active"])
        if uid == int(user["id"]):
            c2.caption("Vous ne pouvez pas désactiver votre propre compte.")
        elif c2.button("Désactiver" if actif else "Réactiver", key="ad_toggle"):
            ui.act(sv.basculer_utilisateur, user, int(uid), not actif, ok="Compte mis à jour.")


def audit(user, ch):
    ui.header("Journal d'audit", "Traçabilité de toutes les opérations sensibles")
    c1, c2 = st.columns([1, 2])
    limite = c1.selectbox("Entrées affichées", [100, 300, 1000], key="au_lim")
    q = c2.text_input("Filtrer (action, détail, utilisateur)", key="au_q")
    d = db.df("SELECT a.ts AS date, u.full_name AS utilisateur, a.action, a.detail AS détail FROM audit_log a "
              "LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT ?", (int(limite),))
    if q.strip() and not d.empty:
        mask = d.apply(lambda r: q.strip().lower() in " ".join(str(x) for x in r.values).lower(), axis=1)
        d = d[mask]
    if d.empty:
        st.info("Aucune entrée.")
        return
    ui.show_df(d)
    if sv.has(user, "exporter"):
        ui.csv_button("⬇️ Exporter (CSV)", d, "audit.csv", "au_csv")


def compte(user, ch):
    ui.header("Mon compte", "Mot de passe et droits de votre profil")
    st.write(f"**{user['full_name']}** — {config.ROLES[user['role']]} (`{user['username']}`)")
    with st.form("f_pwd", clear_on_submit=True):
        ancien = st.text_input("Mot de passe actuel", type="password")
        nouveau = st.text_input("Nouveau mot de passe", type="password")
        confirm = st.text_input("Confirmer le nouveau mot de passe", type="password")
        ok = st.form_submit_button("Changer le mot de passe")
    if ok:
        if nouveau != confirm:
            st.error("Les deux saisies ne correspondent pas.")
        else:
            ui.act(sv.changer_mot_de_passe, user, ancien, nouveau, ok="Mot de passe modifié.")
    st.markdown("#### Droits de votre profil")
    droits = [config.PERMISSION_LABELS[p] for p in config.PERMISSION_LABELS if p in config.PERMISSIONS[user["role"]]]
    st.markdown("\n".join(f"- {d}" for d in droits))
