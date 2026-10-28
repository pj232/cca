# -*- coding: utf-8 -*-
"""Règles métier de LogiChantier (sans dépendance à Streamlit : testable seul).

Chaque opération d'écriture vérifie elle-même le droit ET l'accès au chantier :
l'interface n'est jamais la seule barrière de sécurité.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from . import config, db
from .config import PERMISSIONS


class AccessError(PermissionError):
    pass


# ----------------------------------------------------------------------------
# Droits et périmètre
# ----------------------------------------------------------------------------
def has(user: dict, perm: str) -> bool:
    return perm in PERMISSIONS.get(user["role"], set())


def require(user: dict, perm: str) -> None:
    if not has(user, perm):
        raise AccessError("Action non autorisée pour votre profil.")


def user_chantier_ids(user: dict) -> list[int]:
    if user["role"] == "admin":
        return [int(x) for x in db.col("SELECT id FROM chantiers")]
    return [int(x) for x in db.col("SELECT chantier_id FROM affectations WHERE user_id=?",
                                   (int(user["id"]),))]


def require_chantier(user: dict, chantier_id: int) -> None:
    if int(chantier_id) not in user_chantier_ids(user):
        raise AccessError("Accès refusé à ce chantier.")


def chantiers_visibles(user: dict) -> pd.DataFrame:
    ids = user_chantier_ids(user)
    if not ids:
        return pd.DataFrame(columns=["id", "nom"])
    return db.df(f"SELECT * FROM chantiers WHERE id IN ({_in(ids)}) ORDER BY nom", tuple(ids))


def _in(ids) -> str:
    return ",".join("?" * len(ids))


def _iso(d) -> str:
    return d.isoformat() if isinstance(d, (date, datetime)) else str(d)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def audit(user_id: int | None, action: str, detail: str = "", conn=None) -> None:
    sql = "INSERT INTO audit_log(user_id, action, detail, ts) VALUES (?,?,?,?)"
    params = (user_id, action, detail, _now())
    if conn is not None:
        conn.execute(sql, params)
    else:
        db.execute(sql, params)


def _equipe(conn, chantier_id: int, roles: tuple[str, ...]) -> list[int]:
    ph = ",".join("?" * len(roles))
    rows = conn.execute(
        f"SELECT u.id FROM users u JOIN affectations a ON a.user_id=u.id "
        f"WHERE a.chantier_id=? AND u.active=1 AND u.role IN ({ph})", (chantier_id, *roles)).fetchall()
    return [int(r[0]) for r in rows]


def _notifier(conn, sender_id: int, destinataires: list[int], sujet: str, corps: str,
              chantier_id: int | None = None) -> None:
    for rid in set(destinataires):
        if rid != sender_id:
            conn.execute(
                "INSERT INTO messages(sender_id,recipient_id,chantier_id,subject,body,created_at,important) "
                "VALUES (?,?,?,?,?,?,1)", (sender_id, rid, chantier_id, sujet, corps, _now()))


# ----------------------------------------------------------------------------
# Stocks
# ----------------------------------------------------------------------------
STATUT_STOCK_ICONS = {"Rupture": "🔴", "Sous le seuil": "🟠", "À commander": "🟡", "OK": "🟢"}


def stock_table(ids: list[int], today: date | None = None) -> pd.DataFrame:
    """État du stock par matériau, avec couverture, statut, suggestion de commande et consommation."""
    ids = [int(i) for i in ids]
    if not ids:
        return pd.DataFrame()
    today = today or date.today()
    ph = _in(ids)
    base = db.df(
        f"""SELECT m.id, m.chantier_id, c.nom AS chantier, m.nom, m.categorie, m.unite, m.prix_unitaire,
                   m.seuil_mini, m.besoin_prevu, m.delai_appro_jours, c.avancement_pct,
                   COALESCE(SUM(mv.quantite),0) AS stock,
                   COALESCE(SUM(CASE WHEN mv.type='SORTIE' THEN -mv.quantite ELSE 0 END),0) AS consomme
            FROM materiaux m JOIN chantiers c ON c.id=m.chantier_id
            LEFT JOIN mouvements mv ON mv.materiau_id=m.id
            WHERE m.chantier_id IN ({ph}) AND m.actif=1
            GROUP BY m.id ORDER BY c.nom, m.nom""", tuple(ids))
    if base.empty:
        return base
    since = (today - timedelta(days=config.FENETRE_CONSO_JOURS)).isoformat()
    conso = db.df(f"SELECT materiau_id, SUM(-quantite) AS q FROM mouvements "
                  f"WHERE type='SORTIE' AND date>=? AND chantier_id IN ({ph}) GROUP BY materiau_id",
                  (since, *ids))
    attente = db.df(f"SELECT cl.materiau_id, SUM(cl.quantite) AS q FROM commande_lignes cl "
                    f"JOIN commandes c ON c.id=cl.commande_id "
                    f"WHERE c.statut IN ('Demandée','Approuvée') AND c.chantier_id IN ({ph}) "
                    f"GROUP BY cl.materiau_id", tuple(ids))
    conso_map = dict(zip(conso["materiau_id"].astype(int), conso["q"].astype(float)))
    att_map = dict(zip(attente["materiau_id"].astype(int), attente["q"].astype(float)))
    base["conso_jour"] = base["id"].map(conso_map).fillna(0.0).astype(float) / config.FENETRE_CONSO_JOURS
    base["en_commande"] = base["id"].map(att_map).fillna(0.0).astype(float)
    jour = base["conso_jour"].where(base["conso_jour"] > 0)
    base["couverture_j"] = base["stock"] / jour
    delai = base["delai_appro_jours"]
    base["statut"] = np.select(
        [base["stock"] <= 0, base["stock"] < base["seuil_mini"],
         (base["conso_jour"] > 0) & (base["couverture_j"] < delai)],
        ["Rupture", "Sous le seuil", "À commander"], default="OK")
    cible = np.maximum(base["conso_jour"] * (delai + config.MARGE_SECURITE_JOURS), base["seuil_mini"])
    base["suggere"] = np.ceil(np.maximum(cible - base["stock"] - base["en_commande"], 0)).astype(float)
    base["valeur_stock"] = base["stock"] * base["prix_unitaire"]
    base["conso_pct"] = np.where(base["besoin_prevu"] > 0, base["consomme"] / base["besoin_prevu"] * 100, np.nan)
    base["ecart_conso"] = base["conso_pct"] - base["avancement_pct"]
    base["ratio_conso"] = np.where(base["avancement_pct"] > 0, base["conso_pct"] / base["avancement_pct"], np.nan)
    base["icone"] = base["statut"].map(STATUT_STOCK_ICONS)
    return base


def stock_actuel(materiau_id: int) -> float:
    return float(db.scalar("SELECT COALESCE(SUM(quantite),0) FROM mouvements WHERE materiau_id=?",
                           (int(materiau_id),)) or 0)


def historique_stock(materiau_id: int) -> pd.DataFrame:
    mv = db.df("SELECT date, type, quantite, zone, note FROM mouvements WHERE materiau_id=? ORDER BY date, id",
               (int(materiau_id),))
    if mv.empty:
        return mv
    mv["date"] = pd.to_datetime(mv["date"])
    mv["stock"] = mv["quantite"].cumsum()
    return mv


def _materiau_autorise(user: dict, materiau_id: int) -> dict:
    mat = db.one("SELECT * FROM materiaux WHERE id=? AND actif=1", (int(materiau_id),))
    if not mat:
        raise ValueError("Matériau introuvable.")
    require_chantier(user, mat["chantier_id"])
    return mat


def enregistrer_sortie(user: dict, materiau_id: int, quantite: float, jour: date, zone: str, note: str = "") -> None:
    require(user, "sortie_ecrire")
    mat = _materiau_autorise(user, materiau_id)
    q = float(quantite)
    if q <= 0:
        raise ValueError("La quantité doit être supérieure à 0.")
    if jour > date.today():
        raise ValueError("La date ne peut pas être dans le futur.")
    if not zone.strip():
        raise ValueError("Précisez la zone ou la tâche (ex. Fondations, Dalle R+1).")
    with db.transaction() as conn:
        stock = conn.execute("SELECT COALESCE(SUM(quantite),0) FROM mouvements WHERE materiau_id=?",
                             (mat["id"],)).fetchone()[0]
        if q > stock + 1e-9:
            raise ValueError(f"Stock insuffisant : {stock:g} {mat['unite']} disponible(s).")
        conn.execute("INSERT INTO mouvements(chantier_id,materiau_id,date,type,quantite,zone,note,auteur_id) "
                     "VALUES (?,?,?,'SORTIE',?,?,?,?)",
                     (mat["chantier_id"], mat["id"], _iso(jour), -q, zone.strip(), note.strip(), int(user["id"])))
        restant = stock - q
        if restant < mat["seuil_mini"]:
            _notifier(conn, int(user["id"]), _equipe(conn, mat["chantier_id"], ("ingenieur",)),
                      f"Stock bas : {mat['nom']}",
                      f"Après sortie de {q:g} {mat['unite']}, il reste {restant:g} {mat['unite']} "
                      f"(seuil minimal {mat['seuil_mini']:g}).", mat["chantier_id"])
        audit(int(user["id"]), "sortie_stock", f"{mat['nom']} -{q:g} {mat['unite']} ({zone.strip()})", conn)


def ajuster_stock(user: dict, materiau_id: int, stock_compte: float, motif: str) -> float:
    """Aligne le stock théorique sur le stock compté. Retourne l'écart appliqué."""
    require(user, "inventaire_ajuster")
    mat = _materiau_autorise(user, materiau_id)
    if stock_compte < 0:
        raise ValueError("Le stock compté ne peut pas être négatif.")
    if not motif.strip():
        raise ValueError("Le motif de l'ajustement est obligatoire.")
    with db.transaction() as conn:
        actuel = conn.execute("SELECT COALESCE(SUM(quantite),0) FROM mouvements WHERE materiau_id=?",
                              (mat["id"],)).fetchone()[0]
        delta = float(stock_compte) - float(actuel)
        if abs(delta) < 1e-9:
            return 0.0
        conn.execute("INSERT INTO mouvements(chantier_id,materiau_id,date,type,quantite,note,auteur_id) "
                     "VALUES (?,?,?,'AJUSTEMENT',?,?,?)",
                     (mat["chantier_id"], mat["id"], _iso(date.today()), delta, motif.strip(), int(user["id"])))
        audit(int(user["id"]), "inventaire", f"{mat['nom']} : {actuel:g} -> {stock_compte:g} ({motif.strip()})", conn)
    return delta


