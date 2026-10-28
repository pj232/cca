# -*- coding: utf-8 -*-
"""Configuration, rôles et droits de LogiChantier."""
import os
from pathlib import Path

APP_NAME = "LogiChantier"
DB_PATH = Path(os.environ.get("LOGICHANTIER_DB", "logichantier.db"))
DEMO = os.environ.get("LOGICHANTIER_DEMO", "1") == "1"
DEVISE = os.environ.get("LOGICHANTIER_DEVISE", "FCFA")
# Au-delà de ce montant, une commande doit être approuvée par le maître d'ouvrage.
SEUIL_APPRO_INGENIEUR = float(os.environ.get("LOGICHANTIER_SEUIL_ING", "5000000"))

FENETRE_CONSO_JOURS = 14     # fenêtre de calcul de la consommation moyenne
MARGE_SECURITE_JOURS = 7     # marge ajoutée au délai d'approvisionnement
MIN_PASSWORD = 8
SESSION_INACTIVITE_S = 3600  # déconnexion automatique après 1 h d'inactivité
MAX_ECHECS = 5               # tentatives de connexion avant blocage
BLOCAGE_SECONDES = 300

ROLES = {
    "maitre_ouvrage": "Maître d'ouvrage",
    "chef_chantier": "Chef de chantier",
    "ingenieur": "Ingénieur",
    "admin": "Administrateur",
}
ROLE_ICONS = {"maitre_ouvrage": "🏛️", "chef_chantier": "👷", "ingenieur": "📐", "admin": "🛠️"}

# Droits par profil -----------------------------------------------------------
#  Chef de chantier : demande, réceptionne physiquement, sort le matériau, signale les pannes.
#  Ingénieur        : contrôle la qualité des livraisons, gère le catalogue, l'inventaire,
#                     la maintenance et approuve les commandes jusqu'au seuil.
#  Maître d'ouvrage : approuve toute commande (au-delà du seuil), pilote et consulte les coûts.
#  Administrateur   : comptes, affectations, journal d'audit (lecture seule sur le reste).
PERMISSIONS = {
    "maitre_ouvrage": {
        "dashboard", "analyses", "couts_voir", "stocks_lire", "commandes_lire",
        "commande_approuver_tout", "receptions_lire", "engins_lire", "chantier_creer",
        "chantier_affecter", "exporter", "messagerie",
    },
    "chef_chantier": {
        "dashboard", "analyses", "stocks_lire", "sortie_ecrire", "commande_demander",
        "commandes_lire", "reception_declarer", "receptions_lire", "engins_lire",
        "engin_declarer", "messagerie",
    },
    "ingenieur": {
        "dashboard", "analyses", "couts_voir", "stocks_lire", "materiau_gerer",
        "inventaire_ajuster", "commande_demander", "commande_approuver", "commandes_lire",
        "reception_controler", "receptions_lire", "fournisseur_gerer", "engins_lire",
        "engin_declarer", "engin_gerer", "avancement_maj", "exporter", "messagerie",
    },
    "admin": {
        "dashboard", "analyses", "couts_voir", "stocks_lire", "commandes_lire",
        "receptions_lire", "engins_lire", "chantier_creer", "chantier_affecter",
        "users_gerer", "audit_lire", "exporter", "messagerie",
    },
}
PERMISSION_LABELS = {
    "dashboard": "Tableau de bord",
    "analyses": "Analyses multi-chantiers",
    "couts_voir": "Voir prix, valeurs et coûts",
    "stocks_lire": "Consulter les stocks",
    "sortie_ecrire": "Enregistrer les sorties de stock",
    "materiau_gerer": "Gérer le catalogue de matériaux",
    "inventaire_ajuster": "Ajuster le stock après inventaire",
    "commande_demander": "Demander une commande",
    "commande_approuver": f"Approuver les commandes (jusqu'au seuil)",
    "commande_approuver_tout": "Approuver toute commande (tous montants)",
    "commandes_lire": "Suivre les commandes",
    "reception_declarer": "Déclarer une réception (livraison)",
    "reception_controler": "Contrôler la qualité des livraisons",
    "receptions_lire": "Consulter les réceptions",
    "fournisseur_gerer": "Gérer les fournisseurs",
    "engins_lire": "Consulter le parc d'engins",
    "engin_declarer": "Déclarer utilisation et pannes",
    "engin_gerer": "Planifier la maintenance, clôturer les pannes",
    "avancement_maj": "Mettre à jour l'avancement du chantier",
    "chantier_creer": "Créer des chantiers",
    "chantier_affecter": "Affecter des personnes aux chantiers",
    "users_gerer": "Gérer les comptes",
    "audit_lire": "Consulter le journal d'audit",
    "exporter": "Exporter les données (CSV)",
    "messagerie": "Messagerie",
}

CATEGORIES_MATERIAUX = [
    "Liants & béton", "Aciers", "Granulats & sols", "Bois & coffrage",
    "Revêtements & enrobés", "Maçonnerie", "Réseaux & plomberie", "Électricité",
    "Second œuvre", "Autre",
]
UNITES = ["t", "kg", "m³", "m²", "m", "u", "sac", "L"]
TYPES_OUVRAGE = ["Bâtiment", "Route", "Hydraulique", "Ouvrage d'art", "VRD / Réseaux", "Autre"]
STATUTS_CHANTIER = ["En cours", "Suspendu", "Terminé"]
STATUTS_COMMANDE = ["Demandée", "Approuvée", "Refusée", "Livrée partiellement", "Livrée", "Annulée"]
STATUTS_CONTROLE = ["À contrôler", "Conforme", "Conforme avec réserves", "Non conforme"]
TYPES_ENGIN = [
    "Pelle hydraulique", "Chargeuse", "Camion benne", "Niveleuse", "Compacteur",
    "Grue mobile", "Centrale à béton", "Groupe électrogène", "Autre",
]
