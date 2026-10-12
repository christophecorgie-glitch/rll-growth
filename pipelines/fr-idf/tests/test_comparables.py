"""Vue comparables — sans réseau, sur les données fictives de conftest.py.

1. La vue ne change rien d'autre : un build où `comparables` est neutralisée produit un classeur dont
   toutes les feuilles existantes (colonnes et valeurs) et le CSV principal sont identiques à ceux du build
   complet ; seules les feuilles comparables / comparables_verticals et le CSV comparables s'ajoutent.
2. Contenu : tranche COMPARABLES_POP (bornes incluses), classement par score inchangé, colonne pilote,
   top COMPARABLES_TOP_N par vertical (dix verticals, densité décroissante), CSV = feuille.

Lancer : python3 -m pytest pipelines/fr-idf/tests
"""
from __future__ import annotations

import shutil

import pandas as pd
import pandas.testing as pdt

from conftest import COMMUNES

XLSX = "RLL-IDF-villes-x-verticals-v0.1.xlsx"
NEW_SHEETS = ["comparables", "comparables_verticals"]


def _run(pipe, load_build, neutralise=False):
    out = pipe / ("out_sans" if neutralise else "out")
    build = load_build(pipe)
    if neutralise:
        build.comparables = lambda tbl: (pd.DataFrame(), pd.DataFrame())
    build.main(["--out", str(out)])
    return build, out


def test_existing_sheets_unchanged(pipeline_copy, load_build):
    _, full = _run(pipeline_copy, load_build)
    _, ref = _run(pipeline_copy, load_build, neutralise=True)
    a = pd.read_excel(full / XLSX, sheet_name=None, header=None)
    b = pd.read_excel(ref / XLSX, sheet_name=None, header=None)
    assert list(a) == list(b), "ordre / noms des feuilles existantes modifiés"
    for sheet in a:
        if sheet in NEW_SHEETS:
            continue
        pdt.assert_frame_equal(a[sheet], b[sheet], obj=f"feuille {sheet}")
    assert (full / "RLL-IDF-villes-x-verticals-v0.1.csv").read_bytes() == \
           (ref / "RLL-IDF-villes-x-verticals-v0.1.csv").read_bytes()
    assert list(a)[-2:] == NEW_SHEETS, "les nouvelles feuilles doivent venir après les feuilles existantes"


def test_comparables_content(pipeline_copy, load_build):
    build, out = _run(pipeline_copy, load_build)
    lo, hi = build.COMPARABLES_POP
    comp = pd.read_excel(out / XLSX, sheet_name="comparables", dtype={"code_insee": str})
    vert = pd.read_excel(out / XLSX, sheet_name="comparables_verticals", dtype={"code_insee": str})
    main = pd.read_excel(out / XLSX, sheet_name="villes_x_verticals", dtype={"code_insee": str})

    expected = {c for c, _, _, p in COMMUNES if lo <= p <= hi}
    assert set(comp["code_insee"]) == expected            # 3 000 et 60 000 hab. exclus
    assert comp["rang_tranche"].tolist() == list(range(1, len(comp) + 1))
    assert comp["score_potentiel_RLL"].is_monotonic_decreasing
    # score et rang IDF repris tels quels de villes_x_verticals
    m = main.set_index("code_insee")
    assert (comp.set_index("code_insee")["score_potentiel_RLL"] == m.loc[comp["code_insee"], "score_potentiel_RLL"]).all()
    assert (comp.set_index("code_insee")["rang_IDF"] == m.loc[comp["code_insee"], "rang"]).all()
    assert set(comp.loc[comp["pilote"], "code_insee"]) == set(build.PILOTES)
    for c in ["assos_per_1k", "licences_per_1k", "equipements_per_1k"] + [f"assos_{v}" for v in build.VERTICALS]:
        assert c in comp.columns

    assert set(vert["vertical"]) == set(build.COMPARABLES_VERTICALS) and len(build.COMPARABLES_VERTICALS) == 10
    for v, g in vert.groupby("vertical"):
        assert len(g) == min(build.COMPARABLES_TOP_N, len(comp))
        assert g["assos_vertical_pour_1000_hab"].is_monotonic_decreasing
        assert set(g["code_insee"]) <= expected
        exp = (comp.set_index("code_insee").loc[g["code_insee"], f"assos_{v}"]).tolist()
        assert g["assos_vertical"].tolist() == exp

    csv = pd.read_csv(out / "RLL-IDF-comparables-v0.1.csv", sep=";", dtype={"code_insee": str}, encoding="utf-8-sig")
    pdt.assert_frame_equal(csv, comp, check_dtype=False)
    shutil.rmtree(out)
