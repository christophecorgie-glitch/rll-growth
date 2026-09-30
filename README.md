# rll-growth

Internal growth tooling for RLL (Real Life Life) — see `CLAUDE.md` for the rules and `rll-network/documents/RLL-ADR-15-*` for the decision.

| Pipeline | Scope | Status |
|---|---|---|
| `pipelines/fr-idf` | France — Île-de-France: communes × RLL verticals from RNA, INJEP, Data ES, INSEE | v0.1 skeleton (population only); RNA/INJEP/Data ES to be fetched from an environment allowed to reach data.gouv.fr / injep.fr / insee.fr |

Run: `pip install -r requirements.txt && cd pipelines/fr-idf && python3 build.py --workdir raw --out out`
