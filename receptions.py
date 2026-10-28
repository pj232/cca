# -*- coding: utf-8 -*-
from datetime import date

import streamlit as st

from .. import services as sv, ui


def render(user, ch):
    ui.header("Réceptions & contrôle qualité",
              "Le réceptionnaire déclare la livraison ; le contrôle qualité la valide avant l'entrée en stock")
    if ch.empty:
        st.info("Aucun chantier affecté.")
        return
    ids = ui.names_of(ch)
    labels = []
    if sv.has(user, "reception_declarer"):
        labels.append("Déclarer une livraison")
    if sv.has(user, "reception_controler"):
        labels.append("À contrôler")
    labels.append("Historique")
    tabs = dict(zip(labels, st.tabs(labels)))
    if "Déclarer une livraison" in tabs:
        with tabs["Déclarer une livraison"]:
            _declarer(user, list(ids))
    if "À contrôler" in tabs:
        with tabs["À contrôler"]:
            _controler(user, list(ids))
    with tabs["Historique"]:
        _historique(user, ch)


def _declarer(user, ids):
    cmds = sv.commandes_table(ids, ["Approuvée", "Livrée partiellement"])
    if cmds.empty:
        ui.alert("ok", "Aucune commande n'attend de livraison.")
        return
    opts = {int(r.id): f"n°{r.id} — {r.fournisseur} — {r.chantier} (prévue le {r.date_livraison_prevue})"
            + (f" ⏰ {int(r.retard_j)} j de retard" if r.retard_j > 0 else "") for r in cmds.itertuples()}
    cid = st.selectbox("Commande livrée", list(opts), format_func=opts.get, key="rd_cmd")
    lignes = sv.lignes_commande(cid)
    with st.form("f_rec", clear_on_submit=True):
        c1, c2 = st.columns(2)
        bl = c1.text_input("N° de bon de livraison")
        jour = c2.date_input("Date de réception", value=date.today(), max_value=date.today())
        st.markdown("**Quantités effectivement reçues**")
        recues = {}
        for r in lignes.itertuples():
            reste = max(r.quantite - r.recu_net, 0)
            recues[int(r.id)] = st.number_input(f"{r.materiau} — reste à livrer : {reste:g} {r.unite}",
                                                min_value=0.0, value=0.0, step=1.0, key=f"rq_{cid}_{r.id}")
        obs = st.text_area("Observations (état, emballage, retard…)")
        ok = st.form_submit_button("Déclarer la réception")
    if ok:
        ui.act(sv.declarer_reception, user, int(cid), jour, bl, recues, obs,
               ok="Réception déclarée : en attente de contrôle qualité.")


def _controler(user, ids):
    att = sv.receptions_table(ids, ["À contrôler"])
    if att.empty:
        ui.alert("ok", "Aucune livraison en attente de contrôle.")
        return
    st.caption("Seules les quantités acceptées entrent en stock. Une non-conformité exige une description du défaut.")
    for r in att.itertuples():
        with st.expander(f"BL {r.bon_livraison} — {r.fournisseur} — {r.chantier} — reçu le {r.date}"):
            st.write(f"Réceptionné par **{r.receptionnaire}**. {r.observations or ''}")
            if int(r.receptionnaire_id) == int(user["id"]):
                st.info("Vous avez réceptionné cette livraison : un autre membre doit effectuer le contrôle.")
                continue
            lignes = sv.lignes_reception(r.id)
            with st.form(f"f_ctl_{r.id}"):
                statut = st.radio("Décision", ["Conforme", "Conforme avec réserves", "Non conforme"],
                                  horizontal=True, key=f"ct_st_{r.id}")
                acc = {}
                for l in lignes.itertuples():
                    acc[int(l.id)] = st.number_input(f"{l.materiau} — reçu {l.qte_recue:g} {l.unite} : quantité acceptée",
                                                     min_value=0.0, max_value=float(l.qte_recue),
                                                     value=float(l.qte_recue), step=1.0, key=f"ct_q_{l.id}")
                obs = st.text_area("Observations du contrôle (obligatoire si non conforme)", key=f"ct_o_{r.id}")
                ok = st.form_submit_button("Valider le contrôle")
            if ok:
                ui.act(sv.controler_reception, user, int(r.id), statut, acc, obs, ok="Contrôle enregistré.")


def _historique(user, ch):
    ids = ui.pick(ch, "rh_ch")
    stat = st.multiselect("Résultat du contrôle", list(ui.CONTROLE_TONES), key="rh_st")
    t = sv.receptions_table(ids, stat or None)
    if t.empty:
        st.info("Aucune réception.")
        return
    ctrl = t[t["controle_statut"] != "À contrôler"]
    taux = (ctrl["controle_statut"] != "Non conforme").mean() * 100 if len(ctrl) else float("nan")
    ui.kpi_row([
        ("Réceptions", len(t), "", "neutral"),
        ("À contrôler", int((t["controle_statut"] == "À contrôler").sum()), "", "info"),
        ("Non conformes", int((t["controle_statut"] == "Non conforme").sum()), "", "danger" if (t["controle_statut"] == "Non conforme").any() else "ok"),
        ("Taux de conformité", "—" if taux != taux else f"{taux:.0f} %", "lots contrôlés", "ok" if taux == taux and taux >= 90 else "warn"),
    ])
    ui.show_df(t[["date", "chantier", "fournisseur", "bon_livraison", "receptionnaire", "controle_statut", "controleur",
                  "qte_recue", "qte_acceptee", "observations_controle"]])
    if sv.has(user, "exporter"):
        ui.csv_button("⬇️ Exporter (CSV)", t, "receptions.csv", "rh_csv")
