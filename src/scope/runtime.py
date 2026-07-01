#!/usr/bin/env python3
"""
Ingest dei report di esecuzione — adapter + ledger + stato corrente.

Idea (concordata in discovery):
- L'utente droppa i report Cucumber JSON in runtime/inbox/<suite>/.
- Questo script li scandisce, ne ricorda il contenuto (ledger per-sha: ributtare
  lo stesso file = no-op) e accumula la STORIA normalizzata degli esiti.
- "Eseguita" NON è proprietà di un singolo file: lo STATO CORRENTE è l'overlay
  "ultimo vince per TC-ID" sulle run degli ultimi N giorni (default 30, da oggi).
  Finestra generosa perché misuriamo la salute del parco-test, non un gate di
  rilascio. Il flaky (pass-rate nella finestra) è un segnale SEPARATO dallo stato,
  non punitivo: un KO rientrato non dipinge l'endpoint di rosso oggi.

Il join con gli endpoint avviene a valle (report.py), via TC-ID → scenario →
operationId, riusando scenarios_index/endpoint_scenarios del tag-coverage.

Schema normalizzato (un solo modello, anche quando aggiungeremo altri formati):
  run     = {run_id, suite, file, run_ts, ingested_at, n_ok, n_ko, n_other,
             results: [{tc_id, name, status(OK|KO|OTHER), error?}]}
  ledger  = {sha256: {file, suite, run_ts, ingested_at, n_*}}

Output (in DATA_DIR, gitignored — dati di esecuzione):
  runtime-results.json  → storia di tutte le run ingerite
  runtime-ledger.json   → cosa è già stato elaborato (per-sha)
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone

from .config import (
    DATA_DIR,
    REPORTS_DIR,
    RUNTIME_INBOX_DIR,
    RUNTIME_WINDOW_DAYS,
    SUITE_CONFIG,
)
from .tag_coverage import extract_tc_id

RESULTS_FILE = DATA_DIR / "runtime-results.json"
LEDGER_FILE = DATA_DIR / "runtime-ledger.json"


# ---------------------------------------------------------------------------
# Parsing del report Cucumber JSON → esiti normalizzati
# ---------------------------------------------------------------------------

def aggregate_status(element):
    """Esito aggregato di uno scenario dai suoi step (+ hook before/after):
    KO se un qualsiasi step/hook è 'failed'; OK se tutti 'passed'; altrimenti
    OTHER (skipped/undefined/pending = dato incerto, non un fallimento)."""
    statuses = []
    for part in ("before", "steps", "after"):
        for item in element.get(part, []) or []:
            st = (item.get("result") or {}).get("status")
            if st:
                statuses.append(st)
    if any(s == "failed" for s in statuses):
        return "KO"
    if statuses and all(s == "passed" for s in statuses):
        return "OK"
    return "OTHER"


def first_error(element):
    """Messaggio del primo step fallito (per il drill-down dei KO)."""
    for part in ("before", "steps", "after"):
        for item in element.get(part, []) or []:
            res = item.get("result") or {}
            if res.get("status") == "failed" and res.get("error_message"):
                return res["error_message"].strip().splitlines()[0][:500]
    return ""


def _parse_ts(raw):
    """ISO timestamp Cucumber → datetime aware (UTC). None se non parsabile."""
    if not raw:
        return None
    s = str(raw).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_cucumber(data, fallback_ts):
    """Lista di feature Cucumber → (results, run_ts).

    results: un record per SCENARIO (i 'background' sono scartati).
    run_ts: il più vecchio start_timestamp degli scenari, o fallback_ts (mtime)
    se il report non porta timestamp (questo formato non ha metadata di run).
    """
    results = []
    ts_seen = []
    for feat in data:
        for el in feat.get("elements", []) or []:
            if el.get("type") != "scenario":
                continue
            name = el.get("name", "")
            status = aggregate_status(el)
            rec = {
                "tc_id": extract_tc_id(name),
                "name": name,
                "status": status,
            }
            if status == "KO":
                err = first_error(el)
                if err:
                    rec["error"] = err
            results.append(rec)
            t = _parse_ts(el.get("start_timestamp"))
            if t:
                ts_seen.append(t)
    run_ts = min(ts_seen) if ts_seen else fallback_ts
    return results, run_ts


def _counts(results):
    n_ok = sum(1 for r in results if r["status"] == "OK")
    n_ko = sum(1 for r in results if r["status"] == "KO")
    return n_ok, n_ko, len(results) - n_ok - n_ko


# ---------------------------------------------------------------------------
# Stato corrente: overlay "ultimo vince per TC-ID" nella finestra temporale
# ---------------------------------------------------------------------------

def compute_current_state(runs, now=None, window_days=RUNTIME_WINDOW_DAYS):
    """Dalla storia delle run → stato corrente per TC-ID.

    Considera solo le run con run_ts negli ultimi `window_days` da `now`.
    Per ogni TC-ID l'esito headline è quello della run più RECENTE che lo
    contiene (foto del presente). In parallelo conta ok/ko nella finestra per
    derivare il flaky (segnale di affidabilità, separato dallo stato).
    Ritorna (state, meta).
    """
    now = now or datetime.now(timezone.utc)
    horizon = now - timedelta(days=window_days)

    def ts_of(r):
        return _parse_ts(r.get("run_ts")) or horizon

    in_window = [r for r in runs if ts_of(r) >= horizon]
    state = {}
    for r in sorted(in_window, key=ts_of):           # vecchie → recenti: l'ultima vince
        rts = ts_of(r)
        for res in r.get("results", []):
            tc = res.get("tc_id")
            if not tc:
                continue
            e = state.setdefault(tc, {"ok": 0, "ko": 0, "other": 0, "runs": 0})
            e["runs"] += 1
            key = {"OK": "ok", "KO": "ko"}.get(res["status"], "other")
            e[key] += 1
            e["last_status"] = res["status"]
            e["last_ts"] = rts.isoformat()
            e["last_error"] = res.get("error", "")
            e["last_run_id"] = r.get("run_id", "")
            e["last_file"] = r.get("file", "")
    for tc, e in state.items():
        e["flaky"] = e["ok"] > 0 and e["ko"] > 0
        last = _parse_ts(e["last_ts"]) or now
        e["age_days"] = max(0, (now - last).days)
    meta = {
        "window_days": window_days,
        "computed_at": now.isoformat(),
        "runs_total": len(runs),
        "runs_in_window": len(in_window),
        "scenarios_with_state": len(state),
        "empty": len(in_window) == 0,
    }
    return state, meta


# ---------------------------------------------------------------------------
# Persistenza: ledger (cosa già elaborato) + storia
# ---------------------------------------------------------------------------

def _load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            print(f"  ATTENZIONE: {path.name} illeggibile, riparto vuoto", file=sys.stderr)
    return default


def load_ledger():
    return _load_json(LEDGER_FILE, {"version": 1, "entries": {}})


def load_results():
    return _load_json(RESULTS_FILE, {"version": 1, "runs": []})


def _iter_inbox(inbox_dir):
    """(suite, path) per ogni *.json nelle sottocartelle per-suite dell'inbox."""
    for suite in SUITE_CONFIG:
        d = inbox_dir / suite
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            yield suite, f


