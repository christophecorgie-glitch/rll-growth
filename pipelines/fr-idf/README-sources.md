# Pipeline fr-idf — README

Ce fichier est **rédigé à la main** et n'est jamais écrit par `build.py` (G-002). Le build écrit uniquement dans
`out/` : le classeur, le CSV et **`out/sources.md`**, registre de l'exécution (source, URL, licence, date, statut,
nombre de lignes brutes et conservées, notes, écarts de correspondance des communes, mapping appliqué) — les mêmes
faits que la feuille `sources` du classeur, exigée par `CLAUDE.md`. Les chiffres d'une exécution (dates, nombres
de lignes) se lisent dans `out/sources.md` et la feuille `sources`, pas ici. Le test
`tests/test_writes_only_out.py` (pytest, sans réseau, données fictives) vérifie que le build n'écrit rien en
dehors de `out/`.

Reproduire (depuis la racine du dépôt) : `python3 pipelines/fr-idf/build.py`. Par défaut `--workdir` vaut
`pipelines/fr-idf/raw` et `--out` vaut `pipelines/fr-idf/out`, quel que soit le répertoire courant. Les données
brutes vont dans `raw/` (ignoré par git), chacune avec un fichier `<nom>.source.json` (URL d'origine, date de
téléchargement) : un fichier déjà présent dans `raw/` est relu tel quel, et la feuille `sources` indique alors
« cache local raw/ » avec sa date de téléchargement réelle.

## Focus villes pilotes (G-003) — option `--focus` et feuilles `focus_*`

`python3 pipelines/fr-idf/build.py --focus 91312 91477 91645 92060` (codes INSEE séparés par des espaces ou des
virgules). Valeur par défaut : les 4 villes pilotes — Igny (91312), Palaiseau (91477), Verrières-le-Buisson (91645),
Le Plessis-Robinson (92060). Un code qui n'est pas une commune IDF arrête le build avant tout calcul (les
arrondissements de Paris sont refusés).

Les trois feuilles sont des **vues** sur la table `villes_x_verticals` et sur les associations RNA déjà chargées :
elles ne téléchargent rien de plus (les EPCI/EPT viennent des fichiers `epci.json` et `ept.json` du paquet
@etalab/decoupage-administratif 6.0.0, déjà source de la population) et ne modifient ni le score, ni les autres
feuilles, ni le CSV. Elles sont ajoutées après les feuilles existantes. Les seuils sont des constantes en tête de
`build.py` (`FOCUS_STRATE`, `FOCUS_TOP_N`, `VAGUE2_POP_MIN`, `VAGUE2_TOP_N`).

**Références de comparaison.**
- *IDF* : toutes les communes IDF de niveau `commune` (arrondissements exclus, Paris compté une fois en 75056).
- *Strate* : parmi elles, les communes de 10 000 à 40 000 habitants (population municipale, bornes incluses).
  Les 4 villes pilotes y sont toutes (Igny ~10 800 hab. est proche de la borne basse) ; la colonne `dans_strate`
  le signale pour un focus différent.
