#!/usr/bin/env python3
"""
Tag-based E2E Coverage Analysis.

Traces: Cucumber runner tags → scenarios → step definitions → API calls → OpenAPI endpoints.
Uses multi-hop resolution to follow the wrapper chain: step def → service interface → service impl → generated API.

Usage:
    python3 coverage-tool/tag-coverage.py --suite interop [--optimize] [--runner NrtMinimalTest]

Requires: PyYAML, requests (same as coverage.py)
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime

from .config import PROJECT_ROOT, REPO_ROOT, REPORTS_DIR, SUITE_CONFIG
from .java_analysis import (
    build_resolver as build_operation_id_resolver,
)

# TC-ID: identificatore di business tra parentesi quadre nel titolo dello scenario,
# es. "[TC-PA_LEGALFACT_1] Invio notifica...". È la chiave di join con i report di
# esecuzione: stabile rispetto all'editing del .feature (a differenza di line).
TC_ID_RE = re.compile(r"\[([A-Z0-9][A-Z0-9_\-]*)\]")


def extract_tc_id(name):
    """Estrae il TC-ID dal nome scenario (primo token tra []), o '' se assente."""
    m = TC_ID_RE.search(name or "")
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# 2. Parse step definition files
# ---------------------------------------------------------------------------

def parse_step_definitions(step_dirs, resolver, known_ops):
    """Parse step def files. For each @Given/@When/@Then method, resolve API calls.

    La risoluzione del corpo è già nel grafo del resolver (gli step def file sono
    scansionati anche da build_operation_id_resolver): qui si fa il lookup del
    nodo (classe del file, nome metodo), che usa il perimetro scoped del file.
    """
    step_defs = []

    for d in step_dirs:
        if not d.exists():
            continue
        for f in d.rglob("*.java"):
            text = f.read_text(errors="replace")
            if not re.search(r'@(Given|When|Then|And|But)\s*\(', text):
                continue

            cls_m = re.search(r'\b(?:class|interface|enum|record)\s+(\w+)', text)
            cls = cls_m.group(1) if cls_m else f.stem

            # File-level ops: direct nel testo + unione delle ops dei metodi
            # della classe (fallback quando lo step method ha api_calls vuoto)
            file_helper_ops = _resolve_file_level_ops(text, cls, resolver, known_ops)

            # Blocco di annotazioni step IMPILATE (@And + @And sullo stesso metodo:
            # ogni alias di pattern conta) seguite dalla firma del metodo.
            annotation_block_re = re.compile(
                r'((?:@(?:Given|When|Then|And|But)\s*\(\s*"(?:[^"\\]|\\.)*"\s*\)\s*)+)'
                r'(?:public\s+)?(?:void|[\w<>,\[\]\s]+)\s+(\w+)\s*\([^)]*\)\s*'
                r'(?:throws\s+[\w.,\s]+?)?\s*\{',
                re.DOTALL
            )
            single_annotation_re = re.compile(
                r'@(?:Given|When|Then|And|But)\s*\(\s*"((?:[^"\\]|\\.)*)"\s*\)'
            )

            for m in annotation_block_re.finditer(text):
                method_name = m.group(2)

                # Lookup nel grafo scoped; vista per nome come rete di sicurezza
                method_ops = resolver.ops_for(cls, method_name)
                if not method_ops:
                    method_ops = resolver.get(method_name, set())

                # Una step def per OGNI annotazione del blocco (alias di pattern)
                for am in single_annotation_re.finditer(m.group(1)):
                    pattern_str = am.group(1).replace('\\"', '"')
                    step_defs.append({
                        "file": str(f),
                        "method_name": method_name,
                        "pattern": pattern_str,
                        "api_calls": method_ops,
                        "file_ops": file_helper_ops,
                        "_regex": _cucumber_to_regex(pattern_str),
                    })

    return step_defs


def _resolve_file_level_ops(text, cls, resolver, known_ops):
    """OperationIds raggiungibili da qualsiasi metodo della classe del file.

    Usato come fallback quando il singolo step method ha api_calls vuoto.
    Le chiamate fatte dai metodi della classe sono già risolte (scoped) nel
    grafo, quindi l'unione delle ops dei nodi della classe copre il file.
    """
    ops = set()
    for op in known_ops:
        if "." + op + "(" in text or "." + op + "WithHttpInfo(" in text or "::" + op in text:
            ops.add(op)
    for (c, _m), node_ops in resolver.ops.items():
        if c == cls:
            ops |= node_ops
    return ops


def _cucumber_to_regex(expr):
    """Convert Cucumber expression to Python regex."""
    pattern = re.escape(expr)
    pattern = pattern.replace(r'\{string\}', '"([^"]*)"')
    pattern = pattern.replace(r'\{int\}', r'(-?\d+)')
    pattern = pattern.replace(r'\{float\}', r'([\d.]+)')
    pattern = pattern.replace(r'\{word\}', r'(\S+)')
    pattern = pattern.replace(r'\{bigdecimal\}', r'([\d.]+)')
    pattern = pattern.replace(r'\{long\}', r'(-?\d+)')
    pattern = pattern.replace(r'\{double\}', r'([\d.]+)')
    # Custom parameter types (@ParameterType, es. {delegationRole}, {tenantType}):
    # non conosciamo la regex registrata in Java → match generico non-greedy.
    # Meglio un match largo che uno step perennemente non matchato (bug
    # rejectConsumerDelegation/revokeConsumerDelegation su Interop).
    pattern = re.sub(r'\\\{\w+\\\}', r'(.+?)', pattern)
    # Cucumber optionals and alternatives
    pattern = re.sub(r'\\\(([^)]*?)/([^)]*?)\\\)',
                      lambda m: '(?:' + m.group(1) + '|' + m.group(2) + ')', pattern)
    pattern = re.sub(r'\\\(([^)]*?)\\\)',
                      lambda m: '(?:' + m.group(1) + ')?', pattern)
    return pattern


# ---------------------------------------------------------------------------
# 3. Parse .feature files
# ---------------------------------------------------------------------------

def parse_features(feature_dirs):
    """Parse .feature files → list of scenarios with tags and step texts."""
    scenarios = []

    for d in feature_dirs:
        if not d.exists():
            continue
        for f in d.rglob("*.feature"):
            text = f.read_text(errors="replace")
            lines = text.split("\n")
            try:
                rel_file = str(f.relative_to(REPO_ROOT))
            except ValueError:
                rel_file = str(f)

            feature_tags = set()
            pending_tags = set()
            current_scenario = None

            for lineno, line in enumerate(lines, 1):
                stripped = line.strip()

                if stripped.startswith("@"):
                    pending_tags.update(re.findall(r'@([\w\-]+)', stripped))
                    continue

                if stripped.startswith("Feature:"):
                    feature_tags = pending_tags.copy()
                    pending_tags = set()
                    continue

                if stripped.startswith("Scenario:") or stripped.startswith("Scenario Outline:"):
                    if current_scenario and current_scenario["steps"]:
                        scenarios.append(current_scenario)
                    current_scenario = {
                        "name": stripped.split(":", 1)[1].strip(),
                        "file": rel_file,
                        "line": lineno,
                        "tags": feature_tags | pending_tags,
                        "steps": [],
                    }
                    pending_tags = set()
                    continue

                if stripped.startswith("Examples:"):
                    pending_tags = set()
                    continue

                if current_scenario:
                    step_match = re.match(r'^(Given|When|Then|And|But|\*)\s+(.+)', stripped)
                    if step_match:
                        current_scenario["steps"].append(step_match.group(2))

                if stripped == "":
                    if not current_scenario:
                        pending_tags = set()

            if current_scenario and current_scenario["steps"]:
                scenarios.append(current_scenario)

    return scenarios


# ---------------------------------------------------------------------------
# 4. Match scenarios to step definitions
# ---------------------------------------------------------------------------

# --- Version-family refinement -------------------------------------------
#
# Some step definitions dispatch to a version-specific implementation at
# runtime based on a textual parameter in the step (e.g. webhook steps:
# `getWebhookStep(version)` -> WebhookStepsV23..V29). Static resolution
# credits ALL versions; here we narrow the attribution when the step text
# contains an explicit version token like `versione "V24"` or "più recente".

VERSION_TOKEN_RE = re.compile(r'"\s*(V\d+|più recente)\s*"', re.IGNORECASE)
OP_VERSION_RE = re.compile(r'^(.+?)V(\d+)$')


def build_version_families(all_ops):
    """Group operationIds into version families.

    Returns (families, op_to_base):
      families: base -> {version_int: opId}; version 0 = unversioned base op.
      op_to_base: opId -> base, only for ops belonging to a family with >=2 members.
    """
    raw = {}
    for op in all_ops:
        m = OP_VERSION_RE.match(op)
        if m:
            raw.setdefault(m.group(1), {})[int(m.group(2))] = op

    families, op_to_base = {}, {}
    for base, members in raw.items():
        fam = dict(members)
        if base in all_ops:
            fam[0] = base  # unversioned variant belongs to the family
        if len(fam) < 2:
            continue  # not a real family (e.g. a lone "...V1" op)
        families[base] = fam
        for op in fam.values():
            op_to_base[op] = base
    return families, op_to_base


def parse_version_tokens(text):
    """Extract version tokens like "V24" / "più recente" from step text."""
    versions = []
    for t in VERSION_TOKEN_RE.findall(text):
        if t.lower() == "più recente":
            versions.append("MAX")
        else:
            versions.append(int(t[1:]))
    return versions


def refine_ops_by_version(ops, versions, families, op_to_base):
    """If version(s) are in scope, restrict version families to the named
    member(s). Conservative: families without a resolvable token match are
    kept whole."""
    if not versions:
        return ops

    fam_present = {}
    loose = set()
    for op in ops:
        base = op_to_base.get(op)
        if base:
            fam_present.setdefault(base, set()).add(op)
        else:
            loose.add(op)

    refined = set(loose)
    for base, present in fam_present.items():
        if len(present) <= 1:
            refined |= present  # not smeared, nothing to narrow
            continue
        members = families[base]
        matched = set()
        for v in versions:
            if v == "MAX":
                matched.add(members[max(members)])
            elif v in members:
                matched.add(members[v])
            elif 0 in members:
                # version token with no dedicated member (e.g. V10) -> unversioned op
                matched.add(members[0])
        matched &= present
        refined |= matched if matched else present
    return refined


def match_steps(scenarios, step_defs, all_ops=None):
    """For each scenario, match steps to defs and collect API calls."""
    compiled = []
    for sd in step_defs:
        try:
            compiled.append((re.compile(sd["_regex"]), sd))
        except re.error:
            compiled.append((None, sd))

    families, op_to_base = build_version_families(all_ops or set())

    unmatched_count = 0
    total_steps = 0

    for scenario in scenarios:
        api_calls = set()
        # Scenario-level version context: versions named anywhere in the
        # scenario apply also to its version-less steps (e.g. the version is
        # declared once at stream creation, then generic steps follow).
        scenario_versions = []
        for step_text in scenario["steps"]:
            scenario_versions.extend(parse_version_tokens(step_text))

        for step_text in scenario["steps"]:
            total_steps += 1
            # Semantica Cucumber: tra i pattern che matchano vince il PIÙ SPECIFICO.
            # Con i placeholder generici (.+?) dei custom parameter type, il primo
            # match può essere quello sbagliato: si raccolgono tutti i candidati e
            # si sceglie per (fullmatch > search, più caratteri letterali nel pattern).
            best = None       # (is_fullmatch, literal_len, sd)
            for regex, sd in compiled:
                if regex is None:
                    continue
                try:
                    full = regex.fullmatch(step_text)
                    if full or regex.search(step_text):
                        literal_len = len(re.sub(r'\{\w+\}', '', sd["pattern"]))
                        key = (1 if full else 0, literal_len)
                        if best is None or key > best[0]:
                            best = (key, sd)
                except re.error:
                    continue
            if best:
                sd = best[1]
                # Use method-level ops first, fall back to file-level
                ops = sd["api_calls"] if sd["api_calls"] else sd["file_ops"]
                if families:
                    step_versions = parse_version_tokens(step_text)
                    versions = step_versions or scenario_versions
                    ops = refine_ops_by_version(ops, versions, families, op_to_base)
                api_calls.update(ops)
            else:
                unmatched_count += 1

        scenario["api_calls"] = api_calls

    return total_steps, unmatched_count


# ---------------------------------------------------------------------------
# 5. Parse runner classes
# ---------------------------------------------------------------------------

def parse_runners(runner_dirs):
    """Parse runner Java files to extract @IncludeTags and @ExcludeTags."""
    runners = []
    for d in runner_dirs:
        if not d.exists():
            continue
        for f in d.rglob("*.java"):
            text = f.read_text(errors="replace")
            if "@Suite" not in text and "RunWith" not in text:
                continue
            if "/steps/" in str(f):
                continue

            name = f.stem
            include_tags = set()
            exclude_tags = set()

            for m in re.finditer(r'@IncludeTags\(\{([^}]*)\}\)', text, re.DOTALL):
                include_tags.update(t.strip().strip('"') for t in m.group(1).split(',') if t.strip().strip('"'))
            for m in re.finditer(r'@IncludeTags\("([^"]+)"\)', text):
                include_tags.add(m.group(1))

            for m in re.finditer(r'@ExcludeTags\(\{([^}]*)\}\)', text, re.DOTALL):
                exclude_tags.update(t.strip().strip('"') for t in m.group(1).split(',') if t.strip().strip('"'))
            for m in re.finditer(r'@ExcludeTags\("([^"]+)"\)', text):
                exclude_tags.add(m.group(1))

            include_tags.discard("")
            exclude_tags.discard("")

            runners.append({
                "name": name,
                "include_tags": include_tags,
                "exclude_tags": exclude_tags,
            })

    return runners


# ---------------------------------------------------------------------------
# 6. Compute coverage
# ---------------------------------------------------------------------------

def scenario_matches_runner(scenario, runner):
    """JUnit5: @IncludeTags = OR, @ExcludeTags = OR (any match excludes)."""
    if runner["exclude_tags"] and (scenario["tags"] & runner["exclude_tags"]):
        return False
    if runner["include_tags"]:
        return bool(scenario["tags"] & runner["include_tags"])
    return True


def compute_runner_coverage(runners, scenarios, all_ops):
    results = []
    for runner in runners:
        matching = [s for s in scenarios if scenario_matches_runner(s, runner)]
        covered = set()
        for s in matching:
            covered.update(s.get("api_calls", set()))
        covered &= all_ops

        results.append({
            "runner": runner["name"],
            "include_tags": sorted(runner["include_tags"]),
            "exclude_tags": sorted(runner["exclude_tags"]),
            "scenarios": len(matching),
            "covered": covered,
            "pct": len(covered) / len(all_ops) * 100 if all_ops else 0,
        })

    results.sort(key=lambda r: r["pct"], reverse=True)
    return results


def compute_tag_coverage(scenarios, all_ops):
    tag_ops = defaultdict(set)
    tag_scenarios = defaultdict(int)

    for s in scenarios:
        resolved = s.get("api_calls", set()) & all_ops
        for tag in s["tags"]:
            tag_ops[tag].update(resolved)
            tag_scenarios[tag] += 1

    results = []
    for tag in sorted(tag_ops.keys()):
        ops = tag_ops[tag]
        results.append({
            "tag": tag,
            "scenarios": tag_scenarios[tag],
            "covered": len(ops),
            "pct": len(ops) / len(all_ops) * 100 if all_ops else 0,
            "ops": ops,
        })

    results.sort(key=lambda r: r["covered"], reverse=True)
    return results


def greedy_optimize(tag_results, all_ops, max_tags=25):
    """Greedy set cover to find the tag combination maximizing coverage."""
    remaining = set(all_ops)
    selected = []
    covered = set()

    tag_ops = {t["tag"]: t["ops"] for t in tag_results}

    while remaining and len(selected) < max_tags:
        best_tag = None
        best_gain = 0
        best_new = set()

        for tag, ops in tag_ops.items():
            if tag in [s[0] for s in selected]:
                continue
            new = ops & remaining
            if len(new) > best_gain:
                best_gain = len(new)
                best_tag = tag
                best_new = new

        if best_gain == 0:
            break

        covered.update(best_new)
        remaining -= best_new
        selected.append((best_tag, best_gain, len(covered)))

    return selected, covered


# ---------------------------------------------------------------------------
# 7. Reporting
# ---------------------------------------------------------------------------

def print_runner_report(results, total):
    print(f"\n{'='*90}")
    print(f"  COPERTURA PER RUNNER  ({total} endpoint totali)")
    print(f"{'='*90}\n")
    print(f"  {'Runner':<50} {'Scen.':>6} {'Coperti':>10} {'%':>7}")
    print(f"  {'-'*50} {'-'*6} {'-'*10} {'-'*7}")

    for r in results:
        n = len(r["covered"])
        bar = "█" * int(r["pct"] / 100 * 20) + "░" * (20 - int(r["pct"] / 100 * 20))
        print(f"  {r['runner']:<50} {r['scenarios']:>6} {n:>4}/{total:<4}  {r['pct']:>5.1f}%  {bar}")

    all_covered = set()
    for r in results:
        all_covered.update(r["covered"])
    union_pct = len(all_covered) / total * 100 if total else 0
    print(f"\n  {'UNIONE TUTTI I RUNNER':<50} {'':>6} {len(all_covered):>4}/{total:<4}  {union_pct:>5.1f}%")


def print_tag_report(results, total, top_n=30):
    print(f"\n{'='*90}")
    print(f"  TOP {top_n} TAG PER COPERTURA ENDPOINT  ({total} endpoint totali)")
    print(f"{'='*90}\n")
    print(f"  {'Tag':<45} {'Scen.':>6} {'Coperti':>10} {'%':>7}")
    print(f"  {'-'*45} {'-'*6} {'-'*10} {'-'*7}")

    for r in results[:top_n]:
        bar = "█" * int(r["pct"] / 100 * 20) + "░" * (20 - int(r["pct"] / 100 * 20))
        print(f"  {r['tag']:<45} {r['scenarios']:>6} {r['covered']:>4}/{total:<4}  {r['pct']:>5.1f}%  {bar}")


def print_optimization(selected, covered, total, all_ops):
    print(f"\n{'='*90}")
    print("  COMBINAZIONE OTTIMALE DI TAG (greedy set cover)")
    print(f"{'='*90}\n")
    print(f"  {'#':>3} {'Tag':<45} {'+Nuovi':>7} {'Cumul.':>10} {'%':>7}")
    print(f"  {'-'*3} {'-'*45} {'-'*7} {'-'*10} {'-'*7}")

    for i, (tag, gain, cumul) in enumerate(selected, 1):
        pct = cumul / total * 100 if total else 0
        bar = "█" * int(pct / 100 * 20) + "░" * (20 - int(pct / 100 * 20))
        print(f"  {i:>3} {tag:<45} +{gain:<6} {cumul:>4}/{total:<4}  {pct:>5.1f}%  {bar}")

    final_pct = len(covered) / total * 100 if total else 0
    uncovered = all_ops - covered
    print(f"\n  Copertura finale: {len(covered)}/{total} ({final_pct:.1f}%)")
    print(f"  Endpoint non raggiungibili: {len(uncovered)}")

    if uncovered:
        print("\n  Endpoint mai coperti:")
        for op in sorted(uncovered)[:40]:
            print(f"    - {op}")
        if len(uncovered) > 40:
            print(f"    ... e altri {len(uncovered) - 40}")


def print_runner_detail(runner_results, runner_name, all_ops):
    r = next((r for r in runner_results if r["runner"] == runner_name), None)
    if not r:
        print(f"\n  Runner '{runner_name}' non trovato.")
        return

    total = len(all_ops)
    print(f"\n{'='*90}")
    print(f"  DETTAGLIO RUNNER: {runner_name}")
    print(f"{'='*90}")
    print(f"  Include tags: {', '.join(r['include_tags'])}")
    print(f"  Exclude tags: {', '.join(r['exclude_tags'])}")
    print(f"  Scenari matchati: {r['scenarios']}")
    print(f"  Endpoint coperti: {len(r['covered'])}/{total} ({r['pct']:.1f}%)")

    print("\n  Endpoint coperti:")
    for op in sorted(r["covered"]):
        print(f"    ✓ {op}")

    uncovered = all_ops - r["covered"]
    print(f"\n  Endpoint NON coperti ({len(uncovered)}):")
    for op in sorted(uncovered)[:50]:
        print(f"    ✗ {op}")
    if len(uncovered) > 50:
        print(f"    ... e altri {len(uncovered) - 50}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def find_latest_coverage_report(suite):
    """Find the latest coverage-{suite}.json from reports/ or legacy location."""
    # Check reports/ subdirectories (newest first)
    if REPORTS_DIR.exists():
        run_dirs = sorted(REPORTS_DIR.iterdir(), reverse=True)
        for d in run_dirs:
            candidate = d / f"coverage-{suite}.json"
            if candidate.exists():
                return candidate

    # Fallback: legacy flat files
    legacy = PROJECT_ROOT / f"report-{suite}.json"
    if legacy.exists():
        return legacy

    return None


def make_run_dir(suite):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = REPORTS_DIR / f"{ts}_{suite}_tags"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def find_previous_tag_report(suite):
    """Latest existing tag-coverage JSON for this suite (for delta checks)."""
    if not REPORTS_DIR.exists():
        return None
    for d in sorted(REPORTS_DIR.iterdir(), reverse=True):
        if d.name.endswith(f"_{suite}_tags"):
            candidate = d / f"tag-coverage-{suite}.json"
            if candidate.exists():
                return candidate
    return None


# Soglie dei sanity check ("il misuratore va misurato")
SANITY_THRESHOLDS = {
    "unmatched_pct_max": 30.0,        # % step non matchati oltre cui la misura è inaffidabile
    "scen_no_calls_pct_max": 10.0,    # % scenari senza API calls
    "resolved_methods_drop_pct": 20.0,  # calo metodi risolti vs run precedente
    "reachable_delta_pct": 15.0,      # variazione endpoint reali vs run precedente
    "ops_per_method_median_max": 5.0,   # mediana ops/metodo oltre cui la risoluzione è inquinata
}


def _median(values):
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return float(s[mid]) if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def run_sanity_checks(metrics, suite, families, endpoint_depth):
    """Self-check dei segni vitali della misura. Ritorna (issues, sanity_dict).

    issues: lista di (severity, message); severity in {"FAIL", "WARN", "INFO"}.
    """
    t = SANITY_THRESHOLDS
    issues = []

    if metrics["unmatched_pct"] > t["unmatched_pct_max"]:
        issues.append(("FAIL",
            f"Step non matchati: {metrics['unmatched_pct']:.1f}% "
            f"(soglia {t['unmatched_pct_max']:.0f}%) — il parser non capisce più gli step: "
            f"i numeri SOTTOSTIMANO"))

    if metrics["scen_no_calls_pct"] > t["scen_no_calls_pct_max"]:
        issues.append(("FAIL",
            f"Scenari senza API calls: {metrics['scen_no_calls_pct']:.1f}% "
            f"(soglia {t['scen_no_calls_pct_max']:.0f}%) — la risoluzione wrapper potrebbe essersi rotta"))

    med = metrics.get("ops_per_method_median", 0.0)
    if med > t["ops_per_method_median_max"]:
        issues.append(("WARN",
            f"Inflazione risoluzione: mediana {med:.0f} operationId/metodo "
            f"(soglia {t['ops_per_method_median_max']:.0f}), {metrics.get('methods_over_10_ops_pct', 0):.0f}% "
            f"metodi >10 ops — collisioni di omonimi: 'scenari che lo invocano' SOVRASTIMA"))

    # Delta vs run precedente
    prev_path = find_previous_tag_report(suite)
    if prev_path:
        try:
            prev = json.load(open(prev_path))
            prev_sanity = prev.get("sanity", {}).get("metrics", {})
            prev_methods = prev_sanity.get("resolved_methods")
            if prev_methods:
                drop = (prev_methods - metrics["resolved_methods"]) / prev_methods * 100
                if drop > t["resolved_methods_drop_pct"]:
                    issues.append(("FAIL",
                        f"Metodi risolti: {metrics['resolved_methods']} vs {prev_methods} del run "
                        f"precedente (-{drop:.0f}%) — possibile refactoring dei client non tracciato"))
            prev_reach = prev.get("reachable_endpoints")
            if prev_reach:
                delta = (metrics["reachable"] - prev_reach) / prev_reach * 100
                if abs(delta) > t["reachable_delta_pct"]:
                    issues.append(("WARN",
                        f"Endpoint reali: {metrics['reachable']} vs {prev_reach} del run precedente "
                        f"({delta:+.0f}%) — verificare se il salto è giustificato da commit reali"))
        except (json.JSONDecodeError, OSError):
            pass

    # Famiglie con profondità identica su tutti i membri (marcatore: attribuzione di famiglia non scorporata)
    uniform = []
    for base, members in families.items():
        depths = [endpoint_depth.get(op, 0) for op in members.values()]
        if len(depths) >= 3 and len(set(depths)) == 1 and depths[0] > 0:
            uniform.append(f"{base} ({len(depths)} versioni, depth={depths[0]})")
    if uniform:
        issues.append(("INFO",
            f"Famiglie con profondità identica su tutte le versioni (possibile attribuzione "
            f"di famiglia non scorporata): {', '.join(uniform[:5])}"
            + (f" e altre {len(uniform)-5}" if len(uniform) > 5 else "")))

    sanity = {
        "passed": not any(sev == "FAIL" for sev, _ in issues),
        "metrics": metrics,
        "thresholds": t,
        "issues": [{"severity": sev, "message": msg} for sev, msg in issues],
        "previous_report": str(prev_path) if prev_path else None,
    }
    return issues, sanity


def print_sanity(issues):
    if not issues:
        print("\n  ✅ SANITY CHECK: tutti i segni vitali nella norma")
        return
    icon = {"FAIL": "❌", "WARN": "⚠️ ", "INFO": "ℹ️ "}
    print(f"\n  {'─'*50}")
    print("  SANITY CHECK")
    for sev, msg in issues:
        print(f"  {icon[sev]} [{sev}] {msg}")
    if any(sev == "FAIL" for sev, _ in issues):
        print("  → La misura potrebbe essere DEGRADATA: verificare prima di pubblicare i numeri.")


def main():
    parser = argparse.ArgumentParser(description="Tag-based E2E coverage analysis")
    parser.add_argument("--suite", choices=["send", "interop"], default="interop")
    parser.add_argument("--runner", help="Show details for a specific runner")
    parser.add_argument("--optimize", action="store_true", help="Find optimal tag combination")
    parser.add_argument("--top-tags", type=int, default=30)
    parser.add_argument("--json", help="Output JSON to file (default: auto in reports/<timestamp>/)")
    parser.add_argument("--no-save", action="store_true", help="Do not auto-save to reports/ folder")
    parser.add_argument("--strict", action="store_true",
                        help="Exit code 1 se i sanity check falliscono (per CI)")
    args = parser.parse_args()

    cfg = SUITE_CONFIG[args.suite]
    if not cfg["pom"].exists():
        sys.exit(f"  Target repo non trovato: {REPO_ROOT}\n"
                 f"  Configura il path in config.yaml (target_repo) o con SCOPE_TARGET_REPO.")
    print(f"\n  Analisi copertura per tag — suite {cfg['label']}")
    print(f"  {'─'*50}")

    # Load endpoint inventory
    report_file = find_latest_coverage_report(args.suite)
    if not report_file:
        sys.exit(f"  Report non trovato.\n  Esegui prima: python3 coverage-tool/coverage.py --suite {args.suite}")

    print(f"  Caricamento inventario da: {report_file}")
    report = json.load(open(report_file))
    all_ops = set(r["operation_id"] for r in report if r.get("operation_id"))
    print(f"  Endpoint totali dalle OpenAPI spec: {len(all_ops)}")

    # Build operationId resolver
    print("  Building operationId resolver (multi-hop)...")
    name_to_ops = build_operation_id_resolver(cfg["all_src_dirs"], all_ops)
    print(f"    → {len(name_to_ops)} metodi risolvono a operationIds")

    # Parse step definitions
    print("  Parsing step definitions...")
    step_defs = parse_step_definitions(cfg["step_dirs"], name_to_ops, all_ops)
    print(f"    → {len(step_defs)} step definitions")

    step_ops_count = sum(1 for sd in step_defs if sd["api_calls"])
    print(f"    → {step_ops_count} con API calls dirette, "
          f"{sum(1 for sd in step_defs if sd['file_ops'])} con file-level ops")

    # Parse features
    print("  Parsing feature files...")
    scenarios = parse_features(cfg["feature_dirs"])
    print(f"    → {len(scenarios)} scenari")

    # Match steps
    print("  Matching steps → definitions → API calls...")
    total_steps, unmatched = match_steps(scenarios, step_defs, all_ops)
    print(f"    → {total_steps} step totali, {unmatched} non matchati ({unmatched/total_steps*100:.1f}%)" if total_steps else "")

    scenarios_with_ops = sum(1 for s in scenarios if s.get("api_calls"))
    all_scenario_ops = set()
    endpoint_depth = defaultdict(int)    # opId -> number of scenarios exercising it
    endpoint_scenarios = defaultdict(list)  # opId -> [scenario index, ...]
    scenarios_index = []                 # compact list of scenarios referenced by >=1 endpoint
    for s in scenarios:
        calls = s.get("api_calls", set())
        all_scenario_ops.update(calls)
        relevant = calls & all_ops
        if relevant:
            idx = len(scenarios_index)
            scenarios_index.append({
                "name": s.get("name", ""),
                "tc_id": extract_tc_id(s.get("name", "")),  # chiave di join con i report di run
                "file": s.get("file", ""),
                "line": s.get("line", 0),
                "tags": sorted(s.get("tags", [])),   # per il drill-down tag → scenari
                "ops": sorted(relevant),             # endpoint coperti DA QUESTO scenario
            })
            for op in relevant:
                endpoint_depth[op] += 1
                endpoint_scenarios[op].append(idx)
    print(f"    → {scenarios_with_ops}/{len(scenarios)} scenari con API calls")
    print(f"    → {len(all_scenario_ops & all_ops)}/{len(all_ops)} endpoint raggiungibili dagli scenari")

    # --- Sanity TC-ID: il join con i report di esecuzione regge solo se ogni
    # scenario ha un TC-ID univoco. Misuriamo qui il "soffitto" della feature.
    by_tc = defaultdict(list)
    for i, sc in enumerate(scenarios_index):
        if sc["tc_id"]:
            by_tc[sc["tc_id"]].append(i)
    no_id = [i for i, sc in enumerate(scenarios_index) if not sc["tc_id"]]
    dup_ids = {tc: idxs for tc, idxs in by_tc.items() if len(idxs) > 1}
    n_idx = len(scenarios_index)
    tc_id_sanity = {
        "total": n_idx,
        "with_id": n_idx - len(no_id),
        "without_id": len(no_id),
        "without_id_pct": round(len(no_id) / n_idx * 100, 1) if n_idx else 0.0,
        "duplicate_ids": {tc: idxs for tc, idxs in sorted(dup_ids.items())},
        "duplicate_id_count": len(dup_ids),
    }
    print(f"    → TC-ID: {tc_id_sanity['with_id']}/{n_idx} con id, "
          f"{tc_id_sanity['without_id']} senza, {tc_id_sanity['duplicate_id_count']} duplicati")

    # Parse runners
    print("  Parsing runners...")
    runners = parse_runners(cfg["runner_dirs"])
    print(f"    → {len(runners)} runner")

    # Compute
    runner_results = compute_runner_coverage(runners, scenarios, all_ops)
    tag_results = compute_tag_coverage(scenarios, all_ops)

    # Report
    print_runner_report(runner_results, len(all_ops))
    print_tag_report(tag_results, len(all_ops), args.top_tags)

    if args.optimize:
        selected, covered = greedy_optimize(tag_results, all_ops)
        print_optimization(selected, covered, len(all_ops), all_ops)

    if args.runner:
        print_runner_detail(runner_results, args.runner, all_ops)

    # --- Sanity checks (prima del salvataggio: il confronto è col run precedente) ---
    families, _ = build_version_families(all_ops)
    metrics = {
        "total_steps": total_steps,
        "unmatched_steps": unmatched,
        "unmatched_pct": round(unmatched / total_steps * 100, 1) if total_steps else 0.0,
        "scenarios": len(scenarios),
        "scenarios_with_calls": scenarios_with_ops,
        "scen_no_calls_pct": round((len(scenarios) - scenarios_with_ops) / len(scenarios) * 100, 1) if scenarios else 0.0,
        "resolved_methods": len(name_to_ops),
        "step_definitions": len(step_defs),
        "reachable": len(all_scenario_ops & all_ops),
        "total_endpoints": len(all_ops),
        # Termometro inflazione della risoluzione per nome (collisioni):
        # quanti operationId risolve in media un metodo. Mediana alta = il grafo
        # è inquinato da omonimi → "scenari che lo invocano" sovrastimato.
        "ops_per_method_median": _median([len(v) for v in name_to_ops.values()]),
        "methods_over_10_ops_pct": round(
            sum(1 for v in name_to_ops.values() if len(v) > 10) / len(name_to_ops) * 100, 1
        ) if name_to_ops else 0.0,
    }
    issues, sanity = run_sanity_checks(metrics, args.suite, families, endpoint_depth)
    print_sanity(issues)

    # --- Output ---
    output_data = {
        "_README": {
            "cosa_misura": "Copertura REALE: endpoint raggiungibili da uno scenario Cucumber eseguibile (catena tag -> scenario -> step -> wrapper -> metodo API).",
            "statico_vs_reale": "Lo statico grezzo (coverage.py) risponde a 'esiste una chiamata al metodo da qualche parte nel codice?'. La copertura reale risponde a 'uno scenario eseguibile la invoca davvero?'. La differenza e' la 'copertura fantasma': wrapper implementati ma non esercitati da alcun test. Lo statico grezzo SOVRASTIMA.",
            "analogia": "Statico = quante stanze hanno una porta. Reale = in quante stanze entra davvero qualcuno partendo dall'ingresso.",
            "limite": "Misura AMPIEZZA di superficie (endpoint toccato si/no). La metrica endpoint_depth aggiunge una prima indicazione di PROFONDITA' (n. scenari per endpoint), ma non valuta qualita' delle asserzioni o varieta' dei casi.",
            "reachable_endpoints": "Numero di endpoint raggiungibili da almeno uno scenario = copertura reale massima (unione di tutti i runner).",
            "endpoint_depth": "Per ogni operationId, il numero di scenari Cucumber distinti che lo INVOCANO. 1 = toccato una sola volta; valori alti = esercitato da molti scenari. ATTENZIONE: misura l'invocazione, non la verifica — non dice se lo scenario fa asserzioni significative sulla risposta (un endpoint chiamato solo nel setup conta comunque).",
            "scenarios_index": "Elenco compatto degli scenari (name, tc_id, file relativo alla root del repo, line) referenziati da almeno un endpoint. tc_id = identificatore [TC-...] nel titolo, chiave di join con i report di esecuzione.",
            "tc_id_sanity": "Salute della chiave di join TC-ID: scenari senza id (non joinabili coi report di run) e id duplicati (join ambiguo). Soffitto strutturale della feature esiti di esecuzione.",
            "endpoint_scenarios": "Per ogni operationId, gli indici (in scenarios_index) degli scenari che lo esercitano. len() coincide con endpoint_depth.",
            "repo_root": "Root assoluta del repository al momento della generazione (per costruire link ai file)."
        },
        "suite": args.suite,
        "timestamp": datetime.now().isoformat(),
        "sanity": sanity,
        "total_endpoints": len(all_ops),
        "reachable_endpoints": len(all_scenario_ops & all_ops),
        "tc_id_sanity": tc_id_sanity,
        "endpoint_depth": {op: endpoint_depth[op] for op in sorted(endpoint_depth) if op in all_ops},
        "repo_root": str(REPO_ROOT),
        "scenarios_index": scenarios_index,
        "endpoint_scenarios": {op: endpoint_scenarios[op] for op in sorted(endpoint_scenarios)},
        "runners": [{
            "name": r["runner"],
            "include_tags": r["include_tags"],
            "exclude_tags": r["exclude_tags"],
            "scenarios": r["scenarios"],
            "endpoints_covered": len(r["covered"]),
            "coverage_pct": round(r["pct"], 1),
            "operation_ids": sorted(r["covered"]),
        } for r in runner_results],
        "tags": [{
            "tag": t["tag"],
            "scenarios": t["scenarios"],
            "endpoints_covered": t["covered"],
            "coverage_pct": round(t["pct"], 1),
            "operation_ids": sorted(t["ops"]),
        } for t in tag_results],
    }

    if not args.no_save:
        run_dir = make_run_dir(args.suite)
        json_path = args.json or str(run_dir / f"tag-coverage-{args.suite}.json")
        with open(json_path, "w") as f:
            json.dump(output_data, f, indent=2)
        print(f"\n  📁 Report salvato in: {run_dir}")
    elif args.json:
        with open(args.json, "w") as f:
            json.dump(output_data, f, indent=2)
        print(f"\n  JSON salvato in {args.json}")

    if args.strict and not sanity["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