def ingest(inbox_dir=RUNTIME_INBOX_DIR, ledger=None, results=None):
    """Scandisce l'inbox, ingerisce i file NUOVI (per-sha), aggiorna ledger e
    storia in memoria. Ritorna (n_new, n_skipped, new_runs)."""
    ledger = ledger if ledger is not None else load_ledger()
    results = results if results is not None else load_results()
    entries = ledger.setdefault("entries", {})
    n_new = n_skip = 0
    new_runs = []
    for suite, path in _iter_inbox(inbox_dir):
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        if sha in entries:
            n_skip += 1
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"  ATTENZIONE: {path.name} non è JSON valido ({e}), saltato", file=sys.stderr)
            continue
        fallback_ts = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        res, run_ts = parse_cucumber(data, fallback_ts)
        n_ok, n_ko, n_other = _counts(res)
        run = {
            "run_id": sha[:12],
            "suite": suite,
            "file": path.name,
            "run_ts": run_ts.isoformat(),
            "ingested_at": datetime.now(timezone.utc).isoformat(),
            "n_ok": n_ok, "n_ko": n_ko, "n_other": n_other,
            "results": res,
        }
        results["runs"].append(run)
        entries[sha] = {k: run[k] for k in
                        ("file", "suite", "run_ts", "ingested_at", "n_ok", "n_ko", "n_other")}
        new_runs.append(run)
        n_new += 1
        print(f"  + {suite}/{path.name}: {len(res)} scenari ({n_ok}✓ {n_ko}✗ {n_other}○) "
              f"run del {run_ts.date()}")
    return n_new, n_skip, new_runs


