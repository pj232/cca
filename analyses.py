# -*- coding: utf-8 -*-
from datetime import date, timedelta

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from .. import config, services as sv, ui


def render(user, ch):
    ui.header("Analyses multi-chantiers", "Consommation, approvisionnement, qualité des livraisons, engins et prévisions")
    if ch.empty:
        st.info("Aucun chantier affecté.")
        return
    ids = ui.pick(ch, "an_ch")
    if not ids:
        st.info("Sélectionnez au moins un chantier.")
        return
    today = date.today()
    per = st.date_input("Période d'analyse", value=(today - timedelta(days=90), today), key="an_per")
    if not isinstance(per, (tuple, list)) or len(per) != 2:
        st.info("Choisissez une date de début et une date de fin.")
        return
    d1, d2 = per
    voir_prix = sv.has(user, "couts_voir")
    labels = ["Consommation", "Approvisionnement", "Qualité des livraisons", "Engins", "Prévisions"]
    tabs = dict(zip(labels, st.tabs(labels)))
    stock = sv.stock_table(ids)
    with tabs["Consommation"]:
        _conso(stock, ids, d1, d2)
    with tabs["Approvisionnement"]:
        _appro(user, ids, d1, d2, voir_prix)
    with tabs["Qualité des livraisons"]:
        _qualite(ids, d1, d2)
    with tabs["Engins"]:
        _engins(ids, d1, d2, voir_prix)
    with tabs["Prévisions"]:
        _previsions(stock)
    if sv.has(user, "exporter"):
        with st.expander("⬇️ Exporter les données de la période (CSV)"):
            ui.csv_button("Mouvements de stock", sv.mouvements_table(ids, d1, d2), "mouvements.csv", "an_dl1")
            ui.csv_button("Commandes", sv.commandes_table(ids), "commandes.csv", "an_dl2")
            ui.csv_button("Réceptions", sv.receptions_table(ids), "receptions.csv", "an_dl3")
            if not stock.empty:
                ui.csv_button("État des stocks", stock.drop(columns=["icone"]), "stocks.csv", "an_dl4")


def _conso(stock, ids, d1, d2):
    if stock.empty:
        st.info("Aucun matériau suivi.")
        return
    r = stock[stock["avancement_pct"] > 0].dropna(subset=["conso_pct"])
    if not r.empty:
        r = r.assign(label=r["nom"] + " · " + r["chantier"].str.slice(0, 18))
        r = r.sort_values("ecart_conso")
        colors = ["#C1121F" if x > 15 else "#E85D04" if x > 0 else "#17794F" for x in r["ecart_conso"]]
        fig = go.Figure(go.Bar(x=r["ecart_conso"].round(1), y=r["label"], orientation="h", marker_color=colors))
        fig.add_vline(x=0, line_color="#0B1F33")
        fig.update_layout(xaxis_title="Écart (points) : consommation − avancement du chantier")
        ui.show_fig(ui.style_fig(fig, max(320, 22 * len(r) + 120), "Écart consommation / avancement"), key="an_ecart")
        st.caption("Positif et élevé : surconsommation possible (pertes, vols, erreurs de métré, surdosage). "
                   "Négatif : consommation en retard sur l'avancement déclaré.")
    mv = sv.mouvements_table(ids, d1, d2)
    sorties = mv[mv["type"] == "SORTIE"].copy() if not mv.empty else mv
    if sorties is None or sorties.empty:
        st.info("Aucune sortie sur la période.")
        return
    sorties["quantité"] = -sorties["quantite"]
    sorties["date"] = pd.to_datetime(sorties["date"])
    opts = sorted(sorties["materiau"].unique())
    mat = st.selectbox("Matériau à analyser", opts, key="an_mat")
    s = sorties[sorties["materiau"] == mat]
    daily = s.groupby(["date", "chantier"])["quantité"].sum().reset_index()
    fig = px.bar(daily, x="date", y="quantité", color="chantier")
    tot = s.groupby("date")["quantité"].sum().sort_index().rolling(7, min_periods=1).mean()
    fig.add_scatter(x=tot.index, y=tot.values, mode="lines", name="Moyenne 7 j", line=dict(color="#0B1F33", width=3))
    ui.show_fig(ui.style_fig(fig, 340, f"Consommation journalière — {mat}"), key="an_daily")
    z = s.fillna({"zone": "—"}).groupby("zone")["quantité"].sum().reset_index().sort_values("quantité")
    ui.show_fig(ui.style_fig(px.bar(z, x="quantité", y="zone", orientation="h", color_discrete_sequence=["#0B3C5D"]),
                             300, "Répartition par zone / tâche"), key="an_zone")


