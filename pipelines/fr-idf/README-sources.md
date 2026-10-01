# RLL — IDF villes × verticals v0.1 — sources & méthode

Généré le 2026-09-30 par `build.py` (reproductible : `python3 build.py`).

Périmètre : Île-de-France (75, 77, 78, 91, 92, 93, 94, 95). Données ouvertes françaises uniquement. Aucune donnée personnelle de personne physique dans les sorties (colonnes RNA nominatives jamais chargées ; champ `objet` libre non exporté).

## Source de population (décision 2026-10-01)

La population vient de **@etalab/decoupage-administratif 6.0.0** (npm, données sous Licence Ouverte) : populations de référence 2023 (INSEE, en vigueur au 1er janvier 2026), population municipale — https://www.insee.fr/fr/statistiques/8680726. Elle remplace l'ancienne source primaire `insee_pop` (fichier INSEE des populations légales via data.gouv.fr), retirée du catalogue `SOURCES` : le paquet etalab republie le même fichier INSEE et porte aussi le COG. Version épinglée dans `build.py` (`ETALAB_COG_VERSION`).

## Sources tentées (ordre d'essai) et résultat

| # | Source | URL | Licence | Date | Statut | Lignes brutes | Lignes IDF conservées | Détail |
|---|---|---|---|---|---|---|---|---|
| 1 | INSEE — Populations légales par commune (fichier national) | https://www.data.gouv.fr/api/1/datasets/populations-legales-communes-et-arrondissements-municipaux-france-depuis-1876/ | Licence Ouverte / Open Licence 2.0 (Etalab) | 2026-09-30 | **FAILED** |  |  | blocked / unreachable: Tunnel connection failed: 403 Forbidden |
| 2 | @etalab/decoupage-administratif (npm) — COG + populations légales INSEE republiés | https://registry.npmjs.org/@etalab/decoupage-administratif/-/decoupage-administratif-6.0.0.tgz | Données : Licence Ouverte (Etalab) — code : MIT | 2026-09-30 | **OK** | 37590 | 1286 | package 6.0.0 published 2026-03-09; population = INSEE populations légales bundled in that release (see package README « Millésimes ») |
| 3 | RNA — Répertoire National des Associations (fichier Waldec mensuel) | https://www.data.gouv.fr/api/1/datasets/repertoire-national-des-associations/ | Licence Ouverte / Open Licence 2.0 (Etalab) | 2026-09-30 | **FAILED** |  |  | blocked / unreachable: Tunnel connection failed: 403 Forbidden |
| 4 | RNA agrégé à l'échelle nationale (fallback) | https://www.data.gouv.fr/api/1/datasets/rna-agrege-a-lechelle-nationale/ | Licence Ouverte / Open Licence 2.0 (Etalab) | 2026-09-30 | **FAILED** |  |  | blocked / unreachable: Tunnel connection failed: 403 Forbidden |
| 5 | INJEP / Ministère des Sports — licences et clubs sportifs géocodés | https://www.data.gouv.fr/api/1/datasets/donnees-geocodees-issues-du-recensement-des-licences-et-clubs-aupres-des-federations-sportives-agreees-par-le-ministere-charge-des-sports/ | Licence Ouverte / Open Licence 2.0 (Etalab) | 2026-09-30 | **FAILED** |  |  | blocked / unreachable: Tunnel connection failed: 403 Forbidden |
| 6 | Data ES — Recensement des équipements sportifs et lieux de pratique (complet) | https://www.data.gouv.fr/api/1/datasets/data-es-recensement-des-equipements-sportifs-et-lieux-de-pratique-complet/ | Licence Ouverte / Open Licence 2.0 (Etalab) | 2026-09-30 | **FAILED** |  |  | blocked / unreachable: Tunnel connection failed: 403 Forbidden |

## Notes et réserves