- *Percentile* : rang moyen, 0–100 = part des communes de la référence dont la densité est strictement inférieure,
  plus la moitié des ex aequo (les nombreuses communes à 0 association d'un vertical se partagent donc le même
  percentile, et une ville à 0 n'est pas au percentile 0). Une densité est un nombre pour 1 000 habitants.

**`focus_profil`** — une ligne par ville × mesure, au format long (choisi plutôt que des colonnes pour garder les
mêmes colonnes de comparaison sur chaque ligne et pouvoir filtrer/trier) :
- `vertical` = `total` (toutes associations), puis les 11 verticals RLL (dont `other`), puis deux lignes sport hors
  RNA : `sport_licences` (licences INJEP, au lieu de résidence du licencié) et `sport_equipements` (équipements
  Data ES) ;
- `mesure` dit ce qui est compté ; `nombre` et `pour_1000_hab` sont donc le nombre d'associations et les
  associations pour 1 000 habitants sur les lignes RNA, et le nombre de licences / d'équipements (et leur densité)
  sur les deux lignes sport ;
- `percentile_IDF`, `percentile_strate`, `mediane_IDF_pour_1000_hab`, `mediane_strate_pour_1000_hab` : position de
  la ville et médianes des deux références, pour la même mesure.

**`focus_associations`** — pour chaque ville du focus et chaque vertical sauf `other`, les 10 associations les plus
structurées, avec exactement le critère et les colonnes des feuilles `top_associations_*` (fédération puis union,
reconnaissance d'utilité publique, SIRET renseigné, ancienneté, puis numéro RNA). Personne morale uniquement :
`rna_id`, dénomination, code et famille Waldec, vertical, groupement, RUP oui/non, SIRET renseigné oui/non, date de
création. Le rang (`rang_dans_commune_vertical`) est calculé par ville × vertical. Un vertical a moins de 10 lignes
quand la ville a moins de 10 associations actives dans ce vertical.

**`focus_vague2`** — liste de travail pour la vague suivante (le choix des villes revient à Christophe) :
- candidates : communes IDF hors focus, hors arrondissements, **population ≥ 5 000 habitants** (seuil qui neutralise
  le biais « micro-communes » du score sans le modifier) ;
- situées dans le même territoire qu'une ville du focus **ou** dans le même département ; `critere` indique lequel
  s'applique (`EPCI`, `EPT`, `département`, ou les deux, ex. `EPCI et département`), `villes_focus_liees` avec
  quelle(s) ville(s) du focus ;
- territoire : l'EPCI à fiscalité propre, sauf dans la **Métropole du Grand Paris** (130 communes, trop large pour
  dire « voisine ») où l'on prend l'**établissement public territorial** (EPT, disponible dans
  decoupage-administratif 6.0.0, `ept.json`). Paris n'appartient à aucun EPT : pour Paris, seul le critère
  département joue ;
- classement par `score_potentiel_RLL` (inchangé ; à égalité, population décroissante), 15 premières ;
- `vertical_dense_1..3` : les 3 verticals (hors `other`) où la commune a le **percentile** de densité
  d'associations le plus élevé, calculé parmi les communes IDF de 5 000 habitants ou plus (la population des
  candidates). C'est une densité relative aux autres communes : en valeur brute pour 1 000 habitants, `asso` et
  `sport` arriveraient en tête partout. La référence n'est pas « toutes les communes IDF » : dans les villages, les
  verticals rares (alumni, gaming, family) sont presque toujours à 0 (75 % des communes IDF pour alumni), si bien
  qu'une seule association de ce type suffirait à placer le vertical en tête ; au-delà de 5 000 habitants, la part
  de zéros tombe à 38 % pour alumni, 20 % pour gaming, 11 % pour family et ~0 % pour les autres.

## Source de population (décision 2026-10-01)

La population vient de **@etalab/decoupage-administratif** (npm, données sous Licence Ouverte), version épinglée
dans `build.py` (`ETALAB_COG_VERSION`, 6.0.0) : populations de référence INSEE, population municipale (millésime
dans `POP_MILLESIME` et dans `out/sources.md`). Elle remplace l'ancienne source primaire `insee_pop` (fichier
INSEE des populations légales via data.gouv.fr), retirée du catalogue `SOURCES` : le paquet etalab republie le
même fichier INSEE et porte aussi le COG (communes, arrondissements municipaux, codes postaux), ce qui évite une
seconde jointure. Changer de version impose de vérifier la section « Millésimes et versions de package » du
README du paquet.

## Téléchargements

Chaque téléchargement passe par le proxy HTTPS de l'environnement. Coupure de connexion, timeout et HTTP 5xx :
jusqu'à 4 tentatives avec délai exponentiel (5 s, 10 s, 20 s), reprise du fichier partiel par `Range` quand le
serveur l'accepte. 403 et 404 ne sont jamais retentés. Délai d'attente par opération réseau : 60 s (API) / 300 s
(fichiers) — il borne chaque lecture, pas la durée totale. Une source en échec est consignée (`FAILED`) dans la
feuille `sources` et dans `out/sources.md` ; ses colonnes restent vides et le build continue.