def _appro(user, ids, d1, d2, voir_prix):
    perf = sv.performance_fournisseurs(ids, d1, d2)
    if perf.empty:
        st.info("Pas de livraisons contrôlées sur la période.")
    else:
        fig = go.Figure()
        for col, nom, couleur in (("completude_pct", "Complétude", "#0B3C5D"), ("conformite_pct", "Conformité", "#17794F"),
                                  ("ponctualite_pct", "Ponctualité", "#E85D04")):
            fig.add_bar(x=perf["fournisseur"], y=perf[col], name=nom, marker_color=couleur)
        fig.update_layout(barmode="group", yaxis_range=[0, 105], yaxis_title="%")
        ui.show_fig(ui.style_fig(fig, 360, "Performance des fournisseurs"), key="an_perf")
        ui.show_df(perf[["fournisseur", "receptions", "delai_moyen_j", "retard_moyen_j", "lots_non_conformes", "score"]])
    cmd = sv.commandes_table(ids)
    if not cmd.empty:
        cmd["date_demande"] = pd.to_datetime(cmd["date_demande"])
        c = cmd[(cmd["date_demande"] >= pd.Timestamp(d1)) & (cmd["date_demande"] <= pd.Timestamp(d2))]
        if not c.empty:
            g = c.groupby("statut").size().reset_index(name="commandes")
            ui.show_fig(ui.style_fig(px.bar(g, x="statut", y="commandes", color="statut",
                                            color_discrete_sequence=ui.SEQ), 300, "Commandes par statut"), key="an_cmd")
    if voir_prix:
        a = sv.achats_par_mois(ids)
        if not a.empty:
            ui.show_fig(ui.style_fig(px.bar(a, x="mois", y="montant", color="chantier"), 340,
                                     f"Achats engagés par mois ({config.DEVISE})"), key="an_achats")


def _qualite(ids, d1, d2):
    r = sv.receptions_table(ids)
    if r.empty:
        st.info("Aucune réception.")
        return
    r["date"] = pd.to_datetime(r["date"])
    r = r[(r["date"] >= pd.Timestamp(d1)) & (r["date"] <= pd.Timestamp(d2))]
    if r.empty:
        st.info("Aucune réception sur la période.")
        return
    g = r.groupby(["fournisseur", "controle_statut"]).size().reset_index(name="lots")
    fig = px.bar(g, x="fournisseur", y="lots", color="controle_statut",
                 color_discrete_map={"Conforme": "#17794F", "Conforme avec réserves": "#F2B705",
                                     "Non conforme": "#C1121F", "À contrôler": "#0B6EBD"})
    ui.show_fig(ui.style_fig(fig, 340, "Résultat du contrôle par fournisseur"), key="an_q1")
    r["mois"] = r["date"].dt.strftime("%Y-%m")
    ctrl = r[r["controle_statut"] != "À contrôler"].assign(nc=lambda x: (x["controle_statut"] == "Non conforme") * 100.0)
    m = ctrl.groupby("mois").agg(taux_nc=("nc", "mean"), lots=("nc", "size")).reset_index()
    if not m.empty:
        ui.show_fig(ui.style_fig(px.line(m, x="mois", y="taux_nc", markers=True,
                                         labels={"taux_nc": "% de lots non conformes"}), 300,
                                 "Évolution du taux de non-conformité"), key="an_q2")