def creer_materiau(user: dict, chantier_id: int, nom: str, categorie: str, unite: str, prix: float,
                   seuil: float, besoin: float, delai: int) -> int:
    require(user, "materiau_gerer")
    require_chantier(user, chantier_id)
    if not nom.strip():
        raise ValueError("Le nom du matériau est obligatoire.")
    if min(prix, seuil, besoin) < 0 or delai < 0:
        raise ValueError("Les valeurs ne peuvent pas être négatives.")
    try:
        mid = db.execute(
            "INSERT INTO materiaux(chantier_id,nom,categorie,unite,prix_unitaire,seuil_mini,besoin_prevu,"
            "delai_appro_jours) VALUES (?,?,?,?,?,?,?,?)",
            (int(chantier_id), nom.strip(), categorie, unite, float(prix), float(seuil), float(besoin), int(delai)))
    except Exception as exc:  # contrainte d'unicité
        if "UNIQUE" in str(exc):
            raise ValueError("Ce matériau existe déjà sur ce chantier.") from exc
        raise
    audit(int(user["id"]), "materiau_cree", f"{nom.strip()} (chantier {chantier_id})")
    return mid


def modifier_materiau(user: dict, materiau_id: int, prix: float, seuil: float, besoin: float,
                      delai: int, actif: bool = True) -> None:
    require(user, "materiau_gerer")
    mat = _materiau_autorise(user, materiau_id)
    if min(prix, seuil, besoin) < 0 or delai < 0:
        raise ValueError("Les valeurs ne peuvent pas être négatives.")
    db.execute("UPDATE materiaux SET prix_unitaire=?, seuil_mini=?, besoin_prevu=?, delai_appro_jours=?, actif=? "
               "WHERE id=?", (float(prix), float(seuil), float(besoin), int(delai), 1 if actif else 0, mat["id"]))
    audit(int(user["id"]), "materiau_modifie", f"{mat['nom']}")


# ----------------------------------------------------------------------------
# Commandes
# ----------------------------------------------------------------------------
def montant_commande(commande_id: int) -> float:
    return float(db.scalar("SELECT COALESCE(SUM(quantite*prix_unitaire),0) FROM commande_lignes WHERE commande_id=?",
                           (int(commande_id),)) or 0)


def creer_commande(user: dict, chantier_id: int, fournisseur_id: int, livraison_prevue: date,
                   lignes: list[tuple[int, float]], note: str = "") -> int:
    require(user, "commande_demander")
    require_chantier(user, chantier_id)
    lignes = [(int(m), float(q)) for m, q in lignes if q and float(q) > 0]
    if not lignes:
        raise ValueError("Ajoutez au moins une ligne avec une quantité positive.")
    if livraison_prevue < date.today():
        raise ValueError("La date de livraison prévue est dans le passé.")
    if not db.scalar("SELECT COUNT(*) FROM fournisseurs WHERE id=? AND actif=1", (int(fournisseur_id),)):
        raise ValueError("Fournisseur inconnu ou inactif.")
    with db.transaction() as conn:
        cid = conn.execute(
            "INSERT INTO commandes(chantier_id,fournisseur_id,date_demande,date_livraison_prevue,statut,"
            "demandeur_id,note) VALUES (?,?,?,?, 'Demandée', ?, ?)",
            (int(chantier_id), int(fournisseur_id), _iso(date.today()), _iso(livraison_prevue),
             int(user["id"]), note.strip())).lastrowid
        total = 0.0
        for mid, q in lignes:
            m = conn.execute("SELECT prix_unitaire, chantier_id, nom FROM materiaux WHERE id=? AND actif=1",
                             (mid,)).fetchone()
            if not m or int(m["chantier_id"]) != int(chantier_id):
                raise ValueError("Matériau invalide pour ce chantier.")
            conn.execute("INSERT INTO commande_lignes(commande_id,materiau_id,quantite,prix_unitaire) VALUES (?,?,?,?)",
                         (cid, mid, q, m["prix_unitaire"]))
            total += q * m["prix_unitaire"]
        approbateurs = _equipe(conn, int(chantier_id), ("ingenieur", "maitre_ouvrage"))
        _notifier(conn, int(user["id"]), approbateurs, f"Demande de commande n°{cid}",
                  f"{len(lignes)} ligne(s), montant estimé {total:,.0f} {config.DEVISE}. À examiner.".replace(",", " "),
                  int(chantier_id))
        audit(int(user["id"]), "commande_demandee", f"n°{cid}, {total:,.0f} {config.DEVISE}".replace(",", " "), conn)
    return cid


