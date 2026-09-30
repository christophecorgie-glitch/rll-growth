# CLAUDE.md — règles du dépôt `rll-growth`

Outillage interne de croissance RLL (ADR-15) : ciblage des Opérateurs sur **open data** (personnes morales), suivi des campagnes par territoire, un pipeline par pays. Dépôt séparé, **zéro couplage** avec `rll-network` et `rll-marketing` : aucune donnée membre, aucun accès Supabase, aucune clé de production.

## Interdits
- **Aucune donnée personnelle de personne physique** dans le dépôt ni dans les sorties : pas de noms de dirigeants/déclarants, pas d'emails, pas de téléphones, même si la source les contient. Les pipelines ne chargent jamais ces colonnes.
- **Aucune liste de contacts chargée depuis l'extérieur** (fichiers d'Opérateurs, achats de fichiers). Décision ADR-15 : la prospection vers des personnes physiques passe par des liens et QR d'invitation, jamais par des fichiers importés.
- **Pas de scraping** : uniquement des jeux de données publiés sous licence ouverte (Licence Ouverte 2.0, ODbL) ; la source, la licence, la date et le nombre de lignes sont consignés dans la feuille `sources` de chaque sortie.
- Pas de secret dans le dépôt ; pas de dépendance Supabase/Stripe/Mapbox.

## Flux de travail
- PR uniquement, merge humain, une tâche = une PR, commits conventionnels (`feat`, `fix`, `docs`, `chore`, `data`).
- Un pipeline = un dossier `pipelines/<pays>-<périmètre>/` avec `build.py`, `README-sources.md`, `out/` (résultats versionnés, sans données brutes).
- Reproductible : `python3 build.py` depuis un environnement ayant accès aux hôtes listés dans `README-sources.md` ; les données brutes vont dans `raw/` (ignoré par git).
- Toute règle de correspondance (ex. codes Waldec → verticals RLL) est documentée dans le README du pipeline et dans une feuille du classeur.
