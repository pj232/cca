# -*- coding: utf-8 -*-
"""Données de démonstration : simulation jour par jour de 3 chantiers (stocks, livraisons, engins)."""
from __future__ import annotations

import math
import os
import random
import secrets
from datetime import date, datetime, timedelta

from . import config, db
from .security import hash_password

# (nom, spécialité, contact, téléphone, retard max (j), proba livraison partielle, proba lot non conforme)
FOURNISSEURS = [
    ("Cimenterie du Centre", "Ciment, béton prêt à l'emploi", "Service commercial", "+000 000 001", 1, 0.05, 0.03),
    ("Aciers & Fers Pro", "Aciers pour béton armé", "M. Responsable ventes", "+000 000 002", 6, 0.35, 0.15),
    ("Carrières Unies", "Sables, graviers, grave", "Chef de carrière", "+000 000 003", 3, 0.15, 0.05),
    ("Matériaux Généraux & Bitumes", "Maçonnerie, bois, bitume", "Service logistique", "+000 000 004", 4, 0.20, 0.08),
    ("Hydro Réseaux SARL", "Conduites, vannes, câbles", "Agence technique", "+000 000 005", 2, 0.10, 0.04),
]

# nom, catégorie, unité, prix, seuil mini, besoin total, délai d'appro (j), index fournisseur
MATERIAUX = {
    0: [("Ciment CPJ 42,5", "Liants & béton", "t", 95_000, 12, 420, 4, 0),
        ("Sable de rivière", "Granulats & sols", "m³", 12_000, 30, 900, 3, 2),
        ("Gravier 5/15", "Granulats & sols", "m³", 18_000, 25, 800, 3, 2),
        ("Acier HA10", "Aciers", "t", 620_000, 3, 60, 6, 1),
        ("Acier HA12", "Aciers", "t", 610_000, 3, 75, 6, 1),
        ("Parpaings de 15", "Maçonnerie", "u", 450, 800, 38_000, 5, 3),
        ("Bois de coffrage", "Bois & coffrage", "m²", 3_500, 100, 2_600, 5, 3)],
    1: [("Grave non traitée 0/31,5", "Granulats & sols", "m³", 9_500, 200, 14_000, 3, 2),
        ("Bitume 35/50", "Revêtements & enrobés", "t", 480_000, 8, 260, 7, 3),
        ("Gravillons 10/14", "Revêtements & enrobés", "m³", 20_000, 40, 3_200, 4, 2),
        ("Buses béton Ø800", "Réseaux & plomberie", "u", 85_000, 6, 150, 8, 4),
        ("Ciment CPJ 42,5", "Liants & béton", "t", 95_000, 8, 210, 4, 0),
        ("Géotextile", "Granulats & sols", "m²", 1_200, 400, 22_000, 6, 3)],
    2: [("Béton prêt à l'emploi C30", "Liants & béton", "m³", 110_000, 10, 1_100, 2, 0),
        ("Acier HA16", "Aciers", "t", 600_000, 4, 120, 6, 1),
        ("Conduites PEHD Ø200", "Réseaux & plomberie", "m", 14_000, 100, 6_500, 8, 4),
        ("Vannes DN200", "Réseaux & plomberie", "u", 350_000, 2, 28, 12, 4),
        ("Câbles électriques 4x16", "Électricité", "m", 4_500, 150, 5_200, 9, 4),
        ("Sable de rivière", "Granulats & sols", "m³", 12_000, 20, 700, 3, 2)],
}
ZONES = {
    0: ["Fondations", "Élévation RDC", "Dalle R+1", "Maçonnerie R+2", "Poteaux R+2"],
    1: ["PK 0+000 à 1+500", "PK 1+500 à 3+000", "Ouvrages hydrauliques", "Couche de forme"],
    2: ["Bassin de décantation", "Local technique", "Réseau de refoulement", "Station de pompage"],
}


