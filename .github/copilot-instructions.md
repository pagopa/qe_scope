# Istruzioni per GitHub Copilot — SCOPE

SCOPE (Spec COverage Probe E2E) misura quanto le suite di test E2E coprono le API di un progetto, confrontando le spec OpenAPI con ciò che i test Cucumber invocano davvero.

## Informazioni essenziali

- **Linguaggio:** Python ≥ 3.10.
- **Codice applicativo:** si trova in `src/scope/` (config, parser Java condiviso, inventario, copertura tag, dashboard, ingest runtime).
- **Sola lettura sul target:** SCOPE analizza il repository target (i test E2E) esclusivamente in lettura; non lo modifica mai.
- **Nessuna operazione Git remota:** SCOPE non esegue mai push, pull, fetch o altre operazioni Git su repository remoti; resta un analizzatore invocabile da CI esterna.
- **Euristiche e golden test:** ogni modifica alle euristiche di analisi (in particolare `OpResolver` in `java_analysis.py`) deve essere accompagnata da una fixture in `tests/fixtures/` e dal relativo golden test. Non validare mai un fix guardando solo i totali aggregati: confronta gli endpoint uno per uno.
- **Controlli obbligatori prima di ogni PR:**
  ```bash
  pytest
  ruff check src/ tests/
  ```
- **Dashboard — asset dedicati:** JavaScript e CSS della dashboard vanno modificati in `src/scope/assets/dashboard.js` e `dashboard.css`, non come stringhe inline in `report.py`.
- **Dashboard — file singolo autocontenuto:** l'HTML generato in `reports/html/` deve restare un unico file autoconsistente, senza dipendenze esterne (JS/CSS vengono inlinati a build time).
- **Dipendenze:** non introdurre nuove dipendenze senza autorizzazione esplicita.

## Note operative

- Non modificare configurazioni CI senza necessità.
- `config.yaml`, `reports/`, `data/runtime-*.json` non sono versionati: non presumere che esistano.

## Tracciabilità

Ogni Pull Request generata deve riportare:

- **Jira key** nel titolo o nella descrizione della PR;
- **collegamento alla card Jira** corrispondente;
- **descrizione dei test eseguiti** per validare la modifica;
- **indicazione esplicita delle verifiche non eseguite**, se presenti.
