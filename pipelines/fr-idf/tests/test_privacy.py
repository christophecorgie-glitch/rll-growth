"""G-001 — aucune donnée de personne physique (CLAUDE.md).

1. Test statique sur build.py : les colonnes RNA nominatives (dir_*, adrg_*, siteweb, observation) ne sont
   jamais lues — `RNA_USECOLS` ne les contient pas, la lecture du zip RNA passe par `usecols` limité à
   `RNA_USECOLS`, et aucun nom de ces colonnes n'apparaît dans le code exécutable (docstrings exclues).
2. Test sur les sorties versionnées (out/*.xlsx, out/*.csv) : aucune colonne de personne physique, aucun
   email ni numéro de téléphone dans les cellules.

Lancer : python3 -m pytest pipelines/fr-idf/tests
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pandas as pd
import pytest

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


def _outputs():
    files = sorted(OUT.glob("*.xlsx")) + sorted(OUT.glob("*.csv"))
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


def test_outputs_have_no_personal_columns():
    bad = []
    for name, df in _outputs():
        for header in _header_rows(df):
            bad += [f"{name}:{h}" for h in header if PERSONAL_HEADER.search(h)]
    assert bad == [], f"colonnes de personne physique dans les sorties : {bad}"


def test_outputs_have_no_email_or_phone():
    bad = []
    for name, df in _outputs():
        for col in df.columns:
            s = df[col].dropna().astype(str)
            hits = s[s.str.contains(EMAIL) | s.str.contains(PHONE)]
            bad += [f"{name}:{v[:60]}" for v in hits.head(3)]
    assert bad == [], f"email / téléphone dans les sorties : {bad}"
