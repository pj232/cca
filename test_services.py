"""Tests de la logique métier (sans Streamlit). Lancement : python tests/test_services.py  (ou pytest)."""
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

os.environ["LOGICHANTIER_DB"] = tempfile.mktemp(suffix=".db")
os.environ["LOGICHANTIER_DEMO"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lc import db, security, seed, services as sv  # noqa: E402

seed.bootstrap()


def U(username):
    r = db.one("SELECT id, username, full_name, role, active, must_change FROM users WHERE username=?", (username,))
    assert r, username
    return r


def raises(exc, fn, *a, **k):
    try:
        fn(*a, **k)
    except exc:
        return True
    raise AssertionError(f"{exc.__name__} attendu")


def test_donnees_demo():
    assert db.scalar("SELECT COUNT(*) FROM chantiers") == 3
    assert db.scalar("SELECT COUNT(*) FROM materiaux") == 19
    assert db.scalar("SELECT COUNT(*) FROM mouvements") > 500
    # Le registre ne doit jamais produire de stock négatif
    neg = db.df("SELECT materiau_id, SUM(quantite) s FROM mouvements GROUP BY materiau_id HAVING s < -1e-6")
    assert neg.empty


def test_authentification_et_blocage():
    assert security.authenticate("maitre", "Maitre!2026")
    assert security.authenticate("MAITRE", "Maitre!2026")
    assert security.authenticate("maitre", "faux") is None
    for _ in range(5):  # 5 échecs autorisés, la 6e tentative est bloquée
        assert security.authenticate("chantier1", "mauvais") is None
    raises(security.LockedError, security.authenticate, "chantier1", "mauvais")
    raises(security.LockedError, security.authenticate, "chantier1", "Chantier1!2026")  # même le bon mdp
    db.execute("DELETE FROM login_attempts")
    assert security.password_problem("abc") and security.password_problem("abcdefgh")
    assert security.password_problem("abcdefg1") is None


def test_perimetre():
    assert len(sv.chantiers_visibles(U("chantier1"))) == 1
    assert len(sv.chantiers_visibles(U("chantier2"))) == 2
    assert len(sv.chantiers_visibles(U("ingenieur"))) == 3
    assert len(sv.chantiers_visibles(U("administrateur"))) == 3
    ch1 = int(sv.chantiers_visibles(U("chantier1")).iloc[0]["id"])
    autre = [i for i in sv.user_chantier_ids(U("ingenieur")) if i != ch1][0]
    raises(sv.AccessError, sv.require_chantier, U("chantier1"), autre)


def test_stock_table():
    ids = sv.user_chantier_ids(U("ingenieur"))
    t = sv.stock_table(ids)
    assert len(t) == 19
    assert set(t["statut"]) <= {"Rupture", "Sous le seuil", "À commander", "OK"}
    assert (t["stock"] >= -1e-6).all()
    assert t["suggere"].ge(0).all()
    # le tronçon B est construit pour surconsommer
    r = t[t["chantier"].str.contains("Route")]["ratio_conso"].mean()
    p = t[t["chantier"].str.contains("Palmiers")]["ratio_conso"].mean()
    assert r > p


def test_sortie_de_stock():
    chef = U("chantier1")
    ids = sv.user_chantier_ids(chef)
    t = sv.stock_table(ids)
    m = t[t["stock"] > 5].iloc[0]
    avant = sv.stock_actuel(int(m["id"]))
    sv.enregistrer_sortie(chef, int(m["id"]), 2, date.today(), "Fondations")
    assert abs(sv.stock_actuel(int(m["id"])) - (avant - 2)) < 1e-6
    raises(ValueError, sv.enregistrer_sortie, chef, int(m["id"]), avant * 10, date.today(), "Fondations")  # stock insuffisant
    raises(ValueError, sv.enregistrer_sortie, chef, int(m["id"]), 1, date.today(), "")                      # zone obligatoire
    raises(ValueError, sv.enregistrer_sortie, chef, int(m["id"]), 1, date.today() + timedelta(days=1), "x")  # futur
    raises(sv.AccessError, sv.enregistrer_sortie, U("ingenieur"), int(m["id"]), 1, date.today(), "x")       # pas son droit
    # un chef ne sort pas de matériau d'un chantier qui n'est pas le sien
    autre = sv.stock_table([i for i in sv.user_chantier_ids(U("ingenieur")) if i not in ids])
    raises(sv.AccessError, sv.enregistrer_sortie, chef, int(autre.iloc[0]["id"]), 1, date.today(), "x")


def test_inventaire():
    ing = U("ingenieur")
    t = sv.stock_table(sv.user_chantier_ids(ing))
    mid = int(t.iloc[0]["id"])
    actuel = sv.stock_actuel(mid)
    delta = sv.ajuster_stock(ing, mid, actuel + 3, "Inventaire mensuel")
    assert abs(delta - 3) < 1e-6 and abs(sv.stock_actuel(mid) - (actuel + 3)) < 1e-6
    raises(ValueError, sv.ajuster_stock, ing, mid, 5, "")
    raises(sv.AccessError, sv.ajuster_stock, U("chantier1"), mid, 5, "x")


def test_workflow_commande_reception():
    chef, ing, moa = U("chantier2"), U("ingenieur"), U("maitre")
    ch = int(sv.chantiers_visibles(chef).iloc[0]["id"])
    mats = sv.stock_table([ch])
    petit, gros = mats.sort_values("prix_unitaire").iloc[0], mats.sort_values("prix_unitaire").iloc[-1]
    fid = int(sv.fournisseurs_table(True).iloc[0]["id"])
    demain = date.today() + timedelta(days=2)

    # Droits
    raises(sv.AccessError, sv.creer_commande, moa, ch, fid, demain, [(int(petit["id"]), 1)])
    raises(ValueError, sv.creer_commande, chef, ch, fid, demain, [])
    raises(ValueError, sv.creer_commande, chef, ch, fid, date.today() - timedelta(days=1), [(int(petit["id"]), 1)])

    # Petite commande : l'ingénieur approuve ; le demandeur ne peut pas s'auto-approuver
    c1 = sv.creer_commande(chef, ch, fid, demain, [(int(petit["id"]), 10)])
    raises(sv.AccessError, sv.decider_commande, chef, c1, True)
    sv.decider_commande(ing, c1, True)
    assert db.scalar("SELECT statut FROM commandes WHERE id=?", (c1,)) == "Approuvée"
    raises(ValueError, sv.decider_commande, ing, c1, True)  # déjà décidée

    # Grosse commande : au-dessus du seuil, seul le maître d'ouvrage peut approuver
    q = int(sv.__dict__["config"].SEUIL_APPRO_INGENIEUR / max(gros["prix_unitaire"], 1)) + 5
    c2 = sv.creer_commande(chef, ch, fid, demain, [(int(gros["id"]), q)])
    assert sv.montant_commande(c2) > sv.config.SEUIL_APPRO_INGENIEUR
    raises(sv.AccessError, sv.decider_commande, ing, c2, True)
    raises(ValueError, sv.decider_commande, moa, c2, False, "")  # refus sans motif
    sv.decider_commande(moa, c2, True)

    # Réception : le chef déclare, l'ingénieur contrôle, le stock augmente seulement après contrôle
    ligne = sv.lignes_commande(c1).iloc[0]
    avant = sv.stock_actuel(int(petit["id"]))
    raises(ValueError, sv.declarer_reception, chef, c1, date.today(), "", {int(ligne["id"]): 4})          # BL requis
    raises(ValueError, sv.declarer_reception, chef, c1, date.today(), "BL-X", {int(ligne["id"]): 50})     # excès > 10 %
    rid = sv.declarer_reception(chef, c1, date.today(), "BL-TEST", {int(ligne["id"]): 6})
    assert db.scalar("SELECT statut FROM commandes WHERE id=?", (c1,)) == "Livrée partiellement"
    assert abs(sv.stock_actuel(int(petit["id"])) - avant) < 1e-9
    raises(sv.AccessError, sv.controler_reception, chef, rid, "Conforme", {})
    ligne_r = sv.lignes_reception(rid).iloc[0]
    sv.controler_reception(ing, rid, "Conforme avec réserves", {int(ligne_r["id"]): 5})
    assert abs(sv.stock_actuel(int(petit["id"])) - (avant + 5)) < 1e-6
    raises(ValueError, sv.controler_reception, ing, rid, "Conforme", {})  # déjà contrôlée

    # Seconde livraison non conforme : rien n'entre en stock
    rid2 = sv.declarer_reception(chef, c1, date.today(), "BL-TEST2", {int(ligne["id"]): 4})
    raises(ValueError, sv.controler_reception, ing, rid2, "Non conforme", {}, "")  # défaut à décrire
    sv.controler_reception(ing, rid2, "Non conforme", {}, "Granulométrie hors fuseau")
    assert abs(sv.stock_actuel(int(petit["id"])) - (avant + 5)) < 1e-6
    assert db.scalar("SELECT statut FROM commandes WHERE id=?", (c1,)) == "Livrée partiellement"  # reste à livrer

    # Notifications produites
    assert db.scalar("SELECT COUNT(*) FROM messages WHERE recipient_id=? AND lu=0", (int(ing["id"]),)) > 0


def test_performance_fournisseurs():
    ids = sv.user_chantier_ids(U("ingenieur"))
    p = sv.performance_fournisseurs(ids)
    assert not p.empty and p["score"].between(0, 100).all()
    sc = dict(zip(p["fournisseur"], p["score"]))
    assert sc["Aciers & Fers Pro"] < sc["Cimenterie du Centre"]  # le fournisseur peu fiable est bien repéré


def test_engins():
    ing, chef = U("ingenieur"), U("chantier2")
    ids = sv.user_chantier_ids(ing)
    e = sv.engins_table(ids)
    assert len(e) == 9 and "En retard" in set(e["maintenance"]) and "En panne" in set(e["statut"])
    dispo = e[(e["statut"] == "Disponible") & (e["chantier"].str.contains("Route"))].iloc[0]
    raises(ValueError, sv.declarer_utilisation, chef, int(dispo["id"]), date.today(), 30)
    sv.declarer_utilisation(chef, int(dispo["id"]), date.today(), 6.5)
    sv.declarer_panne(chef, int(dispo["id"]), date.today(), "Surchauffe moteur")
    raises(ValueError, sv.declarer_utilisation, chef, int(dispo["id"]), date.today(), 2)  # en panne
    j = db.scalar("SELECT id FROM engin_journal WHERE engin_id=? AND type='Panne' AND resolu=0", (int(dispo["id"]),))
    raises(sv.AccessError, sv.cloturer_panne, chef, j, 12, 0)  # seul l'ingénieur clôture
    sv.cloturer_panne(ing, j, 12, 80000)
    assert db.scalar("SELECT statut FROM engins WHERE id=?", (int(dispo["id"]),)) == "Disponible"
    sv.enregistrer_maintenance(ing, int(dispo["id"]), date.today(), "", 100000, 6)
    a = sv.engins_activite(ids, date.today() - timedelta(days=60), date.today())
    assert a["disponibilite_pct"].between(0, 100).all()


def test_alertes_et_kpis():
    ing = U("ingenieur")
    ids = sv.user_chantier_ids(ing)
    al = sv.alertes(ing, ids)
    assert al and al[0]["niveau"] == "danger"
    assert any("panne" in a["texte"].lower() for a in al)
    k = sv.kpis_chantiers(ids)
    assert len(k) == 3 and k["valeur_stock"].sum() > 0


def test_messagerie():
    moa, ing, chef = U("maitre"), U("ingenieur"), U("chantier1")
    sv.envoyer(moa, int(ing["id"]), "Bonjour", important=True)
    assert sv.non_lus(ing) > 0
    c = sv.conversation(ing, int(moa["id"]))
    assert "Bonjour" in " ".join(c["body"])
    assert sv.non_lus_par_expediteur(ing).get(int(moa["id"]), 0) == 0  # lu après ouverture
    raises(ValueError, sv.envoyer, moa, int(ing["id"]), "  ")
    ch = sv.user_chantier_ids(chef)[0]
    sv.poster_canal(chef, ch, "Livraison demain")
    assert "Livraison demain" in " ".join(sv.canal(chef, ch)["body"])
    autre = [i for i in sv.user_chantier_ids(ing) if i != ch][0]
    raises(sv.AccessError, sv.canal, chef, autre)


def test_administration():
    adm = U("administrateur")
    ing = U("ingenieur")
    uid = sv.creer_utilisateur(adm, "nouveau", "Nouvel Ingénieur", "ingenieur", "Motdepasse1", sv.user_chantier_ids(ing)[:1])
    assert len(sv.user_chantier_ids(U("nouveau"))) == 1
    raises(ValueError, sv.creer_utilisateur, adm, "nouveau", "Doublon", "ingenieur", "Motdepasse1", [])
    raises(ValueError, sv.creer_utilisateur, adm, "faible", "Faible", "ingenieur", "abc", [])
    raises(sv.AccessError, sv.creer_utilisateur, ing, "x", "X", "ingenieur", "Motdepasse1", [])
    sv.basculer_utilisateur(adm, uid, False)
    assert security.authenticate("nouveau", "Motdepasse1") is None
    raises(ValueError, sv.basculer_utilisateur, adm, int(adm["id"]), False)
    assert db.scalar("SELECT COUNT(*) FROM audit_log") > 10


if __name__ == "__main__":
    ok = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            ok += 1
            print("OK ", name)
    print(f"{ok} tests réussis")
