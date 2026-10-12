"""Données fictives partagées par les tests du pipeline fr-idf (sans réseau).

`pipeline_copy` copie build.py dans un dossier temporaire avec un cache raw/ complet de fichiers fictifs
(communes etalab, zip RNA avec des colonnes nominatives fictives, INJEP, Data ES) ; `load_build` charge
ce build.py comme un module neuf dont les appels d'API sont servis par un faux `_fetch_once`.
"""
from __future__ import annotations

import importlib.util
import io
import json
import shutil
import socket
import tarfile
import zipfile
from pathlib import Path

import pandas as pd
import pytest

PIPELINE_DIR = Path(__file__).resolve().parents[1]
DATE = "2026-10-01"
FAKE_PERSON = "Personne Fictive"   # valeur des colonnes RNA nominatives fictives

# Communes fictives (codes réels du focus par défaut, populations et noms inventés).
COMMUNES = [
    # code, nom, département, population
    ("91312", "Ville A", "91", 11000),
    ("91477", "Ville B", "91", 35000),
    ("91645", "Ville C", "91", 15000),
    ("92060", "Ville D", "92", 30000),
    ("91001", "Ville E", "91", 8000),
    ("91002", "Ville F", "91", 20000),
    ("92001", "Ville G", "92", 25000),
    ("92002", "Ville H", "92", 6000),
    ("77001", "Ville I", "77", 3000),
    ("78001", "Ville J", "78", 12000),
    ("92003", "Ville K", "92", 60000),   # au-dessus de la tranche comparables
]
FAMILIES = ["011000", "006000", "007000", "014000", "017000", "021000", "022000", "024000", "015000", "050000"]

API = {
    "etalab": "https://registry.npmjs.org/@etalab/decoupage-administratif",
    "rna": "https://www.data.gouv.fr/api/1/datasets/repertoire-national-des-associations/",
    "injep": "https://www.data.gouv.fr/api/1/datasets/donnees-geocodees-issues-du-recensement-des-licences-et-clubs-"
             "aupres-des-federations-sportives-agreees-par-le-ministere-charge-des-sports/",
    "data_es": "https://www.data.gouv.fr/api/1/datasets/data-es-recensement-des-equipements-sportifs-et-lieux-de-"
               "pratique-complet-1/",
}
FILES = {  # nom dans raw/ -> URL fictive de la ressource
    "decoupage-administratif-6.0.0.tgz": "https://registry.npmjs.org/fake/decoupage-administratif-6.0.0.tgz",
    "rna_waldec.zip": "https://media.interieur.gouv.fr/rna/rna_waldec_20261001.zip",
    "injep_licences.csv": "https://static.data.gouv.fr/fake/lic-data-2023.csv",
    "injep_clubs.csv": "https://static.data.gouv.fr/fake/clubs-data-2023.csv",
    "data_es.csv": "https://data.education.gouv.fr/fake/data-es-equipements.csv",
}


def _api_answers() -> dict[str, bytes]:
    def res(key, fmt):
        name = Path(FILES[key]).name
        return {"title": name, "url": FILES[key], "format": fmt, "last_modified": DATE}
    answers = {
        API["etalab"]: {"versions": {"6.0.0": {"dist": {"tarball": FILES["decoupage-administratif-6.0.0.tgz"]}}},
                        "time": {"6.0.0": "2026-03-09T00:00:00Z"}},
        API["rna"]: {"resources": [res("rna_waldec.zip", "zip")]},
        API["injep"]: {"resources": [res("injep_licences.csv", "csv"), res("injep_clubs.csv", "csv")]},
        API["data_es"]: {"resources": [res("data_es.csv", "csv")]},
    }
    return {k: json.dumps(v).encode() for k, v in answers.items()}


