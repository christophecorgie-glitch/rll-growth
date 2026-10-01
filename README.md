# rll-growth

Internal growth tooling for RLL (Real Life Life) — see `CLAUDE.md` for the rules and `rll-network/documents/RLL-ADR-15-*` for the decision.

| Pipeline | Scope | Status |
|---|---|---|
| `pipelines/fr-idf` | France — Île-de-France: communes × RLL verticals from RNA, INJEP, Data ES; population INSEE via @etalab/decoupage-administratif | v0.1 — 5/5 sources OK (build 2026-10-01): RNA Waldec 2026-10-01, INJEP 2023, Data ES 2026-09-28, populations de référence 2023 |

Run: `pip install -r requirements.txt && cd pipelines/fr-idf && python3 build.py` (defaults: `--workdir raw --out out`)