def seed(conn) -> None:
    rnd = random.Random(2026)
    today = date.today()
    iso = lambda d: d.isoformat()

    def add_user(username, pwd, nom, role):
        return conn.execute("INSERT INTO users(username,password_hash,full_name,role,must_change) VALUES (?,?,?,?,1)",
                            (username, hash_password(pwd), nom, role)).lastrowid

    add_user("administrateur", "Admin!2026", "Administrateur plateforme", "admin")
    moa = add_user("maitre", "Maitre!2026", "Direction maîtrise d'ouvrage", "maitre_ouvrage")
    chef1 = add_user("chantier1", "Chantier1!2026", "Chef de chantier — Résidence", "chef_chantier")
    chef2 = add_user("chantier2", "Chantier2!2026", "Chef de chantier — Route et station", "chef_chantier")
    ing = add_user("ingenieur", "Ingenieur!2026", "Ingénieur travaux", "ingenieur")

    fids = [conn.execute("INSERT INTO fournisseurs(nom,specialite,contact,telephone) VALUES (?,?,?,?)",
                         f[:4]).lastrowid for f in FOURNISSEURS]

    sites = [
        dict(nom="Résidence Les Palmiers R+3", loc="Zone résidentielle Est", typ="Bâtiment",
             debut=today - timedelta(days=100), fin=today + timedelta(days=200), avanc=44.0, chef=chef1, facteur=1.0),
        dict(nom="Route de désenclavement — tronçon B", loc="Axe nord", typ="Route",
             debut=today - timedelta(days=80), fin=today + timedelta(days=160), avanc=28.0, chef=chef2, facteur=1.3),
        dict(nom="Station de traitement d'eau", loc="Périmètre industriel", typ="Hydraulique",
             debut=today - timedelta(days=50), fin=today + timedelta(days=220), avanc=17.0, chef=chef2, facteur=0.95),
    ]
    mat_ids: dict[tuple[int, str], int] = {}
    for si, s in enumerate(sites):
        cid = conn.execute("INSERT INTO chantiers(nom,localisation,type_ouvrage,date_debut,date_fin_prevue,avancement_pct) "
                           "VALUES (?,?,?,?,?,?)", (s["nom"], s["loc"], s["typ"], iso(s["debut"]), iso(s["fin"]), s["avanc"])).lastrowid
        s["id"] = cid
        for u in {moa, ing, s["chef"]}:
            conn.execute("INSERT INTO affectations VALUES (?,?)", (u, cid))
        total_j = (s["fin"] - s["debut"]).days
        for (nom, cat, unite, prix, seuil, besoin, delai, fi) in MATERIAUX[si]:
            mid = conn.execute("INSERT INTO materiaux(chantier_id,nom,categorie,unite,prix_unitaire,seuil_mini,besoin_prevu,"
                               "delai_appro_jours) VALUES (?,?,?,?,?,?,?,?)", (cid, nom, cat, unite, prix, seuil, besoin, delai)).lastrowid
            mat_ids[(si, nom)] = mid
            rate = besoin / total_j * 7 / 6 * s["facteur"]
            fid = fids[fi]
            _, _, _, _, retard_max, p_part, p_nc = FOURNISSEURS[fi]
            stock, pending, d = 0.0, None, s["debut"]
            while d <= today:
                if pending is None and d == s["debut"]:
                    pending = _commande(conn, rnd, cid, fid, d - timedelta(days=delai), d, seuil * 2.5, mid, prix,
                                        s["chef"], ing, moa, 0)
                if pending and pending["arrivee"] <= d:
                    stock += _livraison(conn, rnd, pending, d, mid, s["chef"], ing, p_part, p_nc, today)
                    pending = None
                if d.weekday() != 6:
                    sortie = min(stock, rate * rnd.uniform(0.6, 1.4))
                    if sortie > 0.001:
                        conn.execute("INSERT INTO mouvements(chantier_id,materiau_id,date,type,quantite,zone,auteur_id) "
                                     "VALUES (?,?,?,'SORTIE',?,?,?)",
                                     (cid, mid, iso(d), -round(sortie, 2), rnd.choice(ZONES[si]), s["chef"]))
                        stock -= round(sortie, 2)
                if pending is None and stock < seuil:
                    qte = math.ceil(rate * (delai + 10))
                    pending = _commande(conn, rnd, cid, fid, d, d + timedelta(days=delai), qte, mid, prix, s["chef"], ing,
                                        moa, rnd.randint(0, retard_max))
                d += timedelta(days=1)

    # Demandes en attente (workflow d'approbation)
    s0, s2 = sites[0], sites[2]
    fo = fids[0]
    cmd = conn.execute("INSERT INTO commandes(chantier_id,fournisseur_id,date_demande,date_livraison_prevue,statut,demandeur_id,note) "
                       "VALUES (?,?,?,?, 'Demandée', ?, ?)", (s0["id"], fo, iso(today), iso(today + timedelta(days=5)),
                                                              chef1, "Coulage de la dalle R+2")).lastrowid
    for nom, q in (("Ciment CPJ 42,5", 30), ("Acier HA12", 8)):
        mid = mat_ids[(0, nom)]
        px = conn.execute("SELECT prix_unitaire FROM materiaux WHERE id=?", (mid,)).fetchone()[0]
        conn.execute("INSERT INTO commande_lignes(commande_id,materiau_id,quantite,prix_unitaire) VALUES (?,?,?,?)", (cmd, mid, q, px))
    cmd = conn.execute("INSERT INTO commandes(chantier_id,fournisseur_id,date_demande,date_livraison_prevue,statut,demandeur_id) "
                       "VALUES (?,?,?,?, 'Demandée', ?)", (s2["id"], fids[2], iso(today), iso(today + timedelta(days=4)), chef2)).lastrowid
    mid = mat_ids[(2, "Sable de rivière")]
    conn.execute("INSERT INTO commande_lignes(commande_id,materiau_id,quantite,prix_unitaire) VALUES (?,?,?,?)", (cmd, mid, 60, 12_000))

    _seed_engins(conn, rnd, sites, chef1, chef2, ing)

    conn.execute("INSERT INTO messages(sender_id,recipient_id,chantier_id,subject,body,created_at,important) VALUES (?,?,?,?,?,?,1)",
                 (moa, ing, sites[1]["id"], None, "Les ruptures de bitume sur le tronçon B coûtent des jours. "
                  "Pouvez-vous réviser le seuil et le fournisseur ?", datetime.now().isoformat(timespec="seconds")))
    conn.execute("INSERT INTO messages(sender_id,recipient_id,chantier_id,subject,body,created_at) VALUES (?,NULL,?,?,?,?)",
                 (chef2, sites[1]["id"], None, "Livraison de buses prévue demain matin : prévoir le chargeur.",
                  datetime.now().isoformat(timespec="seconds")))


