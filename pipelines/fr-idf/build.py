#!/usr/bin/env python3
"""
RLL — Île-de-France : villes x verticals (v0.1)
================================================
Builds RLL-IDF-villes-x-verticals-v0.1.xlsx (+ CSV, + the generated part of README-sources.md) from French
open data only. Scope: IDF départements 75, 77, 78, 91, 92, 93, 94, 95.

Sources (in order of attempt):
  1. RNA  — Répertoire National des Associations (data.gouv.fr, Licence Ouverte)
  2. INJEP — recensement géocodé des licences et clubs sportifs (data.gouv.fr, LO)
  3. Data ES — recensement des équipements sportifs (data.gouv.fr, fichiers servis par
     data.education.gouv.fr, LO 2.0)
  4. Communes + population — @etalab/decoupage-administratif (npm, Licence Ouverte pour les
     données), which republishes the INSEE COG and the INSEE populations de référence
     (population municipale). The package version is pinned so the millésime is known.

Former INSEE codes (communes fusionnées, déléguées, associées) found in the RNA, INJEP and Data ES
are replaced by the code of the current commune before aggregation (table from the etalab package).

Every download goes through the environment's HTTPS proxy. Connection cuts, timeouts and
HTTP 5xx are retried (up to 4 attempts, exponential back-off); 403 / 404 are never retried.
A failed download is logged in the `sources` sheet and README-sources.md and the pipeline
continues with what it has. Nothing is scraped.

Privacy: no personal data of natural persons is ever written to the outputs.
RNA columns naming people (dir_civilite, adrg_declarant, ...) are never loaded.

Focus (G-003): `--focus` takes INSEE codes (default: the 4 pilot cities) and adds the sheets
focus_profil, focus_associations and focus_vague2. They are views on the table above: they read the
same sources (raw/) plus the EPCI / EPT files of the same etalab package, and change nothing in
the other sheets nor in the CSV.

Reproduce:  python3 build.py [--workdir DIR] [--out DIR] [--focus CODE [CODE ...]]
Requires: pandas, openpyxl (pip install --break-system-packages pandas openpyxl)
"""
from __future__ import annotations

import argparse
import datetime as dt
import http.client
import io
import json
import os
import re
import ssl
import sys
import tarfile
import time
import unicodedata
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

PIPELINE_DIR = Path(__file__).resolve().parent
IDF_DEPS = ("75", "77", "78", "91", "92", "93", "94", "95")
TODAY = dt.date.today().isoformat()
VERSION = "v0.1"

# --------------------------------------------------------------------------- #
# Source catalogue
# --------------------------------------------------------------------------- #
SOURCES = {
    "rna": {
        "label": "RNA — Répertoire National des Associations (fichier Waldec mensuel)",
        "dataset_url": "https://www.data.gouv.fr/fr/datasets/repertoire-national-des-associations/",
        # The API lists the monthly zip resources; we pick the newest "rna_waldec_*" zip.
        "api_url": "https://www.data.gouv.fr/api/1/datasets/repertoire-national-des-associations/",
        "licence": "Licence Ouverte / Open Licence 2.0 (Etalab)",
        "producer": "Ministère de l'Intérieur (DLPAJ)",
    },
    "rna_agrege": {
        "label": "RNA agrégé à l'échelle nationale (fallback)",
        "dataset_url": "https://www.data.gouv.fr/fr/datasets/rna-agrege-a-lechelle-nationale/",
        "api_url": "https://www.data.gouv.fr/api/1/datasets/rna-agrege-a-lechelle-nationale/",
        "licence": "Licence Ouverte / Open Licence 2.0 (Etalab)",
        "producer": "data.gouv.fr (agrégation communautaire du RNA)",
    },
    "injep": {
        "label": "INJEP / Ministère des Sports — licences et clubs sportifs géocodés",
        "dataset_url": "https://www.data.gouv.fr/fr/datasets/donnees-geocodees-issues-du-recensement-des-licences-et-clubs-aupres-des-federations-sportives-agreees-par-le-ministere-charge-des-sports/",
        "api_url": "https://www.data.gouv.fr/api/1/datasets/donnees-geocodees-issues-du-recensement-des-licences-et-clubs-aupres-des-federations-sportives-agreees-par-le-ministere-charge-des-sports/",
        "licence": "Licence Ouverte / Open Licence 2.0 (Etalab)",
        "producer": "INJEP – MEDES / Ministère chargé des Sports",
    },
    "data_es": {
        "label": "Data ES — Recensement des équipements sportifs et lieux de pratique (complet)",
        # data.gouv.fr slug carries a "-1" suffix; the resources themselves are exports served by
        # data.education.gouv.fr (same producer, same licence).
        "dataset_url": "https://www.data.gouv.fr/fr/datasets/data-es-recensement-des-equipements-sportifs-et-lieux-de-pratique-complet-1/",
        "api_url": "https://www.data.gouv.fr/api/1/datasets/data-es-recensement-des-equipements-sportifs-et-lieux-de-pratique-complet-1/",
        "licence": "Licence Ouverte / Open Licence 2.0 (Etalab)",
        "producer": "Ministère chargé des Sports",
    },
    # Population: the former primary source `insee_pop` (INSEE file via data.gouv.fr) was removed
    # (decision 2026-10-01). @etalab/decoupage-administratif is the single population source.
    "etalab_cog": {
        "label": "@etalab/decoupage-administratif (npm) — COG + populations de référence INSEE republiés "
                 "(source de population)",
        "dataset_url": "https://www.npmjs.com/package/@etalab/decoupage-administratif",
        "api_url": "https://registry.npmjs.org/@etalab/decoupage-administratif",
        "licence": "Données : Licence Ouverte (Etalab) — code : MIT",
        "producer": "Etalab / DINUM (republication de l'INSEE)",
    },
}

# Pinned so that the INSEE millésime below stays true. Changing the version means checking
# the package README « Millésimes et versions de package » and its source list.
ETALAB_COG_VERSION = "6.0.0"
POP_MILLESIME = ("populations de référence 2023 (INSEE, en vigueur au 1er janvier 2026), "
                 "population municipale — https://www.insee.fr/fr/statistiques/8680726")

# --------------------------------------------------------------------------- #
# Focus (G-003): pilot cities and the thresholds of the focus_* views
# --------------------------------------------------------------------------- #
FOCUS_DEFAULT = ["91312", "91477", "91645", "92060"]   # Igny, Palaiseau, Verrières-le-Buisson, Le Plessis-Robinson
FOCUS_STRATE = (10_000, 40_000)    # comparison stratum (population municipale, bounds included)
FOCUS_TOP_N = 10                   # focus_associations: associations per city x vertical
VAGUE2_POP_MIN = 5_000             # focus_vague2: population threshold (score bias 1, micro-communes)
VAGUE2_TOP_N = 15

# --------------------------------------------------------------------------- #
# RNA Waldec "objet social" families -> RLL verticals
# The Waldec code is 6 characters; the first 3 identify the family (thème).
# Family labels follow the official RNA nomenclature ("Nomenclature des objets
# sociaux", ministère de l'Intérieur), checked on 2026-10-01 against the titles of the RNA file
# (families 025-029 and 100 have no association; 030-050 were shifted in v0.1). The data.gouv.fr
# dataset "Répertoire National des Associations - Nomenclature WALDEC" (LO 2.0) points to a host
# the build environment cannot reach; labels were cross-checked on its republications.
# Keyword rules refine a few families.
# --------------------------------------------------------------------------- #
WALDEC_FAMILY_LABELS = {
    "001": "Activités politiques",
    "002": "Clubs, cercles de réflexion",
    "003": "Défense de droits fondamentaux, activités civiques",
    "004": "Justice",
    "005": "Information, communication",
    "006": "Culture, pratiques d'activités artistiques, culturelles",
    "007": "Clubs de loisirs, relations",
    "009": "Action socio-culturelle",
    "010": "Préservation du patrimoine",
    "011": "Sports, activités de plein air",
    "013": "Chasse, pêche",
    "014": "Amicales, groupements affinitaires, groupements d'entraide (hors défense de droits fondamentaux)",
    "015": "Éducation, formation",
    "016": "Recherche",
    "017": "Santé",
    "018": "Services et établissements médico-sociaux",
    "019": "Interventions sociales",
    "020": "Associations caritatives, humanitaires, aide au développement, développement du bénévolat",
    "021": "Services familiaux, services aux personnes âgées",
    "022": "Conduite d'activités économiques",
    "023": "Représentation, promotion et défense d'intérêts économiques",
    "024": "Environnement, cadre de vie",
    "030": "Aide à l'emploi, développement local, promotion de solidarités économiques, vie locale",
    "032": "Logement",
    "034": "Tourisme",
    "036": "Sécurité, protection civile",
    "038": "Armée (dont préparation militaire, médailles), anciens combattants",
    "040": "Activités religieuses, spirituelles ou philosophiques",
    "050": "Domaines divers, domaines de nomenclature SITADELE à reclasser",
}

