# Istruzioni per GitHub Copilot — SCOPE

Queste istruzioni aiutano Copilot a lavorare correttamente su questo repository.

- **Progetto**: SCOPE è un progetto **Python ≥ 3.10**.
- **Codice applicativo**: vive interamente in `src/scope/`.
- **Sola lettura sul target**: SCOPE analizza il repository dei test target **esclusivamente in lettura**; non deve mai modificarlo.
- **Nessuna operazione Git remote**: SCOPE non esegue mai operazioni Git remote (push, pull, fetch, ecc.) da sé; resta un analizzatore invocato dall'esterno (es. CI).
- **Euristiche**: qualsiasi modifica alle euristiche di analisi (risoluzione chiamate, classificazione, copertura) richiede fixture dedicate e golden test a corredo.
- **Controlli obbligatori** prima di proporre modifiche:
  - `pytest`
  - `ruff check src/ tests/`
- **Dashboard — JS/CSS**: i sorgenti JavaScript e CSS della dashboard vanno modificati solo negli asset dedicati in `src/scope/assets/` (`dashboard.js`, `dashboard.css`), mai come stringhe inline in `report.py`.
- **Dashboard — output**: l'HTML generato deve restare un **singolo file autoconsistente**, senza dipendenze esterne.
- **Dipendenze**: non introdurre nuove dipendenze senza autorizzazione esplicita.