def decider_commande(user: dict, commande_id: int, approuver: bool, motif: str = "") -> None:
    cmd = db.one("SELECT * FROM commandes WHERE id=?", (int(commande_id),))
    if not cmd:
        raise ValueError("Commande introuvable.")
    require_chantier(user, cmd["chantier_id"])
    if cmd["statut"] != "Demandée":
        raise ValueError("Cette commande n'est plus en attente de décision.")
    peut_tout = has(user, "commande_approuver_tout")
    peut_limite = has(user, "commande_approuver")
    if not (peut_tout or peut_limite):
        raise AccessError("Votre profil ne peut pas statuer sur les commandes.")
    total = montant_commande(commande_id)
    if approuver:
        if cmd["demandeur_id"] == int(user["id"]):
            raise AccessError("Vous ne pouvez pas approuver votre propre demande.")
        if not peut_tout and total > config.SEUIL_APPRO_INGENIEUR:
            raise AccessError(f"Montant supérieur à {config.SEUIL_APPRO_INGENIEUR:,.0f} {config.DEVISE} : "
                              f"l'approbation du maître d'ouvrage est requise.".replace(",", " "))
    elif not motif.strip():
        raise ValueError("Indiquez le motif du refus.")
    statut = "Approuvée" if approuver else "Refusée"
    with db.transaction() as conn:
        conn.execute("UPDATE commandes SET statut=?, decideur_id=?, date_decision=?, motif_refus=? WHERE id=?",
                     (statut, int(user["id"]), _iso(date.today()), None if approuver else motif.strip(), cmd["id"]))
        texte = f"Votre demande n°{cmd['id']} a été {statut.lower()}" + ("." if approuver else f" : {motif.strip()}")
        _notifier(conn, int(user["id"]), [cmd["demandeur_id"]], f"Commande n°{cmd['id']} {statut.lower()}",
                  texte, cmd["chantier_id"])
        audit(int(user["id"]), f"commande_{statut.lower()}", f"n°{cmd['id']} ({total:,.0f})".replace(",", " "), conn)


def annuler_commande(user: dict, commande_id: int) -> None:
    cmd = db.one("SELECT * FROM commandes WHERE id=?", (int(commande_id),))
    if not cmd:
        raise ValueError("Commande introuvable.")
    require_chantier(user, cmd["chantier_id"])
    if cmd["statut"] != "Demandée":
        raise ValueError("Seule une demande en attente peut être annulée.")
    if cmd["demandeur_id"] != int(user["id"]) and not has(user, "commande_approuver_tout"):
        raise AccessError("Seul le demandeur peut annuler sa demande.")
    db.execute("UPDATE commandes SET statut='Annulée' WHERE id=?", (cmd["id"],))
    audit(int(user["id"]), "commande_annulee", f"n°{cmd['id']}")


def commandes_table(ids: list[int], statuts: list[str] | None = None) -> pd.DataFrame:
    ids = [int(i) for i in ids]
    if not ids:
        return pd.DataFrame()
    sql = f"""SELECT c.id, c.chantier_id, ch.nom AS chantier, f.nom AS fournisseur, c.date_demande,
                     c.date_livraison_prevue, c.statut, u.full_name AS demandeur, c.motif_refus,
                     COALESCE(SUM(cl.quantite*cl.prix_unitaire),0) AS montant, COUNT(cl.id) AS nb_lignes
              FROM commandes c JOIN chantiers ch ON ch.id=c.chantier_id JOIN fournisseurs f ON f.id=c.fournisseur_id
              JOIN users u ON u.id=c.demandeur_id LEFT JOIN commande_lignes cl ON cl.commande_id=c.id
              WHERE c.chantier_id IN ({_in(ids)})"""
    params: list = list(ids)
    if statuts:
        sql += f" AND c.statut IN ({_in(statuts)})"
        params += list(statuts)
    sql += " GROUP BY c.id ORDER BY c.date_demande DESC, c.id DESC"
    out = db.df(sql, tuple(params))
    if not out.empty:
        prevue = pd.to_datetime(out["date_livraison_prevue"])
        attendu = out["statut"].isin(["Approuvée", "Livrée partiellement"])
        retard = (pd.Timestamp(date.today()) - prevue).dt.days
        out["retard_j"] = np.where(attendu & (retard > 0), retard, 0)
    return out


def lignes_commande(commande_id: int) -> pd.DataFrame:
    return db.df(
        """SELECT cl.id, m.nom AS materiau, m.unite, cl.quantite, cl.prix_unitaire,
                  COALESCE((SELECT SUM(CASE WHEN r.controle_statut='À contrôler' THEN rl.qte_recue
                                            ELSE COALESCE(rl.qte_acceptee,0) END)
                            FROM reception_lignes rl JOIN receptions r ON r.id=rl.reception_id
                            WHERE rl.ligne_id=cl.id),0) AS recu_net
           FROM commande_lignes cl JOIN materiaux m ON m.id=cl.materiau_id WHERE cl.commande_id=?""",
        (int(commande_id),))


def _recalcul_statut_commande(conn, commande_id: int) -> None:
    lignes = conn.execute(
        """SELECT cl.quantite,
                  COALESCE((SELECT SUM(CASE WHEN r.controle_statut='À contrôler' THEN rl.qte_recue
                                            ELSE COALESCE(rl.qte_acceptee,0) END)
                            FROM reception_lignes rl JOIN receptions r ON r.id=rl.reception_id
                            WHERE rl.ligne_id=cl.id),0) AS net
           FROM commande_lignes cl WHERE cl.commande_id=?""", (commande_id,)).fetchall()
    if lignes and all(l["net"] >= l["quantite"] * 0.999 for l in lignes):
        statut = "Livrée"
    elif any(l["net"] > 0 for l in lignes):
        statut = "Livrée partiellement"
    else:
        statut = "Approuvée"
    conn.execute("UPDATE commandes SET statut=? WHERE id=?", (statut, commande_id))