- Population: INSEE populations légales as republished in @etalab/decoupage-administratif 6.0.0 (published 2026-03-09). The exact INSEE millésime is the one referenced in the package README (insee.fr/fr/statistiques/8680726); verify before quoting a year.
- Sources refusées par le proxy de sortie (403 sur CONNECT, politique d'organisation) ou inaccessibles lors de cette exécution : insee_pop, rna, rna_agrege, injep, data_es. Les colonnes correspondantes sont vides ; relancer `build.py` depuis un poste ayant accès à data.gouv.fr / insee.fr les remplit sans autre modification.
- Le score `score_potentiel_RLL` est une **heuristique** : moyenne des z-scores (calculés sur les communes IDF, hors arrondissements) de log1p(assos_total), log1p(licences_sport), log1p(equipements_sportifs) et des ratios pour 1 000 habitants (plafonnés au 99e centile), pour les seules sources disponibles. Ce n'est ni une taille de marché ni une prédiction.
- Paris figure en une ligne `commune` (75056) plus 20 lignes `arrondissement` (75101–75120) ; le rang n'est attribué qu'aux communes.
- RNA : seules les associations `position = A` (actives) avec un code INSEE de commune IDF sont comptées ; le champ `adrs_codeinsee` peut être vide/obsolète pour des associations anciennes (sous-estimation possible). Le fichier Waldec ne couvre pas l'Alsace-Moselle (hors périmètre ici).
- Mapping objets Waldec → verticals RLL : par famille (3 premiers caractères de `objet_social1`), puis règles par mots-clés (alumni, gaming, wellness). La nomenclature officielle des objets sociaux doit être vérifiée contre le fichier de référence publié avec le RNA ; le mapping reste une convention RLL, discutable pour 005 (information/communication → culture), 015 (éducation → asso), 013 (chasse/pêche → nature) et 027 (tourisme → fun).
- INJEP : millésime = dernier disponible dans le jeu de données au moment du build (voir `sources`). Les licences sont comptées au lieu de résidence du licencié, les clubs au siège du club.
- Data ES : un équipement = une ligne dédoublonnée sur l'identifiant d'équipement ; les lieux de pratique non bâtis (sentiers, plans d'eau) sont inclus.

## Mapping familles Waldec → verticals RLL

| Famille | Libellé | Vertical RLL |
|---|---|---|
| 011 | Sports, activités de plein air | sport |
| 013 | Chasse, pêche | nature |
| 024 | Environnement, cadre de vie | nature |
| 006 | Culture, pratiques d'activités artistiques, culturelles | culture |
| 010 | Préservation du patrimoine | culture |
| 005 | Information, communication | culture |
| 007 | Clubs de loisirs, relations | fun |
| 027 | Tourisme | fun |
| 017 | Santé | wellness |
| 018 | Services et établissements médico-sociaux | wellness |
| 021 | Services familiaux, services aux personnes âgées | family |
| 022 | Conduite d'activités économiques | business |
| 023 | Représentation, promotion et défense d'intérêts économiques | business |
| 025 | Aide à l'emploi, développement local, vie locale | business |
| 036 | Aide à l'emploi, développement local (bis) | business |
| 038 | Groupements de professionnels (ex. syndicats professionnels) | business |
| 014 | Amicales, groupements affinitaires, groupements d'entraide | asso |
| 003 | Défense de droits fondamentaux, activités civiques | asso |
| 004 | Justice | asso |
| 009 | Action socio-culturelle | asso |
| 015 | Éducation, formation | asso |
| 016 | Recherche | asso |
| 019 | Interventions sociales | asso |
| 020 | Associations caritatives, humanitaires, aide au développement | asso |
| 026 | Logement | asso |
| 028 | Sécurité, protection civile | asso |
| 029 | Armée (anciens combattants, ...) | asso |
| 032 | Activités religieuses, spirituelles ou philosophiques | asso |
| 001 | Activités politiques | other |
| 002 | Clubs, cercles de réflexion | other |
| 030 | Domaines divers | other |
| 034 | Domaines divers (non classé) | other |
| 050 | Activités politiques (bis) | other |
| 100 | Inconnu / non renseigné | other |

Règles par mots-clés (appliquées après le mapping par famille) :

- **alumni** (priorité 1) : titre/objet ~ anciens?\s+(?:élèves|eleves|étudiants|etudiants)|alumni|ancien(?:ne)?s?\s+de\s+l['’]?école
- **gaming** (priorité 2) : vertical fun ET titre/objet ~ jeux?\s+vid[ée]o|e-?sport|esport|jeu[x]?\s+de\s+r[ôo]le|ludique|jeux?\s+de\s+soci[ée]t[ée]|jeux?\s+de\s+plateau|figurines|wargame|manga|cosplay
- **wellness** (priorité 3) : vertical fun/sport/asso ET titre/objet ~ bien[- ]?[êe]tre|yoga|m[ée]ditation|sophrologie|relaxation|qi\s?gong|pilates|zen\b

## Fichiers

- `RLL-IDF-villes-x-verticals-v0.1.xlsx` — feuilles : villes_x_verticals, top_associations_sport, top_associations_culture, top_associations_loisirs, federations_idf, sources, mapping_waldec_verticals
- `RLL-IDF-villes-x-verticals-v0.1.csv` — feuille principale (séparateur `;`, UTF-8 BOM)
- `build.py` — script de construction

## Contexte d'exécution (2026-09-30)

- Hôtes refusés par la politique d'egress de la session (403 sur CONNECT, aucun contournement tenté) :
  `www.data.gouv.fr`, `static.data.gouv.fr`, `object.files.data.gouv.fr`, `files.data.gouv.fr`, `tabular-api.data.gouv.fr`,
  `www.insee.fr`, `api.insee.fr`, `data.iledefrance.fr`, `equipements.sports.gouv.fr`, `www.sports.gouv.fr`, `injep.fr`,
  `geo.api.gouv.fr`, `opendata.paris.fr`, `*.opendatasoft.com`, `huggingface.co`, `unpkg.com`, `cdn.jsdelivr.net`.
  Hôtes joignables : `registry.npmjs.org`, `pypi.org`, `raw.githubusercontent.com` (aucune copie du RNA / INJEP / Data ES
  n'y est connue, donc rien d'autre n'a été récupéré).
- Conséquence : dans cette v0.1, seule l'ossature communes + population est renseignée. Les colonnes `assos_*`,
  `clubs_sport`, `licences_sport`, `equipements_sportifs`, les ratios et `score_potentiel_RLL` sont vides, et les feuilles
  `top_associations_*` / `federations_idf` ne contiennent qu'une note. Le tri actuel est donc **par population**.
- Les branches RNA / INJEP / Data ES de `build.py` (lecture en chunks, filtre IDF, mapping, agrégats, roll-up Paris) ont été
  validées sur des fixtures synthétiques, pas sur les fichiers réels : la détection des noms de colonnes est heuristique
  (`_find`, `RNA_USECOLS`, `valcols`) et peut nécessiter un ajustement au premier run avec accès réseau.
- Aucune donnée personnelle : `dir_civilite`, `adrg_*`, `siteweb`, `observation` du RNA ne sont jamais lus ; le champ libre
  `objet` sert au mapping mais n'est pas exporté dans les feuilles `top_associations_*`.