# Mapping validated by Christophe on 2026-10-01 (PR #2 review). Codes 025-029 do not exist in
# the nomenclature; any code absent from this table (000, 008, 012, blank ...) falls in "other".
WALDEC_FAMILY_TO_VERTICAL = {
    "011": "sport",
    "013": "nature",     # chasse, pêche -> outdoor / nature
    "024": "nature",     # environnement, cadre de vie
    "006": "culture",
    "010": "culture",    # patrimoine
    "005": "culture",    # information, communication (médias, radios associatives)
    "007": "fun",        # clubs de loisirs, relations (refined by keywords -> gaming)
    "034": "fun",        # tourisme
    "017": "wellness",   # santé (refined: bien-être, yoga, méditation...)
    "018": "wellness",   # médico-social
    "021": "family",     # services familiaux, personnes âgées
    "022": "business",
    "023": "business",
    "030": "business",   # aide à l'emploi, développement local
    "014": "asso",       # amicales, groupements affinitaires (refined -> alumni)
    "003": "asso",
    "004": "asso",
    "009": "asso",
    "015": "asso",       # éducation / formation (parents d'élèves, soutien scolaire)
    "016": "asso",
    "019": "asso",
    "020": "asso",
    "032": "asso",       # logement
    "036": "asso",       # sécurité, protection civile
    "038": "asso",       # armée, anciens combattants
    "040": "asso",       # activités religieuses, spirituelles ou philosophiques
    "001": "other",
    "002": "other",
    "050": "other",      # domaines divers
}

VERTICALS = ["sport", "fun", "culture", "wellness", "family", "business",
             "alumni", "asso", "gaming", "nature", "other"]

# Keyword refinements applied on titre + objet (lower-cased, accents kept).
KW_ALUMNI = re.compile(r"anciens?\s+(?:élèves|eleves|étudiants|etudiants)|alumni|ancien(?:ne)?s?\s+de\s+l['’]?école", re.I)
KW_GAMING = re.compile(r"jeux?\s+vid[ée]o|e-?sport|esport|jeu[x]?\s+de\s+r[ôo]le|ludique|jeux?\s+de\s+soci[ée]t[ée]|jeux?\s+de\s+plateau|figurines|wargame|manga|cosplay", re.I)
KW_WELLNESS = re.compile(r"bien[- ]?[êe]tre|yoga|m[ée]ditation|sophrologie|relaxation|qi\s?gong|pilates|zen\b", re.I)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
LOG: list[dict] = []          # one entry per source attempt -> `sources` sheet
NOTES: list[str] = []         # free-text caveats -> README


def log_source(key, url, status, rows_raw=None, rows_kept=None, detail="", licence=None, date=None):
    meta = SOURCES.get(key, {})
    LOG.append({
        "source_key": key,
        "source": meta.get("label", key),
        "producer": meta.get("producer", ""),
        "url": url,
        "licence": licence or meta.get("licence", ""),
        "download_date": TODAY if date is None else date,
        "status": status,
        "rows_raw": rows_raw,
        "rows_kept_idf": rows_kept,
        "detail": detail,
    })
    print(f"[{status}] {key}: {url} — {detail}", file=sys.stderr)


FETCH_ATTEMPTS = 4            # total attempts, first one included
FETCH_BACKOFF = 5             # seconds; waits 5, 10, 20 between attempts (relay cuts come in bursts)
# urllib's timeout applies to each socket operation (connect, each read), not to the whole
# transfer: a 410 MB RNA zip may take as long as it needs as long as bytes keep arriving.
TIMEOUT_API = 60
TIMEOUT_FILE = 300

# Connection cuts and timeouts: retried. Anything else (bad URL, TLS verification, ...) is not.
_TRANSIENT = (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError,
              http.client.RemoteDisconnected, http.client.IncompleteRead,
              ssl.SSLEOFError, ssl.SSLZeroReturnError)


def _is_transient(e: BaseException) -> bool:
    if isinstance(e, urllib.error.HTTPError):
        return 500 <= e.code < 600          # 5xx only; never 403 / 404 / other 4xx
    if isinstance(e, urllib.error.URLError):
        reason = e.reason
        if isinstance(reason, BaseException):
            return _is_transient(reason)
        return False                        # e.g. "Tunnel connection failed: 403 Forbidden"
    if isinstance(e, OSError) and re.search(r"Tunnel connection failed: (403|404|407)", str(e)):
        return False                        # proxy refusal: a policy, not an outage
    return isinstance(e, _TRANSIENT)


def _fetch_once(url: str, dest: Path | None, timeout: int) -> bytes | Path:
    ctx = ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE") or None)
    headers = {"User-Agent": "rll-opendata-build/0.1"}
    part = dest.with_name(dest.name + ".part") if dest is not None else None
    offset = part.stat().st_size if part is not None and part.exists() else 0
    if offset:
        headers["Range"] = f"bytes={offset}-"   # resume after a cut; restart if server ignores it
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        if dest is None:
            return r.read()
        dest.parent.mkdir(parents=True, exist_ok=True)
        mode = "ab" if offset and r.status == 206 else "wb"
        expected = r.headers.get("Content-Length")
        written = 0
        with open(part, mode) as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                written += len(chunk)
        if expected is not None and written < int(expected):
            raise http.client.IncompleteRead(b"", int(expected) - written)
        part.replace(dest)                  # only a complete file ever gets the final name
        return dest


def fetch(url: str, dest: Path | None = None, timeout: int | None = None) -> bytes | Path:
    """GET through the environment proxy, with retries on connection cuts, timeouts and 5xx.
    Raises the last urllib / OSError on failure. With `dest`, streams to `dest.part` and
    renames on completion; a retry resumes from the partial file when the server allows."""
    timeout = timeout or (TIMEOUT_FILE if dest is not None else TIMEOUT_API)
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            return _fetch_once(url, dest, timeout)
        except Exception as e:  # noqa: BLE001
            if attempt == FETCH_ATTEMPTS or not _is_transient(e):
                raise
            wait = FETCH_BACKOFF * 2 ** (attempt - 1)
            print(f"[retry {attempt}/{FETCH_ATTEMPTS - 1}] {url} — {type(e).__name__}: {e}; "
                  f"next attempt in {wait}s", file=sys.stderr)
            time.sleep(wait)


def try_fetch(key, url, dest=None):
    try:
        return fetch(url, dest)
    except urllib.error.HTTPError as e:
        log_source(key, url, "FAILED", detail=f"HTTP {e.code} {e.reason}")
    except urllib.error.URLError as e:
        log_source(key, url, "FAILED", detail=f"blocked / unreachable: {e.reason}")
    except Exception as e:  # noqa: BLE001
        log_source(key, url, "FAILED", detail=f"{type(e).__name__}: {e}")
    return None


def _cache_meta_path(dest: Path) -> Path:
    return dest.with_name(dest.name + ".source.json")


def cache_meta(dest: Path) -> dict | None:
    """Metadata of a file already in raw/ (origin URL, download date), or None if absent.
    A file without its .source.json (left by an older build) has an unknown origin."""
    if not dest.exists():
        return None
    try:
        return json.loads(_cache_meta_path(dest).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"url": "", "downloaded": "", "title": ""}


def fetch_cached(key: str, url: str, dest: Path, title: str = "") -> dict | None:
    """Return metadata of the file actually read: the copy in raw/ when present, otherwise a fresh
    download of `url` (its origin and date are written next to it, in <dest>.source.json)."""
    meta = cache_meta(dest)
    if meta is not None:
        return {**meta, "cache": True}
    if not try_fetch(key, url, dest):
        return None
    meta = {"url": url, "downloaded": TODAY, "title": title}
    _cache_meta_path(dest).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return {**meta, "cache": False}


def origin_note(meta: dict) -> str:
    if not meta.get("cache"):
        return f"téléchargé pendant ce build ({meta['downloaded']})"
    if not meta.get("url"):
        return "cache local raw/ sans métadonnées d'origine (fichier antérieur à ce build)"
    return f"cache local raw/ (téléchargé le {meta['downloaded']})"