## Score `score_potentiel_RLL` — une heuristique

Le score est une **heuristique** : moyenne des z-scores (calculés sur les communes IDF, hors arrondissements) de
log1p(assos_total), log1p(licences_sport), log1p(equipements_sportifs) et des ratios pour 1 000 habitants
(plafonnés au 99e centile), pour les seules sources disponibles. Ce n'est ni une taille de marché ni une
prédiction.

Biais connus (décision 2026-10-01 : score inchangé) : (1) les ratios pour 1 000 habitants placent des
micro-communes en tête malgré le plafonnement au 99e centile — un seuil de population (≥ 5 000 habitants) est
appliqué dans la feuille `focus_vague2` ; (2) les arrondissements centraux de Paris sont gonflés par les sièges
sociaux domiciliés (domiciliation, sièges nationaux), qui ne reflètent pas une activité locale.

Paris figure en une ligne `commune` (75056) plus 20 lignes `arrondissement` (75101–75120) ; le rang n'est attribué
qu'aux communes. **Sommer toutes les lignes compte Paris deux fois** : pour un total régional, filtrer
`niveau = commune`.

## Sources et règles de lecture

- **RNA** (fichier Waldec mensuel, Licence Ouverte 2.0) : seules les associations `position = A` (actives) avec un
  code INSEE de commune IDF sont comptées ; `adrs_codeinsee` peut être vide/obsolète pour des associations
  anciennes (sous-estimation possible). Le fichier Waldec ne couvre pas l'Alsace-Moselle (hors périmètre). Les
  colonnes nominatives (`dir_*`, `adrg_*`, `siteweb`, `observation`…) ne sont jamais lues (`RNA_USECOLS`) ; le champ
  libre `objet` sert au classement par mots-clés mais n'est jamais exporté.
- **INJEP** (licences et clubs géocodés, Licence Ouverte 2.0) : millésime = dernier disponible dans le jeu de
  données au moment du build. Licences comptées au lieu de résidence du licencié, clubs au siège du club.
- **Data ES** (équipements sportifs, Licence Ouverte 2.0) : un équipement = une ligne dédoublonnée sur
  l'identifiant d'équipement ; les lieux de pratique non bâtis (sentiers, plans d'eau) sont inclus.
- **Communes fusionnées** : avant agrégation, les anciens codes INSEE du RNA, de l'INJEP et de Data ES sont
  remplacés par le code de la commune actuelle (table construite depuis @etalab/decoupage-administratif : communes
  déléguées/associées → `chefLieu`, `anciensCodes` d'une commune actuelle → son code ; un code encore actuel n'est
  jamais réaffecté). Écarts avant/après : feuille `correspondance_communes` et `out/sources.md`.
- **Feuilles `top_associations_*`** : 5 associations par commune (ou arrondissement de Paris) et par feuille. Le
  RNA ne publie ni nombre d'adhérents, ni budget, ni effectif : la taille est approchée, dans l'ordre, par (1) le
  groupement (fédération, puis union, puis association simple), (2) la reconnaissance d'utilité publique (`rup_mi`
  renseigné), (3) l'immatriculation SIRENE (`siret` renseigné), (4) l'ancienneté, puis le numéro RNA.
  Dénominations d'associations conservées (personnes morales, décision 2026-10-01) ; aucun champ nominatif.

## Mapping familles Waldec → verticals RLL

