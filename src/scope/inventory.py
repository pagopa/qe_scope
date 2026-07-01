#!/usr/bin/env python3
"""
E2E Coverage Report: OpenAPI endpoints vs test usage.

Parses the pom.xml to find all OpenAPI Generator executions,
downloads the specs, extracts endpoints (method + path + operationId),
then scans the Java source to find which generated API methods are
actually invoked. Produces a per-service coverage report.

Supports two suites:
  - send    (default): root pom.xml + src/
  - interop:           interop-qa-tests/pom.xml + interop-qa-tests/src/

Usage:
    python3 coverage-tool/coverage.py [--suite send|interop] [--latest-only] [--no-cache] [--output report.csv]

Requires: PyYAML, requests
    pip install pyyaml requests
"""

import argparse
import csv
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import (
    DATA_DIR,
    PROJECT_ROOT,
    REPO_ROOT,
    REPORTS_DIR,
    SUITE_CONFIG,
)
from .config import (
    MAVEN_NS as NS,
)
from .java_analysis import build_resolver

try:
    import requests
except ImportError:
    sys.exit("Missing dependency: pip install requests")

try:
    import yaml
except ImportError:
    sys.exit("Missing dependency: pip install pyyaml")


CACHE_DIR = PROJECT_ROOT / ".spec-cache"


# ── Phase 1: Parse pom.xml ─────────────────────────────────────────────

def parse_maven_properties(tree: ET.ElementTree) -> dict[str, str]:
    props = {}
    props_el = tree.find(".//m:properties", NS)
    if props_el is not None:
        for child in props_el:
            tag = child.tag.replace(f"{{{NS['m']}}}", "")
            props[tag] = child.text or ""
    return props


def resolve_vars(text: str, props: dict[str, str]) -> str:
    def replacer(m):
        key = m.group(1)
        return props.get(key, m.group(0))

    resolved = text
    for _ in range(5):
        new = re.sub(r"\$\{([^}]+)}", replacer, resolved)
        if new == resolved:
            break
        resolved = new
    return resolved


INTERNAL_SPEC_PATTERNS = re.compile(
    r"api-private|api-rework-private|api-internal-v1|api-internal-pn-service-desk"
    r"|gestore-repository|pn-internal-templates|pn-radd-alt-private|pn-radd-fsu-v1"
    r"|api-mock-received-message|gpd\.yaml"
)

INTERNAL_PATH_PATTERNS = re.compile(
    r"-private/|/private/|/internal/"
)

B2B_OVERRIDE_PATTERNS = re.compile(
    r"api-internal-b2b|api-internal-pn-delivery-push\.yaml"
)


def classify_visibility(input_spec_url: str, endpoint_path: str) -> str:
    spec_filename = input_spec_url.rsplit("/", 1)[-1].lower()
    if B2B_OVERRIDE_PATTERNS.search(spec_filename):
        return "public"
    if INTERNAL_SPEC_PATTERNS.search(spec_filename):
        return "internal"
    if INTERNAL_PATH_PATTERNS.search(endpoint_path):
        return "internal"
    return "public"


def extract_executions(tree: ET.ElementTree, props: dict[str, str]) -> list[dict]:
    executions = []
    for plugin in tree.findall(".//m:plugin", NS):
        artifact = plugin.find("m:artifactId", NS)
        if artifact is None or artifact.text != "openapi-generator-maven-plugin":
            continue
        for ex in plugin.findall(".//m:execution", NS):
            ex_id_el = ex.find("m:id", NS)
            ex_id = ex_id_el.text if ex_id_el is not None else "unknown"

            input_spec_el = ex.find(".//m:inputSpec", NS)
            if input_spec_el is None or not input_spec_el.text:
                continue
            input_spec = resolve_vars(input_spec_el.text.strip(), props)

            api_pkg = ""
            for ap in ex.findall(".//m:apiPackage", NS):
                if ap.text:
                    api_pkg = ap.text.strip()
                    break

            if not input_spec.startswith("http"):
                continue

            repo_match = re.search(r"githubusercontent\.com/([^/]+)/([^/]+)/", input_spec)
            service = repo_match.group(2) if repo_match else "unknown"

            spec_file = input_spec.rsplit("/", 1)[-1] if "/" in input_spec else input_spec
            spec_name = re.sub(r"\.(yaml|yml|json)$", "", spec_file)

            executions.append({
                "id": ex_id,
                "input_spec": input_spec,
                "api_package": api_pkg,
                "service": service,
                "spec_name": spec_name,
            })
    return executions