def _write_raw(raw: Path) -> None:
    raw.mkdir(parents=True)
    # @etalab/decoupage-administratif: communes, EPCI, EPT
    communes = [{"code": c, "nom": n, "departement": d, "population": p, "type": "commune-actuelle",
                 "codesPostaux": [f"{c[:2]}000"]} for c, n, d, p in COMMUNES]
    epci = [{"code": "200000001", "nom": "EPCI fictif", "type": "CA",
             "membres": [{"code": c} for c, _, d, _ in COMMUNES if d == "91"]}]
    ept = [{"code": "200000002", "nom": "EPT fictif", "membres": [{"code": c} for c, _, d, _ in COMMUNES if d == "92"]}]
    with tarfile.open(raw / "decoupage-administratif-6.0.0.tgz", "w:gz") as t:
        for name, obj in (("communes", communes), ("epci", epci), ("ept", ept)):
            data = json.dumps(obj).encode()
            info = tarfile.TarInfo(f"package/data/{name}.json")
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    # RNA: inclut des colonnes nominatives fictives, que build.py ne doit jamais lire
    rows = []
    for i, (code, *_rest) in enumerate(COMMUNES):
        for j in range(3 + i):
            rows.append({"id": f"W{code}{j:04d}", "date_creat": f"19{80 + j % 20}-01-01",
                         "titre": f"ASSOCIATION FICTIVE {i}-{j}", "objet": "objet fictif",
                         "objet_social1": FAMILIES[(i + j) % len(FAMILIES)], "objet_social2": "",
                         "adrs_codeinsee": code, "adrs_codepostal": f"{code[:2]}000", "adrs_libcommune": "X",
                         "position": "A", "nature": "D", "groupement": "S", "rup_mi": "", "siret": "",
                         "dir_civilite": "PM", "adrg_declarant": FAKE_PERSON, "siteweb": "",
                         "observation": FAKE_PERSON})
    buf = io.StringIO()
    pd.DataFrame(rows).to_csv(buf, sep=";", index=False)
    with zipfile.ZipFile(raw / "rna_waldec.zip", "w") as z:
        z.writestr("rna_waldec_20261001.csv", buf.getvalue().encode("utf-8-sig"))
    # INJEP licences et clubs
    for kind, val in (("licences", "Total"), ("clubs", "Clubs")):
        pd.DataFrame([{"Code Commune": c, "Département": d, "Code": "101", "Fédération": "Fédération fictive",
                       val: p // 10} for c, _, d, p in COMMUNES]) \
            .to_csv(raw / f"injep_{kind}.csv", sep=";", index=False, encoding="utf-8-sig")
    # Data ES
    pd.DataFrame([{"EquipementId": f"E{c}{k}", "ComInsee": c} for c, *_ in COMMUNES for k in range(2)]) \
        .to_csv(raw / "data_es.csv", sep=";", index=False, encoding="utf-8-sig")
    for name, url in FILES.items():
        (raw / f"{name}.source.json").write_text(json.dumps({"url": url, "downloaded": DATE, "title": Path(url).name}))


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("connexion réseau tentée pendant le test")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture
def pipeline_copy(tmp_path, no_network) -> Path:
    pipe = tmp_path / "pipeline"
    pipe.mkdir()
    shutil.copy(PIPELINE_DIR / "build.py", pipe / "build.py")
    _write_raw(pipe / "raw")
    return pipe


_N = 0


@pytest.fixture
def load_build(monkeypatch):
    """Fresh module from `pipe`/build.py (its module-level registers start empty), network calls faked."""
    def load(pipe: Path):
        global _N
        _N += 1
        spec = importlib.util.spec_from_file_location(f"build_under_test_{_N}", pipe / "build.py")
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
        answers = _api_answers()

        def fake_fetch_once(url, dest, timeout):
            assert dest is None, f"téléchargement inattendu (le cache raw/ est complet) : {url}"
            assert url in answers, f"URL inattendue : {url}"
            return answers[url]
        monkeypatch.setattr(build, "_fetch_once", fake_fetch_once)
        return build
    return load