Par famille (3 premiers caractères de `objet_social1`), puis règles par mots-clés. Mapping validé par Christophe le
2026-10-01 ; libellés vérifiés contre la nomenclature WALDEC et les titres réels du fichier RNA (les familles
025–029 n'existent pas). Les codes hors nomenclature (000, 008, 012, vide, codes isolés) tombent en `other`. Source
de vérité : `WALDEC_FAMILY_TO_VERTICAL` dans `build.py`, recopiée dans la feuille `mapping_waldec_verticals` et dans
`out/sources.md` à chaque build.

| Famille | Libellé | Vertical RLL |
|---|---|---|
| 011 | Sports, activités de plein air | sport |
| 013 | Chasse, pêche | nature |
| 024 | Environnement, cadre de vie | nature |
| 006 | Culture, pratiques d'activités artistiques, culturelles | culture |
| 010 | Préservation du patrimoine | culture |
| 005 | Information, communication | culture |
| 007 | Clubs de loisirs, relations | fun |
| 034 | Tourisme | fun |
| 017 | Santé | wellness |
| 018 | Services et établissements médico-sociaux | wellness |
| 021 | Services familiaux, services aux personnes âgées | family |
| 022 | Conduite d'activités économiques | business |
| 023 | Représentation, promotion et défense d'intérêts économiques | business |
| 030 | Aide à l'emploi, développement local, promotion de solidarités économiques, vie locale | business |
| 014 | Amicales, groupements affinitaires, groupements d'entraide (hors défense de droits fondamentaux) | asso |
| 003 | Défense de droits fondamentaux, activités civiques | asso |
| 004 | Justice | asso |
| 009 | Action socio-culturelle | asso |
| 015 | Éducation, formation | asso |
| 016 | Recherche | asso |
| 019 | Interventions sociales | asso |
| 020 | Associations caritatives, humanitaires, aide au développement, développement du bénévolat | asso |
| 032 | Logement | asso |
| 036 | Sécurité, protection civile | asso |
| 038 | Armée (dont préparation militaire, médailles), anciens combattants | asso |
| 040 | Activités religieuses, spirituelles ou philosophiques | asso |
| 001 | Activités politiques | other |
| 002 | Clubs, cercles de réflexion | other |
| 050 | Domaines divers, domaines de nomenclature SITADELE à reclasser | other |

Règles par mots-clés (appliquées après le mapping par famille, sur titre + objet) :

- **alumni** (priorité 1) : anciens élèves / étudiants, alumni, anciens de l'école ;
- **gaming** (priorité 2) : vertical `fun` ET jeux vidéo, e-sport, jeux de rôle / de société / de plateau,
  ludique, figurines, wargame, manga, cosplay ;
- **wellness** (priorité 3) : vertical `fun`/`sport`/`asso` ET bien-être, yoga, méditation, sophrologie,
  relaxation, qi gong, pilates, zen.

Expressions régulières exactes : `KW_ALUMNI`, `KW_GAMING`, `KW_WELLNESS` dans `build.py` (recopiées dans la feuille
`mapping_waldec_verticals`). Le mapping reste une convention RLL, discutable pour 005 (information/communication →
culture), 015 (éducation → asso), 013 (chasse/pêche → nature) et 034 (tourisme → fun).

## Fichiers

- `build.py` — script de construction ; n'écrit que dans `--out` (et dans `--workdir` pour un téléchargement absent
  du cache).
- `out/RLL-IDF-villes-x-verticals-v0.1.xlsx` — feuilles : villes_x_verticals, top_associations_sport,
  top_associations_culture, top_associations_loisirs, federations_idf, sources, mapping_waldec_verticals,
  correspondance_communes, focus_profil, focus_associations, focus_vague2.
- `out/RLL-IDF-villes-x-verticals-v0.1.csv` — feuille principale (séparateur `;`, UTF-8 BOM).
- `out/sources.md` — registre des sources de l'exécution (généré).
- `tests/` — tests pytest sans réseau (`python3 -m pytest pipelines/fr-idf/tests`).

## Hôtes requis

`www.data.gouv.fr`, `static.data.gouv.fr` (API et fichiers INJEP), `media.interieur.gouv.fr` (zip RNA Waldec),
`data.education.gouv.fr` (exports Data ES), `registry.npmjs.org` (paquet etalab).