# ----------------------------------------------------------------------------
# Réceptions et contrôle qualité
# ----------------------------------------------------------------------------
def declarer_reception(user: dict, commande_id: int, jour: date, bon_livraison: str,
                       recues: dict[int, float], observations: str = "") -> int:
    require(user, "reception_declarer")
    cmd = db.one("SELECT * FROM commandes WHERE id=?", (int(commande_id),))
    if not cmd:
        raise ValueError("Commande introuvable.")
    require_chantier(user, cmd["chantier_id"])
    if cmd["statut"] not in ("Approuvée", "Livrée partiellement"):
        raise ValueError("Cette commande n'attend pas de livraison.")
    if jour > date.today():
        raise ValueError("La date de réception ne peut pas être dans le futur.")
    if jour < date.fromisoformat(cmd["date_demande"]):
        raise ValueError("La réception ne peut pas précéder la demande de commande.")
    if not bon_livraison.strip():
        raise ValueError("Le numéro de bon de livraison est obligatoire.")
    recues = {int(k): float(v) for k, v in recues.items() if v and float(v) > 0}
    if not recues:
        raise ValueError("Saisissez au moins une quantité reçue.")
    lignes = {int(r["id"]): r for r in lignes_commande(commande_id).to_dict("records")}
    with db.transaction() as conn:
        rid = conn.execute(
            "INSERT INTO receptions(commande_id,date,bon_livraison,receptionnaire_id,observations) VALUES (?,?,?,?,?)",
            (cmd["id"], _iso(jour), bon_livraison.strip(), int(user["id"]), observations.strip())).lastrowid
        for lid, q in recues.items():
            if lid not in lignes:
                raise ValueError("Ligne de commande invalide.")
            l = lignes[lid]
            if l["recu_net"] + q > l["quantite"] * 1.10 + 1e-9:
                raise ValueError(f"Quantité trop élevée pour « {l['materiau']} » : "
                                 f"{l['quantite'] - l['recu_net']:g} {l['unite']} restant(s) à livrer (+10 % tolérés).")
            mid = conn.execute("SELECT materiau_id FROM commande_lignes WHERE id=?", (lid,)).fetchone()[0]
            conn.execute("INSERT INTO reception_lignes(reception_id,ligne_id,materiau_id,qte_recue) VALUES (?,?,?,?)",
                         (rid, lid, mid, q))
        _recalcul_statut_commande(conn, cmd["id"])
        _notifier(conn, int(user["id"]), _equipe(conn, cmd["chantier_id"], ("ingenieur",)),
                  f"Livraison à contrôler (commande n°{cmd['id']})",
                  f"Bon de livraison {bon_livraison.strip()} : contrôle qualité requis avant mise en stock.",
                  cmd["chantier_id"])
        audit(int(user["id"]), "reception_declaree", f"commande n°{cmd['id']}, BL {bon_livraison.strip()}", conn)
    return rid


def receptions_table(ids: list[int], statuts: list[str] | None = None) -> pd.DataFrame:
    ids = [int(i) for i in ids]
    if not ids:
        return pd.DataFrame()
    sql = f"""SELECT r.id, r.commande_id, ch.nom AS chantier, f.nom AS fournisseur, r.date, r.bon_livraison,
                     r.receptionnaire_id, u.full_name AS receptionnaire, r.controle_statut, cu.full_name AS controleur,
                     r.controle_date, r.observations, r.observations_controle,
                     (SELECT SUM(qte_recue) FROM reception_lignes WHERE reception_id=r.id) AS qte_recue,
                     (SELECT SUM(qte_acceptee) FROM reception_lignes WHERE reception_id=r.id) AS qte_acceptee
              FROM receptions r JOIN commandes c ON c.id=r.commande_id JOIN chantiers ch ON ch.id=c.chantier_id
              JOIN fournisseurs f ON f.id=c.fournisseur_id JOIN users u ON u.id=r.receptionnaire_id
              LEFT JOIN users cu ON cu.id=r.controle_par WHERE c.chantier_id IN ({_in(ids)})"""
    params: list = list(ids)
    if statuts:
        sql += f" AND r.controle_statut IN ({_in(statuts)})"
        params += list(statuts)
    sql += " ORDER BY r.date DESC, r.id DESC"
    return db.df(sql, tuple(params))


def lignes_reception(reception_id: int) -> pd.DataFrame:
    return db.df("""SELECT rl.id, m.nom AS materiau, m.unite, rl.qte_recue, rl.qte_acceptee
                    FROM reception_lignes rl JOIN materiaux m ON m.id=rl.materiau_id WHERE rl.reception_id=?""",
                 (int(reception_id),))


def controler_reception(user: dict, reception_id: int, statut: str, acceptees: dict[int, float],
                        observations: str = "") -> None:
    require(user, "reception_controler")
    rec = db.one("SELECT r.*, c.chantier_id FROM receptions r JOIN commandes c ON c.id=r.commande_id WHERE r.id=?",
                 (int(reception_id),))
    if not rec:
        raise ValueError("Réception introuvable.")
    require_chantier(user, rec["chantier_id"])
    if rec["controle_statut"] != "À contrôler":
        raise ValueError("Cette réception a déjà été contrôlée.")
    if rec["receptionnaire_id"] == int(user["id"]):
        raise AccessError("Le contrôle doit être effectué par une autre personne que le réceptionnaire.")
    if statut not in ("Conforme", "Conforme avec réserves", "Non conforme"):
        raise ValueError("Statut de contrôle invalide.")
    if statut == "Non conforme" and not observations.strip():
        raise ValueError("Décrivez le défaut constaté (obligatoire pour une non-conformité).")
    with db.transaction() as conn:
        total_acc = 0.0
        for l in conn.execute("SELECT id, materiau_id, qte_recue FROM reception_lignes WHERE reception_id=?",
                              (rec["id"],)).fetchall():
            acc = 0.0 if statut == "Non conforme" else float(acceptees.get(int(l["id"]), l["qte_recue"]))
            if acc < 0 or acc > l["qte_recue"] + 1e-9:
                raise ValueError("La quantité acceptée doit être comprise entre 0 et la quantité reçue.")
            conn.execute("UPDATE reception_lignes SET qte_acceptee=? WHERE id=?", (acc, l["id"]))
            if acc > 0:
                conn.execute("INSERT INTO mouvements(chantier_id,materiau_id,date,type,quantite,note,ref_reception_id,"
                             "auteur_id) VALUES (?,?,?,'ENTREE',?,?,?,?)",
                             (rec["chantier_id"], l["materiau_id"], _iso(date.today()), acc,
                              f"BL {rec['bon_livraison']}", rec["id"], int(user["id"])))
            total_acc += acc
        conn.execute("UPDATE receptions SET controle_statut=?, controle_par=?, controle_date=?, "
                     "observations_controle=? WHERE id=?",
                     (statut, int(user["id"]), _iso(date.today()), observations.strip(), rec["id"]))
        _recalcul_statut_commande(conn, rec["commande_id"])
        cible = _equipe(conn, rec["chantier_id"], ("chef_chantier", "maitre_ouvrage")) if statut == "Non conforme" \
            else [rec["receptionnaire_id"]]
        _notifier(conn, int(user["id"]), cible, f"Livraison {statut.lower()} (commande n°{rec['commande_id']})",
                  f"BL {rec['bon_livraison']} : {statut.lower()}. {observations.strip()}".strip(), rec["chantier_id"])
        audit(int(user["id"]), "reception_controlee", f"n°{rec['id']} : {statut}", conn)


# ----------------------------------------------------------------------------
# Fournisseurs
# ----------------------------------------------------------------------------
def fournisseurs_table(actifs_seulement: bool = False) -> pd.DataFrame:
    sql = "SELECT id, nom, specialite, contact, telephone, actif FROM fournisseurs"
    if actifs_seulement:
        sql += " WHERE actif=1"
    return db.df(sql + " ORDER BY nom")


def creer_fournisseur(user: dict, nom: str, specialite: str, contact: str, telephone: str) -> int:
    require(user, "fournisseur_gerer")
    if not nom.strip():
        raise ValueError("Le nom du fournisseur est obligatoire.")
    try:
        fid = db.execute("INSERT INTO fournisseurs(nom,specialite,contact,telephone) VALUES (?,?,?,?)",
                         (nom.strip(), specialite.strip(), contact.strip(), telephone.strip()))
    except Exception as exc:
        if "UNIQUE" in str(exc):
            raise ValueError("Ce fournisseur existe déjà.") from exc
        raise
    audit(int(user["id"]), "fournisseur_cree", nom.strip())
    return fid


