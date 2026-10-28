# -*- coding: utf-8 -*-
"""Accès SQLite : schéma, requêtes et transactions."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import pandas as pd

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('maitre_ouvrage','chef_chantier','ingenieur','admin')),
    active INTEGER NOT NULL DEFAULT 1,
    must_change INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS chantiers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nom TEXT NOT NULL,
    localisation TEXT,
    type_ouvrage TEXT,
    date_debut TEXT NOT NULL,
    date_fin_prevue TEXT NOT NULL,
    avancement_pct REAL NOT NULL DEFAULT 0,
    statut TEXT NOT NULL DEFAULT 'En cours'
);
CREATE TABLE IF NOT EXISTS affectations (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    chantier_id INTEGER NOT NULL REFERENCES chantiers(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, chantier_id)
);
CREATE TABLE IF NOT EXISTS fournisseurs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nom TEXT NOT NULL UNIQUE,
    specialite TEXT,
    contact TEXT,
    telephone TEXT,
    actif INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS materiaux (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chantier_id INTEGER NOT NULL REFERENCES chantiers(id) ON DELETE CASCADE,
    nom TEXT NOT NULL,
    categorie TEXT NOT NULL,
    unite TEXT NOT NULL,
    prix_unitaire REAL NOT NULL DEFAULT 0,
    seuil_mini REAL NOT NULL DEFAULT 0,
    besoin_prevu REAL NOT NULL DEFAULT 0,
    delai_appro_jours INTEGER NOT NULL DEFAULT 3,
    actif INTEGER NOT NULL DEFAULT 1,
    UNIQUE (chantier_id, nom)
);
CREATE TABLE IF NOT EXISTS commandes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chantier_id INTEGER NOT NULL REFERENCES chantiers(id) ON DELETE CASCADE,
    fournisseur_id INTEGER NOT NULL REFERENCES fournisseurs(id),
    date_demande TEXT NOT NULL,
    date_livraison_prevue TEXT NOT NULL,
    statut TEXT NOT NULL DEFAULT 'Demandée',
    demandeur_id INTEGER NOT NULL REFERENCES users(id),
    decideur_id INTEGER REFERENCES users(id),
    date_decision TEXT,
    motif_refus TEXT,
    note TEXT
);
CREATE TABLE IF NOT EXISTS commande_lignes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    commande_id INTEGER NOT NULL REFERENCES commandes(id) ON DELETE CASCADE,
    materiau_id INTEGER NOT NULL REFERENCES materiaux(id),
    quantite REAL NOT NULL,
    prix_unitaire REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS receptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    commande_id INTEGER NOT NULL REFERENCES commandes(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    bon_livraison TEXT,
    receptionnaire_id INTEGER NOT NULL REFERENCES users(id),
    observations TEXT,
    controle_statut TEXT NOT NULL DEFAULT 'À contrôler',
    controle_par INTEGER REFERENCES users(id),
    controle_date TEXT,
    observations_controle TEXT
);
CREATE TABLE IF NOT EXISTS reception_lignes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reception_id INTEGER NOT NULL REFERENCES receptions(id) ON DELETE CASCADE,
    ligne_id INTEGER NOT NULL REFERENCES commande_lignes(id),
    materiau_id INTEGER NOT NULL REFERENCES materiaux(id),
    qte_recue REAL NOT NULL,
    qte_acceptee REAL
);
-- Registre des mouvements : quantité signée (entrée > 0, sortie < 0, ajustement ±).
CREATE TABLE IF NOT EXISTS mouvements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chantier_id INTEGER NOT NULL REFERENCES chantiers(id) ON DELETE CASCADE,
    materiau_id INTEGER NOT NULL REFERENCES materiaux(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('ENTREE','SORTIE','AJUSTEMENT')),
    quantite REAL NOT NULL,
    zone TEXT,
    note TEXT,
    ref_reception_id INTEGER REFERENCES receptions(id),
    auteur_id INTEGER NOT NULL REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS engins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chantier_id INTEGER NOT NULL REFERENCES chantiers(id) ON DELETE CASCADE,
    nom TEXT NOT NULL,
    type_engin TEXT NOT NULL,
    immatriculation TEXT,
    statut TEXT NOT NULL DEFAULT 'Disponible',
    compteur_h REAL NOT NULL DEFAULT 0,
    intervalle_maintenance_h REAL NOT NULL DEFAULT 250,
    derniere_maintenance_h REAL NOT NULL DEFAULT 0,
    cout_horaire REAL NOT NULL DEFAULT 0,
    actif INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS engin_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    engin_id INTEGER NOT NULL REFERENCES engins(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('Utilisation','Panne','Maintenance')),
    heures REAL NOT NULL DEFAULT 0,
    duree_arret_h REAL NOT NULL DEFAULT 0,
    cout REAL NOT NULL DEFAULT 0,
    description TEXT,
    resolu INTEGER NOT NULL DEFAULT 1,
    auteur_id INTEGER NOT NULL REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id INTEGER NOT NULL REFERENCES users(id),
    recipient_id INTEGER REFERENCES users(id),     -- NULL = canal de chantier
    chantier_id INTEGER REFERENCES chantiers(id) ON DELETE CASCADE,
    subject TEXT,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    lu INTEGER NOT NULL DEFAULT 0,
    important INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    action TEXT NOT NULL,
    detail TEXT,
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_attempts (
    username TEXT NOT NULL COLLATE NOCASE,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_mv_mat ON mouvements(materiau_id, date);
CREATE INDEX IF NOT EXISTS ix_mv_ch ON mouvements(chantier_id, date);
CREATE INDEX IF NOT EXISTS ix_cmd_ch ON commandes(chantier_id, statut);
CREATE INDEX IF NOT EXISTS ix_rec_cmd ON receptions(commande_id);
CREATE INDEX IF NOT EXISTS ix_ej_engin ON engin_journal(engin_id, date);
CREATE INDEX IF NOT EXISTS ix_msg_rec ON messages(recipient_id, lu);
CREATE INDEX IF NOT EXISTS ix_la ON login_attempts(username, ts);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()


@contextmanager
def transaction():
    """Transaction atomique : tout est validé, ou rien."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def df(sql: str, params: tuple = ()) -> pd.DataFrame:
    conn = connect()
    try:
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()


def one(sql: str, params: tuple = ()) -> dict | None:
    conn = connect()
    try:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def col(sql: str, params: tuple = ()) -> list:
    conn = connect()
    try:
        return [r[0] for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def scalar(sql: str, params: tuple = ()):
    conn = connect()
    try:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def execute(sql: str, params: tuple = ()) -> int:
    with transaction() as conn:
        return conn.execute(sql, params).lastrowid
