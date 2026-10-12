"""G-001 — aucune donnée de personne physique (CLAUDE.md).

1. Test statique sur build.py : les colonnes RNA nominatives (dir_*, adrg_*, siteweb, observation) ne sont
   jamais lues — `RNA_USECOLS` ne les contient pas, la lecture du zip RNA passe par `usecols` limité à
   `RNA_USECOLS`, et aucun nom de ces colonnes n'apparaît dans le code exécutable (docstrings exclues).
2. Test sur les sorties versionnées (out/*.xlsx, out/*.csv, dont la vue comparables : feuilles comparables /
   comparables_verticals et RLL-IDF-comparables-v0.1.csv) : aucune colonne de personne physique, aucun
   email ni numéro de téléphone dans les cellules.
3. Même contrôle sur un build complet à partir des données fictives de conftest.py, dont le zip RNA contient
   des colonnes nominatives fictives : leur contenu n'apparaît dans aucune sortie.

Lancer : python3 -m pytest pipelines/fr-idf/tests
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pandas as pd
import pytest

from conftest import FAKE_PERSON

PIPELINE_DIR = Path(__file__).resolve().parents[1]
OUT = PIPELINE_DIR / "out"
RNA_PERSONAL = re.compile(r"^(dir_.*|adrg_.*|siteweb|observation)$")
PERSONAL_HEADER = re.compile(r"(^|_)(dir|adrg|declarant|civilite|dirigeant|president|prenom|nom_(?!association\b)"
                             r"[a-z_]*personne|e?mail|courriel|tel|telephone|phone|siteweb|observation)($|_)"
                             r"|^objet$",          # texte libre RNA (peut nommer des personnes) ; code_objet_waldec reste permis
                             re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"(?<!\d)(?:\+33\s?|0)[1-9](?:[\s.-]?\d{2}){4}(?!\d)")


def _tree():
    return ast.parse((PIPELINE_DIR / "build.py").read_text(encoding="utf-8"))


def _docstrings(tree) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                ids.add(id(first.value))
    return ids


def test_rna_usecols_excludes_personal_columns():
    tree = _tree()
    usecols = next(ast.literal_eval(n.value) for n in ast.walk(tree)
                   if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "RNA_USECOLS" for t in n.targets))
    assert usecols, "RNA_USECOLS introuvable"
    assert [c for c in usecols if RNA_PERSONAL.match(c)] == []


def test_rna_read_is_limited_to_usecols():
    load_rna = next(n for n in ast.walk(_tree()) if isinstance(n, ast.FunctionDef) and n.name == "load_rna")
    reads = [c for c in ast.walk(load_rna) if isinstance(c, ast.Call) and getattr(c.func, "attr", None) == "read_csv"]
    assert reads, "load_rna ne lit aucun CSV ?"
    for call in reads:
        kw = {k.arg: k.value for k in call.keywords}
        assert "usecols" in kw, "read_csv du RNA sans usecols : toutes les colonnes seraient lues"
        names = {n.id for n in ast.walk(kw["usecols"]) if isinstance(n, ast.Name)}
        assert "RNA_USECOLS" in names, "usecols du RNA ne se limite pas à RNA_USECOLS"


def test_no_personal_column_name_in_code():
    tree = _tree()
    skip = _docstrings(tree)
    found = sorted({n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                    and id(n) not in skip and re.search(r"\b(dir_\w+|adrg_\w+|siteweb|observation)\b", n.value)})
    assert found == [], f"noms de colonnes nominatives présents dans le code : {found}"


EXPECTED_OUTPUTS = {"RLL-IDF-villes-x-verticals-v0.1.csv", "RLL-IDF-comparables-v0.1.csv",
                    "RLL-IDF-villes-x-verticals-v0.1.xlsx:comparables",
                    "RLL-IDF-villes-x-verticals-v0.1.xlsx:comparables_verticals",
                    "RLL-IDF-villes-x-verticals-v0.1.xlsx:villes_x_verticals"}


def _outputs(out: Path = OUT):
    files = sorted(out.glob("*.xlsx")) + sorted(out.glob("*.csv"))
    if not files:
        pytest.skip("aucune sortie dans out/")
    for f in files:
        if f.suffix == ".xlsx":
            for sheet, df in pd.read_excel(f, sheet_name=None, dtype=str, header=None).items():
                yield f"{f.name}:{sheet}", df
        else:
            yield f.name, pd.read_csv(f, sep=";", dtype=str, encoding="utf-8-sig", header=None)


def _header_rows(df: pd.DataFrame) -> list[list[str]]:
    # Feuilles à plusieurs tableaux (mapping, correspondance_communes) : un en-tête suit chaque ligne vide.
    rows, prev_blank = [], True
    for _, r in df.iterrows():
        blank = r.isna().all()
        if prev_blank and not blank:
            rows.append([str(v) for v in r.dropna()])
        prev_blank = blank
    return rows


def _personal_headers(outputs) -> list[str]:
    bad = []
    for name, df in outputs:
        for header in _header_rows(df):
            bad += [f"{name}:{h}" for h in header if PERSONAL_HEADER.search(h)]
    return bad


def _emails_phones(outputs) -> list[str]:
    bad = []
    for name, df in outputs:
        for col in df.columns:
            s = df[col].dropna().astype(str)
            hits = s[s.str.contains(EMAIL) | s.str.contains(PHONE)]
            bad += [f"{name}:{v[:60]}" for v in hits.head(3)]
    return bad


def test_versioned_outputs_include_new_views():
    names = {name for name, _ in _outputs()}
    assert EXPECTED_OUTPUTS <= names, f"sorties attendues absentes de out/ : {EXPECTED_OUTPUTS - names}"


def test_outputs_have_no_personal_columns():
    bad = _personal_headers(_outputs())
    assert bad == [], f"colonnes de personne physique dans les sorties : {bad}"


def test_outputs_have_no_email_or_phone():
    bad = _emails_phones(_outputs())
    assert bad == [], f"email / téléphone dans les sorties : {bad}"


def test_fake_build_outputs_have_no_personal_data(pipeline_copy, load_build):
    build = load_build(pipeline_copy)
    build.main([])
    outputs = list(_outputs(pipeline_copy / "out"))
    assert EXPECTED_OUTPUTS <= {name for name, _ in outputs}
    assert _personal_headers(outputs) == []
    assert _emails_phones(outputs) == []
    leaked = [name for name, df in outputs
              if df.astype(str).apply(lambda c: c.str.contains(FAKE_PERSON, regex=False)).any().any()]
    assert leaked == [], f"donnée nominative fictive du RNA présente dans : {leaked}"
