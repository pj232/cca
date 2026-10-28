# -*- coding: utf-8 -*-
import streamlit as st

from .. import config, services as sv, ui


def render(user, ch):
    ui.header("Messagerie", "Échanges directs et canaux d'équipe par chantier — les alertes métier arrivent ici")
    t1, t2 = st.tabs(["💬 Conversations", "👥 Canaux de chantier"])
    with t1:
        _conversations(user)
    with t2:
        _canaux(user, ch)


def _conversations(user):
    ct = sv.contacts(user)
    if ct.empty:
        st.info("Aucun interlocuteur : vous n'avez pas de chantier en commun avec d'autres membres.")
        return
    nu = sv.non_lus_par_expediteur(user)
    opts = {int(r.id): f"{config.ROLE_ICONS[r.role]} {r.full_name} — {config.ROLES[r.role]}"
            + (f"   🔵 {nu[int(r.id)]}" if nu.get(int(r.id)) else "") for r in ct.itertuples()}
    # les contacts avec des messages non lus passent en premier
    ordre = sorted(opts, key=lambda i: (-nu.get(i, 0), opts[i]))
    autre = st.selectbox("Interlocuteur", ordre, format_func=opts.get, key="ms_dest")
    conv = sv.conversation(user, int(autre))
    box = st.container(height=420, border=True)
    with box:
        if conv.empty:
            st.caption("Aucun message. Écrivez le premier.")
        for r in conv.itertuples():
            mine = int(r.sender_id) == int(user["id"])
            with st.chat_message("user" if mine else "assistant", avatar="🧑‍💼" if mine else "💬"):
                st.caption(f"{'Vous' if mine else r.full_name} · {str(r.created_at).replace('T', ' ')}"
                           + ("  ❗ important" if r.important else ""))
                if r.subject:
                    st.markdown(f"**🔔 {r.subject}**")
                st.markdown(r.body)
    with st.form("f_msg", clear_on_submit=True):
        corps = st.text_area("Votre message", height=90)
        imp = st.checkbox("Marquer comme important")
        ok = st.form_submit_button("Envoyer")
    if ok:
        ui.act(sv.envoyer, user, int(autre), corps, imp, ok="Message envoyé.")


def _canaux(user, ch):
    if ch.empty:
        st.info("Aucun chantier affecté.")
        return
    cid = ui.pick_one(ch, "ms_canal", "Canal du chantier")
    d = sv.canal(user, int(cid))
    box = st.container(height=380, border=True)
    with box:
        if d.empty:
            st.caption("Canal vide.")
        for r in d.itertuples():
            with st.chat_message(r.full_name, avatar=config.ROLE_ICONS.get(r.role, "👤")):
                st.caption(f"{r.full_name} — {config.ROLES[r.role]} · {str(r.created_at).replace('T', ' ')}"
                           + ("  ❗ important" if r.important else ""))
                st.markdown(r.body)
    with st.form("f_canal", clear_on_submit=True):
        corps = st.text_area("Message à toute l'équipe du chantier", height=80)
        imp = st.checkbox("Important")
        ok = st.form_submit_button("Publier")
    if ok:
        ui.act(sv.poster_canal, user, int(cid), corps, imp, ok="Message publié.")
