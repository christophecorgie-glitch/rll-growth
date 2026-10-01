#!/usr/bin/env python3
"""
RLL — Île-de-France : villes x verticals (v0.1)
================================================
Builds RLL-IDF-villes-x-verticals-v0.1.xlsx (+ CSV + README-sources.md) from French
open data only. Scope: IDF départements 75, 77, 78, 91, 92, 93, 94, 95.

Sources (in order of attempt):
  1. RNA  — Répertoire National des Associations (data.gouv.fr, Licence Ouverte)
  2. INJEP — recensement géocodé des licences et clubs sportifs (data.gouv.fr, LO)
  3. Data ES — recensement des équipements sportifs (data.gouv.fr, fichiers servis par
     data.education.gouv.fr, LO 2.0)
  4. Communes + population — @etalab/decoupage-administratif (npm, Licence Ouverte pour les
     données), which republishes the INSEE COG and the INSEE populations de référence
     (population municipale). The package version is pinned so the millésime is known.

Every download goes through the environment's HTTPS proxy. Connection cuts, timeouts and
HTTP 5xx are retried (up to 4 attempts, exponential back-off); 403 / 404 are never retried.
A failed download is logged in the `sources` sheet and README-sources.md and the pipeline
continues with what it has. Nothing is scraped.

Privacy: no personal data of natural persons is ever written to the outputs.
RNA columns naming people (dir_civilite, adrg_declarant, ...) are never loaded.

Reproduce:  python3 build.py [--workdir DIR] [--out DIR]
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
# RNA Waldec "objet social" families -> RLL verticals
# The Waldec code is 6 characters; the first 3 identify the family (thème).
# Family labels follow the official RNA nomenclature ("Nomenclature des objets
# sociaux", ministère de l'Intérieur). Keyword rules refine a few families.
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
    "014": "Amicales, groupements affinitaires, groupements d'entraide",
    "015": "Éducation, formation",
    "016": "Recherche",
    "017": "Santé",
    "018": "Services et établissements médico-sociaux",
    "019": "Interventions sociales",
    "020": "Associations caritatives, humanitaires, aide au développement",
    "021": "Services familiaux, services aux personnes âgées",
    "022": "Conduite d'activités économiques",
    "023": "Représentation, promotion et défense d'intérêts économiques",
    "024": "Environnement, cadre de vie",
    "025": "Aide à l'emploi, développement local, vie locale",
    "026": "Logement",
    "027": "Tourisme",
    "028": "Sécurité, protection civile",
    "029": "Armée (anciens combattants, ...)",
    "030": "Domaines divers",
    "032": "Activités religieuses, spirituelles ou philosophiques",
    "034": "Domaines divers (non classé)",
    "036": "Aide à l'emploi, développement local (bis)",
    "038": "Groupements de professionnels (ex. syndicats professionnels)",
    "050": "Activités politiques (bis)",
    "100": "Inconnu / non renseigné",
}

WALDEC_FAMILY_TO_VERTICAL = {
    "011": "sport",
    "013": "nature",     # chasse, pêche -> outdoor / nature
    "024": "nature",     # environnement, cadre de vie
    "006": "culture",
    "010": "culture",    # patrimoine
    "005": "culture",    # information, communication (médias, radios associatives)
    "007": "fun",        # clubs de loisirs, relations (refined by keywords -> gaming)
    "027": "fun",        # tourisme
    "017": "wellness",   # santé (refined: bien-être, yoga, méditation...)
    "018": "wellness",   # médico-social
    "021": "family",     # services familiaux, personnes âgées
    "022": "business",
    "023": "business",
    "025": "business",   # aide à l'emploi, développement local
    "036": "business",
    "038": "business",
    "014": "asso",       # amicales, groupements affinitaires (refined -> alumni)
    "003": "asso",
    "004": "asso",
    "009": "asso",
    "015": "asso",       # éducation / formation (parents d'élèves, soutien scolaire)
    "016": "asso",
    "019": "asso",
    "020": "asso",
    "026": "asso",
    "028": "asso",
    "029": "asso",
    "032": "asso",
    "001": "other",
    "002": "other",
    "030": "other",
    "034": "other",
    "050": "other",
    "100": "other",
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
        "download_date": date or TODAY,
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


def is_idf_code(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str[:2].isin(IDF_DEPS)


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
    if not tgz.exists():
        if not try_fetch("etalab_cog", tarball, tgz):
            raise SystemExit("No commune backbone available — cannot build the table.")
    with tarfile.open(tgz) as t:
        communes = json.load(t.extractfile("package/data/communes.json"))
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
               detail=f"package {ver} (publié le {pub}) ; population = {POP_MILLESIME}",
               date=TODAY)
    NOTES.append(f"Population : {POP_MILLESIME}, telles que republiées par @etalab/decoupage-administratif "
                 f"{ver} (publié le {pub}, version épinglée dans build.py). Cette source remplace l'ancienne "
                 f"source primaire `insee_pop` (fichier INSEE via data.gouv.fr), retirée le 2026-10-01.")
    return out


# --------------------------------------------------------------------------- #
# 1. RNA
# --------------------------------------------------------------------------- #
RNA_USECOLS = ["id", "date_creat", "titre", "objet", "objet_social1", "objet_social2",
               "adrs_codeinsee", "adrs_codepostal", "adrs_libcommune", "position", "nature"]
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
    api = try_fetch("rna", SOURCES["rna"]["api_url"])
    if not api:
        # try the aggregated fallback dataset
        api2 = try_fetch("rna_agrege", SOURCES["rna_agrege"]["api_url"])
        if not api2:
            return None
        j = json.loads(api2)
        key = "rna_agrege"
    else:
        j = json.loads(api)
        key = "rna"
    r = pick_resource(j, r"rna_waldec|waldec", fmt="zip") or pick_resource(j, r"waldec") or \
        pick_resource(j, r"rna|association")
    if not r:
        log_source(key, SOURCES[key]["api_url"], "FAILED", detail="no Waldec resource found in API listing")
        return None
    zpath = workdir / "rna_waldec.zip"
    if not zpath.exists() and not try_fetch(key, r["url"], zpath):
        return None
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
    # (an IDF code postal with a non-IDF code INSEE is dropped too)
    df = df[(df["code_insee"].str.len() == 5) & is_idf_code(df["code_insee"])].copy()
    df["famille_objet"] = df["objet_social1"].fillna("").str.strip().str[:3]
    df["vertical"] = classify_vertical(df["famille_objet"], df["titre"].fillna("") + " " + df["objet"].fillna(""))
    log_source(key, r["url"], "OK", rows_raw=raw_total, rows_kept=len(df),
               detail=f"{r.get('title','')} — filtered position='A' and IDF code INSEE/CP; "
                      f"last_modified {str(r.get('last_modified',''))[:10]}")
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
        if not p.exists() and not try_fetch("injep", r["url"], p):
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
        df["code_insee"] = df[code].astype(str).str.zfill(5)
        year = re.search(r"\d{4}", val) or re.search(r"\d{4}", r.get("title", ""))
        frames[kind] = (df, val, fed, fedcode, year.group(0) if year else "?")
        log_source("injep", r["url"], "OK", rows_raw=raw, rows_kept=len(df),
                   detail=f"{r.get('title','')} — value column '{val}'")
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
    if not p.exists() and not try_fetch("data_es", r["url"], p):
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
    g = df.groupby("code_insee").size().rename("equipements").reset_index()
    log_source("data_es", r["url"], "OK", rows_raw=raw, rows_kept=len(df), detail=r.get("title", ""))
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


def zscore(s: pd.Series) -> pd.Series:
    s = s.astype(float)
    sd = s.std(ddof=0)
    return (s - s.mean()) / sd if sd and not np.isnan(sd) else s * 0


def build(workdir: Path, outdir: Path):
    workdir.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)

    communes = load_communes(workdir)
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

    # --- top associations per vertical for the top 20 communes
    top20 = tbl[tbl["niveau"] == "commune"].head(20)["code_insee"].tolist()
    top_sheets = {}
    for sheet, vert in (("top_associations_sport", ["sport"]), ("top_associations_culture", ["culture"]),
                        ("top_associations_loisirs", ["fun", "gaming"])):
        if rna is None:
            top_sheets[sheet] = pd.DataFrame([{"note": "Source RNA indisponible lors de cette exécution "
                                                       "(voir feuille sources)."}])
            continue
        codes = set(top20) | ({c for c in rna["code_insee"].unique() if c.startswith("751")} if "75056" in top20 else set())
        sub = rna[rna["vertical"].isin(vert) & rna["code_insee"].isin(codes)].copy()
        sub["famille_objet_libelle"] = sub["famille_objet"].map(WALDEC_FAMILY_LABELS)
        sub = sub.sort_values(["code_insee", "date_creat"], ascending=[True, False]).head(500)
        top_sheets[sheet] = sub[["id", "titre", "adrs_libcommune", "code_insee", "objet_social1",
                                 "famille_objet_libelle", "vertical", "date_creat"]].rename(
            columns={"id": "rna_id", "titre": "nom_association", "adrs_libcommune": "commune",
                     "objet_social1": "code_objet_waldec", "date_creat": "date_creation"})
        # objet (free text) is NOT exported: it can contain names of natural persons.

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

    xlsx = outdir / f"RLL-IDF-villes-x-verticals-{VERSION}.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as xw:
        tbl.to_excel(xw, sheet_name="villes_x_verticals", index=False)
        for k, v in top_sheets.items():
            v.to_excel(xw, sheet_name=k, index=False)
        fed_sheet.to_excel(xw, sheet_name="federations_idf", index=False)
        sources.to_excel(xw, sheet_name="sources", index=False)
        mapping.to_excel(xw, sheet_name="mapping_waldec_verticals", index=False)
        mapping_kw.to_excel(xw, sheet_name="mapping_waldec_verticals", index=False, startrow=len(mapping) + 3)
        # cosmetic: freeze header, autosize
        for ws in xw.book.worksheets:
            ws.freeze_panes = "A2"
            for col in ws.columns:
                width = min(60, max(10, max(len(str(c.value)) if c.value is not None else 0 for c in col[:200]) + 2))
                ws.column_dimensions[col[0].column_letter].width = width
    tbl.to_csv(outdir / f"RLL-IDF-villes-x-verticals-{VERSION}.csv", index=False, sep=";", encoding="utf-8-sig")
    write_readme(outdir, sources, mapping, mapping_kw, tbl)
    return tbl, sources


def write_readme(outdir: Path, sources: pd.DataFrame, mapping: pd.DataFrame, mapping_kw: pd.DataFrame, tbl: pd.DataFrame):
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
        "- Paris figure en une ligne `commune` (75056) plus 20 lignes `arrondissement` (75101–75120) ; le rang n'est "
        "attribué qu'aux communes.",
        "- RNA : seules les associations `position = A` (actives) avec un code INSEE de commune IDF sont comptées ; "
        "le champ `adrs_codeinsee` peut être vide/obsolète pour des associations anciennes (sous-estimation possible). "
        "Le fichier Waldec ne couvre pas l'Alsace-Moselle (hors périmètre ici).",
        "- Mapping objets Waldec → verticals RLL : par famille (3 premiers caractères de `objet_social1`), puis règles "
        "par mots-clés (alumni, gaming, wellness). La nomenclature officielle des objets sociaux doit être vérifiée "
        "contre le fichier de référence publié avec le RNA ; le mapping reste une convention RLL, discutable pour "
        "005 (information/communication → culture), 015 (éducation → asso), 013 (chasse/pêche → nature) et 027 (tourisme → fun).",
        "- INJEP : millésime = dernier disponible dans le jeu de données au moment du build (voir `sources`). Les licences "
        "sont comptées au lieu de résidence du licencié, les clubs au siège du club.",
        "- Data ES : un équipement = une ligne dédoublonnée sur l'identifiant d'équipement ; les lieux de pratique "
        "non bâtis (sentiers, plans d'eau) sont inclus.",
        "", "## Mapping familles Waldec → verticals RLL", "",
        "| Famille | Libellé | Vertical RLL |", "|---|---|---|"]
    for _, r in mapping.iterrows():
        lines.append(f"| {r['famille_waldec']} | {r['libelle']} | {r['vertical_RLL']} |")
    lines += ["", "Règles par mots-clés (appliquées après le mapping par famille) :", ""]
    for _, r in mapping_kw.iterrows():
        lines.append(f"- **{r['regle']}** (priorité {r['priorite']}) : {r['condition']}")
    lines += ["", "## Fichiers", "",
              f"- `RLL-IDF-villes-x-verticals-{VERSION}.xlsx` — feuilles : villes_x_verticals, top_associations_sport, "
              "top_associations_culture, top_associations_loisirs, federations_idf, sources, mapping_waldec_verticals",
              f"- `RLL-IDF-villes-x-verticals-{VERSION}.csv` — feuille principale (séparateur `;`, UTF-8 BOM)",
              "- `build.py` — script de construction", "",
              "## Hôtes requis", "",
              "`www.data.gouv.fr`, `static.data.gouv.fr` (API et fichiers INJEP), `media.interieur.gouv.fr` (zip RNA "
              "Waldec), `data.education.gouv.fr` (exports Data ES), `data-pipeline-open.s3.sbg.io.cloud.ovh.net`, "
              "`registry.npmjs.org` (paquet etalab).", ""]
    (PIPELINE_DIR / "README-sources.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=str(PIPELINE_DIR / "raw"))
    ap.add_argument("--out", default=str(PIPELINE_DIR / "out"))
    a = ap.parse_args()
    tbl, src = build(Path(a.workdir), Path(a.out))
    print(src.to_string(), file=sys.stderr)
    print(tbl.head(15).to_string(), file=sys.stderr)