# ── Phase 2: Download and parse OpenAPI specs ───────────────────────────

def download_spec(url: str, use_cache: bool = True) -> Optional[str]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_key = hashlib.sha256(url.encode()).hexdigest()[:16]
    cache_file = CACHE_DIR / f"{cache_key}.yaml"

    if use_cache and cache_file.exists():
        return cache_file.read_text()

    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        content = resp.text
        cache_file.write_text(content)
        return content
    except Exception as e:
        print(f"  WARNING: failed to download {url}: {e}", file=sys.stderr)
        return None


def extract_endpoints(spec_text: str) -> list[dict]:
    try:
        spec = yaml.safe_load(spec_text)
    except yaml.YAMLError:
        return []

    if not isinstance(spec, dict) or "paths" not in spec:
        return []

    endpoints = []
    for path, methods in spec.get("paths", {}).items():
        if not isinstance(methods, dict):
            continue
        for method, details in methods.items():
            if method.lower() in ("get", "post", "put", "delete", "patch", "head", "options"):
                op_id = ""
                if isinstance(details, dict):
                    op_id = details.get("operationId", "")
                endpoints.append({
                    "method": method.upper(),
                    "path": path,
                    "operation_id": op_id,
                })
    return endpoints


# ── Phase 2b: Lock-file e sanity dell'inventario ───────────────────────
# L'inventario dipende da un download di rete (spec OpenAPI dai pom.xml). Un
# fallimento silenzioso (404, spec spostata, contenuto vuoto) rimpicciolisce
# l'inventario senza accorgersene → ogni percentuale ne esce falsata. Il
# lock-file fissa una baseline riproducibile (sha + n. endpoint per execution),
# il sanity check confronta ogni run con la baseline e segnala le derive.

LOCK_FILE = DATA_DIR / "spec-lock.json"

INVENTORY_THRESHOLDS = {
    "total_drop_pct": 10.0,    # calo % endpoint totali vs lock oltre cui FAIL
    "spec_drift_pct": 15.0,    # variazione % endpoint di una singola spec → WARN
}


def spec_fingerprint(executions: list[dict]) -> dict:
    """Impronta dell'inventario: per ogni execution, url/servizio/spec, numero di
    endpoint e sha256 del testo spec. `downloaded=False` se il download è fallito."""
    fp = {}
    for ex in executions:
        text = ex.get("_spec_text")
        fp[ex["id"]] = {
            "url": ex["input_spec"],
            "service": ex["service"],
            "spec_name": ex.get("spec_name", ""),
            "endpoint_count": len(ex.get("endpoints", [])),
            "sha256": hashlib.sha256(text.encode()).hexdigest()[:16] if text else None,
            "downloaded": text is not None,
        }
    return fp