def basculer_fournisseur(user: dict, fournisseur_id: int, actif: bool) -> None:
    require(user, "fournisseur_gerer")
    db.execute("UPDATE fournisseurs SET actif=? WHERE id=?", (1 if actif else 0, int(fournisseur_id)))
    audit(int(user["id"]), "fournisseur_statut", f"id {fournisseur_id} -> {'actif' if actif else 'inactif'}")


def performance_fournisseurs(ids: list[int], d1: date | None = None, d2: date | None = None) -> pd.DataFrame:
    """Ponctualité, complétude et conformité des livraisons par fournisseur."""
    ids = [int(i) for i in ids]
    if not ids:
        return pd.DataFrame()
    sql = f"""SELECT f.id AS fid, f.nom AS fournisseur, c.id AS cid, c.date_demande, c.date_livraison_prevue AS prevue,
                     r.id AS rid, r.date AS date_reception, r.controle_statut, rl.ligne_id, rl.qte_recue,
                     COALESCE(rl.qte_acceptee,0) AS qte_acceptee, cl.quantite AS qte_cmd, cl.prix_unitaire
              FROM reception_lignes rl JOIN receptions r ON r.id=rl.reception_id
              JOIN commande_lignes cl ON cl.id=rl.ligne_id JOIN commandes c ON c.id=cl.commande_id
              JOIN fournisseurs f ON f.id=c.fournisseur_id
              WHERE c.chantier_id IN ({_in(ids)}) AND r.controle_statut<>'À contrôler'"""
    params: list = list(ids)
    if d1:
        sql += " AND r.date>=?"
        params.append(_iso(d1))
    if d2:
        sql += " AND r.date<=?"
        params.append(_iso(d2))
    rows = db.df(sql, tuple(params))
    if rows.empty:
        return pd.DataFrame()
    for c in ("date_demande", "prevue", "date_reception"):
        rows[c] = pd.to_datetime(rows[c])
    rec = rows.drop_duplicates("rid").copy()
    rec["retard"] = (rec["date_reception"] - rec["prevue"]).dt.days.clip(lower=0)
    rec["delai"] = (rec["date_reception"] - rec["date_demande"]).dt.days
    rec["nc"] = (rec["controle_statut"] == "Non conforme").astype(int)
    lig = rows.groupby(["fid", "ligne_id"]).agg(recu=("qte_recue", "sum"), cmd=("qte_cmd", "first")).reset_index()
    lig["complet"] = (lig["recu"] / lig["cmd"]).clip(upper=1)
    out = rec.groupby(["fid", "fournisseur"]).agg(
        receptions=("rid", "count"), commandes=("cid", "nunique"), retard_moyen_j=("retard", "mean"),
        delai_moyen_j=("delai", "mean"), lots_non_conformes=("nc", "sum")).reset_index()
    qty = rows.groupby("fid").agg(recu=("qte_recue", "sum"), accepte=("qte_acceptee", "sum")).reset_index()
    out = out.merge(qty, on="fid").merge(lig.groupby("fid")["complet"].mean().reset_index(), on="fid")
    out["conformite_pct"] = np.where(out["recu"] > 0, out["accepte"] / out["recu"] * 100, np.nan)
    out["completude_pct"] = out["complet"] * 100
    out["ponctualite_pct"] = (1 - (out["retard_moyen_j"] / 7).clip(upper=1)) * 100
    out["score"] = 0.4 * out["completude_pct"] + 0.4 * out["conformite_pct"] + 0.2 * out["ponctualite_pct"]
    return out.drop(columns=["fid", "recu", "accepte", "complet"]).sort_values("score", ascending=False).round(1)


# ----------------------------------------------------------------------------
# Engins et maintenance
# ----------------------------------------------------------------------------
def engins_table(ids: list[int]) -> pd.DataFrame:
    ids = [int(i) for i in ids]
    if not ids:
        return pd.DataFrame()
    out = db.df(f"SELECT e.*, c.nom AS chantier FROM engins e JOIN chantiers c ON c.id=e.chantier_id "
                f"WHERE e.actif=1 AND e.chantier_id IN ({_in(ids)}) ORDER BY c.nom, e.nom", tuple(ids))
    if out.empty:
        return out
    out["heures_restantes"] = out["intervalle_maintenance_h"] - (out["compteur_h"] - out["derniere_maintenance_h"])
    out["maintenance"] = np.select(
        [out["heures_restantes"] <= 0, out["heures_restantes"] <= 0.1 * out["intervalle_maintenance_h"]],
        ["En retard", "À planifier"], default="OK")
    return out


def _engin_autorise(user: dict, engin_id: int) -> dict:
    e = db.one("SELECT * FROM engins WHERE id=? AND actif=1", (int(engin_id),))
    if not e:
        raise ValueError("Engin introuvable.")
    require_chantier(user, e["chantier_id"])
    return e


def creer_engin(user: dict, chantier_id: int, nom: str, type_engin: str, immat: str, intervalle_h: float,
                compteur_h: float, cout_horaire: float) -> int:
    require(user, "engin_gerer")
    require_chantier(user, chantier_id)
    if not nom.strip():
        raise ValueError("Le nom de l'engin est obligatoire.")
    if intervalle_h <= 0 or compteur_h < 0 or cout_horaire < 0:
        raise ValueError("Valeurs invalides (intervalle > 0, compteur et coût ≥ 0).")
    eid = db.execute("INSERT INTO engins(chantier_id,nom,type_engin,immatriculation,compteur_h,"
                     "intervalle_maintenance_h,derniere_maintenance_h,cout_horaire) VALUES (?,?,?,?,?,?,?,?)",
                     (int(chantier_id), nom.strip(), type_engin, immat.strip(), float(compteur_h), float(intervalle_h),
                      float(compteur_h), float(cout_horaire)))
    audit(int(user["id"]), "engin_cree", nom.strip())
    return eid


def declarer_utilisation(user: dict, engin_id: int, jour: date, heures: float, note: str = "") -> None:
    require(user, "engin_declarer")
    e = _engin_autorise(user, engin_id)
    if e["statut"] != "Disponible":
        raise ValueError(f"L'engin est « {e['statut'].lower()} » : utilisation impossible.")
    if not 0 < heures <= 24:
        raise ValueError("Les heures doivent être comprises entre 0 et 24.")
    if jour > date.today():
        raise ValueError("La date ne peut pas être dans le futur.")
    with db.transaction() as conn:
        conn.execute("INSERT INTO engin_journal(engin_id,date,type,heures,description,auteur_id) "
                     "VALUES (?,?, 'Utilisation', ?, ?, ?)", (e["id"], _iso(jour), float(heures), note.strip(), int(user["id"])))
        conn.execute("UPDATE engins SET compteur_h=compteur_h+? WHERE id=?", (float(heures), e["id"]))
        apres = e["compteur_h"] + heures - e["derniere_maintenance_h"]
        if apres >= e["intervalle_maintenance_h"] and (e["compteur_h"] - e["derniere_maintenance_h"]) < e["intervalle_maintenance_h"]:
            _notifier(conn, int(user["id"]), _equipe(conn, e["chantier_id"], ("ingenieur",)),
                      f"Maintenance due : {e['nom']}",
                      f"L'intervalle de {e['intervalle_maintenance_h']:g} h est atteint.", e["chantier_id"])