def save(ledger, results):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LEDGER_FILE.write_text(json.dumps(ledger, indent=2, ensure_ascii=False))
    RESULTS_FILE.write_text(json.dumps(results, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Sanity check sugli esiti di esecuzione (--strict per CI): la salute del join coi report di run
# ---------------------------------------------------------------------------

# Soglie. unmatched = TC-ID del report assenti dall'inventario (scenari
# rinominati/rimossi, o file droppato nella suite sbagliata): è il segnale più
# grave. Sul dato reale SEND era 0,1%.
SANITY_UNMATCHED_FAIL = 15.0
SANITY_UNMATCHED_WARN = 5.0
SANITY_NOID_WARN = 40.0
SANITY_OTHER_WARN = 20.0


def _latest_index_tc_ids(suite):
    """TC-ID noti dall'ultimo tag-coverage della suite (per il check unmatched).
    None se nessun report disponibile (check saltato, non fallito)."""
    cands = sorted(REPORTS_DIR.glob(f"*_{suite}_tags/tag-coverage-{suite}.json"), reverse=True)
    for f in cands:
        try:
            j = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        return {s.get("tc_id") for s in j.get("scenarios_index", []) if s.get("tc_id")}
    return None


def run_sanity(runs, suite, now=None, window_days=RUNTIME_WINDOW_DAYS, known_tc_ids=None):
    """Salute del livello esecuzione per una suite. Ritorna (issues, sanity).
    issues = lista di (severity, msg); sanity = dict con metriche + passed."""
    now = now or datetime.now(timezone.utc)
    horizon = now - timedelta(days=window_days)

    def ts_of(r):
        return _parse_ts(r.get("run_ts")) or horizon

    in_window = [r for r in runs if ts_of(r) >= horizon]
    issues = []

    # 1. Stantio: storia ma niente in finestra.
    if runs and not in_window:
        issues.append(("WARN", f"[{suite}] nessuna run negli ultimi {window_days}gg "
                               f"(storia: {len(runs)}) — stato esecuzione non disponibile"))

    results = [res for r in in_window for res in r.get("results", [])]
    total = len(results)
    with_id = [res for res in results if res.get("tc_id")]
    noid = total - len(with_id)
    other = sum(1 for res in results if res.get("status") == "OTHER")
    report_tc = {res["tc_id"] for res in with_id}

    # 2. Duplicati nel singolo report = join ambiguo (prendiamo il primo).
    dup_total = 0
    for r in in_window:
        seen, dups = set(), set()
        for res in r.get("results", []):
            tc = res.get("tc_id")
            if not tc:
                continue
            (dups if tc in seen else seen).add(tc)
        dup_total += len(dups)

    # 3. Unmatched vs inventario (se disponibile).
    unmatched = unmatched_pct = None
    if known_tc_ids is not None and report_tc:
        miss = report_tc - known_tc_ids
        unmatched = len(miss)
        unmatched_pct = round(unmatched / len(report_tc) * 100, 1)

    noid_pct = round(noid / total * 100, 1) if total else 0.0
    other_pct = round(other / total * 100, 1) if total else 0.0

    if unmatched_pct is not None:
        if unmatched_pct > SANITY_UNMATCHED_FAIL:
            issues.append(("FAIL", f"[{suite}] {unmatched_pct}% dei TC-ID nei report non esiste "
                                   f"nell'inventario ({unmatched}) — report stantio o suite errata?"))
        elif unmatched_pct > SANITY_UNMATCHED_WARN:
            issues.append(("WARN", f"[{suite}] {unmatched_pct}% dei TC-ID nei report non agganciati ({unmatched})"))
    if noid_pct > SANITY_NOID_WARN:
        issues.append(("WARN", f"[{suite}] {noid_pct}% degli esiti senza TC-ID: non joinabili"))
    if dup_total:
        issues.append(("WARN", f"[{suite}] {dup_total} TC-ID duplicati nei report: join ambiguo (vince il primo)"))
    if other_pct > SANITY_OTHER_WARN:
        issues.append(("WARN", f"[{suite}] {other_pct}% esiti incerti (skipped/undefined)"))

    sanity = {
        "suite": suite,
        "runs_in_window": len(in_window),
        "results": total,
        "noid_pct": noid_pct,
        "other_pct": other_pct,
        "duplicate_tc": dup_total,
        "unmatched": unmatched,
        "unmatched_pct": unmatched_pct,
        "passed": not any(sev == "FAIL" for sev, _ in issues),
    }
    return issues, sanity


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Ingest dei report di esecuzione")
    ap.add_argument("--window", type=int, default=RUNTIME_WINDOW_DAYS,
                    help=f"giorni della finestra per lo stato corrente (default {RUNTIME_WINDOW_DAYS})")
    ap.add_argument("--dry-run", action="store_true",
                    help="elabora ma non scrive ledger/storia")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 se un un sanity check sugli esiti di esecuzione fallisce (per CI)")
    args = ap.parse_args()

    print(f"Ingest da {RUNTIME_INBOX_DIR}…")
    ledger = load_ledger()
    results = load_results()
    n_new, n_skip, _ = ingest(ledger=ledger, results=results)
    print(f"  → {n_new} nuovi, {n_skip} già visti (skip)")

    if not args.dry_run and n_new:
        save(ledger, results)
        print(f"  → salvati {RESULTS_FILE.name} e {LEDGER_FILE.name}")
    elif args.dry_run:
        print("  → dry-run: niente scritto")

    any_fail = False
    for suite in SUITE_CONFIG:
        runs = [r for r in results["runs"] if r["suite"] == suite]
        if not runs:
            continue
        state, meta = compute_current_state(runs, window_days=args.window)
        if meta["empty"]:
            print(f"  {suite}: nessuna run negli ultimi {args.window}gg "
                  f"(storia: {meta['runs_total']} run) → stato esecuzione non disponibile")
        else:
            ok = sum(1 for e in state.values() if e["last_status"] == "OK")
            ko = sum(1 for e in state.values() if e["last_status"] == "KO")
            flaky = sum(1 for e in state.values() if e["flaky"])
            print(f"  {suite}: {meta['runs_in_window']}/{meta['runs_total']} run in finestra · "
                  f"{len(state)} scenari con stato ({ok}✓ {ko}✗) · {flaky} instabili")

        # Sanity sugli esiti di esecuzione
        issues, sanity = run_sanity(runs, suite, window_days=args.window,
                                    known_tc_ids=_latest_index_tc_ids(suite))
        for sev, msg in issues:
            icon = {"FAIL": "❌", "WARN": "⚠️ ", "INFO": "ℹ️ "}.get(sev, "")
            print(f"    {icon} {msg}")
        if not sanity["passed"]:
            any_fail = True

    if args.strict and any_fail:
        print("  ❌ sanity esiti di esecuzione FALLITO (--strict)")
        sys.exit(1)


if __name__ == "__main__":
    main()
