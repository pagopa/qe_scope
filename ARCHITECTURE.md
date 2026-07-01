# Come è costruito SCOPE (architettura)

Questo documento spiega **come funziona SCOPE all'interno**: il modello concettuale, la pipeline, le euristiche centrali e come lo strumento verifica sé stesso. È pensato per chi deve manutenere o estendere il codice. Per l'uso quotidiano, vedi il [README](README.md).

---

## TL;DR (in parole semplici)

Immagina l'elenco di tutte le "porte" della tua applicazione: gli endpoint delle API, quelli dichiarati nelle specifiche. SCOPE **parte dagli scenari di test** e, per ognuno, segue il filo — *questo scenario, passando per i suoi passi, quale endpoint finisce per chiamare?* — poi confronta ciò che i test raggiungono con l'elenco completo delle porte.

Il risultato è che ogni endpoint finisce in una di tre caselle:

- 🟢 **testato davvero** — c'è uno scenario che lo esercita;
- 🟡 **solo apparente** — il codice per chiamarlo c'è, ma nessuno scenario lo usa (sembra coperto, non lo è);
- 🔴 **scoperto** — non è previsto in nessun test.

Volendo, SCOPE aggancia anche gli **esiti delle esecuzioni reali**, così dice pure se quei test, l'ultima volta che sono girati, sono passati o falliti. E permette di dare **più peso agli endpoint critici** per il business, perché una copertura dell'80% "sulle cose che contano" vale più di un 80% sparso a caso.

In una frase: **SCOPE misura quanto le API sono davvero protette dai test end-to-end, distinguendo ciò che è testato sul serio da ciò che sembra testato ma non lo è.** L'output è una dashboard consultabile, sempre rigenerabile.

> ⚠️ Un'onestà importante: SCOPE verifica che un endpoint venga *chiamato* da uno scenario, non che lo scenario ne *controlli a fondo* la risposta. Misura l'ampiezza della copertura, non la sua profondità di verifica.

---

## 1. Il modello concettuale

SCOPE risponde a una domanda diversa da "esistono dei test?". Per ogni endpoint dichiarato nelle specifiche OpenAPI pone **due domande separate sullo stesso codice**:

1. *Il metodo client generato per questo endpoint è invocato da qualche parte nel sorgente?* → **copertura statica**
2. *Esiste uno scenario Cucumber eseguibile che, partendo da un tag, arriva a invocarlo?* → **copertura reale**

La differenza tra le due risposte è il cuore del progetto:

| | Statica | Reale | Categoria |
|---|:---:|:---:|---|
| Metodo invocato **e** raggiunto da uno scenario | ✅ | ✅ | 🟢 **Reale** |
| Metodo invocato ma **nessuno** scenario lo raggiunge | ✅ | ❌ | 🟡 **Fantasma** |
| Né invocato né raggiunto | ❌ | ❌ | 🔴 **Mai implementato** |

**Analogia.** La copertura statica conta *quante stanze hanno una porta* (il wrapper esiste nel codice); la copertura reale conta *in quante stanze entra davvero qualcuno partendo dall'ingresso* (uno scenario eseguibile ci arriva). Una stanza può avere la porta ma essere irraggiungibile.

**Invariante.** Poiché entrambi i livelli usano lo stesso parser (§3), vale sempre `reale ⊆ statica`: un endpoint raggiunto da uno scenario è per forza anche invocato nel codice. Se questa relazione si rompe, c'è un bug nel parser.

> SCOPE misura l'**invocazione**, non la **verifica**: un endpoint "reale" è raggiunto da uno scenario, ma SCOPE non analizza se lo scenario fa asserzioni significative sulla risposta.

---

## 2. La pipeline

