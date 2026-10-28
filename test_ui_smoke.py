"""Test de fumée des vues SANS serveur Streamlit : un faux `st` renvoie des valeurs par défaut
et chaque page est exécutée pour chaque profil (détecte erreurs Python / pandas / SQL).

Si Streamlit est installé, préférer aussi : streamlit run app.py
"""
import os
import sys
import tempfile
import types
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

os.environ["LOGICHANTIER_DB"] = tempfile.mktemp(suffix=".db")
os.environ["LOGICHANTIER_DEMO"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class AttrDict(dict):
    __getattr__ = dict.get

    def __setattr__(self, k, v):
        self[k] = v


class Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __getattr__(self, name):
        return getattr(FAKE, name)


def _first(options, index=0):
    options = list(options)
    return options[index] if options else None


class FakeSt:
    def __init__(self):
        self.session_state = AttrDict()
        self.sidebar = Ctx()
        self.column_config = MagicMock()
        self.calls = 0

    def cache_resource(self, f=None, **k):
        return f if f else (lambda g: g)

    def columns(self, spec, **k):
        n = spec if isinstance(spec, int) else len(spec)
        return [Ctx() for _ in range(n)]

    def tabs(self, labels):
        return [Ctx() for _ in labels]

    def selectbox(self, label, options=(), index=0, **k):
        return _first(options, index or 0)

    def multiselect(self, label, options=(), default=None, **k):
        return list(default) if default is not None else []

    def radio(self, label, options=(), index=0, **k):
        return _first(options, index or 0)

    def text_input(self, label, value="", **k):
        return value

    text_area = text_input

    def number_input(self, label, min_value=None, max_value=None, value=None, **k):
        if value is not None:
            return value
        return min_value if min_value is not None else 0.0

    def date_input(self, label, value=None, **k):
        return value if value is not None else date.today()

    def checkbox(self, label, value=False, **k):
        return value

    toggle = checkbox

    def slider(self, label, min_value=0, max_value=100, value=None, **k):
        return value if value is not None else min_value

    def button(self, *a, **k):
        return False

    form_submit_button = download_button = button

    def data_editor(self, df, **k):
        return df

    def expander(self, *a, **k):
        return Ctx()

    container = form = chat_message = spinner = empty = expander

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *a, **k: None


FAKE = FakeSt()
sys.modules["streamlit"] = FAKE
for m in ("plotly", "plotly.express", "plotly.graph_objects"):
    sys.modules[m] = MagicMock()
sys.modules["plotly"].express = sys.modules["plotly.express"]
sys.modules["plotly"].graph_objects = sys.modules["plotly.graph_objects"]

from lc import db, seed, services as sv  # noqa: E402
from lc.views import admin, analyses, appro, dashboard, engins, messagerie, receptions, stocks  # noqa: E402

seed.bootstrap()

PAGES = [("dashboard", dashboard.render, "dashboard"), ("stocks", stocks.render, "stocks_lire"),
         ("appro", appro.render, "commandes_lire"), ("receptions", receptions.render, "receptions_lire"),
         ("engins", engins.render, "engins_lire"), ("analyses", analyses.render, "analyses"),
         ("messagerie", messagerie.render, "messagerie"), ("chantiers", admin.chantiers, "chantier_affecter"),
         ("utilisateurs", admin.utilisateurs, "users_gerer"), ("audit", admin.audit, "audit_lire"),
         ("compte", admin.compte, None)]


def test_toutes_les_pages_tous_les_profils():
    n = 0
    for username in ("maitre", "chantier1", "chantier2", "ingenieur", "administrateur"):
        user = db.one("SELECT id, username, full_name, role, active, must_change FROM users WHERE username=?", (username,))
        ch = sv.chantiers_visibles(user)
        for name, fn, perm in PAGES:
            if perm and not sv.has(user, perm):
                continue
            try:
                fn(user, ch)
            except Exception as exc:  # noqa: BLE001
                raise AssertionError(f"{username} / page {name} : {type(exc).__name__}: {exc}") from exc
            n += 1
    print(n, "rendus de pages sans erreur")


def test_page_vide():
    """Un chantier sans donnée ne doit pas faire planter les pages."""
    adm = db.one("SELECT id, username, full_name, role, active, must_change FROM users WHERE username='administrateur'")
    cid = sv.creer_chantier(adm, "Chantier vide", "", "Bâtiment", date.today(), date(2099, 1, 1))
    import pandas as pd
    ch = pd.DataFrame(db.df("SELECT * FROM chantiers WHERE id=?", (cid,)))
    for name, fn, perm in PAGES:
        if perm and not sv.has(adm, perm):
            continue
        fn(adm, ch)


if __name__ == "__main__":
    test_toutes_les_pages_tous_les_profils()
    test_page_vide()
    print("OK")
