# -*- coding: utf-8 -*-
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

from .. import config, services as sv, ui


def _niveau(df: pd.DataFrame) -> pd.Series:
    base = (df["seuil_mini"] * 2).where(df["seuil_mini"] > 0)
    return (df["stock"] / base).clip(0, 1).mul(100).fillna(100)


def render(user, ch):
    ui.header("Stocks de matériaux", "Niveaux en temps réel, couverture en jours et traçabilité des mouvements")
    if ch.empty:
        st.info("Aucun chantier affecté.")
        return
    labels = ["État des stocks", "Mouvements"]
    if sv.has(user, "sortie_ecrire"):
        labels.append("Sortie de stock")
    if sv.has(user, "inventaire_ajuster"):
        labels.append("Inventaire")
    if sv.has(user, "materiau_gerer"):
        labels.append("Catalogue")
    tabs = dict(zip(labels, st.tabs(labels)))
    with tabs["État des stocks"]:
        _etat(user, ch)
    with tabs["Mouvements"]:
        _mouvements(user, ch)
    if "Sortie de stock" in tabs:
        with tabs["Sortie de stock"]:
            _sortie(user, ch)
    if "Inventaire" in tabs:
        with tabs["Inventaire"]:
            _inventaire(user, ch)
    if "Catalogue" in tabs:
        with tabs["Catalogue"]:
            _catalogue(user, ch)


def _etat(user, ch):
    ids = ui.pick(ch, "st_ch")
    t = sv.stock_table(ids)
    if t.empty:
        st.info("Aucun matériau suivi sur ces chantiers.")
        return
    c1, c2, c3 = st.columns(3)
    cats = c1.multiselect("Catégorie", sorted(t["categorie"].unique()), key="st_cat")
    stat = c2.multiselect("Statut", ui.STATUT_ORDRE, key="st_stat")
    q = c3.text_input("Rechercher un matériau", key="st_q")
    v = t.copy()
    if cats:
        v = v[v["categorie"].isin(cats)]
    if stat:
        v = v[v["statut"].isin(stat)]
    if q.strip():
        v = v[v["nom"].str.contains(q.strip(), case=False, regex=False)]
    ui.kpi_row([(s, int((v["statut"] == s).sum()), "", tone) for s, tone in
                (("Rupture", "danger"), ("Sous le seuil", "warn"), ("À commander", "warn"), ("OK", "ok"))])
    if v.empty:
        st.info("Aucun résultat pour ces filtres.")
        return
    v = v.assign(niveau=_niveau(v))
    cols = {"icone": "🚦", "chantier": "Chantier", "nom": "Matériau", "categorie": "Catégorie", "stock": "Stock",
            "unite": "Unité", "niveau": "Niveau", "seuil_mini": "Seuil mini", "couverture_j": "Couverture (j)",
            "conso_jour": "Conso. / jour", "statut": "Statut", "suggere": "À commander"}
    if sv.has(user, "couts_voir"):
        cols["valeur_stock"] = "Valeur"
    show = v[list(cols)].rename(columns=cols).round(2)
    ui.show_df(show, column_config={
        "Niveau": st.column_config.ProgressColumn("Niveau", min_value=0, max_value=100, format="%d %%"),
        "Stock": st.column_config.NumberColumn("Stock", format="%.1f"),
        **({"Valeur": st.column_config.NumberColumn("Valeur", format="%d")} if "Valeur" in show else {})})

    st.markdown("#### 🔎 Détail d'un matériau")
    opts = {int(r.id): f"{r.nom} — {r.chantier}" for r in v.itertuples()}
    mid = st.selectbox("Matériau", list(opts), format_func=opts.get, key="st_detail")
    row = v[v["id"] == mid].iloc[0]
    jours = row["couverture_j"]
    ui.kpi_row([
        ("Stock actuel", f"{row['stock']:g} {row['unite']}", f"seuil mini {row['seuil_mini']:g}", ui_tone(row["statut"])),
        ("Couverture", "—" if pd.isna(jours) else f"{jours:.1f} j", f"délai d'appro {int(row['delai_appro_jours'])} j",
         "danger" if row["statut"] in ("Rupture", "À commander") else "ok"),
        ("Consommation", "—" if pd.isna(row["conso_pct"]) else f"{row['conso_pct']:.0f} %",
         f"du besoin prévu ({row['besoin_prevu']:g} {row['unite']})", "info"),
        ("Rupture estimée", "—" if pd.isna(jours) else (date.today() + timedelta(days=int(jours))).strftime("%d/%m/%Y"),
         f"commande suggérée : {row['suggere']:g} {row['unite']}", "neutral"),
    ])
    h = sv.historique_stock(mid)
    if h.empty:
        return
    fig = go.Figure(go.Scatter(x=h["date"], y=h["stock"], mode="lines", line_shape="hv", name="Stock",
                               line=dict(color="#0B3C5D", width=3), fill="tozeroy", fillcolor="rgba(11,60,93,0.12)"))
    fig.add_hline(y=row["seuil_mini"], line_dash="dash", line_color="#C1121F",
                  annotation_text="seuil mini", annotation_position="top left")
    ui.show_fig(ui.style_fig(fig, 340, f"Évolution du stock — {row['nom']}"), key="st_hist")
    sorties = h[h["type"] == "SORTIE"].copy()
    if not sorties.empty:
        sorties["quantité"] = -sorties["quantite"]
        z = sorties.fillna({"zone": "—"}).groupby("zone")["quantité"].sum().reset_index().sort_values("quantité")
        fig = px.bar(z, x="quantité", y="zone", orientation="h", color_discrete_sequence=["#E85D04"])
        ui.show_fig(ui.style_fig(fig, 280, "Consommation par zone / tâche"), key="st_zone")