def is_idf_code(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str[:2].isin(IDF_DEPS)


# Former INSEE codes (communes fusionnées, déléguées, associées, renumérotées) -> current commune.
# Filled by load_communes() from @etalab/decoupage-administratif, national scope.
INSEE_REMAP: dict[str, str] = {}
INSEE_CURRENT: set[str] = set()      # IDF communes actuelles + arrondissements municipaux
COMMUNE_NAMES: dict[str, str] = {}   # every code in the package (current and former) -> nom
# IDF commune -> its territory for focus_vague2: EPT for the Métropole du Grand Paris (ept.json),
# EPCI à fiscalité propre otherwise (epci.json). Filled by load_communes(); read by the focus views only.
TERRITOIRES: dict[str, dict] = {}
TERRITOIRES_SRC: dict[str, int] = {}
FOCUS_INFO: dict = {}                # focus codes and reference sizes -> README
REMAP_REPORT: list[dict] = []        # per source: orphans before / after -> README + sheet
REMAP_DETAIL: list[dict] = []        # per source x former code: rows / value reassigned


def remap_insee(codes: pd.Series, source: str, weight: pd.Series | None = None) -> pd.Series:
    """Replace former INSEE codes by the code of the current commune and record the gap
    (rows whose code is not an IDF commune / arrondissement) before and after."""
    codes = codes.fillna("").astype(str).str.strip()
    w = pd.Series(1.0, index=codes.index) if weight is None else pd.to_numeric(weight, errors="coerce").fillna(0)
    new = codes.map(INSEE_REMAP).fillna(codes)
    changed = new != codes
    before = ~codes.isin(INSEE_CURRENT)
    after = ~new.isin(INSEE_CURRENT)
    REMAP_REPORT.append({
        "source": source, "total": float(w.sum()),
        "hors_communes_avant": float(w[before].sum()), "hors_communes_apres": float(w[after].sum()),
        "reaffectes": float(w[changed].sum()),
        "codes_restants": ", ".join(f"{k} ({int(v)})" for k, v in w[after].groupby(codes[after]).sum()
                                    .sort_values(ascending=False).items()),
    })
    if changed.any():
        g = pd.DataFrame({"ancien": codes[changed], "actuel": new[changed], "n": w[changed]}) \
            .groupby(["ancien", "actuel"], as_index=False)["n"].sum()
        for r in g.itertuples():
            REMAP_DETAIL.append({"source": source, "ancien_code": r.ancien, "code_actuel": r.actuel, "n": r.n})
    return new


def pick_resource(api_json: dict, pattern: str, fmt=None):
    """Newest resource whose title/url matches `pattern`."""
    res = [r for r in api_json.get("resources", [])
           if re.search(pattern, (r.get("title") or "") + " " + (r.get("url") or ""), re.I)
           and (fmt is None or (r.get("format") or "").lower() == fmt)]
    res.sort(key=lambda r: r.get("last_modified") or "", reverse=True)
    return res[0] if res else None


# --------------------------------------------------------------------------- #
# 4. Communes + population (backbone)
# --------------------------------------------------------------------------- #
def build_insee_remap(communes: list[dict]) -> None:
    """Former code -> current commune, from the package itself:
    - communes déléguées / associées whose code differs from their chef-lieu -> chefLieu;
    - `anciensCodes` of a current commune (fusions, renumbering, 1968 départements) -> its code.
    A code that is still a current commune / arrondissement is never remapped; a former code
    claimed by two different current communes (2 cases, none in IDF) is left as is."""
    current = {c["code"] for c in communes if c["type"] in ("commune-actuelle", "arrondissement-municipal")}
    cand: dict[str, set[str]] = {}
    for c in communes:
        COMMUNE_NAMES.setdefault(c["code"], c["nom"])
        target = c["code"] if c["type"] in ("commune-actuelle", "arrondissement-municipal") else c.get("chefLieu")
        if c["type"] in ("commune-deleguee", "commune-associee") and c["code"] != target:
            cand.setdefault(c["code"], set()).add(target)
        for a in c.get("anciensCodes", []):
            cand.setdefault(a, set()).add(target)
    INSEE_REMAP.clear()
    INSEE_REMAP.update({k: next(iter(v)) for k, v in cand.items()
                        if len(v) == 1 and k not in current and None not in v})
    INSEE_CURRENT.clear()
    INSEE_CURRENT.update(c["code"] for c in communes if c["code"] in current and c.get("departement") in IDF_DEPS)


def load_territoires(epci: list[dict], ept: list[dict]) -> None:
    """IDF commune -> territory used by focus_vague2. The Métropole du Grand Paris (METRO, 130 communes)
    is too wide to mean "nearby": its communes take their établissement public territorial (ept.json,
    INSEE, 11 EPT). Paris (75056) is a territory on its own and is in no EPT: it keeps the MGP as EPCI
    and focus_vague2 falls back on the département for it."""
    TERRITOIRES.clear()
    for e in epci:
        for m in e["membres"]:
            if m["code"][:2] in IDF_DEPS:
                TERRITOIRES[m["code"]] = {"type": "EPCI", "code": e["code"], "nom": e["nom"],
                                          "metropole": e["type"] == "METRO"}
    for e in ept:
        for m in e["membres"]:
            TERRITOIRES[m["code"]] = {"type": "EPT", "code": e["code"], "nom": e["nom"], "metropole": True}
    TERRITOIRES_SRC.update(n_epci=len(epci), n_ept=len(ept))


def load_communes(workdir: Path) -> pd.DataFrame:
    """Return DataFrame: code_insee, nom, departement, population, niveau, commune_parent.
    Source: @etalab/decoupage-administratif (INSEE COG + populations de référence)."""
    tgz = workdir / f"decoupage-administratif-{ETALAB_COG_VERSION}.tgz"
    meta = try_fetch("etalab_cog", SOURCES["etalab_cog"]["api_url"])
    if not meta:
        raise SystemExit("No commune backbone available — cannot build the table.")
    m = json.loads(meta)
    ver = ETALAB_COG_VERSION
    tarball = m["versions"][ver]["dist"]["tarball"]
    pub = m["time"].get(ver, "")[:10]
    got = fetch_cached("etalab_cog", tarball, tgz)
    if not got:
        raise SystemExit("No commune backbone available — cannot build the table.")
    with tarfile.open(tgz) as t:
        communes = json.load(t.extractfile("package/data/communes.json"))
        epci = json.load(t.extractfile("package/data/epci.json"))
        ept = json.load(t.extractfile("package/data/ept.json"))
    build_insee_remap(communes)
    load_territoires(epci, ept)
    rows = []
    for c in communes:
        if c.get("departement") not in IDF_DEPS:
            continue
        if c["type"] not in ("commune-actuelle", "arrondissement-municipal"):
            continue  # communes déléguées / associées are counted in their parent
        rows.append({
            "code_insee": c["code"],
            "nom": c["nom"],
            "departement": c["departement"],
            "population": c.get("population"),
            "niveau": "arrondissement" if c["type"] == "arrondissement-municipal" else "commune",
            "commune_parent": c.get("commune", c["code"]),
            "codes_postaux": "|".join(c.get("codesPostaux", [])),
        })
    out = pd.DataFrame(rows)
    LOG.append({
        "source_key": "insee_pop", "source": "INSEE — Populations légales par commune (fichier national)",
        "producer": "INSEE", "url": "https://www.insee.fr/fr/statistiques/8680726",
        "licence": "Licence Ouverte / Open Licence 2.0 (Etalab)", "download_date": "", "status": "RETIRÉE",
        "rows_raw": None, "rows_kept_idf": None,
        "detail": f"Source retirée le 2026-10-01, remplacée par etalab_cog ({ver}) qui republie le même "
                  f"fichier INSEE : {POP_MILLESIME}. Non téléchargée."})
    log_source("etalab_cog", tarball, "OK", rows_raw=len(communes), rows_kept=len(out),
               detail=f"package {ver} (publié le {pub}) — {origin_note(got)} ; population = {POP_MILLESIME}",
               date=got["downloaded"])
    NOTES.append(f"Population : {POP_MILLESIME}, telles que republiées par @etalab/decoupage-administratif "
                 f"{ver} (publié le {pub}, version épinglée dans build.py). Cette source remplace l'ancienne "
                 f"source primaire `insee_pop` (fichier INSEE via data.gouv.fr), retirée le 2026-10-01.")
    return out


# --------------------------------------------------------------------------- #
# 1. RNA
# --------------------------------------------------------------------------- #
RNA_USECOLS = ["id", "date_creat", "titre", "objet", "objet_social1", "objet_social2",
               "adrs_codeinsee", "adrs_codepostal", "adrs_libcommune", "position", "nature",
               "groupement", "rup_mi", "siret"]
# siret / rup_mi are only turned into yes/no flags (size proxy for the top_associations sheets).
# NOTE: dir_civilite, adrg_declarant, adrg_* (declarant's address), siteweb ... are deliberately
# never loaded: no personal data of natural persons in the outputs.


def classify_vertical(fam: pd.Series, text: pd.Series) -> pd.Series:
    v = fam.map(WALDEC_FAMILY_TO_VERTICAL).fillna("other")
    t = text.fillna("")
    v = v.where(~t.str.contains(KW_ALUMNI), "alumni")
    v = v.where(~((v == "fun") & t.str.contains(KW_GAMING)), "gaming")
    v = v.where(~((v.isin(["fun", "sport", "asso"])) & t.str.contains(KW_WELLNESS)), "wellness")
    return v


def load_rna(workdir: Path) -> pd.DataFrame | None:
    """IDF active associations with vertical. Reads the monthly Waldec zip in chunks."""
    # The file actually read decides what the `sources` sheet says: a zip already in raw/ is
    # logged with its own origin URL and download date, whatever the API answers. The fallback
    # dataset rna_agrege is only tried when there is neither an API answer nor a cached zip, and
    # stays NOT_USED otherwise.
    zpath = workdir / "rna_waldec.zip"
    api_err = None
    try:
        api = fetch(SOURCES["rna"]["api_url"])
    except Exception as e:  # noqa: BLE001
        api, api_err = None, f"{type(e).__name__}: {getattr(e, 'reason', e)}"
    key, got, r = "rna", None, None
    if api:
        r = pick_resource(json.loads(api), r"rna_waldec", fmt="zip")
        if r:
            got = fetch_cached("rna", r["url"], zpath, r.get("title", ""))
        elif cache_meta(zpath) is None:
            log_source("rna", SOURCES["rna"]["api_url"], "FAILED", detail="no rna_waldec zip in API listing")
    if got is None and zpath.exists():
        got = fetch_cached("rna", "", zpath)        # cached zip: no download
    if got is None:
        if api_err:
            log_source("rna", SOURCES["rna"]["api_url"], "FAILED", detail=f"API unreachable: {api_err}")
        api2 = try_fetch("rna_agrege", SOURCES["rna_agrege"]["api_url"])
        r = pick_resource(json.loads(api2), r"waldec", fmt="zip") if api2 else None
        if api2 and not r:
            log_source("rna_agrege", SOURCES["rna_agrege"]["api_url"], "FAILED",
                       detail="no zip resource in API listing (the parquet export is not read by build.py)")
        if not r:
            return None
        key, zpath = "rna_agrege", workdir / "rna_agrege_waldec.zip"
        got = fetch_cached("rna_agrege", r["url"], zpath, r.get("title", ""))
        if not got:
            return None
    note = origin_note(got)
    if api_err:
        note += f" ; API data.gouv.fr indisponible pendant ce build ({api_err})"
    elif r and got.get("cache") and got.get("url") and got["url"] != r["url"]:
        note += f" ; fichier plus récent listé par l'API, non téléchargé : {r['url']}"
    kept, raw_total = [], 0
    with zipfile.ZipFile(zpath) as z:
        for name in z.namelist():
            if not name.lower().endswith(".csv"):
                continue
            with z.open(name) as f:
                # Waldec CSVs: UTF-8 with BOM, every field double-quoted (objet may contain ';').
                for chunk in pd.read_csv(f, sep=";", dtype=str, encoding="utf-8-sig",
                                         usecols=lambda c: c in RNA_USECOLS,
                                         chunksize=200_000, on_bad_lines="skip"):
                    raw_total += len(chunk)
                    cp = chunk.get("adrs_codepostal", pd.Series(index=chunk.index, dtype=str))
                    ci = chunk.get("adrs_codeinsee", pd.Series(index=chunk.index, dtype=str))
                    m = (is_idf_code(cp.fillna("")) | is_idf_code(ci.fillna(""))) & \
                        (chunk["position"].fillna("").str.strip().str.upper() == "A")
                    kept.append(chunk[m])
    df = pd.concat(kept, ignore_index=True) if kept else pd.DataFrame(columns=RNA_USECOLS)
    df["code_insee"] = df["adrs_codeinsee"].fillna("").str.strip()
    # fall back on code postal -> code insee is ambiguous; keep only rows with a code insee
    # (an IDF code postal with a non-IDF code INSEE is dropped too). Former codes (communes
    # fusionnées...) are first replaced by the current commune, so the IDF test sees both.
    current = df["code_insee"].map(INSEE_REMAP).fillna(df["code_insee"])
    df = df[(df["code_insee"].str.len() == 5) & (is_idf_code(df["code_insee"]) | is_idf_code(current))].copy()
    df["code_insee"] = remap_insee(df["code_insee"], "RNA (associations actives)")
    df = df[is_idf_code(df["code_insee"])].copy()
    df["famille_objet"] = df["objet_social1"].fillna("").str.strip().str[:3]
    df["vertical"] = classify_vertical(df["famille_objet"], df["titre"].fillna("") + " " + df["objet"].fillna(""))
    log_source(key, got["url"] or "origine inconnue", "OK", rows_raw=raw_total, rows_kept=len(df),
               date=got["downloaded"] or "inconnue",
               detail=f"{Path(got['url']).name or zpath.name} — {note} — filtered position='A' and IDF code INSEE/CP")
    if key == "rna":
        log_source("rna_agrege", SOURCES["rna_agrege"]["api_url"], "NOT_USED",
                   detail="source de secours non lue (fichier Waldec disponible)", date="")
    return df


# --------------------------------------------------------------------------- #
# 2. INJEP licences & clubs
# --------------------------------------------------------------------------- #
def _norm(col: str) -> str:
    """'Code Commune' -> 'code_commune', 'Département' -> 'departement' (BOM, accents, spaces)."""
    col = unicodedata.normalize("NFKD", str(col).lstrip("\ufeff")).encode("ascii", "ignore").decode()
    return re.sub(r"[\s\-]+", "_", col.strip().lower())


def _find(cols, *cands):
    low = {_norm(c): c for c in cols}
    for c in cands:
        for k, orig in low.items():
            if re.fullmatch(c, k):
                return orig
    return None


def load_injep(workdir: Path):
    """Returns (per_commune DataFrame[code_insee, licences, clubs], per_fed DataFrame) or (None, None)."""
    api = try_fetch("injep", SOURCES["injep"]["api_url"])
    if not api:
        return None, None
    j = json.loads(api)
    # Match the file name only: every resource URL contains "licences-et-clubs" (dataset slug).
    lic = pick_resource(j, r"(^|/)lic-data-\d{4}\.csv", fmt="csv")
    clubs = pick_resource(j, r"(^|/)clubs-data-\d{4}\.csv", fmt="csv")
    per_commune, per_fed = None, None
    frames = {}
    for kind, r in (("licences", lic), ("clubs", clubs)):
        if not r:
            continue
        p = workdir / f"injep_{kind}.csv"
        got = fetch_cached("injep", r["url"], p, r.get("title", ""))
        if not got:
            continue
        parts = []
        raw = 0
        for chunk in pd.read_csv(p, sep=";", dtype=str, encoding="utf-8-sig", chunksize=200_000):
            raw += len(chunk)
            code = _find(chunk.columns, r"code_commune", r"codgeo", r"insee.*", r"code_insee", r"com_code")
            dep = _find(chunk.columns, r"dep_code", r"code_dep.*", r"departement_code", r"departement")
            if code is None:
                break
            m = is_idf_code(chunk[code].fillna("")) if dep is None else chunk[dep].astype(str).isin(IDF_DEPS)
            parts.append(chunk[m])
        if not parts:
            log_source("injep", r["url"], "FAILED", rows_raw=raw, detail="commune column not recognised")
            continue
        df = pd.concat(parts, ignore_index=True)
        code = _find(df.columns, r"code_commune", r"codgeo", r"insee.*", r"code_insee", r"com_code")
        fed = _find(df.columns, r"federation", r"fed_libelle", r"fede.*", r"nom_fed.*")
        fedcode = _find(df.columns, r"fed_code", r"code_fed.*", r"code")
        # value column: clubs file -> "Clubs" (clubs only, EPA excluded); licences file -> "Total"
        # (all ages, both sexes). Older layouts: l_2022 / c_2022 / nb_*.
        val = _find(df.columns, r"clubs") if kind == "clubs" else _find(df.columns, r"total")
        if val is None:
            valcols = [c for c in df.columns if re.fullmatch(r"(l|c)_\d{4}|total.*|nb_.*|licences?|clubs?", _norm(c))]
            val = valcols[-1] if valcols else None
        if val is None:
            log_source("injep", r["url"], "FAILED", rows_raw=raw, detail="value column not recognised")
            continue
        df[val] = pd.to_numeric(df[val], errors="coerce").fillna(0)
        df["code_insee"] = remap_insee(df[code].astype(str).str.strip().str.zfill(5), f"INJEP {kind}", df[val])
        year = re.search(r"\d{4}", val) or re.search(r"\d{4}", r.get("title", ""))
        frames[kind] = (df, val, fed, fedcode, year.group(0) if year else "?")
        log_source("injep", got["url"] or r["url"], "OK", rows_raw=raw, rows_kept=len(df), date=got["downloaded"],
                   detail=f"{r.get('title','')} — {origin_note(got)} — value column '{val}'")
    if not frames:
        return None, None
    pc = None
    pf = None
    for kind, (df, val, fed, fedcode, year) in frames.items():
        g = df.groupby("code_insee", as_index=False)[val].sum().rename(columns={val: kind})
        pc = g if pc is None else pc.merge(g, on="code_insee", how="outer")
        if fed:
            gf = df.groupby([c for c in (fedcode, fed) if c], as_index=False)[val].sum().rename(columns={val: f"{kind}_{year}"})
            pf = gf if pf is None else pf.merge(gf, how="outer")
        NOTES.append(f"INJEP {kind}: year {year} (latest available in the dataset at build time).")
    return pc, pf


# --------------------------------------------------------------------------- #
# 3. Data ES equipments
# --------------------------------------------------------------------------- #
def load_data_es(workdir: Path):
    api = try_fetch("data_es", SOURCES["data_es"]["api_url"])
    if not api:
        return None
    j = json.loads(api)
    r = pick_resource(j, r"equipement|équipement|data-es|dataes", fmt="csv") or pick_resource(j, r"csv")
    if not r:
        log_source("data_es", SOURCES["data_es"]["api_url"], "FAILED", detail="no CSV resource found")
        return None
    p = workdir / "data_es.csv"
    got = fetch_cached("data_es", r["url"], p, r.get("title", ""))
    if not got:
        return None
    parts, raw = [], 0
    for chunk in pd.read_csv(p, sep=";", dtype=str, encoding="utf-8-sig", chunksize=100_000, on_bad_lines="skip"):
        raw += len(chunk)
        code = _find(chunk.columns, r"cominsee", r"com_insee", r"commune_insee", r"inst_com_code", r"code_insee",
                     r"insee.*")
        eq = _find(chunk.columns, r"equipementid", r"equ_id", r"id_equipement", r"numero.*equip.*")
        if code is None:
            break
        m = is_idf_code(chunk[code].fillna(""))
        sub = chunk.loc[m, [code] + ([eq] if eq else [])].rename(columns={code: "code_insee"})
        parts.append(sub)
    if not parts:
        log_source("data_es", r["url"], "FAILED", rows_raw=raw, detail="commune column not recognised")
        return None
    df = pd.concat(parts, ignore_index=True)
    eqcol = [c for c in df.columns if c != "code_insee"]
    if eqcol:
        df = df.drop_duplicates(subset=eqcol)
    df["code_insee"] = remap_insee(df["code_insee"], "Data ES (équipements)")
    g = df.groupby("code_insee").size().rename("equipements").reset_index()
    log_source("data_es", got["url"] or r["url"], "OK", rows_raw=raw, rows_kept=len(df), date=got["downloaded"],
               detail=f"{r.get('title', '')} — {origin_note(got)}")
    return g


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def rollup_paris(df: pd.DataFrame) -> pd.DataFrame:
    """Add a 75056 (Paris) row = sum of arrondissement rows 75101-75120, unless the source
    already carries 75056 itself (then arrondissement rows are left as they are)."""
    df = df.copy()
    if "75056" in set(df["code_insee"]):
        return df
    arr = df[df["code_insee"].str.startswith("751")]
    if arr.empty:
        return df
    tot = arr.drop(columns="code_insee").sum(numeric_only=True)
    tot["code_insee"] = "75056"
    return pd.concat([df, pd.DataFrame([tot])], ignore_index=True)


TOP_N_PER_COMMUNE = 5
GROUPEMENT_RANK = {"F": 0, "U": 1}   # fédération, union, then simple association (S / blank)
GROUPEMENT_LABEL = {"F": "fédération", "U": "union", "S": "simple"}
TOP_CRITERE = (
    f"{TOP_N_PER_COMMUNE} associations par commune (ou arrondissement de Paris) et par feuille. Le RNA ne "
    "publie ni nombre d'adhérents, ni budget, ni effectif : la taille est approchée, dans l'ordre, par "
    "(1) le groupement (fédération, puis union, puis association simple), (2) la reconnaissance d'utilité "
    "publique (rup_mi renseigné), (3) l'immatriculation SIRENE (siret renseigné : employeur, subventions "
    "ou activité économique), (4) l'ancienneté (date de création la plus ancienne), puis le numéro RNA.")


def top_associations(sub: pd.DataFrame, communes: pd.DataFrame, n: int = TOP_N_PER_COMMUNE) -> pd.DataFrame:
    """Biggest associations (size proxy, see TOP_CRITERE) of every commune for one sheet."""
    sub = sub.copy()
    grp = sub["groupement"].fillna("").str.strip().str.upper()
    sub["groupement_type"] = grp.map(GROUPEMENT_LABEL).fillna("simple")
    sub["reconnue_utilite_publique"] = np.where(sub["rup_mi"].fillna("").str.strip() != "", "oui", "non")
    sub["siret_renseigne"] = np.where(sub["siret"].fillna("").str.strip() != "", "oui", "non")
    sub["_g"] = grp.map(GROUPEMENT_RANK).fillna(2)
    sub["_d"] = pd.to_datetime(sub["date_creat"], errors="coerce").fillna(pd.Timestamp.max)
    sub = sub.sort_values(["code_insee", "_g", "reconnue_utilite_publique", "siret_renseigne", "_d", "id"],
                          ascending=[True, True, False, False, True, True])
    sub = sub.groupby("code_insee", sort=False).head(n).copy()
    sub["rang_dans_commune"] = sub.groupby("code_insee").cumcount() + 1
    sub["commune"] = sub["code_insee"].map(communes.set_index("code_insee")["nom"])
    sub["famille_objet_libelle"] = sub["famille_objet"].map(WALDEC_FAMILY_LABELS)
    return sub[["code_insee", "commune", "rang_dans_commune", "id", "titre", "objet_social1",
                "famille_objet_libelle", "vertical", "groupement_type", "reconnue_utilite_publique",
                "siret_renseigne", "date_creat"]].rename(
        columns={"id": "rna_id", "titre": "nom_association", "objet_social1": "code_objet_waldec",
                 "date_creat": "date_creation"})
    # objet (free text) is NOT exported: it can contain names of natural persons.


def zscore(s: pd.Series) -> pd.Series:
    s = s.astype(float)
    sd = s.std(ddof=0)
    return (s - s.mean()) / sd if sd and not np.isnan(sd) else s * 0


# --------------------------------------------------------------------------- #
# Focus views (G-003). Read-only on `tbl`: the score and the other sheets are left as they are.
# --------------------------------------------------------------------------- #
FOCUS_MEASURES = ([("total", "associations (toutes)", "assos_total")]
                  + [(v, "associations", f"assos_{v}") for v in VERTICALS]
                  + [("sport_licences", "licences sportives (INJEP)", "licences_sport"),
                     ("sport_equipements", "équipements sportifs (Data ES)", "equipements_sportifs")])


def percentile(ref: pd.Series, x: float) -> float:
    """Mid-rank percentile of `x` in `ref`: share of `ref` strictly below x, plus half the ties (0-100)."""
    ref = ref.dropna()
    if pd.isna(x) or ref.empty:
        return np.nan
    return round(100 * ((ref < x).sum() + 0.5 * (ref == x).sum()) / len(ref), 1)


def density_frame(tbl: pd.DataFrame) -> pd.DataFrame:
    """code_insee-indexed per-1 000-inhabitant density of every FOCUS_MEASURES base column."""
    pop = pd.to_numeric(tbl["population"], errors="coerce").replace(0, np.nan)
    d = pd.DataFrame({base: tbl[base].astype(float) / pop * 1000 for _, _, base in FOCUS_MEASURES})
    d.index = tbl["code_insee"].values
    return d


def focus_refs(tbl: pd.DataFrame):
    """Reference sets: IDF communes (no arrondissements; Paris counted once as 75056) and the
    10 000 - 40 000 inhabitants stratum within them."""
    com = tbl[tbl["niveau"] == "commune"]
    pop = pd.to_numeric(com["population"], errors="coerce")
    strate = com[(pop >= FOCUS_STRATE[0]) & (pop <= FOCUS_STRATE[1])]
    return com["code_insee"].tolist(), strate["code_insee"].tolist()


def focus_profil(tbl: pd.DataFrame, focus: list[str]) -> pd.DataFrame:
    dens = density_frame(tbl)
    ref_idf, ref_strate = focus_refs(tbl)
    t = tbl.set_index("code_insee")
    rows = []
    for code in focus:
        pop = t.at[code, "population"]
        for vert, mesure, base in FOCUS_MEASURES:
            x = dens.at[code, base]
            rows.append({
                "code_insee": code, "commune": t.at[code, "nom"], "population": pop,
                "dans_strate": "oui" if FOCUS_STRATE[0] <= pop <= FOCUS_STRATE[1] else "non",
                "vertical": vert, "mesure": mesure,
                "nombre": t.at[code, base],
                "pour_1000_hab": round(x, 2) if pd.notna(x) else np.nan,
                "percentile_IDF": percentile(dens.loc[ref_idf, base], x),
                "percentile_strate": percentile(dens.loc[ref_strate, base], x),
                "mediane_IDF_pour_1000_hab": round(dens.loc[ref_idf, base].median(), 2),
                "mediane_strate_pour_1000_hab": round(dens.loc[ref_strate, base].median(), 2),
            })
    return pd.DataFrame(rows)


def focus_associations(rna: pd.DataFrame | None, communes: pd.DataFrame, focus: list[str]) -> pd.DataFrame:
    if rna is None:
        return pd.DataFrame([{"note": "Source RNA indisponible lors de cette exécution (voir feuille sources)."}])
    sub = rna[rna["code_insee"].isin(focus) & (rna["vertical"] != "other")]
    parts = [top_associations(sub[sub["vertical"] == v], communes, FOCUS_TOP_N) for v in VERTICALS if v != "other"]
    out = pd.concat(parts, ignore_index=True)
    out["_c"] = out["code_insee"].map({c: i for i, c in enumerate(focus)})
    out["_v"] = out["vertical"].map({v: i for i, v in enumerate(VERTICALS)})
    out = out.sort_values(["_c", "_v", "rang_dans_commune"]).drop(columns=["_c", "_v"])
    return out.rename(columns={"rang_dans_commune": "rang_dans_commune_vertical"}).reset_index(drop=True)


def focus_vague2(tbl: pd.DataFrame, focus: list[str]) -> pd.DataFrame:
    """Next-wave candidates: IDF communes outside the focus, population >= VAGUE2_POP_MIN, in the same
    territory (EPT in the Métropole du Grand Paris, EPCI elsewhere) as a focus city or in the same
    département; ranked by score_potentiel_RLL, top VAGUE2_TOP_N. A working list, not a choice."""
    dens = density_frame(tbl)
    # Top-3 verticals: percentile among IDF communes >= VAGUE2_POP_MIN, not all IDF communes — in the
    # villages most rare verticals (alumni, gaming, family) are at 0, so any non-zero density would rank high.
    big = tbl[(tbl["niveau"] == "commune") & (pd.to_numeric(tbl["population"], errors="coerce") >= VAGUE2_POP_MIN)]
    pct = {base: dens.loc[big["code_insee"], base].dropna() for _, _, base in FOCUS_MEASURES}

    def terr(code):
        t = TERRITOIRES.get(code)
        # MGP commune outside every EPT (Paris): no usable territory -> département only
        return None if t is None or (t["type"] == "EPCI" and t["metropole"]) else t

    t = tbl.set_index("code_insee")
    focus_terr, focus_dep = {}, {}
    for code in focus:
        if terr(code):
            focus_terr.setdefault(terr(code)["code"], []).append(t.at[code, "nom"])
        focus_dep.setdefault(t.at[code, "departement"], []).append(t.at[code, "nom"])
    rows = []
    com = tbl[(tbl["niveau"] == "commune") & ~tbl["code_insee"].isin(focus)
              & (pd.to_numeric(tbl["population"], errors="coerce") >= VAGUE2_POP_MIN)]
    for r in com.itertuples(index=False):
        tr = terr(r.code_insee)
        in_terr = tr is not None and tr["code"] in focus_terr
        in_dep = r.departement in focus_dep
        if not (in_terr or in_dep):
            continue
        critere = " et ".join(([tr["type"]] if in_terr else []) + (["département"] if in_dep else []))
        liees = focus_terr[tr["code"]] if in_terr else focus_dep[r.departement]
        verts = sorted(((percentile(pct[f"assos_{v}"], dens.at[r.code_insee, f"assos_{v}"]), v)
                        for v in VERTICALS if v != "other"), reverse=True)[:3]
        row = {"code_insee": r.code_insee, "commune": r.nom, "departement": r.departement,
               "population": r.population,
               "territoire_type": tr["type"] if tr else "—", "territoire": tr["nom"] if tr else "—",
               "critere": critere, "villes_focus_liees": ", ".join(liees),
               "score_potentiel_RLL": r.score_potentiel_RLL, "rang_IDF_score": int(r.rang),
               "assos_total": r.assos_total,
               "assos_pour_1000_hab": round(r.assos_per_1k, 2) if pd.notna(r.assos_per_1k) else np.nan}
        for i, (p, v) in enumerate(verts, 1):
            x = dens.at[r.code_insee, f"assos_{v}"]
            row[f"vertical_dense_{i}"] = f"{v} ({x:.2f} pour 1 000 hab., percentile {p:.0f})"
        rows.append(row)
    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame([{"note": "Aucune commune candidate avec les critères de focus_vague2."}])
    out = out.sort_values(["score_potentiel_RLL", "population"], ascending=[False, False],
                          na_position="last").head(VAGUE2_TOP_N)
    out.insert(0, "rang_vague2", range(1, len(out) + 1))
    return out.reset_index(drop=True)


def check_focus(communes: pd.DataFrame, focus: list[str]) -> list[str]:
    """`--focus` values (space- or comma-separated) -> unique IDF commune codes, in the given order."""
    codes = [c.strip() for f in focus for c in f.split(",") if c.strip()]
    known = set(communes.loc[communes["niveau"] == "commune", "code_insee"])
    bad = [c for c in codes if c not in known]
    if bad:
        raise SystemExit(f"--focus: code(s) INSEE inconnu(s) ou hors communes IDF : {', '.join(bad)}")
    return list(dict.fromkeys(codes))


def build(workdir: Path, outdir: Path, focus: list[str] | None = None):
    workdir.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)

    communes = load_communes(workdir)
    focus = check_focus(communes, focus or FOCUS_DEFAULT)
    rna = load_rna(workdir)
    inj_commune, inj_fed = load_injep(workdir)
    es = load_data_es(workdir)

    tbl = communes.copy()
    # --- RNA counts per vertical
    if rna is not None and len(rna):
        pv = rna.pivot_table(index="code_insee", columns="vertical", values="id", aggfunc="count", fill_value=0)
        for v in VERTICALS:
            if v not in pv.columns:
                pv[v] = 0
        pv = pv[VERTICALS]
        pv.columns = [f"assos_{v}" for v in VERTICALS]
        pv["assos_total"] = pv.sum(axis=1)
        pv = rollup_paris(pv.reset_index()).set_index("code_insee")
        tbl = tbl.merge(pv, left_on="code_insee", right_index=True, how="left")
    else:
        for v in VERTICALS:
            tbl[f"assos_{v}"] = np.nan
        tbl["assos_total"] = np.nan
    # --- INJEP
    if inj_commune is not None:
        inj_commune = rollup_paris(inj_commune)
        tbl = tbl.merge(inj_commune.rename(columns={"licences": "licences_sport", "clubs": "clubs_sport"}),
                        on="code_insee", how="left")
    for c in ("clubs_sport", "licences_sport"):
        if c not in tbl.columns:
            tbl[c] = np.nan
    # --- Data ES
    if es is not None:
        es = rollup_paris(es)
        tbl = tbl.merge(es.rename(columns={"equipements": "equipements_sportifs"}), on="code_insee", how="left")
    if "equipements_sportifs" not in tbl.columns:
        tbl["equipements_sportifs"] = np.nan

    # zero-fill where the source succeeded (absence = 0), keep NaN where the source failed
    # (INJEP: per file — the clubs file can fail while the licences file succeeds)
    inj_cols = set(inj_commune.columns) if inj_commune is not None else set()
    for col, ok in (("assos_total", rna is not None), ("clubs_sport", "clubs" in inj_cols),
                    ("licences_sport", "licences" in inj_cols), ("equipements_sportifs", es is not None)):
        if ok:
            tbl[col] = tbl[col].fillna(0)
    if rna is not None:
        for v in VERTICALS:
            tbl[f"assos_{v}"] = tbl[f"assos_{v}"].fillna(0)

    # --- ratios per 1 000 inhabitants
    pop = pd.to_numeric(tbl["population"], errors="coerce").replace(0, np.nan)
    tbl["assos_per_1k"] = tbl["assos_total"] / pop * 1000
    tbl["licences_per_1k"] = tbl["licences_sport"] / pop * 1000
    tbl["equipements_per_1k"] = tbl["equipements_sportifs"] / pop * 1000

    # --- heuristic RLL potential score
    # = mean of z-scores (over communes, not arrondissements) of log1p(assos_total),
    #   log1p(licences), log1p(equipements) and their per-1k ratios, for the sources that are
    #   available. Volume and density both count: a big city with many clubs and a dense small
    #   town both score. Purely heuristic — not a market-size estimate.
    comp = [np.log1p(tbl[base].astype(float))
            for base in ("assos_total", "licences_sport", "equipements_sportifs") if tbl[base].notna().any()]
    ratio_cols = [c for c, b in (("assos_per_1k", "assos_total"), ("licences_per_1k", "licences_sport"),
                                 ("equipements_per_1k", "equipements_sportifs")) if tbl[b].notna().any()]
    mask_com = tbl["niveau"] == "commune"
    zs = []
    for s in comp:
        ref = s[mask_com]
        zs.append((s - ref.mean()) / (ref.std(ddof=0) or 1))
    for c in ratio_cols:
        s = tbl[c].astype(float).clip(upper=tbl.loc[mask_com, c].quantile(0.99))  # cap outliers (tiny villages)
        ref = s[mask_com]
        zs.append((s - ref.mean()) / (ref.std(ddof=0) or 1))
    if zs:
        tbl["score_potentiel_RLL"] = pd.concat(zs, axis=1).mean(axis=1).round(3)
        tbl["score_composants"] = ", ".join([f"log1p({b})" for b in ("assos_total", "licences_sport", "equipements_sportifs")
                                             if tbl[b].notna().any()] + ratio_cols)
    else:
        tbl["score_potentiel_RLL"] = np.nan
        tbl["score_composants"] = "aucune source de comptage disponible (voir sources)"
    tbl["score_note"] = "HEURISTIQUE — moyenne de z-scores (communes IDF), pas une estimation de marché"

    ordered = ["code_insee", "nom", "departement", "niveau", "commune_parent", "population"] + \
              [f"assos_{v}" for v in VERTICALS] + ["assos_total", "clubs_sport", "licences_sport",
                                                   "equipements_sportifs", "assos_per_1k", "licences_per_1k",
                                                   "equipements_per_1k", "score_potentiel_RLL",
                                                   "score_composants", "score_note"]
    ordered = [c for c in ordered if c in tbl.columns] + [c for c in tbl.columns if c not in ordered]
    tbl = tbl[ordered]
    tbl = tbl.sort_values(["score_potentiel_RLL", "population"], ascending=[False, False], na_position="last")
    tbl.insert(0, "rang", np.where(tbl["niveau"] == "commune",
                                   (tbl["niveau"] == "commune").cumsum(), np.nan))

    # --- top associations per commune x vertical
    top_sheets = {}
    for sheet, vert in (("top_associations_sport", ["sport"]), ("top_associations_culture", ["culture"]),
                        ("top_associations_loisirs", ["fun", "gaming"])):
        if rna is None:
            top_sheets[sheet] = pd.DataFrame([{"note": "Source RNA indisponible lors de cette exécution "
                                                       "(voir feuille sources)."}])
            continue
        top_sheets[sheet] = top_associations(rna[rna["vertical"].isin(vert)], communes)

    fed_sheet = inj_fed if inj_fed is not None else pd.DataFrame(
        [{"note": "Source INJEP indisponible lors de cette exécution (voir feuille sources)."}])

    sources = pd.DataFrame(LOG)
    mapping = pd.DataFrame([{"famille_waldec": k, "libelle": WALDEC_FAMILY_LABELS.get(k, ""),
                             "vertical_RLL": v} for k, v in WALDEC_FAMILY_TO_VERTICAL.items()])
    mapping_kw = pd.DataFrame([
        {"regle": "alumni", "condition": "titre/objet ~ " + KW_ALUMNI.pattern, "priorite": 1},
        {"regle": "gaming", "condition": "vertical fun ET titre/objet ~ " + KW_GAMING.pattern, "priorite": 2},
        {"regle": "wellness", "condition": "vertical fun/sport/asso ET titre/objet ~ " + KW_WELLNESS.pattern, "priorite": 3},
    ])

    remap_report = pd.DataFrame(REMAP_REPORT)
    remap_detail = pd.DataFrame(REMAP_DETAIL, columns=["source", "ancien_code", "code_actuel", "n"])
    if len(remap_detail):
        remap_detail = remap_detail.pivot_table(index=["ancien_code", "code_actuel"], columns="source",
                                                values="n", aggfunc="sum", fill_value=0).reset_index()
        remap_detail.columns.name = None
        # the package names déléguées / associées, not bare `anciensCodes`
        remap_detail.insert(1, "nom_ancien", remap_detail["ancien_code"].map(COMMUNE_NAMES)
                            .fillna("(ancien code, nom non fourni par le paquet)"))
        remap_detail.insert(3, "nom_actuel", remap_detail["code_actuel"].map(COMMUNE_NAMES))
        sort_col = next((c for c in remap_detail.columns if c.startswith("RNA")), remap_detail.columns[-1])
        remap_detail = remap_detail.sort_values(sort_col, ascending=False)
    for r in REMAP_REPORT:
        print(f"[remap] {r['source']}: hors communes {r['hors_communes_avant']:.0f} -> "
              f"{r['hors_communes_apres']:.0f} (réaffectés {r['reaffectes']:.0f}) ; restants : "
              f"{r['codes_restants'] or '—'}", file=sys.stderr)

    # --- focus views (G-003): computed from tbl / rna, appended after the existing sheets
    focus_sheets = {"focus_profil": focus_profil(tbl, focus),
                    "focus_associations": focus_associations(rna, communes, focus),
                    "focus_vague2": focus_vague2(tbl, focus)}
    ref_idf, ref_strate = focus_refs(tbl)
    FOCUS_INFO.update(focus=focus, noms=[communes.set_index("code_insee").at[c, "nom"] for c in focus],
                      n_idf=len(ref_idf), n_strate=len(ref_strate),
                      n_big=int(((tbl["niveau"] == "commune") & (tbl["population"] >= VAGUE2_POP_MIN)).sum()))

    xlsx = outdir / f"RLL-IDF-villes-x-verticals-{VERSION}.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as xw:
        tbl.to_excel(xw, sheet_name="villes_x_verticals", index=False)
        for k, v in top_sheets.items():
            v.to_excel(xw, sheet_name=k, index=False)
        fed_sheet.to_excel(xw, sheet_name="federations_idf", index=False)
        sources.to_excel(xw, sheet_name="sources", index=False)
        mapping.to_excel(xw, sheet_name="mapping_waldec_verticals", index=False)
        mapping_kw.to_excel(xw, sheet_name="mapping_waldec_verticals", index=False, startrow=len(mapping) + 3)
        remap_report.to_excel(xw, sheet_name="correspondance_communes", index=False)
        remap_detail.to_excel(xw, sheet_name="correspondance_communes", index=False,
                              startrow=len(remap_report) + 3)
        for k, v in focus_sheets.items():
            v.to_excel(xw, sheet_name=k, index=False)
        # cosmetic: freeze header, autosize
        for ws in xw.book.worksheets:
            ws.freeze_panes = "A2"
            for col in ws.columns:
                width = min(60, max(10, max(len(str(c.value)) if c.value is not None else 0 for c in col[:200]) + 2))
                ws.column_dimensions[col[0].column_letter].width = width
    tbl.to_csv(outdir / f"RLL-IDF-villes-x-verticals-{VERSION}.csv", index=False, sep=";", encoding="utf-8-sig")
    write_readme(outdir, sources, mapping, mapping_kw, remap_report, remap_detail)
    return tbl, sources


