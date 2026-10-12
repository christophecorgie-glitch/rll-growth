"""G-002 — build.py n'écrit jamais en dehors de out/.

Sans réseau, sur des données fictives : build.py est copié dans un dossier temporaire à côté d'un
README-sources.md témoin, son cache raw/ est rempli de fichiers fictifs (avec leur .source.json), les
appels d'API sont servis par un faux `_fetch_once` et toute connexion réseau fait échouer le test.
Le build tourne avec les options par défaut ; seuls des fichiers de out/ peuvent avoir changé.

Lancer : python3 -m pytest pipelines/fr-idf/tests
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

from conftest import DATE, PIPELINE_DIR


def _snapshot(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and "__pycache__" not in p.parts}


def test_build_writes_only_in_out(tmp_path, monkeypatch, pipeline_copy, load_build):
    pipe = pipeline_copy
    # README témoin, avec les anciens marqueurs de bloc généré : aucune version de build.py ne doit y écrire
    (pipe / "README-sources.md").write_text("# README rédigé à la main — ne pas écraser\n\n"
                                            "<!-- BEGIN GENERATED: build.py -->\n<!-- END GENERATED: build.py -->\n",
                                            encoding="utf-8")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    build = load_build(pipe)

    repo_before = _snapshot(PIPELINE_DIR)
    before = _snapshot(tmp_path)
    build.main([])                                   # options par défaut : --workdir raw, --out out
    after = _snapshot(tmp_path)

    changed = {k for k in after if before.get(k) != after[k]} | (before.keys() - after.keys())
    outside = sorted(k for k in changed if not k.startswith("pipeline/out/"))
    assert outside == [], f"build.py a écrit en dehors de out/ : {outside}"
    assert _snapshot(PIPELINE_DIR) == repo_before, "build.py a modifié le pipeline du dépôt"
    assert {"pipeline/out/sources.md", "pipeline/out/RLL-IDF-villes-x-verticals-v0.1.xlsx",
            "pipeline/out/RLL-IDF-villes-x-verticals-v0.1.csv", "pipeline/out/RLL-IDF-comparables-v0.1.csv"} <= changed

    # Registre des sources : source, licence, date, nombre de lignes (feuille `sources` et out/sources.md)
    sheet = pd.read_excel(pipe / "out" / "RLL-IDF-villes-x-verticals-v0.1.xlsx", sheet_name="sources", dtype=str)
    assert {"source", "licence", "download_date", "rows_raw", "rows_kept_idf"} <= set(sheet.columns)
    ok = sheet[sheet["status"] == "OK"]
    assert set(ok["source_key"]) == {"etalab_cog", "rna", "injep", "data_es"}
    assert ok["licence"].str.len().gt(0).all() and (ok["download_date"] == DATE).all()
    assert ok["rows_raw"].notna().all() and ok["rows_kept_idf"].notna().all()
    md = (pipe / "out" / "sources.md").read_text(encoding="utf-8")
    rna_rows = int(ok.loc[ok["source_key"] == "rna", "rows_raw"].iloc[0])
    assert "| Licence |" in md and f"| {rna_rows} |" in md
