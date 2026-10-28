# -*- coding: utf-8 -*-
from datetime import date, timedelta

import plotly.express as px
import streamlit as st

from .. import config, services as sv, ui

STATUT_ICON = {"Disponible": "🟢", "En panne": "🔴"}
MAINT_ICON = {"OK": "🟢", "À planifier": "🟠", "En retard": "🔴"}


def render(user, ch):
    ui.header("Engins & maintenance", "Disponibilité du parc, maintenance préventive et suivi des pannes")
    if ch.empty:
        st.info("Aucun chantier affecté.")
        return
    labels = ["Parc"]
    if sv.has(user, "engin_declarer"):
        labels.append("Déclarer")
    if sv.has(user, "engin_gerer"):
        labels.append("Maintenance")
    labels.append("Historique")
    tabs = dict(zip(labels, st.tabs(labels)))
    with tabs["Parc"]:
        _parc(user, ch)
    if "Déclarer" in tabs:
        with tabs["Déclarer"]:
            _declarer(user, ch)
    if "Maintenance" in tabs:
        with tabs["Maintenance"]:
            _maintenance(user, ch)
    with tabs["Historique"]:
        _historique(user, ch)


def _parc(user, ch):
    ids = ui.pick(ch, "en_ch")
    e = sv.engins_table(ids)
    if e.empty:
        st.info("Aucun engin enregistré.")
        return
    ui.kpi_row([
        ("Engins", len(e), "", "neutral"),
        ("Disponibles", int((e["statut"] == "Disponible").sum()), "", "ok"),
        ("En panne", int((e["statut"] == "En panne").sum()), "", "danger" if (e["statut"] == "En panne").any() else "ok"),
        ("Maintenance en retard", int((e["maintenance"] == "En retard").sum()), f"{int((e['maintenance'] == 'À planifier').sum())} à planifier",
         "warn" if (e["maintenance"] == "En retard").any() else "ok"),
    ])
    e = e.assign(usure=((e["compteur_h"] - e["derniere_maintenance_h"]) / e["intervalle_maintenance_h"] * 100).clip(0, 100),
                 etat=e["statut"].map(STATUT_ICON) + " " + e["statut"],
                 maint=e["maintenance"].map(MAINT_ICON) + " " + e["maintenance"])
    show = e[["nom", "chantier", "type_engin", "immatriculation", "etat", "compteur_h", "usure", "heures_restantes", "maint"]]
    ui.show_df(show.rename(columns={"nom": "Engin", "chantier": "Chantier", "type_engin": "Type", "immatriculation": "Immat.",
                                    "etat": "État", "compteur_h": "Compteur (h)", "usure": "Intervalle consommé",
                                    "heures_restantes": "Heures avant entretien", "maint": "Maintenance"}).round(0),
               column_config={"Intervalle consommé": st.column_config.ProgressColumn(
                   "Intervalle consommé", min_value=0, max_value=100, format="%d %%")})
    fig = px.bar(e.sort_values("heures_restantes"), x="heures_restantes", y="nom", orientation="h",
                 color="maintenance", color_discrete_map={"OK": "#17794F", "À planifier": "#E85D04", "En retard": "#C1121F"})
    fig.add_vline(x=0, line_color="#0B1F33")
    ui.show_fig(ui.style_fig(fig, 340, "Heures restantes avant la prochaine maintenance"), key="en_bar")


def _declarer(user, ch):
    cid = ui.pick_one(ch, "ed_ch")
    e = sv.engins_table([cid])
    if e.empty:
        st.info("Aucun engin sur ce chantier.")
        return
    opts = {int(r.id): f"{r.nom} ({r.statut})" for r in e.itertuples()}
    eid = st.selectbox("Engin", list(opts), format_func=opts.get, key="ed_engin")
    mode = st.radio("Que déclarer ?", ["Heures d'utilisation", "Panne"], horizontal=True, key="ed_mode")
    if mode == "Heures d'utilisation":
        with st.form("f_util", clear_on_submit=True):
            c1, c2 = st.columns(2)
            jour = c1.date_input("Date", value=date.today(), max_value=date.today())
            h = c2.number_input("Heures travaillées", min_value=0.0, max_value=24.0, step=0.5, value=0.0)
            note = st.text_input("Tâche réalisée")
            ok = st.form_submit_button("Enregistrer l'utilisation")
        if ok:
            ui.act(sv.declarer_utilisation, user, int(eid), jour, h, note, ok="Utilisation enregistrée.")
    else:
        with st.form("f_panne", clear_on_submit=True):
            jour = st.date_input("Date de la panne", value=date.today(), max_value=date.today())
            desc = st.text_area("Description de la panne")
            ok = st.form_submit_button("Déclarer la panne")
        if ok:
            ui.act(sv.declarer_panne, user, int(eid), jour, desc, ok="Panne déclarée : l'équipe a été prévenue.")


