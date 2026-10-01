# RLL — IDF villes × verticals v0.1 — sources & méthode

Partie générée le 2026-10-01 par `build.py` (reproductible : `python3 build.py`).

Périmètre : Île-de-France (75, 77, 78, 91, 92, 93, 94, 95). Données ouvertes françaises uniquement. Aucune donnée personnelle de personne physique dans les sorties (colonnes RNA nominatives jamais chargées ; champ `objet` libre non exporté).

## Sources tentées (ordre d'essai) et résultat

| # | Source | URL | Licence | Date | Millésime | Statut | Lignes brutes | Lignes IDF conservées | Détail |
|---|---|---|---|---|---|---|---|---|---|
| 1 | @etalab/decoupage-administratif (npm) — COG + populations légales INSEE republiés (source de population) | https://registry.npmjs.org/@etalab/decoupage-administratif/-/decoupage-administratif-6.0.0.tgz | Données : Licence Ouverte (Etalab) — code : MIT | 2026-10-01 | COG 2026 ; population 2023 | **OK** | 37590 | 1286 | package 6.0.0 published 2026-03-09; population = populations de référence INSEE 2023 (insee.fr/fr/statistiques/8680726); remplace l'ancienne source insee_pop (data.gouv.fr, slug disparu) |
| 2 | RNA — Répertoire National des Associations (fichier Waldec mensuel) | https://media.interieur.gouv.fr/rna/rna_waldec_20261001.zip | Licence Ouverte / Open Licence 2.0 (Etalab) | 2026-10-01 | extraction du 2026-10-01 | **OK** | 2314908 | 331440 | rna_waldec_20261001.zip — filtered position='A' and IDF code INSEE; last_modified 2026-09-30 |
| 3 | RNA agrégé à l'échelle nationale (fallback) | https://www.data.gouv.fr/api/1/datasets/rna-agrege-a-lechelle-nationale/ | Licence Ouverte / Open Licence 2.0 (Etalab) | 2026-10-01 |  | **NOT_USED** |  |  | fallback non sollicité : le fichier Waldec principal a été lu |
| 4 | INJEP / Ministère des Sports — licences et clubs sportifs géocodés | https://static.data.gouv.fr/resources/donnees-geocodees-issues-du-recensement-des-licences-et-clubs-aupres-des-federations-sportives-agreees-par-le-ministere-charge-des-sports/20251229-163107/lic-data-2023.csv | Licence Ouverte / Open Licence 2.0 (Etalab) | 2026-10-01 | 2023 | **OK** | 1014746 | 73285 | lic-data-2023.csv — colonnes lues ['Code Commune', 'Département', 'Code', 'Fédération', 'Total'], valeur 'Total' |
| 5 | INJEP / Ministère des Sports — licences et clubs sportifs géocodés | https://static.data.gouv.fr/resources/donnees-geocodees-issues-du-recensement-des-licences-et-clubs-aupres-des-federations-sportives-agreees-par-le-ministere-charge-des-sports/20251229-163249/clubs-data-2023.csv | Licence Ouverte / Open Licence 2.0 (Etalab) | 2026-10-01 | 2023 | **OK** | 121700 | 12697 | clubs-data-2023.csv — colonnes lues ['Code Commune', 'Département', 'Code', 'Fédération', 'Clubs'], valeur 'Clubs' |
| 6 | Data ES — Recensement des équipements sportifs et lieux de pratique (complet) | https://data.education.gouv.fr/api/explore/v2.1/catalog/datasets/data-es-recensement-des-equipements-sportifs-et-lieux-de-pratique-complet/exports/csv?use_labels=true | Licence Ouverte / Open Licence 2.0 (Etalab) | 2026-10-01 | export du 2026-09-28 | **OK** | 333720 | 30070 | data-es-recensement-des-equipements-sportifs-et-lieux-de-pratique-complet.csv — colonnes lues ['Commune INSEE', "Numéro de l'équipement sportif"] |

## Notes et réserves

- Population : populations de référence INSEE 2023 (insee.fr/fr/statistiques/8680726), republiées dans @etalab/decoupage-administratif 6.0.0 (publié 2026-03-09). Cette source remplace l'ancienne source primaire insee_pop (data.gouv.fr), retirée.
- INJEP licences: year 2023 (latest available in the dataset at build time).
- INJEP clubs: year 2023 (latest available in the dataset at build time).
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

> Historique : état du 2026-09-30, levé le 2026-10-01 (hôtes ouverts, voir « Premier run complet »).

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

## Premier run G-001 (2026-09-30) — caveats

> Historique : run à 1 source sur 6 ; remplacé par le premier run complet du 2026-10-01 ci-dessous.

Commande : `python3 build.py --workdir raw --out out` (durée du build : 4 s ; seul le tarball npm est téléchargé).

| Source | Statut | Lignes brutes | Lignes IDF |
|---|---|---|---|
| insee_pop (data.gouv.fr) | **refusée** — 403 sur CONNECT `www.data.gouv.fr` | – | – |
| etalab_cog (registry.npmjs.org, 6.0.0) | OK | 37 590 | 1 286 (1 266 communes + 20 arrondissements) |
| rna (data.gouv.fr) | **refusée** — 403 | – | – |
| rna_agrege (data.gouv.fr) | **refusée** — 403 | – | – |
| injep (data.gouv.fr) | **refusée** — 403 | – | – |
| data_es (data.gouv.fr) | **refusée** — 403 | – | – |