def ui_tone(statut: str) -> str:
    return {"Rupture": "danger", "Sous le seuil": "warn", "À commander": "warn"}.get(statut, "ok")


def _mouvements(user, ch):
    ids = ui.pick(ch, "mv_ch")
    today = date.today()
    per = st.date_input("Période", value=(today - timedelta(days=30), today), key="mv_per")
    if not isinstance(per, (tuple, list)) or len(per) != 2:
        st.info("Choisissez une date de début et une date de fin.")
        return
    typ = st.multiselect("Type", ["ENTREE", "SORTIE", "AJUSTEMENT"], key="mv_type")
    m = sv.mouvements_table(ids, per[0], per[1])
    if typ and not m.empty:
        m = m[m["type"].isin(typ)]
    if m.empty:
        st.info("Aucun mouvement sur la période.")
        return
    ui.show_df(m)
    if sv.has(user, "exporter"):
        ui.csv_button("⬇️ Exporter (CSV)", m, "mouvements.csv", "mv_csv")


def _sortie(user, ch):
    cid = ui.pick_one(ch, "so_ch")
    t = sv.stock_table([cid])
    t = t[t["stock"] > 0] if not t.empty else t
    if t.empty:
        st.warning("Aucun matériau disponible en stock sur ce chantier.")
        return
    opts = {int(r.id): f"{r.nom} — {r.stock:g} {r.unite} en stock" for r in t.itertuples()}
    mid = st.selectbox("Matériau", list(opts), format_func=opts.get, key="so_mat")
    row = t[t["id"] == mid].iloc[0]
    with st.form("f_sortie", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        qte = c1.number_input(f"Quantité ({row['unite']})", min_value=0.0, max_value=float(row["stock"]),
                              step=0.5, format="%.2f")
        jour = c2.date_input("Date", value=date.today(), max_value=date.today())
        zone = c3.text_input("Zone / tâche (ex. Dalle R+1)")
        note = st.text_input("Note (facultatif)")
        ok = st.form_submit_button("Enregistrer la sortie")
    if ok:
        ui.act(sv.enregistrer_sortie, user, int(mid), qte, jour, zone, note, ok="Sortie enregistrée.")


def _inventaire(user, ch):
    st.caption("Le stock compté remplace le stock théorique. L'écart est tracé dans le registre et l'audit.")
    cid = ui.pick_one(ch, "inv_ch")
    t = sv.stock_table([cid])
    if t.empty:
        st.info("Aucun matériau.")
        return
    opts = {int(r.id): f"{r.nom} — théorique {r.stock:g} {r.unite}" for r in t.itertuples()}
    mid = st.selectbox("Matériau", list(opts), format_func=opts.get, key="inv_mat")
    row = t[t["id"] == mid].iloc[0]
    with st.form("f_inv", clear_on_submit=True):
        compte = st.number_input(f"Stock compté ({row['unite']})", min_value=0.0, value=float(row["stock"]),
                                 step=0.5, format="%.2f")
        motif = st.text_input("Motif (obligatoire) : inventaire mensuel, casse, vol…")
        ok = st.form_submit_button("Ajuster le stock")
    if ok:
        res, done = ui.act(sv.ajuster_stock, user, int(mid), compte, motif, rerun=False)
        if done:
            st.success("Stock déjà conforme." if abs(res) < 1e-9 else f"Ajustement appliqué : {res:+g} {row['unite']}.")


def _catalogue(user, ch):
    names = ui.names_of(ch)
    with st.expander("➕ Ajouter un matériau"):
        with st.form("f_mat", clear_on_submit=True):
            c1, c2 = st.columns(2)
            cid = c1.selectbox("Chantier", list(names), format_func=names.get)
            nom = c2.text_input("Nom du matériau")
            c3, c4, c5 = st.columns(3)
            cat = c3.selectbox("Catégorie", config.CATEGORIES_MATERIAUX)
            unite = c4.selectbox("Unité", config.UNITES)
            prix = c5.number_input(f"Prix unitaire ({config.DEVISE})", min_value=0.0, step=1000.0)
            c6, c7, c8 = st.columns(3)
            seuil = c6.number_input("Seuil minimal", min_value=0.0, step=1.0)
            besoin = c7.number_input("Besoin total prévu", min_value=0.0, step=1.0)
            delai = c8.number_input("Délai d'appro (jours)", min_value=0, value=3, step=1)
            ok = st.form_submit_button("Ajouter")
        if ok:
            ui.act(sv.creer_materiau, user, int(cid), nom, cat, unite, prix, seuil, besoin, int(delai),
                   ok="Matériau ajouté.")
    with st.expander("✏️ Modifier un matériau"):
        t = sv.stock_table(list(names))
        if t.empty:
            st.info("Aucun matériau.")
            return
        opts = {int(r.id): f"{r.nom} — {r.chantier}" for r in t.itertuples()}
        mid = st.selectbox("Matériau", list(opts), format_func=opts.get, key="cat_mat")
        row = t[t["id"] == mid].iloc[0]
        with st.form(f"f_edit_{mid}"):
            c1, c2, c3, c4 = st.columns(4)
            prix = c1.number_input("Prix unitaire", min_value=0.0, value=float(row["prix_unitaire"]), step=1000.0)
            seuil = c2.number_input("Seuil mini", min_value=0.0, value=float(row["seuil_mini"]), step=1.0)
            besoin = c3.number_input("Besoin prévu", min_value=0.0, value=float(row["besoin_prevu"]), step=1.0)
            delai = c4.number_input("Délai (j)", min_value=0, value=int(row["delai_appro_jours"]), step=1)
            actif = st.checkbox("Matériau actif", value=True)
            ok = st.form_submit_button("Enregistrer les modifications")
        if ok:
            ui.act(sv.modifier_materiau, user, int(mid), prix, seuil, besoin, int(delai), actif, ok="Matériau modifié.")