def declarer_panne(user: dict, engin_id: int, jour: date, description: str) -> None:
    require(user, "engin_declarer")
    e = _engin_autorise(user, engin_id)
    if not description.strip():
        raise ValueError("Décrivez la panne.")
    if e["statut"] == "En panne":
        raise ValueError("Une panne est déjà ouverte sur cet engin.")
    with db.transaction() as conn:
        conn.execute("INSERT INTO engin_journal(engin_id,date,type,description,resolu,auteur_id) "
                     "VALUES (?,?, 'Panne', ?, 0, ?)", (e["id"], _iso(jour), description.strip(), int(user["id"])))
        conn.execute("UPDATE engins SET statut='En panne' WHERE id=?", (e["id"],))
        _notifier(conn, int(user["id"]), _equipe(conn, e["chantier_id"], ("ingenieur", "maitre_ouvrage")),
                  f"Panne : {e['nom']}", description.strip(), e["chantier_id"])
        audit(int(user["id"]), "panne_declaree", e["nom"], conn)


def cloturer_panne(user: dict, journal_id: int, duree_arret_h: float, cout: float) -> None:
    require(user, "engin_gerer")
    j = db.one("SELECT * FROM engin_journal WHERE id=? AND type='Panne'", (int(journal_id),))
    if not j:
        raise ValueError("Panne introuvable.")
    e = _engin_autorise(user, j["engin_id"])
    if j["resolu"]:
        raise ValueError("Cette panne est déjà clôturée.")
    if duree_arret_h < 0 or cout < 0:
        raise ValueError("Valeurs invalides.")
    with db.transaction() as conn:
        conn.execute("UPDATE engin_journal SET resolu=1, duree_arret_h=?, cout=? WHERE id=?",
                     (float(duree_arret_h), float(cout), j["id"]))
        autres = conn.execute("SELECT COUNT(*) FROM engin_journal WHERE engin_id=? AND type='Panne' AND resolu=0",
                              (e["id"],)).fetchone()[0]
        if autres == 0:
            conn.execute("UPDATE engins SET statut='Disponible' WHERE id=?", (e["id"],))
        audit(int(user["id"]), "panne_cloturee", f"{e['nom']} ({duree_arret_h:g} h d'arrêt)", conn)


def enregistrer_maintenance(user: dict, engin_id: int, jour: date, description: str, cout: float,
                            duree_arret_h: float) -> None:
    require(user, "engin_gerer")
    e = _engin_autorise(user, engin_id)
    if cout < 0 or duree_arret_h < 0:
        raise ValueError("Valeurs invalides.")
    if jour > date.today():
        raise ValueError("La date ne peut pas être dans le futur.")
    with db.transaction() as conn:
        conn.execute("INSERT INTO engin_journal(engin_id,date,type,description,cout,duree_arret_h,auteur_id) "
                     "VALUES (?,?, 'Maintenance', ?, ?, ?, ?)",
                     (e["id"], _iso(jour), description.strip() or "Maintenance préventive", float(cout),
                      float(duree_arret_h), int(user["id"])))
        conn.execute("UPDATE engins SET derniere_maintenance_h=compteur_h WHERE id=?", (e["id"],))
        audit(int(user["id"]), "maintenance", e["nom"], conn)


def pannes_ouvertes(ids: list[int]) -> pd.DataFrame:
    ids = [int(i) for i in ids]
    if not ids:
        return pd.DataFrame()
    return db.df(f"SELECT g.id, e.nom AS engin, c.nom AS chantier, g.date, g.description FROM engin_journal g "
                 f"JOIN engins e ON e.id=g.engin_id JOIN chantiers c ON c.id=e.chantier_id "
                 f"WHERE g.type='Panne' AND g.resolu=0 AND e.chantier_id IN ({_in(ids)}) ORDER BY g.date", tuple(ids))


def engin_journal_table(ids: list[int], d1: date | None = None, d2: date | None = None) -> pd.DataFrame:
    ids = [int(i) for i in ids]
    if not ids:
        return pd.DataFrame()
    sql = (f"SELECT g.date, e.nom AS engin, c.nom AS chantier, g.type, g.heures, g.duree_arret_h, g.cout, "
           f"g.description, g.resolu, u.full_name AS auteur FROM engin_journal g JOIN engins e ON e.id=g.engin_id "
           f"JOIN chantiers c ON c.id=e.chantier_id JOIN users u ON u.id=g.auteur_id "
           f"WHERE e.chantier_id IN ({_in(ids)})")
    params: list = list(ids)
    if d1:
        sql += " AND g.date>=?"
        params.append(_iso(d1))
    if d2:
        sql += " AND g.date<=?"
        params.append(_iso(d2))
    return db.df(sql + " ORDER BY g.date DESC, g.id DESC", tuple(params))


def engins_activite(ids: list[int], d1: date, d2: date) -> pd.DataFrame:
    """Disponibilité, MTBF et coût d'exploitation par engin sur la période."""
    ids = [int(i) for i in ids]
    if not ids:
        return pd.DataFrame()
    j = db.df(f"""SELECT e.id, e.nom, c.nom AS chantier, e.cout_horaire,
                         SUM(CASE WHEN g.type='Utilisation' THEN g.heures ELSE 0 END) AS heures,
                         SUM(CASE WHEN g.type<>'Utilisation' THEN g.duree_arret_h ELSE 0 END) AS arret_h,
                         SUM(CASE WHEN g.type='Panne' THEN 1 ELSE 0 END) AS pannes,
                         SUM(CASE WHEN g.type='Maintenance' THEN 1 ELSE 0 END) AS maintenances,
                         SUM(g.cout) AS cout_reparations
                  FROM engins e JOIN chantiers c ON c.id=e.chantier_id
                  LEFT JOIN engin_journal g ON g.engin_id=e.id AND g.date>=? AND g.date<=?
                  WHERE e.actif=1 AND e.chantier_id IN ({_in(ids)}) GROUP BY e.id ORDER BY c.nom, e.nom""",
              (_iso(d1), _iso(d2), *ids))
    if j.empty:
        return j
    j = j.fillna({"heures": 0, "arret_h": 0, "pannes": 0, "maintenances": 0, "cout_reparations": 0})
    jours_ouvres = max(((d2 - d1).days + 1) * 6 / 7, 1)
    j["disponibilite_pct"] = (1 - (j["arret_h"] / (jours_ouvres * 8)).clip(upper=1)) * 100
    j["mtbf_h"] = np.where(j["pannes"] > 0, j["heures"] / j["pannes"].where(j["pannes"] > 0), np.nan)
    j["cout_exploitation"] = j["heures"] * j["cout_horaire"] + j["cout_reparations"]
    return j.round(1)