def _commande(conn, rnd, cid, fid, d_cmd, prevue, qte, mid, prix, chef, ing, moa, retard) -> dict:
    total = qte * prix
    decideur = ing if total <= config.SEUIL_APPRO_INGENIEUR else moa
    cmd = conn.execute(
        "INSERT INTO commandes(chantier_id,fournisseur_id,date_demande,date_livraison_prevue,statut,demandeur_id,"
        "decideur_id,date_decision) VALUES (?,?,?,?, 'Approuvée', ?, ?, ?)",
        (cid, fid, d_cmd.isoformat(), prevue.isoformat(), chef, decideur, d_cmd.isoformat())).lastrowid
    ligne = conn.execute("INSERT INTO commande_lignes(commande_id,materiau_id,quantite,prix_unitaire) VALUES (?,?,?,?)",
                         (cmd, mid, qte, prix)).lastrowid
    return {"cmd": cmd, "ligne": ligne, "qte": qte, "arrivee": prevue + timedelta(days=retard), "cid": cid}


def _livraison(conn, rnd, p, d, mid, chef, ing, p_part, p_nc, today) -> float:
    """Crée la réception ; retourne la quantité ajoutée au stock simulé (0 si en attente de contrôle)."""
    recue = round(p["qte"] * (rnd.uniform(0.7, 0.95) if rnd.random() < p_part else 1.0), 2)
    en_attente = (today - d).days <= 1
    nc = (not en_attente) and rnd.random() < p_nc
    statut = "À contrôler" if en_attente else ("Non conforme" if nc else "Conforme")
    rid = conn.execute(
        "INSERT INTO receptions(commande_id,date,bon_livraison,receptionnaire_id,controle_statut,controle_par,controle_date,"
        "observations_controle) VALUES (?,?,?,?,?,?,?,?)",
        (p["cmd"], d.isoformat(), f"BL-{p['cmd']:05d}", chef, statut, None if en_attente else ing,
         None if en_attente else d.isoformat(), "Béton/granulats hors spécification." if nc else None)).lastrowid
    accepte = None if en_attente else (0.0 if nc else recue)
    conn.execute("INSERT INTO reception_lignes(reception_id,ligne_id,materiau_id,qte_recue,qte_acceptee) VALUES (?,?,?,?,?)",
                 (rid, p["ligne"], mid, recue, accepte))
    if accepte:
        conn.execute("INSERT INTO mouvements(chantier_id,materiau_id,date,type,quantite,note,ref_reception_id,auteur_id) "
                     "VALUES (?,?,?,'ENTREE',?,?,?,?)", (p["cid"], mid, d.isoformat(), accepte, f"BL-{p['cmd']:05d}", rid, ing))
    if en_attente:
        net = recue
    else:
        net = accepte or 0.0
    statut_cmd = "Livrée" if net >= p["qte"] * 0.999 else ("Livrée partiellement" if net > 0 else "Approuvée")
    conn.execute("UPDATE commandes SET statut=? WHERE id=?", (statut_cmd, p["cmd"]))
    return accepte or 0.0