```
                      ┌─────────────────────────────────────────────┐
   pom.xml ──────────►│ inventory.py                                │
   OpenAPI spec       │  perimetro: METHOD + path + operationId     │──► coverage-<suite>.json
   (download)         │  copertura STATICA (resolver.directly_invoked)│    (inventario + covered)
                      └─────────────────────────────────────────────┘
                                        │ (stesso resolver Java)
   .feature ─────────┐                  ▼
   step defs ────────┼──►┌─────────────────────────────────────────┐
   runner ───────────┘   │ tag_coverage.py                          │──► tag-coverage-<suite>.json
                         │  tag → scenario → step → resolver → opId  │    (copertura reale + profondità)
                         └─────────────────────────────────────────┘
                                        │
   report Cucumber ─────►┌─────────────────────────────────────────┐
   (esiti di run)        │ runtime.py: ingest + join via TC-ID      │──► data/runtime-results.json
                         └─────────────────────────────────────────┘
                                        │
                         ┌─────────────────────────────────────────┐
                         │ report.py: reconcile() → dashboard HTML  │──► reports/html/*.html
                         └─────────────────────────────────────────┘
```

I moduli comunicano tramite **artefatti JSON su disco** (`reports/…`), non con chiamate dirette: ogni livello è eseguibile e ispezionabile in isolamento, e le run non si sovrascrivono (abilita il trend).

---

## 3. Il cuore: il parser Java condiviso (`java_analysis.py`)

Tutta l'accuratezza di SCOPE dipende da un'unica domanda tecnica: *dato un metodo Java, quali `operationId` finisce per invocare?* La risposta la dà la classe **`OpResolver`**, usata identica dai due livelli — così le euristiche (e le loro correzioni) vivono in un solo posto.

### Il fondamento: operationId ↔ metodo generato

L'OpenAPI Generator produce, per ogni `operationId` della spec, un metodo Java con lo **stesso nome**. Questa corrispondenza 1:1 è ciò che rende possibile mappare "chiamata a un metodo" → "endpoint".

### Il grafo delle chiamate

`OpResolver` costruisce un grafo dove i **nodi sono coppie `(classe, metodo)`** e gli archi sono le chiamate. Il punto delicato è risolvere il **tipo del receiver** di ogni chiamata `x.metodo(...)`: a quale classe appartiene `x`?

SCOPE usa una *symbol table dei poveri*: legge i tipi dichiarati di campi e variabili locali nel file per attribuire ogni chiamata alla classe giusta. Questo evita il problema degli **omonimi** — due classi diverse con un metodo dallo stesso nome non si "inquinano" a vicenda ereditando ciascuna gli endpoint dell'altra.

Cosa il resolver sa seguire:

