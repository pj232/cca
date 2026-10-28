# -*- coding: utf-8 -*-
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

from .. import services as sv, ui


def render(user, ch):
    ui.header("Tableau de bord", "Santé logistique de vos chantiers : stocks, livraisons et engins")
    if ch.empty:
        st.info("Aucun chantier ne vous est affecté. Contactez l'administrateur.")
        return
    ids = ui.pick(ch, "dash_ch")
    if not ids:
        st.info("Sélectionnez au moins un chantier.")
        return
    k = sv.kpis_chantiers(ids)
    stock = sv.stock_table(ids)

    crit = int(k["ruptures"].sum() + k["sous_seuil"].sum())
    items = [
        ("Matériaux critiques", crit, f"dont {int(k['ruptures'].sum())} en rupture", "danger" if crit else "ok"),
        ("À commander", int(k["a_commander"].sum()), "couverture < délai d'appro", "warn" if k["a_commander"].sum() else "ok"),
        ("Commandes en attente", int(k["cmd_attente"].sum()), f"{int(k['cmd_retard'].sum())} livraison(s) en retard",
         "warn" if k["cmd_retard"].sum() else "info"),
        ("Engins en panne", int(k["engins_panne"].sum()), f"{int(k['maint_retard'].sum())} maintenance(s) en retard",
         "danger" if k["engins_panne"].sum() else "ok"),
    ]
    if sv.has(user, "reception_controler"):
        items.append(("Livraisons à contrôler", int(k["a_controler"].sum()), "avant mise en stock",
                      "info" if k["a_controler"].sum() else "ok"))
    if sv.has(user, "couts_voir"):
        items.append(("Valeur du stock", ui.money(k["valeur_stock"].sum()), "au prix catalogue", "neutral"))
    ui.kpi_row(items)

    st.markdown("#### 🚨 Alertes prioritaires")
    al = sv.alertes(user, ids)
    if not al:
        ui.alert("ok", "Aucune alerte : la logistique est maîtrisée sur les chantiers sélectionnés.")
    for i, a in enumerate(al[:8]):
        c1, c2 = st.columns([8, 1.3])
        c1.markdown(ui.alert_html(a["niveau"], a["texte"]), unsafe_allow_html=True)
        c2.button("Ouvrir", key=f"al_{i}", on_click=ui.go_to, args=(a["page"],))
    if len(al) > 8:
        with st.expander(f"Voir les {len(al) - 8} autres alertes"):
            for a in al[8:]:
                ui.alert(a["niveau"], a["texte"])

    c1, c2 = st.columns(2)
    with c1:
        if not stock.empty:
            g = stock.groupby(["chantier", "statut"]).size().reset_index(name="matériaux")
            fig = px.bar(g, x="chantier", y="matériaux", color="statut", color_discrete_map=ui.STATUT_COLORS,
                         category_orders={"statut": ui.STATUT_ORDRE})
            ui.show_fig(ui.style_fig(fig, 380, "État des stocks par chantier"), key="dash_stock")
    with c2:
        r = stock.dropna(subset=["ratio_conso"]) if not stock.empty else stock
        r = r[r["avancement_pct"] > 0]
        if not r.empty:
            r = r.sort_values("ratio_conso", ascending=False).head(10).iloc[::-1]
            colors = ["#C1121F" if v > 1.15 else "#E85D04" if v > 1.0 else "#17794F" for v in r["ratio_conso"]]
            fig = go.Figure(go.Bar(x=r["ratio_conso"].round(2), y=r["nom"], orientation="h", marker_color=colors,
                                   text=r["ratio_conso"].round(2), textposition="outside",
                                   hovertext=r["chantier"]))
            fig.add_vline(x=1, line_dash="dash", line_color="#0B1F33")
            ui.show_fig(ui.style_fig(fig, 380, "Consommation / avancement (1 = conforme au plan)"), key="dash_ratio")

    st.markdown("#### 🏗️ Synthèse par chantier")
    d = pd.DataFrame({
        "Chantier": k["chantier"], "Avancement (%)": k["avancement"], "Matériaux": k["materiaux"],
        "Ruptures": k["ruptures"], "Sous seuil": k["sous_seuil"], "À commander": k["a_commander"],
        "Cmd en attente": k["cmd_attente"], "Cmd en retard": k["cmd_retard"],
        "Engins en panne": k["engins_panne"],
    })
    ui.show_df(d, column_config={"Avancement (%)": st.column_config.ProgressColumn(
        "Avancement (%)", min_value=0, max_value=100, format="%.0f %%")})
