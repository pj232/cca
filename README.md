# LogiChantier — logistique de chantier (génie civil)

Application web (Python / Streamlit / SQLite) qui gère **la chaîne logistique des chantiers** :
stocks de matériaux, approvisionnement et validation des commandes, réception avec contrôle qualité,
parc d'engins et maintenance, avec analyses multi-chantiers et messagerie.

**Problème traité** : éviter les ruptures et les surstocks, maîtriser les délais fournisseurs,
détecter les surconsommations et les pannes d'engins, et garder la trace de chaque décision.

## Lancement

```bash
pip install -r requirements.txt
streamlit run app.py
```

Au premier lancement, une base de démonstration est créée (3 chantiers, ~100 jours simulés :
stocks, livraisons avec retards et lots non conformes, pannes d'engins).

| Profil | Identifiant | Mot de passe |
|---|---|---|
| Maître d'ouvrage | `maitre` | `Maitre!2026` |
| Chef de chantier | `chantier1` / `chantier2` | `Chantier1!2026` / `Chantier2!2026` |
| Ingénieur | `ingenieur` | `Ingenieur!2026` |
| Administrateur | `administrateur` | `Admin!2026` |

### Tests
```bash
python tests/test_services.py     # règles métier, droits, workflows (12 tests)
python tests/test_ui_smoke.py     # exécute chaque page pour chaque profil (Streamlit simulé)
```

### Production
- `LOGICHANTIER_DEMO=0` : pas de données de démo ; compte `admin` créé avec `LOGICHANTIER_ADMIN_PASSWORD`
  (sinon mot de passe affiché une fois dans la console). Le changement de mot de passe est alors imposé à la 1re connexion.
- Autres variables : `LOGICHANTIER_DB`, `LOGICHANTIER_DEVISE`, `LOGICHANTIER_SEUIL_ING` (seuil d'approbation de l'ingénieur).
- Derrière HTTPS (reverse proxy) ; sauvegardes régulières du fichier `.db`.

## Rôles et droits (moindre privilège, séparation des tâches)

Chacun ne voit que **les chantiers auxquels il est affecté** (l'administrateur voit tout).
Les droits sont vérifiés dans la couche métier (`lc/services.py`), pas seulement dans l'interface.

| Fonction | Maître d'ouvrage | Chef de chantier | Ingénieur | Admin |
|---|:-:|:-:|:-:|:-:|
| Tableau de bord, analyses | ✅ | ✅ | ✅ | ✅ |
| Prix, valeurs, coûts | ✅ | ❌ | ✅ | ✅ |
| Consulter stocks / commandes / engins | ✅ | ✅ | ✅ | ✅ |
| Sortie de stock (consommation) | ❌ | ✅ | ❌ | ❌ |
| Inventaire, catalogue matériaux, fournisseurs | ❌ | ❌ | ✅ | ❌ |
| Demander une commande | ❌ | ✅ | ✅ | ❌ |
| Approuver une commande | tout montant | ❌ | jusqu'au seuil | ❌ |
| Déclarer une réception (livraison) | ❌ | ✅ | ❌ | ❌ |
| Contrôler la qualité d'une livraison | ❌ | ❌ | ✅ | ❌ |
| Déclarer utilisation / panne d'engin | ❌ | ✅ | ✅ | ❌ |
| Maintenance, clôture des pannes | ❌ | ❌ | ✅ | ❌ |
| Mettre à jour l'avancement | ❌ | ❌ | ✅ | ❌ |
| Créer chantier / affecter équipes | ✅ | ❌ | ❌ | ✅ |
| Gérer les comptes, journal d'audit | ❌ | ❌ | ❌ | ✅ |
| Export CSV | ✅ | ❌ | ✅ | ✅ |

**Contrôles internes intégrés** (reflet des pratiques de chantier) :
- Le demandeur ne peut pas approuver sa propre commande.
- Au-delà du seuil de l'ingénieur, seul le maître d'ouvrage approuve.
- Le contrôle qualité d'une livraison doit être fait par une autre personne que le réceptionnaire.
- Un matériau n'entre en stock **qu'après** le contrôle, et seule la quantité acceptée compte.
- Une sortie ne peut pas dépasser le stock ; tout ajustement d'inventaire exige un motif.
- Toutes les opérations sensibles sont écrites dans le journal d'audit.

## Fonctionnalités
- **Stocks** : niveau, couverture en jours, date de rupture estimée, quantité à commander suggérée,
  historique et consommation par zone.
- **Approvisionnement** : tableau de commande éditable (suggestions prêtes à ajuster), workflow
  demande → décision → livraison, suivi des retards, **score fournisseur** (complétude, conformité, ponctualité).
- **Réceptions** : livraisons partielles, tolérance de +10 %, contrôle qualité, non-conformités notifiées.
- **Engins** : maintenance préventive par heures de compteur, pannes, disponibilité, MTBF, coût d'exploitation.
- **Analyses** : écart consommation / avancement, performance fournisseurs, qualité des livraisons,
  engins et **prévisions de stock** avec curseur d'intensité des travaux.
- **Messagerie** : conversations directes, canaux par chantier, notifications automatiques
  (stock bas, commande à valider, livraison à contrôler, panne).
- **Sécurité** : mots de passe scrypt, blocage après 5 échecs (persistant), mot de passe 8+ caractères avec lettre et chiffre,
  déconnexion après 1 h d'inactivité.

## Structure du projet
```
app.py               # navigation, connexion, session
lc/config.py         # rôles, droits, constantes
lc/db.py             # schéma SQLite et accès
lc/security.py       # mots de passe, authentification, anti force brute
lc/services.py       # règles métier (testables sans interface)
lc/seed.py           # simulation des données de démonstration
lc/ui.py             # thème contrasté et composants
lc/views/*.py        # une page par fichier
tests/               # tests métier et test de fumée des pages
```