- Les refus viennent de la politique réseau de l'environnement d'exécution (proxy d'egress), pas des producteurs.
  Aucun contournement ni scraping tenté. Pour débloquer : autoriser au minimum `www.data.gouv.fr`,
  `static.data.gouv.fr` et `object.files.data.gouv.fr` dans l'environnement, puis relancer.
- Résultat identique au squelette v0.1 déjà versionné (contenu des 7 feuilles et du CSV inchangé, seul le binaire
  xlsx diffère par ses métadonnées) : **aucun commit `data(fr-idf)` n'a été fait** pour éviter un diff sans contenu.
- `score_potentiel_RLL` et les ratios `*_per_1k` sont vides pour les 1 286 lignes : pas de classement par score possible,
  le `rang` actuel suit la population.
- Le mapping Waldec → verticals n'a pas pu être confronté aux données réelles (RNA non téléchargé) : les anomalies
  de correspondance (familles inconnues, `objet_social1` vide, colonnes renommées) restent à mesurer au prochain run.
- `build.py` écrit `README-sources.md` dans le dossier `--out` : avec `--out out`, il produit un doublon
  `out/README-sources.md` (non versionné ici) ; avec le `--out` par défaut (dossier du pipeline), il **écrase** ce
  fichier et efface les sections rédigées à la main (« Contexte d'exécution », celle-ci). À corriger dans une PR dédiée
  (écrire le README généré à part, ou préserver une section manuelle).

## Premier run complet G-001 (2026-10-01) — réserves

Commande : `python3 build.py --workdir pipelines/fr-idf/raw --out pipelines/fr-idf/out` (depuis la racine du dépôt) ;
exit 0, 132 s, dont le téléchargement du zip RNA (410 Mo). Deux coupures de connexion sur data.gouv.fr ont été
absorbées par les relances de `fetch()`.

**Sources : 5/5 au statut attendu** — 4 sources lues `OK` (etalab_cog, rna, injep ×2 fichiers, data_es) et
`rna_agrege` tracé `NOT_USED` (repli non sollicité, le Waldec principal ayant été lu).

| Source | Hôte du fichier | Millésime | Lignes brutes | Lignes IDF |
|---|---|---|---|---|
| etalab_cog (source de population) | registry.npmjs.org | COG 2026 ; populations de référence INSEE 2023 | 37 590 | 1 286 (1 266 communes + 20 arrondissements) |
| rna | media.interieur.gouv.fr | extraction Waldec du 2026-10-01 | 2 314 908 | 331 440 actives |
| rna_agrege | — | — | non lu | — |
| injep licences | static.data.gouv.fr | 2023 | 1 014 746 | 73 285 |
| injep clubs | static.data.gouv.fr | 2023 | 121 700 | 12 697 |
| data_es | data.education.gouv.fr | export du 2026-09-28 | 333 720 | 30 070 équipements |

Changements de sources : `insee_pop` (data.gouv.fr) est **retirée** (slug disparu, aucun jeu INSEE équivalent sur
data.gouv.fr) ; `etalab_cog`, qui republie les populations de référence INSEE 2023 (insee.fr/fr/statistiques/8680726),
est désormais la source de population. Le slug `data_es` est corrigé (`…-lieux-de-pratique-complet-1`) ; le fichier
est servi par data.education.gouv.fr.

Réserves :

- **Mapping Waldec à revoir (décision en attente, non modifié)** : sur les données réelles, les familles 025 à 029
  n'existent pas, et les titres montrent que les codes 030 à 040 ne correspondent pas aux libellés du mapping
  (030 ≈ emploi / développement local, 032 ≈ logement, 034 ≈ tourisme, 036 ≈ sécurité / protection civile,
  038 ≈ armée / anciens combattants, 040 ≈ activités religieuses, absent du mapping → `other`, 8 535 associations IDF).
  Les verticals `business`, `asso` et `other` en sont affectés ; la règle « 027 tourisme → fun » ne s'applique à aucune
  association.
- **Associations hors COG** : 1 108 associations actives ont un `adrs_codeinsee` IDF qui n'existe plus dans le COG 2026
  (communes fusionnées) et ne sont pas rattachées : total tableau 330 327 contre 331 440 lues. Même effet, plus faible,
  sur INJEP (2 527 664 licences rattachées sur 2 561 431 en IDF).
- **Score** : la moitié des composantes sont des ratios pour 1 000 habitants ; de très petites communes (< 1 000 hab.)
  occupent une partie du top 15. Heuristique conservée telle quelle.
- **Feuilles `top_associations_*`** : le tri par `code_insee` puis la coupe à 500 lignes ne retiennent que les premiers
  arrondissements de Paris, pas les 20 premières communes. À corriger dans une tâche dédiée.
- **README généré** : `build.py` écrit toujours `README-sources.md` dans `--out`. Pour ce run, la partie générée
  ci-dessus a été recopiée ici et le doublon `out/README-sources.md` n'est pas versionné.
- Paris : les quatre sources codent Paris par arrondissement (aucun 75056 brut) ; la ligne 75056 est la somme des
  20 arrondissements.