def _seed_engins(conn, rnd, sites, chef1, chef2, ing) -> None:
    parc = {
        0: [("Grue mobile 40 t", "Grue mobile", "GM-401", 300, 35_000), ("Groupe électrogène 60 kVA", "Groupe électrogène", "GE-060", 250, 6_000),
            ("Chargeuse sur pneus", "Chargeuse", "CH-210", 250, 28_000)],
        1: [("Pelle hydraulique 22 t", "Pelle hydraulique", "PH-322", 250, 40_000), ("Compacteur vibrant", "Compacteur", "CV-112", 250, 25_000),
            ("Camion benne 20 t", "Camion benne", "CB-720", 300, 30_000), ("Niveleuse", "Niveleuse", "NV-050", 300, 38_000)],
        2: [("Pelle hydraulique 14 t", "Pelle hydraulique", "PH-214", 250, 32_000), ("Camion benne 10 t", "Camion benne", "CB-410", 300, 22_000)],
    }
    today = date.today()
    for si, s in enumerate(sites):
        for (nom, typ, immat, interv, cout_h) in parc[si]:
            comp = rnd.uniform(800, 2500)
            derniere = comp - rnd.uniform(0, interv * 0.6)
            eid = conn.execute("INSERT INTO engins(chantier_id,nom,type_engin,immatriculation,compteur_h,intervalle_maintenance_h,"
                               "derniere_maintenance_h,cout_horaire) VALUES (?,?,?,?,?,?,?,?)",
                               (s["id"], nom, typ, immat, comp, interv, derniere, cout_h)).lastrowid
            d = s["debut"]
            while d <= today:
                if d.weekday() != 6 and rnd.random() < 0.85:
                    h = round(rnd.uniform(4, 9), 1)
                    comp += h
                    conn.execute("INSERT INTO engin_journal(engin_id,date,type,heures,auteur_id) VALUES (?,?, 'Utilisation', ?, ?)",
                                 (eid, d.isoformat(), h, s["chef"]))
                    if rnd.random() < 0.012:
                        arret = rnd.choice([10, 18, 30, 48, 72])
                        conn.execute("INSERT INTO engin_journal(engin_id,date,type,duree_arret_h,cout,description,resolu,auteur_id) "
                                     "VALUES (?,?, 'Panne', ?, ?, 'Panne hydraulique / mécanique', 1, ?)",
                                     (eid, d.isoformat(), arret, round(arret * rnd.uniform(4_000, 9_000)), s["chef"]))
                    if comp - derniere >= interv and nom != "Niveleuse" and rnd.random() < 0.85:
                        conn.execute("INSERT INTO engin_journal(engin_id,date,type,duree_arret_h,cout,description,auteur_id) "
                                     "VALUES (?,?, 'Maintenance', 8, ?, 'Vidange, filtres, graissage', ?)",
                                     (eid, d.isoformat(), round(rnd.uniform(150_000, 400_000)), ing))
                        derniere = comp
                d += timedelta(days=1)
            statut = "Disponible"
            if nom == "Pelle hydraulique 14 t":
                statut = "En panne"
                conn.execute("INSERT INTO engin_journal(engin_id,date,type,description,resolu,auteur_id) "
                             "VALUES (?,?, 'Panne', 'Fuite sur le vérin de flèche', 0, ?)",
                             (eid, (today - timedelta(days=1)).isoformat(), chef2))
            conn.execute("UPDATE engins SET compteur_h=?, derniere_maintenance_h=?, statut=? WHERE id=?", (comp, derniere, statut, eid))


def bootstrap() -> None:
    """Crée le schéma et, si la base est vide, les données de démonstration ou le compte admin initial."""
    db.init_schema()
    if db.scalar("SELECT COUNT(*) FROM users"):
        return
    with db.transaction() as conn:
        if config.DEMO:
            seed(conn)
        else:
            pwd = os.environ.get("LOGICHANTIER_ADMIN_PASSWORD") or secrets.token_urlsafe(10)
            conn.execute("INSERT INTO users(username,password_hash,full_name,role,must_change) VALUES ('admin',?,?, 'admin',1)",
                         (hash_password(pwd), "Administrateur"))
            print(f"[LogiChantier] Compte initial : admin / {pwd}  (à changer à la première connexion)")
