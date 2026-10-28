# -*- coding: utf-8 -*-
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from .. import config, db, services as sv, ui


def render(user, ch):
    ui.header("Approvisionnement", "Demandes, validation, suivi des livraisons et performance des fournisseurs")
    if ch.empty:
        st.info("Aucun chantier affecté.")
        return
    labels = []
    if sv.has(user, "commande_demander"):
        labels.append("Nouvelle commande")
    labels.append("Suivi des commandes")
    if sv.has(user, "commande_approuver") or sv.has(user, "commande_approuver_tout"):
        labels.append("Décisions")
    labels.append("Fournisseurs")
    tabs = dict(zip(labels, st.tabs(labels)))
    if "Nouvelle commande" in tabs:
        with tabs["Nouvelle commande"]:
            _nouvelle(user, ch)
    with tabs["Suivi des commandes"]:
        _suivi(user, ch)
    if "Décisions" in tabs:
        with tabs["Décisions"]:
            _decisions(user, ch)
    with tabs["Fournisseurs"]:
        _fournisseurs(user, ch)


def _nouvelle(user, ch):
    cid = ui.pick_one(ch, "no_ch")
    t = sv.stock_table([cid])
    if t.empty:
        st.info("Aucun matériau suivi sur ce chantier.")
        return
    only = st.toggle("Seulement les articles à réapprovisionner", value=True, key="no_only")
    v = t[(t["suggere"] > 0) | (t["statut"] != "OK")] if only else t
    if v.empty:
        ui.alert("ok", "Rien à réapprovisionner pour le moment.")
        return
    v = v.reset_index(drop=True)
    voir_prix = sv.has(user, "couts_voir")
    df = pd.DataFrame({
        "Inclure": v["suggere"] > 0, "Matériau": v["nom"], "Unité": v["unite"], "Stock": v["stock"].round(2),
        "Couverture (j)": v["couverture_j"].round(1), "En commande": v["en_commande"].round(2),
        "Quantité": v["suggere"].astype(float)})
    if voir_prix:
        df["Prix unitaire"] = v["prix_unitaire"]
    st.caption("Cochez les lignes à commander et ajustez les quantités : la suggestion couvre le délai d'appro "
               f"+ {config.MARGE_SECURITE_JOURS} jours de marge.")
    ed = st.data_editor(
        df, hide_index=True, key=f"no_ed_{cid}_{only}",
        disabled=[c for c in df.columns if c not in ("Inclure", "Quantité")],
        column_config={"Quantité": st.column_config.NumberColumn("Quantité", min_value=0.0, step=1.0),
                       "Inclure": st.column_config.CheckboxColumn("Inclure")})
    lignes, total = [], 0.0
    for i in range(len(v)):
        q = float(ed.loc[i, "Quantité"] or 0)
        if bool(ed.loc[i, "Inclure"]) and q > 0:
            lignes.append((int(v.loc[i, "id"]), q))
            total += q * float(v.loc[i, "prix_unitaire"])
    c1, c2, c3 = st.columns(3)
    c1.metric("Lignes sélectionnées", len(lignes))
    if voir_prix:
        c2.metric("Montant estimé", ui.money(total))
    if total > config.SEUIL_APPRO_INGENIEUR:
        ui.alert("warn", "Cette commande dépasse le seuil de l'ingénieur : "
                         "l'approbation du maître d'ouvrage sera requise.")
    fourn = sv.fournisseurs_table(True)
    if fourn.empty:
        st.warning("Aucun fournisseur actif.")
        return
    fopts = dict(zip(fourn["id"].astype(int), fourn["nom"]))
    delai = int(max([int(v.loc[i, "delai_appro_jours"]) for i in range(len(v))
                     if any(l[0] == int(v.loc[i, "id"]) for l in lignes)] or [3]))
    c1, c2 = st.columns(2)
    fid = c1.selectbox("Fournisseur", list(fopts), format_func=fopts.get, key="no_fourn")
    liv = c2.date_input("Livraison souhaitée", value=date.today() + timedelta(days=delai),
                        min_value=date.today(), key="no_liv")
    note = st.text_input("Note / usage prévu (ex. coulage dalle R+2)", key="no_note")
    if st.button("📨 Envoyer la demande de commande", key="no_send"):
        ui.act(sv.creer_commande, user, int(cid), int(fid), liv, lignes, note, ok="Demande envoyée pour validation.")


def _suivi(user, ch):
    ids = ui.pick(ch, "su_ch")
    stat = st.multiselect("Statut", config.STATUTS_COMMANDE, key="su_st")
    t = sv.commandes_table(ids, stat or None)
    if t.empty:
        st.info("Aucune commande.")
        return
    ui.kpi_row([
        ("Commandes", len(t), "", "neutral"),
        ("En attente de décision", int((t["statut"] == "Demandée").sum()), "", "info"),
        ("Livraisons en retard", int((t["retard_j"] > 0).sum()), "", "danger" if (t["retard_j"] > 0).any() else "ok"),
        ("Livrées", int((t["statut"] == "Livrée").sum()), "", "ok"),
    ])
    cols = ["id", "chantier", "fournisseur", "date_demande", "date_livraison_prevue", "statut", "retard_j",
            "nb_lignes", "demandeur"]
    show = t[cols].copy()
    if sv.has(user, "couts_voir"):
        show["montant"] = ui.money_col(t["montant"])
    ui.show_df(show.rename(columns={"id": "N°", "retard_j": "retard (j)", "nb_lignes": "lignes"}))

    st.markdown("#### Détail d'une commande")
    opts = {int(r.id): f"n°{r.id} — {r.fournisseur} — {r.statut}" for r in t.itertuples()}
    cid = st.selectbox("Commande", list(opts), format_func=opts.get, key="su_cmd")
    lg = sv.lignes_commande(cid)
    if not sv.has(user, "couts_voir"):
        lg = lg.drop(columns=["prix_unitaire"])
    ui.show_df(lg.drop(columns=["id"]).rename(columns={"recu_net": "reçu net"}))
    rec = sv.receptions_table(ids)
    rec = rec[rec["commande_id"] == cid] if not rec.empty else rec
    if not rec.empty:
        st.markdown("**Réceptions liées**")
        ui.show_df(rec[["date", "bon_livraison", "receptionnaire", "controle_statut", "controleur", "observations_controle"]])
    row = t[t["id"] == cid].iloc[0]
    if row["statut"] == "Demandée" and st.button("Annuler cette demande", key=f"su_cancel_{cid}"):
        ui.act(sv.annuler_commande, user, int(cid), ok="Demande annulée.")