README_BEGIN = "<!-- BEGIN GENERATED: build.py -->"
README_END = "<!-- END GENERATED: build.py -->"


def write_readme(outdir: Path, sources: pd.DataFrame, mapping: pd.DataFrame, mapping_kw: pd.DataFrame,
                 remap_report: pd.DataFrame, remap_detail: pd.DataFrame):
    """Rewrite the generated part between the README_BEGIN / README_END markers of the pipeline
    README, leaving the rest of that file as is. Nothing is written to `outdir`."""
    lines = [f"# RLL — IDF villes × verticals {VERSION} — sources & méthode", "",
             f"Généré le {TODAY} par `build.py` (reproductible : `python3 build.py`).", "",
             "Périmètre : Île-de-France (75, 77, 78, 91, 92, 93, 94, 95). Données ouvertes françaises uniquement. "
             "Aucune donnée personnelle de personne physique dans les sorties (colonnes RNA nominatives jamais chargées ; "
             "champ `objet` libre non exporté).", "",
             "## Source de population (décision 2026-10-01)", "",
             f"La population vient de **@etalab/decoupage-administratif {ETALAB_COG_VERSION}** (npm, données sous "
             f"Licence Ouverte) : {POP_MILLESIME}. Elle remplace l'ancienne source primaire `insee_pop` (fichier INSEE "
             "des populations légales via data.gouv.fr), retirée du catalogue `SOURCES` : le paquet etalab republie "
             "le même fichier INSEE et porte aussi le COG (communes, arrondissements municipaux, codes postaux), "
             "ce qui évite une seconde jointure. La version du paquet est épinglée dans `build.py` "
             "(`ETALAB_COG_VERSION`) pour que le millésime annoncé reste exact ; changer de version impose de "
             "vérifier la section « Millésimes et versions de package » du README du paquet.", "",
             "## Téléchargements", "",
             f"Chaque téléchargement passe par le proxy HTTPS de l'environnement. Coupure de connexion, timeout et "
             f"HTTP 5xx : jusqu'à {FETCH_ATTEMPTS} tentatives avec délai exponentiel ({FETCH_BACKOFF} s, "
             f"{FETCH_BACKOFF * 2} s, {FETCH_BACKOFF * 4} s), reprise du fichier partiel par `Range` quand le serveur "
             f"l'accepte. 403 et 404 ne sont jamais retentés. Délai d'attente par opération réseau : {TIMEOUT_API} s "
             f"(API) / {TIMEOUT_FILE} s (fichiers) — il borne chaque lecture, pas la durée totale, d'où un zip RNA de "
             "~410 Mo téléchargeable sans plafond global.", "",
             "## Sources tentées (ordre d'essai) et résultat", ""]
    lines.append("| # | Source | URL | Licence | Date | Statut | Lignes brutes | Lignes IDF conservées | Détail |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for i, r in sources.iterrows():
        lines.append(f"| {i+1} | {r['source']} | {r['url']} | {r['licence']} | {r['download_date']} | **{r['status']}** | "
                     f"{'' if pd.isna(r['rows_raw']) else int(r['rows_raw'])} | "
                     f"{'' if pd.isna(r['rows_kept_idf']) else int(r['rows_kept_idf'])} | {r['detail']} |")
    lines += ["", "## Notes et réserves", ""]
    for n in NOTES:
        lines.append(f"- {n}")
    failed = sorted(set(sources.loc[sources["status"] == "FAILED", "source_key"])
                    - set(sources.loc[sources["status"] == "OK", "source_key"]))
    if failed:
        lines.append(f"- Sources en échec lors de cette exécution : {', '.join(failed)}. Les colonnes "
                     f"correspondantes sont vides ; relancer `build.py` depuis un poste ayant accès aux hôtes listés "
                     f"ci-dessous les remplit sans autre modification.")
    lines += [
        "- Le score `score_potentiel_RLL` est une **heuristique** : moyenne des z-scores (calculés sur les communes IDF, "
        "hors arrondissements) de log1p(assos_total), log1p(licences_sport), log1p(equipements_sportifs) et des ratios "
        "pour 1 000 habitants (plafonnés au 99e centile), pour les seules sources disponibles. Ce n'est ni une taille de "
        "marché ni une prédiction.",
        "- Biais connus du score (décision 2026-10-01 : score inchangé) : (1) les ratios pour 1 000 habitants placent "
        "des micro-communes en tête malgré le plafonnement au 99e centile — un seuil de population "
        f"(≥ {VAGUE2_POP_MIN} habitants) est appliqué dans la feuille `focus_vague2` (G-003) ; (2) les "
        "arrondissements centraux de Paris (1er–9e : 8e à ~170 "
        "et 1er à ~146 associations pour 1 000 habitants, contre ~50 pour Paris entier) sont gonflés par les "
        "sièges sociaux domiciliés (domiciliation, sièges nationaux), qui ne reflètent pas une activité locale.",
        "- Paris figure en une ligne `commune` (75056) plus 20 lignes `arrondissement` (75101–75120) ; le rang n'est "
        "attribué qu'aux communes. **Sommer toutes les lignes compte Paris deux fois** (75056 = somme des "
        "arrondissements) : pour un total régional, filtrer `niveau = commune`.",
        "- RNA : seules les associations `position = A` (actives) avec un code INSEE de commune IDF sont comptées ; "
        "le champ `adrs_codeinsee` peut être vide/obsolète pour des associations anciennes (sous-estimation possible). "
        "Le fichier Waldec ne couvre pas l'Alsace-Moselle (hors périmètre ici).",
        "- Mapping objets Waldec → verticals RLL : par famille (3 premiers caractères de `objet_social1`), puis règles "
        "par mots-clés (alumni, gaming, wellness). Mapping validé par Christophe le 2026-10-01 ; libellés vérifiés "
        "contre la nomenclature WALDEC et les titres réels du fichier RNA (les familles 025–029 n'existent pas ; "
        "030–050 étaient décalées en v0.1). Les codes hors nomenclature présents dans le fichier (000, 008, 012, vide, "
        "quelques codes isolés) tombent en `other`. Le mapping reste une convention RLL, discutable pour "
        "005 (information/communication → culture), 015 (éducation → asso), 013 (chasse/pêche → nature) et 034 (tourisme → fun).",
        "- Communes fusionnées : avant agrégation par commune, les anciens codes INSEE du RNA, de l'INJEP et de Data ES "
        "sont remplacés par le code de la commune actuelle (table construite depuis @etalab/decoupage-administratif "
        f"{ETALAB_COG_VERSION} : communes déléguées/associées → `chefLieu`, `anciensCodes` d'une commune actuelle → son "
        "code ; un code encore actuel n'est jamais réaffecté). Le fichier des mouvements du COG de l'INSEE n'a pas été "
        "nécessaire (aucun code ancien résiduel ne relève d'une fusion). Détail : feuille `correspondance_communes`.",
        "- Feuilles `top_associations_*` : " + TOP_CRITERE + " Dénominations d'associations conservées (personnes "
        "morales, décision 2026-10-01) ; aucun champ nominatif ni `objet` libre.",
        "- INJEP : millésime = dernier disponible dans le jeu de données au moment du build (voir `sources`). Les licences "
        "sont comptées au lieu de résidence du licencié, les clubs au siège du club.",
        "- Data ES : un équipement = une ligne dédoublonnée sur l'identifiant d'équipement ; les lieux de pratique "
        "non bâtis (sentiers, plans d'eau) sont inclus.",
        f"- Focus (G-003) : {', '.join(f'{n} ({c})' for c, n in zip(FOCUS_INFO['focus'], FOCUS_INFO['noms']))} "
        f"(option `--focus`). Références des percentiles : {FOCUS_INFO['n_idf']} communes IDF (hors arrondissements, "
        f"Paris compté une fois) ; strate {FOCUS_STRATE[0]}–{FOCUS_STRATE[1]} habitants : {FOCUS_INFO['n_strate']} "
        f"communes ; `vertical_dense_*` de `focus_vague2` : {FOCUS_INFO['n_big']} communes de {VAGUE2_POP_MIN} "
        "habitants ou plus. Méthode : voir la partie rédigée de ce README.",
        f"- Territoires (feuille `focus_vague2`) : `epci.json` ({TERRITOIRES_SRC['n_epci']} EPCI à fiscalité propre) "
        f"et `ept.json` ({TERRITOIRES_SRC['n_ept']} établissements publics territoriaux) du paquet "
        f"@etalab/decoupage-administratif {ETALAB_COG_VERSION} déjà utilisé pour la population : aucune nouvelle "
        "source. Métropole du Grand Paris → EPT ; Paris, hors EPT → département.",
        "", "## Communes fusionnées — écart avant / après", "",
        "| Source | Total IDF | Hors communes avant | Hors communes après | Réaffectés | Codes restants (non INSEE) |",
        "|---|---|---|---|---|---|"]
    for _, r in remap_report.iterrows():
        lines.append(f"| {r['source']} | {r['total']:.0f} | {r['hors_communes_avant']:.0f} | "
                     f"{r['hors_communes_apres']:.0f} | {r['reaffectes']:.0f} | {r['codes_restants'] or '—'} |")
    if len(remap_detail):
        val_cols = [c for c in remap_detail.columns if c not in ("ancien_code", "nom_ancien", "code_actuel", "nom_actuel")]
        lines += ["", "Anciens codes réaffectés (10 premiers, par nombre d'associations) :", "",
                  "| Ancien code | Ancienne commune | Code actuel | Commune actuelle | " + " | ".join(val_cols) + " |",
                  "|---" * (4 + len(val_cols)) + "|"]
        for _, r in remap_detail.head(10).iterrows():
            lines.append(f"| {r['ancien_code']} | {r['nom_ancien']} | {r['code_actuel']} | {r['nom_actuel']} | "
                         + " | ".join(f"{r[c]:.0f}" for c in val_cols) + " |")
    lines += [
        "", "## Mapping familles Waldec → verticals RLL", "",
        "| Famille | Libellé | Vertical RLL |", "|---|---|---|"]
    for _, r in mapping.iterrows():
        lines.append(f"| {r['famille_waldec']} | {r['libelle']} | {r['vertical_RLL']} |")
    lines += ["", "Règles par mots-clés (appliquées après le mapping par famille) :", ""]
    for _, r in mapping_kw.iterrows():
        lines.append(f"- **{r['regle']}** (priorité {r['priorite']}) : {r['condition']}")
    lines += ["", "## Fichiers", "",
              f"- `RLL-IDF-villes-x-verticals-{VERSION}.xlsx` — feuilles : villes_x_verticals, top_associations_sport, "
              "top_associations_culture, top_associations_loisirs, federations_idf, sources, mapping_waldec_verticals, "
              "correspondance_communes, focus_profil, focus_associations, focus_vague2",
              f"- `RLL-IDF-villes-x-verticals-{VERSION}.csv` — feuille principale (séparateur `;`, UTF-8 BOM)",
              "- `build.py` — script de construction", "",
              "## Hôtes requis", "",
              "`www.data.gouv.fr`, `static.data.gouv.fr` (API et fichiers INJEP), `media.interieur.gouv.fr` (zip RNA "
              "Waldec), `data.education.gouv.fr` (exports Data ES), `registry.npmjs.org` (paquet etalab).", ""]
    text = "\n".join(lines)
    readme = PIPELINE_DIR / "README-sources.md"
    current = readme.read_text(encoding="utf-8") if readme.exists() else ""
    if README_BEGIN in current and README_END in current:
        head, rest = current.split(README_BEGIN, 1)
        tail = rest.split(README_END, 1)[1]
        readme.write_text(f"{head}{README_BEGIN}\n{text}\n{README_END}{tail}", encoding="utf-8")
    else:
        print(f"[readme] markers not found in {readme}; README left unchanged", file=sys.stderr)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    # Defaults are relative to this file, not to the current directory: pipelines/fr-idf/raw and
    # pipelines/fr-idf/out whether build.py is run from the repo root or from the pipeline folder.
    ap.add_argument("--workdir", default=str(PIPELINE_DIR / "raw"), help="raw downloads (default: pipelines/fr-idf/raw)")
    ap.add_argument("--out", default=str(PIPELINE_DIR / "out"), help="outputs (default: pipelines/fr-idf/out)")
    ap.add_argument("--focus", nargs="+", default=FOCUS_DEFAULT, metavar="CODE_INSEE",
                    help="codes INSEE des villes du focus, séparés par des espaces ou des virgules "
                         f"(défaut : villes pilotes {' '.join(FOCUS_DEFAULT)}) -> feuilles focus_*")
    a = ap.parse_args()
    tbl, src = build(Path(a.workdir), Path(a.out), a.focus)
    print(src.to_string(), file=sys.stderr)
    print(tbl.head(15).to_string(), file=sys.stderr)