def _maintenance(user, ch):
    ids = list(ui.names_of(ch))
    st.markdown("#### Pannes ouvertes")
    p = sv.pannes_ouvertes(ids)
    if p.empty:
        ui.alert("ok", "Aucune panne ouverte.")
    for r in p.itertuples():
        with st.expander(f"🔴 {r.engin} — {r.chantier} — depuis le {r.date}"):
            st.write(r.description)
            c1, c2 = st.columns(2)
            arret = c1.number_input("Durée d'arrêt (h)", min_value=0.0, step=1.0, key=f"pa_h_{r.id}")
            cout = c2.number_input(f"Coût de réparation ({config.DEVISE})", min_value=0.0, step=10000.0, key=f"pa_c_{r.id}")
            if st.button("Clôturer la panne", key=f"pa_b_{r.id}"):
                ui.act(sv.cloturer_panne, user, int(r.id), arret, cout, ok="Panne clôturée, engin remis en service.")
    with st.expander("🛠️ Enregistrer une maintenance préventive"):
        e = sv.engins_table(ids)
        if not e.empty:
            opts = {int(r.id): f"{r.nom} — {r.chantier} ({r.maintenance})" for r in e.itertuples()}
            with st.form("f_maint", clear_on_submit=True):
                eid = st.selectbox("Engin", list(opts), format_func=opts.get)
                c1, c2, c3 = st.columns(3)
                jour = c1.date_input("Date", value=date.today(), max_value=date.today())
                arret = c2.number_input("Durée d'arrêt (h)", min_value=0.0, step=1.0)
                cout = c3.number_input(f"Coût ({config.DEVISE})", min_value=0.0, step=10000.0)
                desc = st.text_input("Intervention (vidange, filtres, graissage…)")
                ok = st.form_submit_button("Enregistrer la maintenance")
            if ok:
                ui.act(sv.enregistrer_maintenance, user, int(eid), jour, desc, cout, arret,
                       ok="Maintenance enregistrée : compteur d'entretien remis à zéro.")
    with st.expander("➕ Ajouter un engin"):
        names = ui.names_of(ch)
        with st.form("f_engin", clear_on_submit=True):
            c1, c2 = st.columns(2)
            cid = c1.selectbox("Chantier", list(names), format_func=names.get)
            nom = c2.text_input("Désignation")
            c3, c4 = st.columns(2)
            typ = c3.selectbox("Type", config.TYPES_ENGIN)
            immat = c4.text_input("Immatriculation")
            c5, c6, c7 = st.columns(3)
            interv = c5.number_input("Intervalle d'entretien (h)", min_value=1.0, value=250.0, step=10.0)
            compteur = c6.number_input("Compteur actuel (h)", min_value=0.0, step=10.0)
            cout_h = c7.number_input(f"Coût horaire ({config.DEVISE})", min_value=0.0, step=1000.0)
            ok = st.form_submit_button("Ajouter l'engin")
        if ok:
            ui.act(sv.creer_engin, user, int(cid), nom, typ, immat, interv, compteur, cout_h, ok="Engin ajouté.")


def _historique(user, ch):
    ids = ui.pick(ch, "eh_ch")
    today = date.today()
    per = st.date_input("Période", value=(today - timedelta(days=30), today), key="eh_per")
    if not isinstance(per, (tuple, list)) or len(per) != 2:
        st.info("Choisissez une date de début et une date de fin.")
        return
    typ = st.multiselect("Type d'événement", ["Utilisation", "Panne", "Maintenance"], key="eh_type")
    j = sv.engin_journal_table(ids, per[0], per[1])
    if typ and not j.empty:
        j = j[j["type"].isin(typ)]
    if j.empty:
        st.info("Aucun événement sur la période.")
        return
    if not sv.has(user, "couts_voir"):
        j = j.drop(columns=["cout"])
    ui.show_df(j.drop(columns=["resolu"]))