def load_lock(suite: str, path: Path = LOCK_FILE) -> dict:
    """Baseline lockata per la suite (vuoto se assente)."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text()).get(suite, {})
    except (json.JSONDecodeError, OSError):
        return {}


def save_lock(suite: str, fingerprint: dict, path: Path = LOCK_FILE):
    """Aggiorna la baseline della suite, preservando le altre suite nel file."""
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            data = {}
    data[suite] = fingerprint
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def check_inventory(suite: str, current: dict, lock: dict,
                    thresholds: dict = INVENTORY_THRESHOLDS):
    """Confronta l'impronta corrente con la baseline lockata.
    Ritorna (issues, summary). issues: lista (severity, msg) in {FAIL,WARN,INFO}."""
    t = thresholds
    issues = []

    # Download falliti (a prescindere dal lock): inventario amputato
    for ex_id, fp in sorted(current.items()):
        if not fp["downloaded"]:
            issues.append(("FAIL",
                f"{ex_id}: download spec FALLITO ({fp['url']}) — inventario incompleto"))

    if not lock:
        issues.append(("INFO",
            "nessuna baseline lockata per questa suite — esegui --update-lock "
            "per fissarla (riproducibilità + rilevamento derive)"))
    else:
        # Per-spec: sparizioni, collassi a zero, drift
        for ex_id, lf in sorted(lock.items()):
            cf = current.get(ex_id)
            if cf is None:
                issues.append(("FAIL",
                    f"{ex_id}: presente nel lock ma SPARITA dal run corrente "
                    f"(execution rimossa dal pom o spec irraggiungibile)"))
                continue
            lock_n, cur_n = lf["endpoint_count"], cf["endpoint_count"]
            if lock_n > 0 and cur_n == 0:
                issues.append(("FAIL",
                    f"{ex_id}: collassata a 0 endpoint (lock={lock_n}) — "
                    f"spec vuota o malformata"))
            elif lock_n > 0:
                drift = abs(cur_n - lock_n) / lock_n * 100
                if drift > t["spec_drift_pct"]:
                    issues.append(("WARN",
                        f"{ex_id}: endpoint {cur_n} vs {lock_n} del lock "
                        f"({(cur_n-lock_n)/lock_n*100:+.0f}%) — verificare il salto"))
                elif cf["sha256"] != lf.get("sha256"):
                    issues.append(("INFO",
                        f"{ex_id}: contenuto spec cambiato (endpoint invariati: {cur_n})"))
        # Nuove execution non ancora lockate
        for ex_id in sorted(set(current) - set(lock)):
            issues.append(("INFO",
                f"{ex_id}: nuova execution non nel lock (aggiorna con --update-lock)"))
        # Aggregato: calo totale endpoint
        lock_total = sum(f["endpoint_count"] for f in lock.values())
        cur_total = sum(f["endpoint_count"] for f in current.values())
        if lock_total > 0:
            drop = (lock_total - cur_total) / lock_total * 100
            if drop > t["total_drop_pct"]:
                issues.append(("FAIL",
                    f"Endpoint totali {cur_total} vs {lock_total} del lock "
                    f"(-{drop:.0f}%) — l'inventario si è ristretto: NON pubblicare"))

    summary = {
        "passed": not any(sev == "FAIL" for sev, _ in issues),
        "issues": [{"severity": s, "message": m} for s, m in issues],
        "executions": len(current),
        "endpoints_total": sum(f["endpoint_count"] for f in current.values()),
        "download_failures": sum(1 for f in current.values() if not f["downloaded"]),
        "locked": bool(lock),
    }
    return issues, summary


def print_inventory_sanity(issues):
    if not issues:
        print("\n  ✅ INVENTARIO: nessuna deriva rispetto alla baseline lockata")
        return
    icon = {"FAIL": "❌", "WARN": "⚠️ ", "INFO": "ℹ️ "}
    print(f"\n  {'─'*50}")
    print("  SANITY INVENTARIO")
    for sev, msg in issues:
        print(f"  {icon[sev]} [{sev}] {msg}")
    if any(sev == "FAIL" for sev, _ in issues):
        print("  → L'inventario è degradato: i numeri di copertura sono inaffidabili.")


# ── Phase 3: Scan Java source for API method invocations ───────────────
# La copertura statica usa lo STESSO resolver di tag-coverage.py
# (java_analysis.OpResolver): un solo parser Java per entrambi gli script.
# 'covered' = operationId invocato direttamente nel sorgente
# (.opId( / .opIdWithHttpInfo( / ::opId), via resolver.directly_invoked().
# Questo garantisce l'invariante reachable ⊆ static_covered (vedi P2).


def compute_static_covered(src_dirs: list[Path], all_op_ids: set[str]) -> set[str]:
    """Operationid invocati direttamente da qualche parte nel sorgente."""
    resolver = build_resolver(src_dirs, all_op_ids)
    return resolver.directly_invoked()


# ── Phase 4: Match and produce report ──────────────────────────────────

def classify_version(exec_id: str) -> Optional[str]:
    """Extract version suffix like _v1, _v2, _v21, _v23, _v24, _v25, _v26 or None for latest."""
    m = re.search(r"[_-]v(\d+)$", exec_id)
    if m:
        return f"v{m.group(1)}"
    return None


def is_latest_execution(exec_id: str) -> bool:
    return classify_version(exec_id) is None


def build_report(
    executions: list[dict],
    covered_ops: set[str],
    latest_only: bool = False,
) -> list[dict]:
    rows = []
    for ex in executions:
        if latest_only and not is_latest_execution(ex["id"]):
            continue

        version = classify_version(ex["id"]) or "latest"

        for ep in ex.get("endpoints", []):
            op_id = ep["operation_id"]
            if not op_id:
                covered = False
                match_source = "no-operationId"
            else:
                covered = op_id in covered_ops
                match_source = "invoked" if covered else ""

            visibility = classify_visibility(ex["input_spec"], ep["path"])

            rows.append({
                "service": ex["service"],
                "spec_name": ex.get("spec_name", ""),
                "execution_id": ex["id"],
                "version": version,
                "method": ep["method"],
                "path": ep["path"],
                "operation_id": op_id,
                "visibility": visibility,
                "covered": covered,
                "match_source": match_source,
            })
    return rows


def print_summary(rows: list[dict], suite_label: str, group_by: str = "service"):
    by_group = defaultdict(lambda: {
        "public": {"total": 0, "covered": 0},
        "internal": {"total": 0, "covered": 0},
        "endpoints": [],
    })

    for r in rows:
        if group_by == "spec":
            key = f"{r['service']} / {r.get('spec_name', '')}"
        else:
            key = r["service"]
        vis = r["visibility"]
        by_group[key][vis]["total"] += 1
        if r["covered"]:
            by_group[key][vis]["covered"] += 1
        by_group[key]["endpoints"].append(r)

    pub_total = sum(v["public"]["total"] for v in by_group.values())
    pub_covered = sum(v["public"]["covered"] for v in by_group.values())
    int_total = sum(v["internal"]["total"] for v in by_group.values())
    int_covered = sum(v["internal"]["covered"] for v in by_group.values())
    all_total = pub_total + int_total
    all_covered = pub_covered + int_covered

    print("\n" + "=" * 80)
    print(f"  E2E COVERAGE REPORT — {suite_label} test suite")
    if all_total > 0:
        print(f"  Public:   {pub_covered}/{pub_total} ({pub_covered/pub_total*100:.1f}%)" if pub_total else "")
        print(f"  Internal: {int_covered}/{int_total} ({int_covered/int_total*100:.1f}%)" if int_total else "")
        print(f"  Overall:  {all_covered}/{all_total} ({all_covered/all_total*100:.1f}%)")
    else:
        print("  No endpoints found")
    print("=" * 80)

    for service in sorted(by_group.keys()):
        data = by_group[service]
        pub = data["public"]
        intl = data["internal"]
        total = pub["total"] + intl["total"]
        covered = pub["covered"] + intl["covered"]
        pct = (covered / total * 100) if total > 0 else 0
        bar_len = 30
        filled = int(bar_len * pct / 100)
        bar = "█" * filled + "░" * (bar_len - filled)

        vis_detail = []
        if pub["total"] > 0:
            pub_pct = pub["covered"] / pub["total"] * 100
            vis_detail.append(f"public {pub['covered']}/{pub['total']} ({pub_pct:.0f}%)")
        if intl["total"] > 0:
            int_pct = intl["covered"] / intl["total"] * 100
            vis_detail.append(f"internal {intl['covered']}/{intl['total']} ({int_pct:.0f}%)")

        print(f"\n  {service}")
        print(f"    {bar} {covered}/{total} ({pct:.0f}%)  [{', '.join(vis_detail)}]")

        uncovered = [e for e in data["endpoints"] if not e["covered"]]
        if uncovered:
            print("    Uncovered:")
            for e in uncovered[:10]:
                vis_tag = "PUB" if e["visibility"] == "public" else "INT"
                print(f"      [{vis_tag}] {e['method']:7s} {e['path']}")
                if e["operation_id"]:
                    print(f"                operationId: {e['operation_id']}")
            if len(uncovered) > 10:
                print(f"      ... and {len(uncovered) - 10} more")

    print("\n" + "=" * 80)


def write_csv(rows: list[dict], output_path: str):
    fieldnames = ["service", "spec_name", "execution_id", "version", "method", "path",
                  "operation_id", "visibility", "covered", "match_source"]
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nCSV report written to {output_path}")


# ── Main ────────────────────────────────────────────────────────────────

LEGEND_TEXT = """\
LEGGIMI — Report di copertura E2E (coverage.py, analisi STATICA)
================================================================

