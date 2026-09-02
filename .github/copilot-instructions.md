# Istruzioni repository per GitHub Copilot — SCOPE

SCOPE (Spec COverage Probe E2E) misura quanto le suite di test E2E coprano davvero le API di un progetto,
confrontando le spec OpenAPI con ciò che i test Cucumber effettivamente raggiungono.

## Cosa sapere prima di modificare il codice

- **Progetto Python ≥ 3.10.** Il codice applicativo vive in `src/scope/`.
- **SCOPE analizza il repository target esclusivamente in sola lettura**: non deve mai scrivere, modificare
  o cancellare file nel repository analizzato (`target_repo` / `SCOPE_TARGET_REPO`).
- **Nessuna operazione Git remote**: SCOPE non deve mai eseguire `git push`, `git commit`, aprire PR o
  interagire con remote git. È un analizzatore statico, invocato eventualmente da una CI esterna.
- **Modifiche alle euristiche** (es. `java_analysis.py`, `inventory.py`, `tag_coverage.py`) richiedono
  **sempre** una fixture in `tests/fixtures/` e il relativo golden test in `tests/`. Non introdurre o
  correggere un'euristica senza fixture a corredo.
- **Controlli obbligatori prima di considerare completa una modifica**:
  - `pytest`
  - `ruff check src/ tests/`
- **JavaScript e CSS della dashboard** vanno modificati esclusivamente nei sorgenti dedicati
  `src/scope/assets/dashboard.js` e `src/scope/assets/dashboard.css` (inlinati a build time da
  `report.py`), mai come stringhe incorporate altrove.
- **La dashboard generata deve restare un singolo file HTML autoconsistente**, senza dipendenze esterne.
- **Non introdurre nuove dipendenze** (runtime o dev) senza autorizzazione esplicita.

Per approfondimenti su modello, pipeline e come estendere SCOPE, vedi `README.md` e `ARCHITECTURE.md`.
