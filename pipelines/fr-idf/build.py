#!/usr/bin/env python3
"""
RLL — Île-de-France : villes x verticals (v0.1)
================================================
Builds RLL-IDF-villes-x-verticals-v0.1.xlsx (+ CSV + README-sources.md) from French
open data only. Scope: IDF départements 75, 77, 78, 91, 92, 93, 94, 95.

Sources (in order of attempt):
  1. RNA  — Répertoire National des Associations (data.gouv.fr, Licence Ouverte)
  2. INJEP — recensement géocodé des licences et clubs sportifs (data.gouv.fr, LO)
  3. Data ES — recensement des équipements sportifs (data.gouv.fr, LO)
  4. Communes + populations légales INSEE : @etalab/decoupage-administratif (npm,
     Licence Ouverte pour les données), republication du COG et des populations de
     référence INSEE. (L'ancienne source primaire `insee_pop` sur data.gouv.fr a été
     retirée : son slug n'existe plus et aucun jeu INSEE équivalent n'y est publié.)

Every download is attempted through the environment's HTTPS proxy. Transient failures
(connection reset, timeout, HTTP 5xx) are retried up to 4 attempts with exponential
backoff; a refusal (403 / 407 / 404) is never retried. A failed download is logged in the `sources`
sheet and README-sources.md and the pipeline continues with what it has.
Nothing is scraped; no retry through other tools.

Privacy: no personal data of natural persons is ever written to the outputs.
RNA columns naming people (dir_civilite, adrg_declarant, ...) are never loaded.

Reproduce:  python3 build.py [--workdir DIR] [--out DIR]
Requires: pandas, openpyxl (pip install --break-system-packages pandas openpyxl)
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import re
import http.client
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
        "dataset_url": "https://www.data.gouv.fr/fr/datasets/data-es-recensement-des-equipements-sportifs-et-lieux-de-pratique-complet-1/",
        "api_url": "https://www.data.gouv.fr/api/1/datasets/data-es-recensement-des-equipements-sportifs-et-lieux-de-pratique-complet-1/",
        "licence": "Licence Ouverte / Open Licence 2.0 (Etalab)",
        "producer": "Ministère chargé des Sports",
    },
    "etalab_cog": {
        "label": "@etalab/decoupage-administratif (npm) — COG + populations légales INSEE republiés (source de population)",
        "dataset_url": "https://www.npmjs.com/package/@etalab/decoupage-administratif",
        "api_url": "https://registry.npmjs.org/@etalab/decoupage-administratif",
        "licence": "Données : Licence Ouverte (Etalab) — code : MIT",
        "producer": "Etalab / DINUM (republication de l'INSEE)",
    },
}

# INSEE population millésime bundled in each @etalab/decoupage-administratif release
# (package README, « Sources » -> insee.fr/fr/statistiques/8680726 = populations de
# référence 2023, en vigueur au 1er janvier 2026). Extend when the package is bumped.
ETALAB_POP_MILLESIME = {"6.0.0": "2023"}

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


def log_source(key, url, status, rows_raw=None, rows_kept=None, detail="", licence=None, date=None,
               millesime=""):
    meta = SOURCES.get(key, {})
    LOG.append({
        "source_key": key,
        "source": meta.get("label", key),
        "producer": meta.get("producer", ""),
        "url": url,
        "licence": licence or meta.get("licence", ""),
        "download_date": date or TODAY,
        "millesime": millesime,
        "status": status,
        "rows_raw": rows_raw,
        "rows_kept_idf": rows_kept,
        "detail": detail,
    })
    print(f"[{status}] {key}: {url} — {detail}", file=sys.stderr)


FETCH_ATTEMPTS = 4
FETCH_TIMEOUT = 300   # seconds per socket operation (not total): a 410 MB zip streams in 1 MB reads


def _retryable(e: BaseException) -> bool:
    """Connection reset / timeout / HTTP 5xx only. Never 4xx, never a proxy refusal."""
    if isinstance(e, urllib.error.HTTPError):
        return e.code >= 500
    if isinstance(e, urllib.error.URLError):
        reason = str(e.reason)
        if re.search(r"\b40[0-9]\b", reason):      # e.g. "Tunnel connection failed: 403 Forbidden"
            return False
        return isinstance(e.reason, (ConnectionError, TimeoutError, ssl.SSLError, OSError))
    return isinstance(e, (ConnectionError, TimeoutError, ssl.SSLError, http.client.IncompleteRead,
                          http.client.RemoteDisconnected))


def _fetch_once(url: str, dest: Path | None, timeout: int) -> bytes | Path:
    ctx = ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE") or None)
    req = urllib.request.Request(url, headers={"User-Agent": "rll-opendata-build/0.1"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        if dest is None:
            return r.read()
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")   # never leave a truncated file under `dest`
        with open(part, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
        part.replace(dest)
        return dest


def fetch(url: str, dest: Path | None = None, timeout=FETCH_TIMEOUT) -> bytes | Path:
    """GET through the environment proxy, retrying transient failures; raise on failure."""
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            return _fetch_once(url, dest, timeout)
        except Exception as e:  # noqa: BLE001
            if attempt == FETCH_ATTEMPTS or not _retryable(e):
                raise
            wait = 2 ** attempt
            print(f"[retry {attempt}/{FETCH_ATTEMPTS - 1}] {url}: {type(e).__name__}: {e} — waiting {wait}s",
                  file=sys.stderr)
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
    """Newest resource whose title matches `pattern` (the URL is not searched: data.gouv URLs
    embed the dataset slug, e.g. '...licences-et-clubs...', which would match every resource)."""
    res = [r for r in api_json.get("resources", [])
           if re.search(pattern, r.get("title") or "", re.I)
           and (fmt is None or (r.get("format") or "").lower() == fmt)]
    res.sort(key=lambda r: r.get("last_modified") or "", reverse=True)
    return res[0] if res else None


# --------------------------------------------------------------------------- #
# 4. Communes + population (backbone)
# --------------------------------------------------------------------------- #
def load_communes(workdir: Path) -> pd.DataFrame:
    """Return DataFrame: code_insee, nom, departement, population, niveau, commune_parent."""
    # etalab npm package (republication of INSEE COG + populations légales) — the population source.
    tgz = workdir / "decoupage.tgz"
    meta = try_fetch("etalab_cog", SOURCES["etalab_cog"]["api_url"])
    if not meta:
        raise SystemExit("No commune backbone available — cannot build the table.")
    m = json.loads(meta)
    ver = m["dist-tags"]["latest"]
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
    pop_year = ETALAB_POP_MILLESIME.get(ver)
    pop_txt = (f"populations de référence INSEE {pop_year} (insee.fr/fr/statistiques/8680726)" if pop_year
               else "millésime INSEE à vérifier dans le README du package (version non répertoriée)")
    log_source("etalab_cog", tarball, "OK", rows_raw=len(communes), rows_kept=len(out),
               detail=f"package {ver} published {pub}; population = {pop_txt}; remplace l'ancienne source "
                      f"insee_pop (data.gouv.fr, slug disparu)",
               date=TODAY, millesime=f"COG {pub[:4]} ; population {pop_year or '?'}")
    NOTES.append(f"Population : {pop_txt}, republiées dans @etalab/decoupage-administratif {ver} (publié {pub}). "
                 f"Cette source remplace l'ancienne source primaire insee_pop (data.gouv.fr), retirée.")
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
                # Waldec files (2026): UTF-8 with BOM, every field double-quoted, ';' separator.
                # Default quoting is required (free-text `objet` contains ';' and newlines).
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
    df = df[df["code_insee"].str.len() == 5].copy()
    df["famille_objet"] = df["objet_social1"].fillna("").str.strip().str[:3]
    df["vertical"] = classify_vertical(df["famille_objet"], df["titre"].fillna("") + " " + df["objet"].fillna(""))
    stamp = re.search(r"(\d{8})", r.get("title", "") + r.get("url", ""))
    log_source(key, r["url"], "OK", rows_raw=raw_total, rows_kept=len(df),
               detail=f"{r.get('title','')} — filtered position='A' and IDF code INSEE; "
                      f"last_modified {str(r.get('last_modified',''))[:10]}",
               millesime=f"extraction du {stamp.group(1)[:4]}-{stamp.group(1)[4:6]}-{stamp.group(1)[6:]}"
               if stamp else "")
    if key == "rna":
        log_source("rna_agrege", SOURCES["rna_agrege"]["api_url"], "NOT_USED",
                   detail="fallback non sollicité : le fichier Waldec principal a été lu")
    return df


# --------------------------------------------------------------------------- #
# 2. INJEP licences & clubs
# --------------------------------------------------------------------------- #
def _norm(col: str) -> str:
    """'Numéro de l'équipement sportif' -> 'numero_de_l_equipement_sportif'."""
    s = unicodedata.normalize("NFKD", str(col)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


def _find(cols, *cands):
    low = {_norm(c): c for c in cols}
    for c in cands:
        for k, orig in low.items():
            if re.fullmatch(c, k):
                return orig
    return None


def _header(path: Path):
    """(separator, column names) from the first line, without loading the file."""
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        line = f.readline()
    sep = max((";", ",", "\t", "|"), key=line.count)
    cols = next(csv.reader([line.rstrip("\r\n")], delimiter=sep))
    return sep, cols


def load_injep(workdir: Path):
    """Returns (per_commune DataFrame[code_insee, licences, clubs], per_fed DataFrame) or (None, None)."""
    api = try_fetch("injep", SOURCES["injep"]["api_url"])
    if not api:
        return None, None
    j = json.loads(api)
    lic = pick_resource(j, r"^lic", fmt="csv") or pick_resource(j, r"licen", fmt="csv")
    clubs = pick_resource(j, r"^clubs?-data", fmt="csv") or pick_resource(j, r"club", fmt="csv")
    per_commune, per_fed = None, None
    frames = {}
    # value column per file: licences -> 'Total' (all ages/sexes); clubs -> 'Clubs' (affiliated clubs,
    # excluding EPA = établissements professionnels agréés, counted separately in the source).
    value_cands = {"licences": (r"l_\d{4}", r"total", r"nb_licences?", r"licences?"),
                   "clubs": (r"c_\d{4}", r"clubs", r"nb_clubs?", r"total")}
    for kind, r in (("licences", lic), ("clubs", clubs)):
        if not r:
            continue
        p = workdir / f"injep_{kind}.csv"
        if not p.exists() and not try_fetch("injep", r["url"], p):
            continue
        sep, cols = _header(p)
        code = _find(cols, r"code_commune", r"codgeo", r"code_insee", r"com_code", r"insee.*")
        dep = _find(cols, r"dep_code", r"code_dep.*", r"departement_code", r"departement")
        fed = _find(cols, r"federation", r"fed_libelle", r"fede.*", r"nom_fed.*")
        fedcode = _find(cols, r"fed_code", r"code_fed.*", r"code")
        val = _find(cols, *value_cands[kind])
        if code is None or val is None:
            log_source("injep", r["url"], "FAILED",
                       detail=f"column not recognised (commune={code}, valeur={val}) in {cols[:12]}")
            continue
        usecols = [c for c in dict.fromkeys((code, dep, fedcode, fed, val)) if c]  # only what is needed
        parts, raw = [], 0
        for chunk in pd.read_csv(p, sep=sep, dtype=str, encoding="utf-8-sig", usecols=usecols,
                                 chunksize=200_000):
            raw += len(chunk)
            m = is_idf_code(chunk[code].fillna("")) if dep is None else \
                chunk[dep].fillna("").str.strip().str.zfill(2).isin(IDF_DEPS)
            parts.append(chunk[m])
        df = pd.concat(parts, ignore_index=True)
        df[val] = pd.to_numeric(df[val], errors="coerce").fillna(0)
        df["code_insee"] = df[code].astype(str).str.strip().str.zfill(5)
        year = re.search(r"\d{4}", val) or re.search(r"\d{4}", r.get("title", ""))
        frames[kind] = (df, val, fed, fedcode, year.group(0) if year else "?")
        log_source("injep", r["url"], "OK", rows_raw=raw, rows_kept=len(df),
                   detail=f"{r.get('title','')} — colonnes lues {usecols}, valeur '{val}'",
                   millesime=year.group(0) if year else "")
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
    sep, cols = _header(p)
    code = _find(cols, r"commune_insee", r"cominsee", r"com_insee", r"inst_com_code", r"code_insee", r"insee.*")
    eq = _find(cols, r"numero_de_l_equipement_sportif", r"equipementid", r"equ_id", r"id_equipement",
               r"numero.*equip.*")
    if code is None:
        log_source("data_es", r["url"], "FAILED", detail=f"commune column not recognised in {cols[:12]}")
        return None
    parts, raw = [], 0
    for chunk in pd.read_csv(p, sep=sep, dtype=str, encoding="utf-8-sig", usecols=[c for c in (code, eq) if c],
                             chunksize=200_000, on_bad_lines="skip"):
        raw += len(chunk)
        m = is_idf_code(chunk[code].fillna(""))
        parts.append(chunk.loc[m].rename(columns={code: "code_insee"}))
    df = pd.concat(parts, ignore_index=True)
    eqcol = [c for c in df.columns if c != "code_insee"]
    if eqcol:
        df = df.drop_duplicates(subset=eqcol)
    g = df.groupby("code_insee").size().rename("equipements").reset_index()
    log_source("data_es", r["url"], "OK", rows_raw=raw, rows_kept=len(df),
               detail=f"{r.get('title', '')} — colonnes lues {[c for c in (code, eq) if c]}",
               millesime=f"export du {str(r.get('last_modified', ''))[:10]}")
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
    for col, ok in (("assos_total", rna is not None), ("clubs_sport", inj_commune is not None),
                    ("licences_sport", inj_commune is not None), ("equipements_sportifs", es is not None)):
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
             "## Sources tentées (ordre d'essai) et résultat", ""]
    lines.append("| # | Source | URL | Licence | Date | Millésime | Statut | Lignes brutes | Lignes IDF conservées | Détail |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for i, r in sources.iterrows():
        lines.append(f"| {i+1} | {r['source']} | {r['url']} | {r['licence']} | {r['download_date']} | {r['millesime']} | **{r['status']}** | "
                     f"{'' if pd.isna(r['rows_raw']) else int(r['rows_raw'])} | "
                     f"{'' if pd.isna(r['rows_kept_idf']) else int(r['rows_kept_idf'])} | {r['detail']} |")
    lines += ["", "## Notes et réserves", ""]
    for n in NOTES:
        lines.append(f"- {n}")
    failed = sources[sources["status"] == "FAILED"]["source_key"].unique().tolist()
    if failed:
        lines.append(f"- Sources refusées par le proxy de sortie (403 sur CONNECT, politique d'organisation) ou "
                     f"inaccessibles lors de cette exécution : {', '.join(failed)}. Les colonnes correspondantes sont "
                     f"vides ; relancer `build.py` depuis un poste ayant accès aux hôtes listés les remplit sans "
                     f"autre modification.")
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
              "- `build.py` — script de construction", ""]
    (outdir / "README-sources.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=str(Path(__file__).resolve().parent / "raw"))
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent))
    a = ap.parse_args()
    tbl, src = build(Path(a.workdir), Path(a.out))
    print(src.to_string(), file=sys.stderr)
    print(tbl.head(15).to_string(), file=sys.stderr)