COSA MISURA QUESTO REPORT (statico grezzo)
------------------------------------------
Risponde alla domanda: "Esiste DA QUALCHE PARTE nel codice Java una
chiamata al metodo generato da questo endpoint?".
La colonna `covered=True` significa che il metodo viene invocato nel
sorgente, NON che un test eseguibile lo eserciti davvero.

ATTENZIONE: questo numero SOVRASTIMA la copertura effettiva.
Include la "copertura fantasma": wrapper/client implementati nel codice
ma che nessuno scenario Cucumber esercita (test non scritto, codice di
setup interno, scenari sotto @ignore).

Per la copertura REALE (endpoint raggiungibili da uno scenario
eseguibile) usare lo script `tag-coverage.py` sulla stessa suite.

Analogia:
  - Statico grezzo (questo report) = "quante stanze hanno una porta?"
  - Copertura reale (tag-coverage)  = "in quante stanze entra davvero
    qualcuno partendo dall'ingresso?"

LIMITE DI ENTRAMBI GLI APPROCCI
-------------------------------
Misurano AMPIEZZA di superficie (endpoint toccato si/no), non
PROFONDITA' funzionale (quanti casi, edge case, qualita' delle
asserzioni per endpoint). Un endpoint con 1 test banale e uno con 50
test risultano entrambi "coperti".

