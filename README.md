# rll-growth

Internal growth tooling for RLL (Real Life Life) — see `CLAUDE.md` for the rules and `rll-network/documents/RLL-ADR-15-*` for the decision.

| Pipeline | Scope | Status |
|---|---|---|
| `pipelines/fr-idf` | France — Île-de-France: communes × RLL verticals from RNA, INJEP, Data ES; population INSEE via @etalab/decoupage-administratif | v0.1 — dernière exécution, sources, millésimes et nombres de lignes : [`out/sources.md`](pipelines/fr-idf/out/sources.md) |

Run (from the repository root): `pip install -r requirements.txt && python3 pipelines/fr-idf/build.py` (the build reads and writes `pipelines/fr-idf/raw/` and `pipelines/fr-idf/out/` whatever the current directory, writes outputs only in `out/`, and records its sources in `out/sources.md`; options in `pipelines/fr-idf/README-sources.md`).

Tests (no network, no secret, no `raw/` needed): `python3 -m pytest pipelines/fr-idf/tests`. The same command runs in CI (`.github/workflows/ci.yml`) on every pull request and on every push to `main`.