- chiamate dirette `api.operationId(...)`;
- varianti `api.operationIdWithHttpInfo(...)` (il generatore emette entrambe);
- **method reference** `api::operationId`;
- chiamate **senza prefisso** (metodi statici o della stessa classe): `helper(...)`, `Classe.metodo(...)`;
- attraversamento **interfaccia → implementazioni** in modo transitivo (una chiamata su un'interfaccia raggiunge gli `operationId` delle sue implementazioni);
- static import.

La chiusura è calcolata a **punto fisso**: la catena `step → service → impl → client generato` viene seguita a qualsiasi profondità, non fino a un numero fisso di passaggi.

Dove il tipo del receiver *non* è ricostruibile (es. catene fluide), il resolver ripiega su una risoluzione per nome semplice — l'unico punto dove sopravvivono possibili collisioni, monitorato da un sanity check (§7).

### Due interrogazioni, un grafo

- `resolver.directly_invoked()` → gli `operationId` chiamati direttamente nel sorgente. È la **copertura statica** (usata da `inventory.py`).
- `resolver.ops_for(classe, metodo)` → tutti gli `operationId` raggiungibili da un metodo. È ciò che serve per la **copertura reale** (usata da `tag_coverage.py`).

---

## 4. Livello inventario — il perimetro (`inventory.py`)

Il denominatore delle percentuali non è compilato a mano: viene dalle spec.

1. **`extract_executions`** legge il `pom.xml` e trova le execution dell'OpenAPI Generator (con `parse_maven_properties` / `resolve_vars` per sciogliere le variabili Maven).
2. **`download_spec`** scarica ogni spec dall'URL (con cache locale in `.spec-cache/`).
3. **`extract_endpoints`** estrae `METHOD + path + operationId` da ogni spec.
4. **`classify_visibility`** etichetta l'endpoint `public`/`internal` con euristiche sul nome della spec e sul path.
5. **`compute_static_covered`** interroga `OpResolver.directly_invoked()` per marcare gli endpoint `covered`.

### Sanity dell'inventario (lock-file)

L'inventario dipende da un download di rete: un fallimento silenzioso (404, spec spostata, contenuto vuoto) rimpicciolirebbe il denominatore **senza accorgersene**, falsando ogni percentuale. Per questo `data/spec-lock.json` fissa una baseline riproducibile (per execution: URL, numero di endpoint, fingerprint del contenuto). A ogni run **`check_inventory`** confronta l'inventario corrente con la baseline e segnala download falliti, spec collassate a 0, execution sparite o cali anomali. Con `--strict` un FAIL fa uscire con codice ≠ 0 (per la CI); la baseline si aggiorna consapevolmente con `--update-lock`.

---

## 5. Livello copertura reale (`tag_coverage.py`)

Traccia la catena `tag → scenario → step → metodo → operationId`.

1. **`parse_features`** legge i `.feature`: per ogni scenario raccoglie tag, testo degli step, `file:line` e il **TC-ID** (l'identificatore `[TC-…]` nel titolo — chiave di join con gli esiti di esecuzione, §6).
2. **`parse_step_definitions`** legge le step definition Java: da ogni `@Given/@When/@Then` ricava il pattern e, via `OpResolver`, gli `operationId` che quel metodo raggiunge. Le annotazioni impilate sullo stesso metodo generano un pattern per alias.
3. **`_cucumber_to_regex`** converte le Cucumber expression (`{string}`, `{int}`, opzionali, alternative, custom parameter type) in regex.
4. **`match_steps`** associa ogni step di scenario alla step definition che lo matcha; a parità, vince il pattern **più specifico** (come fa Cucumber). Da lì raccoglie gli `operationId` invocati dallo scenario.
5. **`compute_runner_coverage`** / **`compute_tag_coverage`** aggregano per runner e per tag; **`scenario_matches_runner`** replica la logica JUnit5 `@IncludeTags` / `@ExcludeTags`. **`greedy_optimize`** trova, con un set-cover greedy, la combinazione di tag che massimizza la copertura.

### Famiglie di versioni

Molte API esistono in più versioni (`operationIdV23 … V29` + una base). Spesso lo scenario **sceglie la versione a runtime** passando una stringa (`getWebhookStep("V24")`). L'analisi statica, da sola, accrediterebbe lo scenario a *tutte* le versioni della famiglia — sovrastimando.

`build_version_families` raggruppa gli `operationId` per famiglia; `parse_version_tokens` estrae dai `.feature` i token di versione (`versione "V24"`, `"più recente"` → versione massima); `refine_ops_by_version` restringe l'attribuzione alla versione citata. Gli step senza token ereditano la versione dichiarata altrove nello stesso scenario; in totale assenza di token, l'attribuzione resta all'intera famiglia (scelta conservativa, dichiarata).

### Profondità

Oltre al sì/no, SCOPE conta per ogni endpoint **quanti scenari distinti lo invocano** (`endpoint_depth`): distingue "toccato una volta" da "esercitato spesso". È una prima misura di robustezza — ma resta **invocazione, non verifica**.

---

## 6. Livello esiti di esecuzione (`runtime.py`, opzionale)

Porta SCOPE oltre l'analisi statica: non solo "uno scenario può invocare l'endpoint", ma "è stato **eseguito** e con quale **esito**".

- **`ingest`** scandisce i report Cucumber JSON depositati in `runtime/inbox/<suite>/`. Un **ledger per-sha** rende l'operazione idempotente (ributtare lo stesso file è un no-op) e accumula la storia normalizzata degli esiti (`parse_cucumber` + `aggregate_status`: OK / KO / incerto).
- **`compute_current_state`** calcola lo stato corrente come overlay *"l'ultima run vince, per TC-ID"* su una **finestra temporale** (default 30 giorni). Il **flaky** (pass-rate nella finestra) è un segnale separato, non punitivo.
- Il **join con gli endpoint** avviene via TC-ID a valle, in `report.py` (`join_runtime`). La salute della chiave di join (scenari senza id, id duplicati) è sorvegliata da un sanity check.

Gli output vivono in `data/runtime-*.json` e non sono versionati (sono dati di run).

---

## 7. Riconciliazione e dashboard (`report.py`)

`report.py` è deliberatamente diviso in **funzioni pure** (calcolo) e **funzioni di rendering** (HTML):

- **`reconcile`** incrocia inventario statico + copertura reale (+ esiti di esecuzione) e produce, per ogni endpoint, la sua categoria (reale/fantasma/mai) e i metadati.
- **`resolve_crit_class`** applica la **copertura pesata**: da `data/criticality.yaml` assegna a ogni endpoint una classe (`core`/`standard`/`marginal`) con precedenza `operation > path > spec > service > default`, così la percentuale può riflettere quanto conta ciò che è coperto.
- **`compute_guide`** / **`compute_tag_suggestions`** calcolano, in modo deterministico, come massimizzare la copertura (quali runner lanciare, quale set minimo di tag, in quale tag inserire un endpoint scoperto).
- **`build_trend`** costruisce la serie storica dalle run in `reports/` (con baseline opzionale).
- **`render_*` + `generate_html`** producono la dashboard.

### Un file HTML autoconsistente

I sorgenti di JS e CSS vivono, **lint-abili**, in `src/scope/assets/dashboard.js` e `dashboard.css`; `report.py` li **inlina a build time** nell'HTML. L'output è così un **singolo file** senza dipendenze esterne, apribile con doppio click e archiviabile — ma la UI si edita in quei due file, non in una stringa gigante. Un test verifica che l'output resti autoconsistente.

---

## 8. Come SCOPE verifica sé stesso

Uno strumento fatto di euristiche può degradarsi in silenzio quando il codice analizzato cambia. Tre difese:

1. **Golden test** (`tests/`, `pytest`) — l'intera pipeline gira su un **mini-repository sintetico** (`tests/fixtures/`) dove la risposta corretta è nota per costruzione. Ogni caso che il resolver deve gestire (varianti `WithHttpInfo`, method reference, chiamate senza prefisso, omonimi cross-classe, interfaccia→implementazione, famiglie di versioni, logica dei tag runner, …) è una fixture: se una modifica rompe l'euristica, un test diventa rosso.
2. **Sanity check a runtime** — a ogni run su dati veri, `run_sanity_checks` confronta i propri "segni vitali" con soglie e con la run precedente (% step non matchati, % scenari senza chiamate, mediana `operationId`/metodo, salto anomalo degli endpoint reali, …) e li salva nel JSON. Con `--strict` un FAIL esce con codice ≠ 0.
3. **Guardie CI** (`scripts/ci-check.sh`) — esegue in blocco golden test + `--strict` su inventario e copertura reale per entrambe le suite: una guardia che non gira è una guardia che non esiste.

**Principio guida:** i fix alle euristiche vanno validati con un **diff endpoint-per-endpoint** tra run pre/post, non solo confrontando i totali aggregati — un aggregato invariato può nascondere endpoint persi e altri guadagnati.

---

## 9. Limiti intrinseci

- **Analisi statica**: SCOPE (salvo il livello esiti di esecuzione) non lancia i test. Un test disabilitato o mai eseguito può risultare "coperto" a livello statico.
- **Invocazione ≠ verifica**: la profondità di *verifica* (qualità delle asserzioni) non è misurata.
- **Euristiche**: chiamate via reflection o client HTTP raw non sono tracciate; la classificazione public/internal è per convenzione di naming, non da configurazione di deployment. I sanity check servono proprio a segnalare quando queste approssimazioni degradano la misura.

---

## 10. Estendere SCOPE

Questa sezione dice **dove mettere le mani** per i tipi di modifica più comuni. Ogni voce indica il *quando* (che problema stai risolvendo), il *dove* (quale file) e il *perché* di una regola.

### A. Migliorare l'accuratezza dell'analisi (modifiche al codice)

**Il resolver non "vede" un modo in cui i test chiamano un'API.**
*Sintomo:* un endpoint appare 🟡 fantasma (o ha meno scenari del previsto), ma leggendo i `.feature` è chiaro che qualche scenario lo esercita. Di solito significa che il codice di test invoca il client in un modo che `OpResolver` non sa ancora seguire (una nuova forma di chiamata, un ulteriore livello di indirezione, un pattern Java inusuale).
*Dove:* `java_analysis.py`, dentro `OpResolver` — è l'unico punto dove vive la logica di risoluzione delle chiamate.
*Regola non negoziabile:* **insieme alla correzione aggiungi una fixture in `tests/fixtures/`** — un mini esempio Java che riproduce quel pattern, con la risposta attesa — e il relativo test. Senza fixture, la prossima modifica può rompere di nuovo il caso in silenzio. (È così che sono stati bloccati tutti i pattern già supportati: method reference, chiamate senza prefisso, varianti `WithHttpInfo`, omonimi tra classi, ecc.)
*Come validare:* non fidarti del totale aggregato. Confronta la lista degli endpoint **reali** prima e dopo la modifica, uno per uno: un totale invariato può nascondere endpoint guadagnati da una parte e persi dall'altra.

**Cambiare come un endpoint viene etichettato o incluso nel perimetro.**
*Sintomo:* un endpoint è classificato `public`/`internal` in modo sbagliato, oppure una spec andrebbe inclusa/esclusa diversamente.
*Dove:* `inventory.py` (`classify_visibility` per la visibilità, `extract_executions`/`extract_endpoints` per cosa entra nel perimetro), con un golden test che fissa il comportamento.

**Aggiungere un dato o una vista nella dashboard.**
*Dove:* il calcolo va in una funzione **pura** in `report.py` (una funzione che prende dati e restituisce dati, senza generare HTML) — così è testabile da sola; il disegno va nel rendering (`render_*`) e la parte interattiva negli asset `dashboard.js`/`dashboard.css`. Tieni separati *cosa calcoli* da *come lo mostri*: rende ogni pezzo verificabile e non trasforma il rendering in una scatola nera.

**Supportare un altro formato di report di esecuzione** (oggi: Cucumber JSON).
*Dove:* un adapter in `runtime.py` che legge il nuovo formato e lo normalizza verso il modello interno già esistente (esito OK/KO/incerto + TC-ID). Il resto della pipeline non cambia.

### B. Integrare SCOPE nel processo (automazione)

**Eseguire SCOPE automaticamente ai merge, con la dashboard come artefatto.**
Oggi SCOPE si lancia a mano. L'evoluzione naturale è renderlo **uno step del processo**: una GitHub Action che, a ogni **merge su `develop` del repository analizzato**, esegue SCOPE e pubblica la dashboard HTML come **artefatto di build** (o su una pagina). Così la fotografia della copertura è sempre aggiornata e condivisibile — senza che nessuno debba ricordarsi di rigenerarla.

Schema dell'integrazione:

1. Il workflow vive nella CI del **repo analizzato** (è lì che nasce l'evento "merge su `develop`").
2. Il job fa il checkout di **due** repository: quello analizzato e SCOPE; installa SCOPE (`pip install -e .`).
3. Punta SCOPE al checkout con la variabile d'ambiente `SCOPE_TARGET_REPO=$GITHUB_WORKSPACE/<repo-analizzato>` ed esegue la pipeline (o `scripts/refresh-all.sh --no-open`).
4. Carica `reports/html/*.html` come artefatto del workflow (es. `actions/upload-artifact`), scaricabile dalla pagina della run; in alternativa lo si pubblica su GitHub Pages per un link stabile.
5. Opzionale ma consigliato: `scripts/ci-check.sh` con `--strict`, così un inventario o una misura degradati **fermano** la pubblicazione invece di diffondere numeri inquinati.

> Nota di principio: SCOPE non esegue mai operazioni git remote da sé. L'automazione vive nella CI (che ha già le sue credenziali e i suoi permessi); SCOPE resta un analizzatore in sola lettura che viene *invocato* dal workflow. Questo mantiene lo strumento semplice e sicuro da eseguire ovunque.