# ----------------------------------------------------------------------------
# Indicateurs, alertes et analyses
# ----------------------------------------------------------------------------
def kpis_chantiers(ids: list[int]) -> pd.DataFrame:
    ids = [int(i) for i in ids]
    if not ids:
        return pd.DataFrame()
    stock = stock_table(ids)
    chs = db.df(f"SELECT * FROM chantiers WHERE id IN ({_in(ids)}) ORDER BY nom", tuple(ids))
    today = _iso(date.today())
    rows = []
    for c in chs.to_dict("records"):
        cid = int(c["id"])
        sub = stock[stock["chantier_id"] == cid] if not stock.empty else stock
        n = lambda s: int((sub["statut"] == s).sum()) if not sub.empty else 0
        ratios = sub["ratio_conso"].dropna() if not sub.empty else pd.Series(dtype=float)
        eng = engins_table([cid])
        rows.append({
            "id": cid, "chantier": c["nom"], "avancement": float(c["avancement_pct"]),
            "materiaux": len(sub), "ruptures": n("Rupture"), "sous_seuil": n("Sous le seuil"),
            "a_commander": n("À commander"),
            "valeur_stock": float(sub["valeur_stock"].sum()) if not sub.empty else 0.0,
            "ratio_conso": float(ratios.mean()) if len(ratios) else float("nan"),
            "cmd_attente": int(db.scalar("SELECT COUNT(*) FROM commandes WHERE chantier_id=? AND statut='Demandée'", (cid,)) or 0),
            "cmd_retard": int(db.scalar("SELECT COUNT(*) FROM commandes WHERE chantier_id=? AND statut IN "
                                        "('Approuvée','Livrée partiellement') AND date_livraison_prevue<?", (cid, today)) or 0),
            "a_controler": int(db.scalar("SELECT COUNT(*) FROM receptions r JOIN commandes c ON c.id=r.commande_id "
                                         "WHERE c.chantier_id=? AND r.controle_statut='À contrôler'", (cid,)) or 0),
            "engins_panne": int((eng["statut"] == "En panne").sum()) if not eng.empty else 0,
            "maint_retard": int((eng["maintenance"] == "En retard").sum()) if not eng.empty else 0,
        })
    return pd.DataFrame(rows)


def alertes(user: dict, ids: list[int]) -> list[dict]:
    """Alertes priorisées : {niveau: danger|warn|info, texte, page}."""
    ids = [int(i) for i in ids]
    out: list[dict] = []
    if not ids:
        return out
    st_ = stock_table(ids)
    if not st_.empty:
        for r in st_[st_["statut"] == "Rupture"].itertuples():
            out.append({"niveau": "danger", "page": "stocks", "texte": f"Rupture de **{r.nom}** — {r.chantier}."})
        for r in st_[st_["statut"] == "Sous le seuil"].itertuples():
            out.append({"niveau": "warn", "page": "stocks",
                        "texte": f"**{r.nom}** sous le seuil ({r.stock:g} {r.unite} pour {r.seuil_mini:g}) — {r.chantier}."})
        for r in st_[st_["statut"] == "À commander"].itertuples():
            out.append({"niveau": "warn", "page": "appro",
                        "texte": f"**{r.nom}** : {r.couverture_j:.1f} j de couverture pour {r.delai_appro_jours} j de délai — "
                                 f"commander maintenant ({r.chantier})."})
        for r in st_[(st_["ratio_conso"] > 1.15) & (st_["avancement_pct"] >= 10)].itertuples():
            out.append({"niveau": "warn", "page": "analyses",
                        "texte": f"Surconsommation de **{r.nom}** : {r.conso_pct:.0f} % du besoin consommé "
                                 f"pour {r.avancement_pct:.0f} % d'avancement ({r.chantier})."})
    cmd = commandes_table(ids)
    if not cmd.empty:
        for r in cmd[cmd["retard_j"] > 0].itertuples():
            out.append({"niveau": "warn", "page": "appro",
                        "texte": f"Commande n°{r.id} ({r.fournisseur}) en retard de {int(r.retard_j)} j — {r.chantier}."})
        if has(user, "commande_approuver") or has(user, "commande_approuver_tout"):
            nb = int((cmd["statut"] == "Demandée").sum())
            if nb:
                out.append({"niveau": "info", "page": "appro", "texte": f"{nb} demande(s) de commande en attente de décision."})
    if has(user, "reception_controler"):
        nb = len(receptions_table(ids, ["À contrôler"]))
        if nb:
            out.append({"niveau": "info", "page": "receptions", "texte": f"{nb} livraison(s) à contrôler avant mise en stock."})
    eng = engins_table(ids)
    if not eng.empty:
        for r in eng[eng["statut"] == "En panne"].itertuples():
            out.append({"niveau": "danger", "page": "engins", "texte": f"Engin en panne : **{r.nom}** — {r.chantier}."})
        for r in eng[eng["maintenance"] == "En retard"].itertuples():
            out.append({"niveau": "warn", "page": "engins",
                        "texte": f"Maintenance en retard : **{r.nom}** ({-r.heures_restantes:.0f} h de dépassement) — {r.chantier}."})
    ordre = {"danger": 0, "warn": 1, "info": 2}
    return sorted(out, key=lambda a: ordre[a["niveau"]])


def achats_par_mois(ids: list[int]) -> pd.DataFrame:
    ids = [int(i) for i in ids]
    if not ids:
        return pd.DataFrame()
    out = db.df(f"""SELECT substr(c.date_demande,1,7) AS mois, ch.nom AS chantier,
                           SUM(cl.quantite*cl.prix_unitaire) AS montant
                    FROM commandes c JOIN chantiers ch ON ch.id=c.chantier_id
                    JOIN commande_lignes cl ON cl.commande_id=c.id
                    WHERE c.chantier_id IN ({_in(ids)}) AND c.statut IN ('Approuvée','Livrée partiellement','Livrée')
                    GROUP BY mois, ch.nom ORDER BY mois""", tuple(ids))
    return out


def mouvements_table(ids: list[int], d1: date | None = None, d2: date | None = None) -> pd.DataFrame:
    ids = [int(i) for i in ids]
    if not ids:
        return pd.DataFrame()
    sql = f"""SELECT mv.date, c.nom AS chantier, m.nom AS materiau, m.categorie, m.unite, mv.type, mv.quantite,
                     mv.zone, mv.note, u.full_name AS auteur
              FROM mouvements mv JOIN materiaux m ON m.id=mv.materiau_id JOIN chantiers c ON c.id=mv.chantier_id
              JOIN users u ON u.id=mv.auteur_id WHERE mv.chantier_id IN ({_in(ids)})"""
    params: list = list(ids)
    if d1:
        sql += " AND mv.date>=?"
        params.append(_iso(d1))
    if d2:
        sql += " AND mv.date<=?"
        params.append(_iso(d2))
    return db.df(sql + " ORDER BY mv.date DESC, mv.id DESC", tuple(params))


# ----------------------------------------------------------------------------
# Chantiers et comptes
# ----------------------------------------------------------------------------
def creer_chantier(user: dict, nom: str, localisation: str, type_ouvrage: str, debut: date, fin: date) -> int:
    require(user, "chantier_creer")
    if not nom.strip():
        raise ValueError("Le nom est obligatoire.")
    if fin <= debut:
        raise ValueError("La date de fin doit être postérieure au début.")
    with db.transaction() as conn:
        cid = conn.execute("INSERT INTO chantiers(nom,localisation,type_ouvrage,date_debut,date_fin_prevue) "
                           "VALUES (?,?,?,?,?)", (nom.strip(), localisation.strip(), type_ouvrage, _iso(debut), _iso(fin))).lastrowid
        if user["role"] != "admin":
            conn.execute("INSERT OR IGNORE INTO affectations VALUES (?,?)", (int(user["id"]), cid))
        audit(int(user["id"]), "chantier_cree", nom.strip(), conn)
    return cid


def affecter(user: dict, chantier_id: int, user_ids: list[int], statut: str) -> None:
    require(user, "chantier_affecter")
    require_chantier(user, chantier_id)
    final = {int(u) for u in user_ids}
    if user["role"] != "admin":
        final.add(int(user["id"]))  # on ne se retire pas soi-même
    with db.transaction() as conn:
        conn.execute("DELETE FROM affectations WHERE chantier_id=?", (int(chantier_id),))
        for u in final:
            role = conn.execute("SELECT role FROM users WHERE id=?", (u,)).fetchone()
            if role and role[0] != "admin":
                conn.execute("INSERT INTO affectations VALUES (?,?)", (u, int(chantier_id)))
        conn.execute("UPDATE chantiers SET statut=? WHERE id=?", (statut, int(chantier_id)))
        audit(int(user["id"]), "affectations", f"chantier {chantier_id}: {len(final)} personne(s), statut {statut}", conn)


