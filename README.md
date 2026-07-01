# SCOPE — Spec COverage Probe E2E

SCOPE misura **quanto le suite di test E2E coprano davvero le API di un progetto**, confrontando gli endpoint dichiarati nelle specifiche OpenAPI con ciò che i test Cucumber effettivamente raggiungono.

A differenza di un conteggio "esistono dei test?", SCOPE distingue tre categorie per ogni endpoint:

| Categoria | Significato |
|---|---|
| 🟢 **Reale** | Uno scenario Cucumber eseguibile arriva a invocare l'endpoint |
| 🟡 **Fantasma** | Il codice client esiste nel repo, ma nessuno scenario lo esercita |
| 🔴 **Mai implementato** | L'endpoint è nelle spec ma non è integrato nei test |

Il perimetro (denominatore) viene dalle spec OpenAPI, non da una lista compilata a mano: la percentuale è quindi oggettiva e tracciabile fino al singolo scenario.

> SCOPE lavora **dall'esterno, in sola lettura**: analizza il repository dei test senza mai modificarlo.

## Come funziona (in breve)

L'analisi è statica e si articola su due livelli, che condividono lo stesso parser Java:

- **Inventario + copertura statica** — dai `pom.xml` risolve le spec OpenAPI, estrae gli endpoint (`METHOD + path + operationId`) e verifica quali metodi client sono invocati nel sorgente.
- **Copertura reale** — traccia la catena `tag runner → scenario → step definition → wrapper → operationId`, così da sapere quali endpoint sono raggiungibili da uno scenario eseguibile.

La riconciliazione dei due livelli produce le tre categorie sopra. Un livello opzionale ingerisce gli **esiti di esecuzione reali** (report Cucumber JSON) e aggiunge, per ogni endpoint, se è stato *eseguito e con quale esito*.

> Nota: SCOPE misura l'**invocazione**, non la **verifica**. Un endpoint "reale" è raggiunto da uno scenario; non è garantito che lo scenario faccia asserzioni significative sulla sua risposta.

## Requisiti

- Python ≥ 3.10
- Il repository dei test da analizzare, disponibile in locale

## Installazione

```bash
pip install -e ".[dev]"      # package + dipendenze runtime (pyyaml, requests) + dev (ruff, pytest)
```

Vengono installati i comandi: `scope-inventory`, `scope-tags`, `scope-report`, `scope-runtime`, `scope-prune`
(equivalenti a `python3 -m scope.<modulo>`).

## Configurazione

Copia il template e imposta il percorso locale del repository dei test:

```bash
cp config.example.yaml config.yaml
```

```yaml
# config.yaml
target_repo: /percorso/al/repo/dei/test
```

`config.yaml` non è versionato (è specifico della tua macchina). In alternativa, con precedenza (utile in CI), usa la variabile d'ambiente:

```bash
SCOPE_TARGET_REPO=/percorso/al/repo/dei/test scope-inventory --suite send
```

Se il target non esiste, i comandi si fermano con un messaggio esplicito.

## Uso

### Tutto in un colpo solo

```bash
scripts/refresh-all.sh              # entrambe le suite + apre la dashboard nel browser
scripts/refresh-all.sh interop      # una sola suite
scripts/refresh-all.sh --no-open    # senza aprire il browser
```

Esegue nell'ordine gli auto-test, l'inventario, l'analisi tag e la generazione della dashboard.

### Passo per passo

```bash
# 1) inventario endpoint + copertura statica (scarica le spec, produce CSV + JSON)
scope-inventory --suite send

# 2) copertura reale per runner/tag (+ combinazione ottimale di tag)
scope-tags --suite send --optimize

# 3) dashboard HTML (riusa gli ultimi report disponibili)
scope-report --open
```

Ogni esecuzione salva in `reports/<timestamp>_<suite>[_tags]/`: le run non si sovrascrivono, così è possibile confrontare l'evoluzione nel tempo (trend). Se il codice dei test non è cambiato, basta rilanciare `scope-report`.

### Esiti di esecuzione (opzionale)

```bash
cp cucumber-report.json runtime/inbox/send/   # deposita i report delle run reali
scope-runtime                                 # ingest (idempotente)
scope-report --open                           # la dashboard mostra ✓ eseguita / ✗ rossa / ○ non eseguita
```

## Output

- **Dashboard HTML** (`reports/html/`): un singolo file autoconsistente (nessuna dipendenza esterna) con sintesi esecutiva, drill-down per endpoint/microservizio/tag/runner, copertura pesata per criticità, trend storico e una FAQ integrata. Si apre con doppio click e si può archiviare o inviare.
- **CSV / JSON** per suite (`reports/<timestamp>_<suite>/`): il dettaglio riga per riga, riutilizzabile da altri strumenti.

### Copertura pesata per criticità

Non tutti gli endpoint valgono uguale. In `data/criticality.yaml` si assegnano classi di criticità (`core` / `standard` / `marginal`) per servizio, spec, path o singolo endpoint; la dashboard mostra allora una **copertura reale pesata** accanto a quella semplice. Le classi si possono modificare direttamente nella dashboard ed esportare di nuovo nel file.

## Struttura del progetto

```
scope/
├── pyproject.toml          metadati, dipendenze, console-scripts, ruff, pytest
├── config.example.yaml     template di configurazione (copiare in config.yaml)
├── src/scope/              il package
│   ├── config.py           configurazione condivisa (percorsi, suite)
│   ├── java_analysis.py    parser Java condiviso (risoluzione chiamate → operationId)
│   ├── inventory.py        inventario endpoint + copertura statica
│   ├── tag_coverage.py     copertura reale per runner/tag
│   ├── report.py           generazione dashboard HTML
│   ├── runtime.py          ingest esiti di esecuzione reali
│   └── assets/             sorgenti JS/CSS della dashboard (inlinati a build)
├── data/                   configurazione versionata (criticality.yaml, spec-lock.json)
├── scripts/                refresh-all.sh, ci-check.sh
├── tests/                  golden test (pytest) + fixture (mini-repo sintetico)
├── .github/workflows/      CI: lint (ruff) + test (pytest)
└── reports/                output delle esecuzioni (non versionato)
```

## Sviluppo

```bash
pytest                      # golden test su un mini-repository sintetico con risposte note
ruff check src/ tests/      # lint
scripts/ci-check.sh         # test + guardie --strict (inventario e copertura) su entrambe le suite
```

I golden test proteggono le euristiche di analisi da regressioni: ogni comportamento atteso è verificato contro fixture dove la risposta corretta è nota per costruzione.

Per capire **come SCOPE è costruito internamente** (modello, pipeline, parser Java condiviso, come si verifica da solo), vedi [ARCHITECTURE.md](ARCHITECTURE.md).

## Limiti

- **Analisi statica**: non esegue i test (salvo il livello opzionale che ne ingerisce gli esiti). Un test disabilitato o mai lanciato può risultare comunque "coperto" a livello statico.
- **Invocazione ≠ verifica**: la copertura reale dice che un endpoint è raggiunto, non che la risposta sia verificata con asserzioni.
- **Euristiche**: la risoluzione delle chiamate e la classificazione di visibilità (public/internal) si basano su convenzioni; casi come reflection o client HTTP raw non sono tracciati. Controlli di sanità integrati segnalano quando la misura potrebbe essere degradata.

## Licenza

Vedi il file `LICENSE` (se presente nel repository).