def _engins(ids, d1, d2, voir_prix):
    a = sv.engins_activite(ids, d1, d2)
    if a.empty:
        st.info("Aucun engin.")
        return
    c1, c2 = st.columns(2)
    with c1:
        fig = px.bar(a.sort_values("disponibilite_pct"), x="disponibilite_pct", y="nom", orientation="h", range_x=[0, 100],
                     color="disponibilite_pct", color_continuous_scale=["#C1121F", "#F2B705", "#17794F"], range_color=[60, 100])
        fig.update_layout(coloraxis_showscale=False)
        ui.show_fig(ui.style_fig(fig, 360, "Disponibilité (%)"), key="an_dispo")
    with c2:
        fig = px.bar(a, x="nom", y=["heures", "arret_h"], barmode="group",
                     color_discrete_sequence=["#0B3C5D", "#C1121F"], labels={"value": "heures", "variable": ""})
        ui.show_fig(ui.style_fig(fig, 360, "Heures d'utilisation vs heures d'arrêt"), key="an_hrs")
    cols = ["nom", "chantier", "heures", "arret_h", "pannes", "maintenances", "disponibilite_pct", "mtbf_h"]
    if voir_prix:
        cols += ["cout_exploitation"]
    ui.show_df(a[cols].rename(columns={"arret_h": "arrêt (h)", "disponibilite_pct": "dispo. (%)", "mtbf_h": "MTBF (h)",
                                       "cout_exploitation": "coût d'exploitation"}))
    st.caption("MTBF = heures d'utilisation moyennes entre deux pannes. Disponibilité = 1 − arrêts / heures ouvrées.")


def _previsions(stock):
    if stock.empty:
        st.info("Aucun matériau suivi.")
        return
    opts = {int(r.id): f"{r.nom} — {r.chantier}" for r in stock.itertuples()}
    mid = st.selectbox("Matériau", list(opts), format_func=opts.get, key="pr_mat")
    row = stock[stock["id"] == mid].iloc[0]
    c1, c2 = st.columns(2)
    inten = c1.slider("Intensité des travaux (% de la consommation récente)", 30, 250, 100, step=5, key="pr_int")
    horizon = c2.slider("Horizon (jours)", 7, 90, 30, key="pr_hor")
    conso = float(row["conso_jour"]) * inten / 100
    if conso <= 0:
        st.info("Pas de consommation récente : aucune prévision possible.")
        return
    jours = np.arange(0, horizon + 1)
    proj = np.maximum(row["stock"] - conso * jours, 0)
    rupture = row["stock"] / conso
    dates = [date.today() + timedelta(days=int(j)) for j in jours]
    fig = go.Figure()
    fig.add_scatter(x=dates, y=proj, mode="lines", line=dict(color="#0B3C5D", width=3), name="Stock prévu",
                    fill="tozeroy", fillcolor="rgba(11,60,93,0.12)")
    fig.add_hline(y=row["seuil_mini"], line_dash="dash", line_color="#E85D04", annotation_text="seuil mini")
    if rupture <= horizon:
        d_r = date.today() + timedelta(days=int(rupture))
        fig.add_scatter(x=[d_r], y=[0], mode="markers", marker=dict(size=14, color="#C1121F"), name="Rupture")
    ui.show_fig(ui.style_fig(fig, 360, f"Projection du stock — {row['nom']}"), key="pr_fig")
    delai = int(row["delai_appro_jours"])
    limite = rupture - delai
    if limite <= 0:
        ui.alert("danger", f"**Commander immédiatement** : rupture dans {rupture:.1f} j pour un délai d'appro de {delai} j.")
    elif limite <= 7:
        ui.alert("warn", f"Commander avant le {(date.today() + timedelta(days=int(limite))).strftime('%d/%m/%Y')} "
                         f"pour éviter la rupture (dans {rupture:.0f} j, délai {delai} j).")
    else:
        ui.alert("ok", f"Couverture suffisante : rupture estimée dans {rupture:.0f} j, délai d'appro {delai} j.")