def maj_avancement(user: dict, chantier_id: int, pct: float) -> None:
    require(user, "avancement_maj")
    require_chantier(user, chantier_id)
    if not 0 <= pct <= 100:
        raise ValueError("L'avancement doit être compris entre 0 et 100 %.")
    db.execute("UPDATE chantiers SET avancement_pct=? WHERE id=?", (float(pct), int(chantier_id)))
    audit(int(user["id"]), "avancement", f"chantier {chantier_id} -> {pct:g} %")


# ----------------------------------------------------------------------------
# Messagerie
# ----------------------------------------------------------------------------
def contacts(user: dict) -> pd.DataFrame:
    uid = int(user["id"])
    if user["role"] == "admin":
        return db.df("SELECT id, full_name, role FROM users WHERE active=1 AND id<>? ORDER BY full_name", (uid,))
    return db.df("SELECT id, full_name, role FROM users WHERE active=1 AND id<>? AND (role='admin' OR id IN "
                 "(SELECT user_id FROM affectations WHERE chantier_id IN "
                 "(SELECT chantier_id FROM affectations WHERE user_id=?))) ORDER BY full_name", (uid, uid))


def non_lus(user: dict) -> int:
    return int(db.scalar("SELECT COUNT(*) FROM messages WHERE recipient_id=? AND lu=0", (int(user["id"]),)) or 0)


def non_lus_par_expediteur(user: dict) -> dict[int, int]:
    d = db.df("SELECT sender_id, COUNT(*) AS n FROM messages WHERE recipient_id=? AND lu=0 GROUP BY sender_id",
              (int(user["id"]),))
    return {int(a): int(b) for a, b in zip(d["sender_id"], d["n"])}


def envoyer(user: dict, destinataire_id: int, corps: str, important: bool = False, chantier_id: int | None = None) -> None:
    require(user, "messagerie")
    if not corps.strip():
        raise ValueError("Le message est vide.")
    if int(destinataire_id) not in [int(x) for x in contacts(user)["id"]]:
        raise AccessError("Ce destinataire n'est pas joignable depuis votre profil.")
    db.execute("INSERT INTO messages(sender_id,recipient_id,chantier_id,body,created_at,important) VALUES (?,?,?,?,?,?)",
               (int(user["id"]), int(destinataire_id), chantier_id, corps.strip(), _now(), 1 if important else 0))


def conversation(user: dict, autre_id: int) -> pd.DataFrame:
    uid = int(user["id"])
    d = db.df("""SELECT m.id, m.sender_id, m.subject, m.body, m.created_at, m.important, u.full_name
                 FROM messages m JOIN users u ON u.id=m.sender_id
                 WHERE (m.sender_id=? AND m.recipient_id=?) OR (m.sender_id=? AND m.recipient_id=?)
                 ORDER BY m.created_at, m.id""", (uid, int(autre_id), int(autre_id), uid))
    db.execute("UPDATE messages SET lu=1 WHERE recipient_id=? AND sender_id=?", (uid, int(autre_id)))
    return d


def canal(user: dict, chantier_id: int) -> pd.DataFrame:
    require_chantier(user, chantier_id)
    d = db.df("""SELECT m.sender_id, m.body, m.created_at, m.important, u.full_name, u.role
                 FROM messages m JOIN users u ON u.id=m.sender_id
                 WHERE m.chantier_id=? AND m.recipient_id IS NULL ORDER BY m.created_at DESC, m.id DESC LIMIT 150""",
              (int(chantier_id),))
    return d.iloc[::-1].reset_index(drop=True)


def poster_canal(user: dict, chantier_id: int, corps: str, important: bool = False) -> None:
    require(user, "messagerie")
    require_chantier(user, chantier_id)
    if not corps.strip():
        raise ValueError("Le message est vide.")
    db.execute("INSERT INTO messages(sender_id,recipient_id,chantier_id,body,created_at,important) VALUES (?,NULL,?,?,?,?)",
               (int(user["id"]), int(chantier_id), corps.strip(), _now(), 1 if important else 0))


# ----------------------------------------------------------------------------
# Comptes utilisateurs
# ----------------------------------------------------------------------------
def creer_utilisateur(admin: dict, username: str, nom: str, role: str, mot_de_passe: str, chantier_ids: list[int]) -> int:
    from . import security
    require(admin, "users_gerer")
    if role not in config.ROLES:
        raise ValueError("Profil inconnu.")
    if not username.strip() or not nom.strip():
        raise ValueError("Identifiant et nom sont obligatoires.")
    prob = security.password_problem(mot_de_passe)
    if prob:
        raise ValueError(prob)
    with db.transaction() as conn:
        try:
            uid = conn.execute("INSERT INTO users(username,password_hash,full_name,role,must_change) VALUES (?,?,?,?,1)",
                               (username.strip(), security.hash_password(mot_de_passe), nom.strip(), role)).lastrowid
        except Exception as exc:
            if "UNIQUE" in str(exc):
                raise ValueError("Cet identifiant existe déjà.") from exc
            raise
        if role != "admin":
            for c in chantier_ids:
                conn.execute("INSERT OR IGNORE INTO affectations VALUES (?,?)", (uid, int(c)))
        audit(int(admin["id"]), "utilisateur_cree", f"{username.strip()} ({role})", conn)
    return uid


def reinitialiser_mot_de_passe(admin: dict, user_id: int, mot_de_passe: str) -> None:
    from . import security
    require(admin, "users_gerer")
    prob = security.password_problem(mot_de_passe)
    if prob:
        raise ValueError(prob)
    db.execute("UPDATE users SET password_hash=?, must_change=1 WHERE id=?", (security.hash_password(mot_de_passe), int(user_id)))
    db.execute("DELETE FROM login_attempts WHERE username=(SELECT username FROM users WHERE id=?)", (int(user_id),))
    audit(int(admin["id"]), "mdp_reinitialise", f"utilisateur {user_id}")


def basculer_utilisateur(admin: dict, user_id: int, actif: bool) -> None:
    require(admin, "users_gerer")
    if int(user_id) == int(admin["id"]):
        raise ValueError("Vous ne pouvez pas désactiver votre propre compte.")
    db.execute("UPDATE users SET active=? WHERE id=?", (1 if actif else 0, int(user_id)))
    audit(int(admin["id"]), "utilisateur_statut", f"{user_id} -> {'actif' if actif else 'désactivé'}")


def changer_mot_de_passe(user: dict, ancien: str, nouveau: str) -> None:
    from . import security
    row = db.one("SELECT password_hash FROM users WHERE id=?", (int(user["id"]),))
    if not row or not security.verify_password(ancien, row["password_hash"]):
        raise ValueError("Mot de passe actuel incorrect.")
    if nouveau == ancien:
        raise ValueError("Le nouveau mot de passe doit être différent.")
    prob = security.password_problem(nouveau)
    if prob:
        raise ValueError(prob)
    db.execute("UPDATE users SET password_hash=?, must_change=0 WHERE id=?", (security.hash_password(nouveau), int(user["id"])))
    audit(int(user["id"]), "mdp_change", "")