def _decisions(user, ch):
    ids = ui.pick(ch, "de_ch")
    pend = sv.commandes_table(ids, ["Demandée"])
    if pend.empty:
        ui.alert("ok", "Aucune demande en attente de décision.")
        return
    st.caption(f"Seuil de l'ingénieur : {ui.money(config.SEUIL_APPRO_INGENIEUR)}. Au-delà, seul le maître d'ouvrage approuve.")
    for r in pend.itertuples():
        total = sv.montant_commande(r.id)
        grosse = total > config.SEUIL_APPRO_INGENIEUR
        titre = f"n°{r.id} — {r.chantier} — {r.fournisseur} — {ui.money(total)}" + ("  ⚠️ MOA" if grosse else "")
        with st.expander(titre):
            st.write(f"Demandée par **{r.demandeur}** le {r.date_demande} · livraison souhaitée le {r.date_livraison_prevue}")
            ui.show_df(sv.lignes_commande(r.id).drop(columns=["id", "recu_net"]))
            peut = sv.has(user, "commande_approuver_tout") or (sv.has(user, "commande_approuver") and not grosse)
            ses_propres = db.scalar("SELECT demandeur_id FROM commandes WHERE id=?", (int(r.id),)) == int(user["id"])
            if ses_propres:
                st.info("Vous ne pouvez pas approuver votre propre demande.")
            elif not peut:
                st.warning("Montant supérieur à votre seuil : l'approbation du maître d'ouvrage est requise.")
            c1, c2 = st.columns(2)
            if c1.button("✅ Approuver", key=f"ok_{r.id}", disabled=bool(ses_propres or not peut)):
                ui.act(sv.decider_commande, user, int(r.id), True, ok=f"Commande n°{r.id} approuvée.")
            motif = c2.text_input("Motif du refus", key=f"mf_{r.id}")
            if c2.button("⛔ Refuser", key=f"ko_{r.id}"):
                ui.act(sv.decider_commande, user, int(r.id), False, motif, ok=f"Commande n°{r.id} refusée.")


def _fournisseurs(user, ch):
    ids = ui.pick(ch, "fo_ch")
    today = date.today()
    per = st.date_input("Période d'évaluation", value=(today - timedelta(days=180), today), key="fo_per")
    if isinstance(per, (tuple, list)) and len(per) == 2:
        perf = sv.performance_fournisseurs(ids, per[0], per[1])
    else:
        perf = pd.DataFrame()
    if perf.empty:
        st.info("Pas encore de livraisons contrôlées sur la période.")
    else:
        fig = px.bar(perf.sort_values("score"), x="score", y="fournisseur", orientation="h", range_x=[0, 100],
                     color="score", color_continuous_scale=["#C1121F", "#F2B705", "#17794F"], range_color=[50, 100])
        fig.update_layout(coloraxis_showscale=False)
        ui.show_fig(ui.style_fig(fig, 320, "Score fournisseur (complétude 40 %, conformité 40 %, ponctualité 20 %)"),
                    key="fo_score")
        ui.show_df(perf.rename(columns={
            "retard_moyen_j": "retard moyen (j)", "delai_moyen_j": "délai moyen (j)", "lots_non_conformes": "lots NC",
            "conformite_pct": "conformité (%)", "completude_pct": "complétude (%)", "ponctualite_pct": "ponctualité (%)"}),
            column_config={"score": st.column_config.ProgressColumn("score", min_value=0, max_value=100, format="%.0f")})
    if sv.has(user, "fournisseur_gerer"):
        with st.expander("➕ Ajouter un fournisseur"):
            with st.form("f_four", clear_on_submit=True):
                c1, c2 = st.columns(2)
                nom = c1.text_input("Nom")
                spec = c2.text_input("Spécialité")
                c3, c4 = st.columns(2)
                contact = c3.text_input("Contact")
                tel = c4.text_input("Téléphone")
                ok = st.form_submit_button("Ajouter")
            if ok:
                ui.act(sv.creer_fournisseur, user, nom, spec, contact, tel, ok="Fournisseur ajouté.")
        with st.expander("🔁 Activer / désactiver un fournisseur"):
            f = sv.fournisseurs_table()
            opts = {int(r.id): f"{r.nom} — {'actif' if r.actif else 'inactif'}" for r in f.itertuples()}
            fid = st.selectbox("Fournisseur", list(opts), format_func=opts.get, key="fo_toggle")
            actif = bool(f[f["id"] == fid].iloc[0]["actif"])
            if st.button("Désactiver" if actif else "Réactiver", key="fo_btn"):
                ui.act(sv.basculer_fournisseur, user, int(fid), not actif, ok="Statut du fournisseur mis à jour.")