COLONNE DEL CSV/JSON
--------------------
service        Repository GitHub di origine della spec
spec_name      Nome del file OpenAPI senza estensione
execution_id   ID dell'execution nel pom.xml
version        latest oppure v1/v2/... (dal suffisso execution_id)
method         Metodo HTTP (GET/POST/PUT/DELETE/PATCH)
path           Path dell'endpoint
operation_id   operationId della spec = nome del metodo Java generato
visibility     public = API esposta a PA/consumatori
               internal = API inter-microservizio
covered        True se il metodo generato e' invocato nel sorgente
               (resolver condiviso con tag-coverage: segue .opId(,
               .opIdWithHttpInfo( e i method reference ::opId)
match_source   invoked    = operationId invocato direttamente nel codice
               (vuoto)    = non trovato
               no-operationId = endpoint senza operationId nella spec

Dettagli completi: coverage-tool/README.md
"""


def write_legend(run_dir: Path):
    """Write a self-contained legend file next to the data, so the
    report folder is autonomous without consulting the README."""
    (run_dir / "LEGGIMI.txt").write_text(LEGEND_TEXT)


def make_run_dir(suite: str) -> Path:
    """Create a timestamped run directory under reports/."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = REPORTS_DIR / f"{ts}_{suite}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def main():
    parser = argparse.ArgumentParser(description="E2E Coverage Report from OpenAPI specs")
    parser.add_argument("--suite", choices=["send", "interop"], default="send",
                        help="Which test suite to analyze (default: send)")
    parser.add_argument("--latest-only", action="store_true",
                        help="Only report on the latest version of each API (skip _v1, _v2, etc.)")
    parser.add_argument("--no-cache", action="store_true",
                        help="Re-download all specs (ignore cache)")
    parser.add_argument("--output", "-o", default=None,
                        help="Output CSV file path (default: auto in reports/<timestamp>/)")
    parser.add_argument("--json", default=None,
                        help="Output JSON file path (default: auto in reports/<timestamp>/)")
    parser.add_argument("--group-by", choices=["service", "spec"], default=None,
                        help="Group summary by 'service' or 'spec' (default: service for send, spec for interop)")
    parser.add_argument("--no-save", action="store_true",
                        help="Do not auto-save reports to reports/ folder")
    parser.add_argument("--update-lock", action="store_true",
                        help="Aggiorna spec-lock.json con l'inventario corrente (baseline)")
    parser.add_argument("--strict", action="store_true",
                        help="Exit code 1 se l'inventario è degradato (per CI)")
    args = parser.parse_args()

    cfg = SUITE_CONFIG[args.suite]
    pom_path = cfg["pom"]
    src_dirs = cfg["all_src_dirs"]
    suite_label = cfg["label"]

    if not pom_path.exists():
        sys.exit(f"Target repo non trovato: {REPO_ROOT}\n"
                 f"Configura il path in config.yaml (target_repo) o con SCOPE_TARGET_REPO.")

    print(f"Suite: {suite_label}")
    print(f"POM:   {pom_path.relative_to(REPO_ROOT)}")
    print(f"Src:   {', '.join(str(d.relative_to(REPO_ROOT)) for d in src_dirs)}")

    print("\nPhase 1: Parsing pom.xml...")
    tree = ET.parse(pom_path)
    props = parse_maven_properties(tree)
    executions = extract_executions(tree, props)
    print(f"  Found {len(executions)} OpenAPI Generator executions")

    print("\nPhase 2: Downloading and parsing OpenAPI specs...")
    use_cache = not args.no_cache
    failed = 0
    for ex in executions:
        spec_text = download_spec(ex["input_spec"], use_cache=use_cache)
        ex["_spec_text"] = spec_text   # per l'impronta (sha); None se download fallito
        if spec_text:
            ex["endpoints"] = extract_endpoints(spec_text)
            print(f"  {ex['id']}: {len(ex['endpoints'])} endpoints")
        else:
            ex["endpoints"] = []
            failed += 1
            print(f"  {ex['id']}: FAILED to download")

    total_endpoints = sum(len(ex["endpoints"]) for ex in executions)
    print(f"  Total: {total_endpoints} endpoints from {len(executions)} specs ({failed} failed)")

    # Sanity dell'inventario: confronto con la baseline lockata (riproducibilità)
    fingerprint = spec_fingerprint(executions)
    lock = load_lock(args.suite)
    inv_issues, inv_sanity = check_inventory(args.suite, fingerprint, lock)
    print_inventory_sanity(inv_issues)
    if args.update_lock:
        save_lock(args.suite, fingerprint)
        print(f"  🔒 Baseline aggiornata in {LOCK_FILE.name} (suite {args.suite})")
    if args.strict and not inv_sanity["passed"]:
        sys.exit("\n  --strict: inventario degradato (FAIL). Esco con codice 1.")

    print("\nPhase 3: Scanning Java sources (resolver condiviso con tag-coverage)...")
    all_op_ids = {ep["operation_id"] for ex in executions
                  for ep in ex.get("endpoints", []) if ep.get("operation_id")}
    covered_ops = compute_static_covered(src_dirs, all_op_ids)
    print(f"  {len(covered_ops)}/{len(all_op_ids)} operationId invocati direttamente nel sorgente")

    print("\nPhase 4: Building coverage report...")
    rows = build_report(executions, covered_ops, latest_only=args.latest_only)

    group_by = args.group_by or ("spec" if args.suite == "interop" else "service")
    print_summary(rows, suite_label, group_by=group_by)

    # --- Output ---
    # Auto-save to reports/<timestamp>/ unless --no-save
    if not args.no_save:
        run_dir = make_run_dir(args.suite)
        csv_path = args.output or str(run_dir / f"coverage-{args.suite}.csv")
        json_path = args.json or str(run_dir / f"coverage-{args.suite}.json")
        write_csv(rows, csv_path)
        with open(json_path, "w") as f:
            json.dump(rows, f, indent=2, default=str)
        print(f"JSON report written to {json_path}")
        write_legend(run_dir)
        print(f"\n  📁 Report salvati in: {run_dir}")
    else:
        if args.output:
            write_csv(rows, args.output)
        if args.json:
            with open(args.json, "w") as f:
                json.dump(rows, f, indent=2, default=str)
            print(f"JSON report written to {args.json}")


if __name__ == "__main__":
    main()
