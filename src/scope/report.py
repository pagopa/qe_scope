#!/usr/bin/env python3
"""
HTML Coverage Dashboard Generator.

Reads the latest coverage-{suite}.json (static) and tag-coverage-{suite}.json
(real, tag-traced) from reports/, reconciles them into the three categories
(real / phantom / never-implemented), and produces a single self-contained
HTML dashboard with drill-down.

Usage:
    python3 coverage-tool/report-html.py                 # both suites
    python3 coverage-tool/report-html.py --suite interop # one suite
    python3 coverage-tool/report-html.py --open          # open in browser

No external dependencies. Output: reports/html/coverage-dashboard-<timestamp>.html
"""

import argparse
import fnmatch
import glob
import json
import os
import re
import webbrowser
from datetime import datetime, timedelta
from importlib.resources import files
from pathlib import Path

from .config import DATA_DIR, REPORTS_DIR
from .runtime import compute_current_state, load_results

CRITICALITY_FILE = DATA_DIR / "criticality.yaml"


def _read_asset(name):
    """CSS/JS del dashboard: sorgenti separati (lint-abili) in scope/assets/,
    inlinati a build time → l'output resta un singolo file HTML auto-consistente.
    Lo strip del newline finale tiene l'output identico al template monolitico."""
    return (files("scope.assets") / name).read_text().rstrip("\n")

DEFAULT_CRIT = {
    "classes": {"core": 3.0, "standard": 1.0, "marginal": 0.3},
    "default": "standard",
    "rules": [],
}


def load_criticality():
    """Load criticality.yaml; fall back to equal-weight defaults."""
    if not CRITICALITY_FILE.exists():
        return dict(DEFAULT_CRIT)
    try:
        import yaml
        data = yaml.safe_load(CRITICALITY_FILE.read_text()) or {}
    except Exception as e:
        print(f"  ⚠️  criticality.yaml non leggibile ({e}): uso pesi uniformi")
        return dict(DEFAULT_CRIT)
    return {
        "classes": data.get("classes") or DEFAULT_CRIT["classes"],
        "default": data.get("default") or DEFAULT_CRIT["default"],
        "rules": data.get("rules") or [],
    }


def resolve_crit_class(ep, crit):
    """Resolve the criticality class of one endpoint.
    Precedence: operation > path > spec > service > default."""
    rules = crit["rules"]
    for kind in ("operation", "path", "spec", "service"):
        for r in rules:
            if kind not in r:
                continue
            val, cls = r[kind], r.get("class", crit["default"])
            if kind == "operation" and ep["operation_id"] == val:
                return cls
            if kind == "path" and fnmatch.fnmatch(ep["path"], val):
                return cls
            if kind == "spec" and ep["spec_name"] == val:
                return cls
            if kind == "service" and ep["service"] == val:
                return cls
    return crit["default"]


# ---------------------------------------------------------------------------
# Data loading & reconciliation
# ---------------------------------------------------------------------------

def find_latest(pattern, fname):
    dirs = sorted(glob.glob(str(REPORTS_DIR / pattern)), reverse=True)
    for d in dirs:
        candidate = os.path.join(d, fname)
        if os.path.exists(candidate):
            return candidate
    return None


def load_suite(suite):
    """Load static + tag reports for one suite from disk, then reconcile."""
    cov_path = find_latest(f"*_{suite}", f"coverage-{suite}.json")
    tag_path = find_latest(f"*_{suite}_tags", f"tag-coverage-{suite}.json")

    if not cov_path:
        return None

    cov = json.load(open(cov_path))
    tag = json.load(open(tag_path)) if tag_path else None
    runs = [r for r in load_results().get("runs", []) if r.get("suite") == suite]
    rt_state, rt_meta = compute_current_state(runs) if runs else ({}, None)
    return reconcile(suite, cov, tag, str(cov_path), str(tag_path or ""),
                     runtime_state=rt_state, runtime_meta=rt_meta, runtime_runs=runs)


def join_runtime(scenarios_index, endpoint_scenarios, runtime_state):
    """Join esiti di esecuzione ↔ inventario, via TC-ID (chiave stabile).
    Funzione PURA. Arricchisce ogni scenario con `exec` (stato corrente) e
    deriva l'esito per endpoint:
      ok    = almeno uno scenario che lo invoca è OK         → eseguita ✓
      ko    = ne ha solo falliti                              → rossa ✗
      other = solo esiti incerti (skipped/undefined)
      none  = nessuno scenario girato nella finestra          → non eseguita ○
    Onestà: 'ok' = lo scenario è passato, NON che la risposta è stata asserita.
    """
    for s in scenarios_index:
        tc = s.get("tc_id")
        st = runtime_state.get(tc) if tc else None
        if st:
            s["exec"] = {
                "status": st["last_status"],       # OK | KO | OTHER
                "age_days": st["age_days"],
                "flaky": st["flaky"],
                "ok": st["ok"], "ko": st["ko"],
                "error": st.get("last_error", ""),
                "run_file": st.get("last_file", ""),
                "run_ts": st.get("last_ts", ""),
            }
    ep_exec = {}
    ep_run = {}
    for op, idxs in endpoint_scenarios.items():
        execs = [scenarios_index[i].get("exec") for i in idxs if i < len(scenarios_index)]
        execs = [x for x in execs if x]
        sts = [x["status"] for x in execs]
        if not sts:
            ep_exec[op] = "none"
        elif "OK" in sts:
            ep_exec[op] = "ok"
        elif "KO" in sts:
            ep_exec[op] = "ko"
        else:
            ep_exec[op] = "other"
        # Riferimento all'esecuzione più recente che ha toccato l'endpoint.
        if execs:
            last = min(execs, key=lambda x: x.get("age_days", 10**9))
            ep_run[op] = {"age_days": last.get("age_days"),
                          "file": last.get("run_file", ""),
                          "ts": last.get("run_ts", "")}
    return ep_exec, ep_run


def compute_run_summaries(runs, scenarios_index, real_ops, runtime_meta=None):
    """Una riga per run ingerita (per il tab Esecuzioni). Funzione PURA.
    coverage = endpoint REALI esercitati dagli scenari della run / totale reali."""
    tc_to_ops = {}
    for s in scenarios_index:
        tc = s.get("tc_id")
        if tc:
            tc_to_ops.setdefault(tc, set()).update(o for o in s.get("ops", []) if o in real_ops)
    window = (runtime_meta or {}).get("window_days", 0)
    horizon = None
    if window:
        from datetime import timezone as _tz
        horizon = datetime.now(_tz.utc) - timedelta(days=window)
    out = []
    for r in runs:
        results = r.get("results", [])
        touched = set()
        for res in results:
            touched |= tc_to_ops.get(res.get("tc_id"), set())
        rts = _parse_run_ts(r.get("run_ts"))
        out.append({
            "run_id": r.get("run_id", ""),
            "file": r.get("file", ""),
            "date": rts.date().isoformat() if rts else (r.get("run_ts", "")[:10]),
            "ts": r.get("run_ts", ""),
            "n_scen": len(results),
            "n_ok": r.get("n_ok", 0),
            "n_ko": r.get("n_ko", 0),
            "n_other": r.get("n_other", 0),
            "cov_n": len(touched),
            "cov_pct": round(len(touched) / len(real_ops) * 100, 1) if real_ops else 0.0,
            "in_window": bool(horizon and rts and rts >= horizon),
        })
    out.sort(key=lambda x: x["ts"], reverse=True)
    return out


def _parse_run_ts(raw):
    if not raw:
        return None
    from datetime import timezone as _tz
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=_tz.utc)


def reconcile(suite, cov, tag, cov_path="", tag_path="", runtime_state=None, runtime_meta=None, runtime_runs=None):
    """Riconcilia statico (cov) + reale (tag) nelle tre categorie. Funzione PURA
    (nessun I/O): testabile con dati sintetici. Calcola anche reachable_not_static
    (guard cross-layer: reach ⊄ static → lo statico sotto-rileva).
    runtime_state/meta (opzionali): esiti di esecuzione per il join via TC-ID."""
    runtime_state = runtime_state or {}
    # --- Static, by operationId (dedup) ---
    rows = [r for r in cov if r.get("operation_id")]
    all_ops = sorted(set(r["operation_id"] for r in rows))
    static_covered = set(r["operation_id"] for r in rows if r.get("covered"))

    # --- Tag (real) covered ---
    tag_covered = set()
    runners = []
    tags = []
    depth = {}
    scenarios_index = []
    endpoint_scenarios = {}
    repo_root = ""
    if tag:
        for r in tag.get("runners", []):
            tag_covered.update(r.get("operation_ids", []))
            runners.append(r)
        tags = tag.get("tags", [])
        depth = tag.get("endpoint_depth", {})
        scenarios_index = tag.get("scenarios_index", [])
        endpoint_scenarios = tag.get("endpoint_scenarios", {})
        repo_root = tag.get("repo_root", "")
    tag_covered &= set(all_ops)

    # --- Per-endpoint enrichment (one representative row per operationId) ---
    op_meta = {}
    for r in rows:
        op = r["operation_id"]
        if op not in op_meta:
            op_meta[op] = {
                "operation_id": op,
                "method": r.get("method", ""),
                "path": r.get("path", ""),
                "service": r.get("service", ""),
                "spec_name": r.get("spec_name", ""),
                "visibility": r.get("visibility", ""),
            }
        in_static = op in static_covered
        in_tag = op in tag_covered
        if in_static and in_tag:
            cat = "real"
        elif in_static and not in_tag:
            cat = "phantom"
        elif in_tag and not in_static:
            cat = "real"
        else:
            cat = "never"
        op_meta[op]["category"] = cat
        op_meta[op]["static"] = in_static
        op_meta[op]["tag"] = in_tag
        op_meta[op]["depth"] = depth.get(op, 0)

    endpoints = sorted(op_meta.values(), key=lambda e: (e["spec_name"], e["path"], e["method"]))

    total = len(all_ops)
    real = sum(1 for e in endpoints if e["category"] == "real")
    phantom = sum(1 for e in endpoints if e["category"] == "phantom")
    never = sum(1 for e in endpoints if e["category"] == "never")

    # Cross-layer guard: l'invariante atteso è reachable ⊆ static_covered
    # ("una stanza in cui si entra ha una porta"). Se violato, lo scanner
    # statico di coverage.py sotto-rileva rispetto al resolver di tag-coverage
    # (es. WithHttpInfo / method reference / chiamate unqualified che il resolver
    # segue ma lo scanner no). Conseguenza visibile: reale > statico grezzo.
    # La categorizzazione resta corretta (tag è autoritativo per "real"), ma il
    # confronto statico↔reale è fuorviante finché i due parser non sono unificati (P2).
    reach_not_static = sorted(set(tag_covered) - set(static_covered))

    op_cat = {op: op_meta[op]["category"] for op in op_meta}
    op_svc = {op: op_meta[op].get("service", "") for op in op_meta}
    guide = compute_guide(set(all_ops), runners, tags, op_cat, op_svc) if tag else None

    control_tags = set()
    for r in runners:
        control_tags.update(r.get("exclude_tags", []))
    suggestions = compute_tag_suggestions(endpoints, tags, control_tags) if tag else {}

    scen_orphans = compute_scenario_runners(scenarios_index, runners) if tag else 0

    # --- Join esiti di esecuzione ---
    exec_active = bool(runtime_state) and bool(runtime_meta) and not (runtime_meta or {}).get("empty")
    exec_ok = exec_ko = exec_none = 0
    run_summaries = []
    if tag and exec_active:
        ep_exec, ep_run = join_runtime(scenarios_index, endpoint_scenarios, runtime_state)
        for op, em in op_meta.items():
            if em["category"] == "real":
                em["exec"] = ep_exec.get(op, "none")
                r = ep_run.get(op)
                if r:
                    em["exec_age"] = r["age_days"]
                    em["exec_run"] = r["file"]
        real_ops = {op for op, em in op_meta.items() if em["category"] == "real"}
        run_summaries = compute_run_summaries(
            runtime_runs or [], scenarios_index, real_ops, runtime_meta)
        reals = [e for e in endpoints if e["category"] == "real"]
        exec_ok = sum(1 for e in reals if e.get("exec") == "ok")
        exec_ko = sum(1 for e in reals if e.get("exec") == "ko")
        exec_none = sum(1 for e in reals if e.get("exec") in (None, "none", "other"))

    return {
        "suite": suite,
        "total": total,
        "static_covered": len(static_covered),
        "reachable_not_static": reach_not_static,
        "real": real,
        "phantom": phantom,
        "never": never,
        "has_tag": tag is not None,
        "endpoints": endpoints,
        "runners": sorted(runners, key=lambda r: r.get("coverage_pct", 0), reverse=True),
        "tags": [t for t in sorted(tags, key=lambda t: t.get("endpoints_covered", 0), reverse=True)],
        "scenarios_index": scenarios_index,
        "scen_orphans": scen_orphans,
        "tc_id_sanity": tag.get("tc_id_sanity", {}) if tag else {},
        "exec_active": exec_active,
        "exec_meta": runtime_meta or {},
        "exec_ok": exec_ok,
        "exec_ko": exec_ko,
        "exec_none": exec_none,
        "run_summaries": run_summaries,
        "endpoint_scenarios": endpoint_scenarios,
        "guide": guide,
        "suggestions": suggestions,
        "repo_root": repo_root,
        "cov_path": cov_path,
        "tag_path": tag_path,
    }


_VER_SUFFIX = re.compile(r"V\d+$")


def _logical_base(op):
    return _VER_SUFFIX.sub("", op)


def compute_scenario_runners(scenarios_index, runners):
    """Per ogni scenario, i runner che lo eseguono (match @IncludeTags meno
    @ExcludeTags, stessa logica di tag_coverage). Arricchisce in-place ogni voce
    con 'runners' (lista nomi). Ritorna il numero di scenari ORFANI: con endpoint
    ma eseguiti da NESSUN runner → la loro copertura è illusoria (test mai lanciato),
    l'analogo lato scenario del 'fantasma' lato endpoint."""
    rules = [(r.get("name", ""), set(r.get("include_tags", [])), set(r.get("exclude_tags", [])))
             for r in runners]
    orphans = 0
    for s in scenarios_index:
        st = set(s.get("tags", []))
        runs = [name for name, inc, exc in rules
                if (not inc or st & inc) and not (st & exc)]
        s["runners"] = sorted(runs)
        if not runs:
            orphans += 1
    return orphans


def compute_tag_suggestions(endpoints, tags, control_tags=None):
    """Per ogni endpoint NON coperto, suggerisce in quale tag inserirlo, fondato
    sull'EVIDENZA (niente generazione, solo deduzione dal grafo):
      1) completamento di famiglia di versione: tag che coprono GIÀ altre versioni
         dello stesso operationId logico (segnale forte: stesso endpoint, altra V);
      2) vicinanza di microservizio: tag che coprono più endpoint dello stesso service.
    Ritorna {operationId → [{tag, kind, reason}]} solo per i non coperti.
    Esclude i control-tag (@ignore & co.) per non suggerire scenari disabilitati."""
    control_tags = control_tags or set()
    cat = {e["operation_id"]: e["category"] for e in endpoints}
    svc = {e["operation_id"]: e.get("service", "") for e in endpoints}
    all_ops = list(cat.keys())
    covered = {o for o in all_ops if cat.get(o) == "real"}
    # tag → insieme di endpoint COPERTI che raggiunge (esclusi i control-tag)
    tag_ops = {t["tag"]: set(t.get("operation_ids", [])) & covered
               for t in tags if t["tag"] not in control_tags}

    out = {}
    for op in all_ops:
        if op in covered:
            continue
        base = _logical_base(op)
        family = {o for o in all_ops if o != op and o in covered and _logical_base(o) == base}
        siblings = {o for o in all_ops if o != op and o in covered and svc.get(o) == svc.get(op)}

        sugg, seen = [], set()
        # 1) famiglia di versione (evidenza più forte)
        if family:
            scored = sorted(((len(ops & family), tg, sorted(ops & family))
                             for tg, ops in tag_ops.items() if ops & family),
                            key=lambda x: (-x[0], x[1]))
            for cnt, tg, hit in scored[:2]:
                sugg.append({"tag": tg, "kind": "family",
                             "reason": f"copre già {cnt} versione/i della stessa famiglia "
                                       f"({base}…): {', '.join(hit[:3])}"
                                       + (" …" if len(hit) > 3 else "")})
                seen.add(tg)
        # 2) vicinanza di microservizio
        if siblings and len(sugg) < 3:
            scored = sorted(((len(ops & siblings), tg)
                             for tg, ops in tag_ops.items()
                             if tg not in seen and ops & siblings),
                            key=lambda x: (-x[0], x[1]))
            for cnt, tg in scored[:3 - len(sugg)]:
                sugg.append({"tag": tg, "kind": "service",
                             "reason": f"copre {cnt} endpoint di {svc.get(op) or 'questo microservizio'}"})
        if sugg:
            out[op] = sugg
    return out


def _greedy_cover(items, total):
    """Set-cover greedy. items: lista (name, set_ops, extra). total: denominatore
    per la %. Ritorna i passi finché aggiungono copertura."""
    remaining = set().union(*[s for _, s, _ in items]) if items else set()
    covered, steps, used = set(), [], set()
    while remaining:
        best = max(((nm, s, ex) for nm, s, ex in items if nm not in used),
                   key=lambda it: len(it[1] & remaining), default=None)
        if not best or not (best[1] & remaining):
            break
        nm, s, ex = best
        gain = len(s & remaining)
        used.add(nm)
        covered |= s
        remaining -= s
        steps.append({"name": nm, "gain": gain, "cumulative": len(covered),
                      "pct": round(len(covered) / total * 100, 1) if total else 0.0,
                      "extra": ex})
    return steps


def compute_guide(all_ops, runners, tags, op_cat, op_svc=None):
    """Raccomandazioni per massimizzare la copertura:
    - runner esistenti da lanciare (set-cover greedy sui runner)
    - nuovo runner = set minimo di tag (greedy sui tag, esclusi i control-tag)
    - gap: endpoint non raggiunti da NESSUN tag (candidati a nuovi scenari)."""
    op_svc = op_svc or {}
    def svcs_of(ops):
        return sorted({op_svc.get(o, "") for o in ops if op_svc.get(o)})
    total = len(all_ops)
    runner_items = [(r["name"], set(r.get("operation_ids", [])) & all_ops,
                     {"tags": sorted(r.get("include_tags", []))}) for r in runners]
    # Control-tag = tag che i runner ESCLUDONO deliberatamente (@ignore & co.):
    # non vanno raccomandati come @IncludeTags per un nuovo runner.
    control_tags = set()
    for r in runners:
        control_tags.update(r.get("exclude_tags", []))
    tag_items = [(t["tag"], set(t.get("operation_ids", [])) & all_ops,
                  {"services": svcs_of(set(t.get("operation_ids", [])) & all_ops)})
                 for t in tags if t["tag"] not in control_tags]

    runners_greedy = _greedy_cover(runner_items, total)
    tags_greedy = _greedy_cover(tag_items, total)

    covered_by_tags = set().union(*[s for _, s, _ in tag_items]) if tag_items else set()
    gap = sorted(all_ops - covered_by_tags)
    gap_phantom = [o for o in gap if op_cat.get(o) == "phantom"]
    gap_never = [o for o in gap if op_cat.get(o) == "never"]

    return {
        "total": total,
        "runners": runners_greedy,
        "runners_ceiling": runners_greedy[-1]["cumulative"] if runners_greedy else 0,
        "new_runner_tags": tags_greedy,
        "tags_ceiling": len(covered_by_tags),
        "gap_total": len(gap),
        "gap_phantom": gap_phantom,
        "gap_never": gap_never,
    }


# ---------------------------------------------------------------------------
# Trend (storia delle esecuzioni)
# ---------------------------------------------------------------------------

def read_baseline():
    """Prima riga di reports/trend-baseline.txt = timestamp minimo per il trend."""
    p = REPORTS_DIR / "trend-baseline.txt"
    if p.exists():
        first = p.read_text().strip().split("\n")[0].strip()
        if first and not first.startswith("#"):
            return first
    return ""


def build_trend(suite, all_runs=False):
    """Serie storica: un punto per ogni run completo (tag accoppiato allo
    statico più recente che lo precede). Default: ultimo run del giorno."""
    if not REPORTS_DIR.exists():
        return []
    baseline = read_baseline()

    tag_dirs = sorted(d for d in REPORTS_DIR.iterdir()
                      if d.is_dir() and d.name.endswith(f"_{suite}_tags"))
    static_dirs = sorted(d for d in REPORTS_DIR.iterdir()
                         if d.is_dir() and d.name.endswith(f"_{suite}"))

    candidates = []
    for td in tag_dirs:
        ts = td.name[:15]
        if baseline and ts < baseline:
            continue
        tj = td / f"tag-coverage-{suite}.json"
        if not tj.exists():
            continue
        # pairing: statico più recente con timestamp <= al run tag (NON filtrato da baseline)
        sj = None
        for d in static_dirs:
            if d.name[:15] <= ts and (d / f"coverage-{suite}.json").exists():
                sj = d / f"coverage-{suite}.json"
        if sj:
            candidates.append((ts, sj, tj))

    if not all_runs:  # ultimo run per giorno
        by_day = {}
        for ts, sj, tj in candidates:
            by_day[ts[:8]] = (ts, sj, tj)
        candidates = [by_day[k] for k in sorted(by_day)]

    points = []
    for ts, sj, tj in candidates:
        cov = json.load(open(sj))
        tag = json.load(open(tj))
        rows = [r for r in cov if r.get("operation_id")]
        all_ops = set(r["operation_id"] for r in rows)
        static_cov = set(r["operation_id"] for r in rows if r.get("covered"))
        reach = set()
        for r in tag.get("runners", []):
            reach.update(r.get("operation_ids", []))
        reach &= all_ops

        total = len(all_ops)
        real = len(reach)
        phantom = len(static_cov - reach)
        never = total - len(static_cov | reach)
        depth = tag.get("endpoint_depth", {})
        depth1 = sum(1 for op, dv in depth.items() if dv == 1 and op in all_ops)
        san = tag.get("sanity", {})
        points.append({
            "ts": ts,
            "date": f"{ts[6:8]}/{ts[4:6]}/{ts[0:4]}",
            "time": f"{ts[9:11]}:{ts[11:13]}",
            "total": total,
            "real": real,
            "phantom": phantom,
            "never": never,
            "static": len(static_cov),
            "real_pct": round(real / total * 100, 1) if total else 0,
            "static_pct": round(len(static_cov) / total * 100, 1) if total else 0,
            "depth1": depth1,
            "unmatched_pct": san.get("metrics", {}).get("unmatched_pct"),
            "sanity_passed": san.get("passed") if san else None,
        })
    return points


# --- SVG charts (inline, nessuna libreria) ---------------------------------

CHART_W, CHART_H, PAD_L, PAD_R, PAD_T, PAD_B = 780, 220, 46, 16, 14, 28


def _xpos(i, n):
    span = CHART_W - PAD_L - PAD_R
    return PAD_L + (span / 2 if n == 1 else i * span / (n - 1))


def svg_kpi_line(points):
    """Linea % reale (verde) + % statico grezzo (grigio), scala 0-100."""
    n = len(points)
    h = CHART_H - PAD_T - PAD_B

    def y(pct):
        return PAD_T + h * (1 - pct / 100)

    grid = ""
    for g in (0, 25, 50, 75, 100):
        gy = y(g)
        grid += (f'<line x1="{PAD_L}" y1="{gy:.1f}" x2="{CHART_W-PAD_R}" y2="{gy:.1f}" class="grid"/>'
                 f'<text x="{PAD_L-6}" y="{gy+4:.1f}" class="axis" text-anchor="end">{g}%</text>')

    def series(key, cls):
        pts = " ".join(f"{_xpos(i,n):.1f},{y(p[key]):.1f}" for i, p in enumerate(points))
        line = f'<polyline points="{pts}" class="{cls}"/>' if n > 1 else ""
        dots = ""
        for i, p in enumerate(points):
            tip = (f"{p['date']} {p['time']} — reale {p['real']}/{p['total']} ({p['real_pct']}%) · "
                   f"statico {p['static']} ({p['static_pct']}%) · fantasma {p['phantom']} · mai {p['never']}")
            dots += (f'<circle cx="{_xpos(i,n):.1f}" cy="{y(p[key]):.1f}" r="4" class="{cls}-dot">'
                     f'<title>{tip}</title></circle>')
        return line + dots

    labels = ""
    for i, p in enumerate(points):
        labels += f'<text x="{_xpos(i,n):.1f}" y="{CHART_H-8}" class="axis" text-anchor="middle">{p["date"]}</text>'

    return (f'<svg viewBox="0 0 {CHART_W} {CHART_H}" class="chart">{grid}'
            f'{series("static_pct","ln-static")}{series("real_pct","ln-real")}{labels}</svg>')


def svg_stacked_area(points):
    """Area impilata assoluta: reale / fantasma / mai."""
    n = len(points)
    h = CHART_H - PAD_T - PAD_B
    max_total = max(p["total"] for p in points) or 1

    def y(v):
        return PAD_T + h * (1 - v / max_total)

    def boundary(key_sum):
        return [(_xpos(i, n), y(key_sum(p))) for i, p in enumerate(points)]

    b_real = boundary(lambda p: p["real"])
    b_phantom = boundary(lambda p: p["real"] + p["phantom"])
    b_total = boundary(lambda p: p["total"])
    base_y = y(0)

    def area(upper, lower_pts=None):
        fwd = " ".join(f"{x:.1f},{yy:.1f}" for x, yy in upper)
        if lower_pts is None:
            back = f"{upper[-1][0]:.1f},{base_y:.1f} {upper[0][0]:.1f},{base_y:.1f}"
        else:
            back = " ".join(f"{x:.1f},{yy:.1f}" for x, yy in reversed(lower_pts))
        return fwd + " " + back

    if n == 1:  # un solo punto: barre invece di aree
        x = _xpos(0, 1)
        p = points[0]
        bw = 60
        seg = ""
        y0 = base_y
        for key, cls in (("real", "a-real"), ("phantom", "a-phantom"), ("never", "a-never")):
            hh = h * p[key] / max_total
            seg += f'<rect x="{x-bw/2}" y="{y0-hh:.1f}" width="{bw}" height="{hh:.1f}" class="{cls}"><title>{key}: {p[key]}</title></rect>'
            y0 -= hh
        shapes = seg
    else:
        shapes = (f'<polygon points="{area(b_total, b_phantom)}" class="a-never"/>'
                  f'<polygon points="{area(b_phantom, b_real)}" class="a-phantom"/>'
                  f'<polygon points="{area(b_real)}" class="a-real"/>')

    grid = ""
    for frac in (0, 0.5, 1.0):
        v = max_total * frac
        gy = y(v)
        grid += (f'<line x1="{PAD_L}" y1="{gy:.1f}" x2="{CHART_W-PAD_R}" y2="{gy:.1f}" class="grid"/>'
                 f'<text x="{PAD_L-6}" y="{gy+4:.1f}" class="axis" text-anchor="end">{v:.0f}</text>')

    dots = ""
    for i, p in enumerate(points):
        tip = f"{p['date']} — reale {p['real']} · fantasma {p['phantom']} · mai {p['never']} · totale {p['total']}"
        dots += (f'<circle cx="{_xpos(i,n):.1f}" cy="{y(p["total"]):.1f}" r="4" class="ln-static-dot">'
                 f'<title>{tip}</title></circle>')

    labels = ""
    for i, p in enumerate(points):
        labels += f'<text x="{_xpos(i,n):.1f}" y="{CHART_H-8}" class="axis" text-anchor="middle">{p["date"]}</text>'

    return f'<svg viewBox="0 0 {CHART_W} {CHART_H}" class="chart">{grid}{shapes}{dots}{labels}</svg>'


def svg_sparkline(points):
    """Sparkline % step non matchati (salute della misura)."""
    vals = [p["unmatched_pct"] for p in points]
    if not any(v is not None for v in vals):
        return '<span class="axis">dato sanity non disponibile per questi run</span>'
    vals = [v if v is not None else 0 for v in vals]
    w, h = 220, 36
    vmax = max(max(vals), 30)  # scala almeno fino alla soglia FAIL
    n = len(vals)

    def x(i):
        return 4 + (w - 8) / 2 if n == 1 else 4 + i * (w - 8) / (n - 1)

    def y(v):
        return 4 + (h - 8) * (1 - v / vmax)

    thr_y = y(30)
    thr = f'<line x1="4" y1="{thr_y:.1f}" x2="{w-4}" y2="{thr_y:.1f}" class="spark-thr"/>'
    if n > 1:
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals))
        line = f'<polyline points="{pts}" class="spark-ln"/>'
    else:
        line = ""
    dots = "".join(
        f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="3" class="spark-dot"><title>{points[i]["date"]}: {v}% step non matchati</title></circle>'
        for i, v in enumerate(vals))
    return f'<svg viewBox="0 0 {w} {h}" class="spark">{thr}{line}{dots}</svg>'


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SCOPE — E2E Coverage Dashboard</title>
<style>
__CSS__
</style>
</head>
<body>
<header>
  <h1>SCOPE <span style="font-weight:400;color:var(--muted)">· Spec Coverage Probe E2E</span></h1>
  <div class="sub">Generato il __TIMESTAMP__ · pn-b2b-client · analisi statica + tracciamento tag Cucumber</div>
  <div class="screen-only">__SWITCHER__</div>
</header>
<main>
  __EXEC__
  <div class="note screen-only">
    <b>Come leggere.</b> La <b style="color:var(--real)">copertura reale</b> conta gli endpoint raggiungibili da uno scenario Cucumber eseguibile
    (catena tag &rarr; scenario &rarr; step &rarr; wrapper &rarr; metodo API). La <b style="color:var(--phantom)">copertura fantasma</b> è codice wrapper implementato
    ma che nessuno scenario esercita: lo statico grezzo la conta erroneamente come coperta, <b>sovrastimando</b>. Gli endpoint
    <b style="color:var(--never)">mai implementati</b> non hanno né wrapper né test.
    <br><b>Scenari che lo invocano</b> = numero di scenari Cucumber distinti che chiamano l'endpoint: distingue "toccato una volta" da "esercitato spesso".
    <b>Attenzione:</b> misura l'<i>invocazione</i>, non la <i>verifica</i> — non dice se lo scenario fa asserzioni significative sulla risposta
    (un endpoint chiamato solo nel setup conta comunque).
    <br><b>Drill-down:</b> clicca su un runner o un tag per vedere quali endpoint copre e quali microservizi coinvolge.
    <br>📖 Per approfondire come vengono calcolati i numeri: <a class="faq-link" href="#faq">vai alla FAQ ↓</a>
  </div>
  <div class="screen-only">__SUITES__</div>
</main>
<section class="faq screen-only" id="faq">
  <h2>FAQ — come leggere i numeri di SCOPE</h2>

  <details>
    <summary>Il tab Endpoint mi dice che un endpoint è "invocato da 163 scenari". Come viene fatto questo calcolo?</summary>
    <div class="faq-body">
      <p>Esempio reale: <code>GET /service-desk/notifications</code> (operationId <code>searchNotificationsFromTaxId</code>), 163 scenari.</p>
      <p>SCOPE percorre staticamente questa catena per ogni scenario Cucumber:</p>
      <span class="chain">scenario (.feature) → step → step definition (@Given/@When/@Then) → metodo wrapper/service → metodo del client generato → operationId</span>
      <p>Il numero è il conteggio di <b>scenari distinti</b> la cui catena arriva a quell'operationId. In dettaglio:</p>
      <ul>
        <li><b>Ogni step dello scenario</b> viene confrontato con le step definition Java (le Cucumber expression come <code>{string}</code> vengono convertite in regex). Lo step matchato contribuisce con gli endpoint che il suo metodo Java raggiunge, risolti attraverso più livelli di indirezione (fino a 3 "hop": step → service → impl → client generato).</li>
        <li><b>Deduplica per scenario</b>: se uno scenario chiama lo stesso endpoint in 5 step diversi, conta 1. I 163 sono scenari, non chiamate.</li>
        <li><b>Endpoint versionati</b>: se lo scenario dichiara una versione (es. <code>versione "V24"</code>), l'invocazione è attribuita solo a quella versione; gli step senza versione ereditano quella dichiarata negli altri step dello stesso scenario; senza alcuna dichiarazione, l'attribuzione va a tutta la famiglia di versioni (conservativo).</li>
      </ul>
      <p>Perché 163 scenari da feature anche "lontane" dal service desk (es. flussi RIM, DigitalSend)? Perché il conteggio include <b>tutti</b> gli scenari che arrivano all'endpoint, anche quando la chiamata avviene in uno step di <i>verifica o setup</i> condiviso tra molti flussi — non solo nei test "dedicati" all'endpoint. Inoltre, quando uno step non è tracciabile con precisione fino al singolo metodo, SCOPE attribuisce in modo conservativo gli endpoint risolvibili dal file della step definition (possibile sovrastima).</p>
      <p><b>Per verificare</b>: clicca sul numero → si apre l'elenco esatto dei 163 scenari con file:riga e link per aprirli nell'IDE. ⚠️ Ricorda: il numero misura l'<b>invocazione</b>, non la <b>verifica</b> — non dice se gli scenari fanno asserzioni sulla risposta.</p>
    </div>
  </details>

  <details>
    <summary>Un endpoint appare tra i "fantasma". Perché? Cosa significa in termini di codice e di test?</summary>
    <div class="faq-body">
      <p><b>Definizione</b>: un endpoint è "fantasma" quando il <b>codice di supporto esiste</b> (il grep statico trova il metodo del client generato invocato da un wrapper, es. in <code>PnWebhookB2bExternalClientImpl</code>) ma <b>nessuno scenario Cucumber risulta raggiungerlo</b> attraverso la catena tag → scenario → step → wrapper.</p>
      <p>In termini pratici: <i>qualcuno ha scritto la "porta" (il metodo wrapper, pronto all'uso) ma nessun test la attraversa</i>. Sono i candidati più economici per aumentare la copertura: il client c'è già, manca solo lo scenario.</p>
      <p><b>Ma attenzione: può essere un falso fantasma.</b> Caso reale istruttivo — <code>DELETE /delivery-progresses/v2.4/streams/{streamId}</code> (operationId <code>removeEventStreamV24</code>) appariva fantasma mentre la versione V2.3 risultava coperta. Indagando: i wrapper V2.4+ dichiarano <code>throws RestClientException</code> (V2.3 no) e il parser non estraeva i metodi con clausola <code>throws</code> — la catena si spezzava lì. Corretto il parser, l'endpoint è risultato <b>reale con 127 scenari</b> (e su SEND sono "riapparsi" 57 endpoint reali in un colpo).</p>
      <p><b>Come distinguere un fantasma vero da un artefatto di misura:</b></p>
      <ul>
        <li>Guarda la <b>famiglia di versioni</b>: se V2.3 è coperta e V2.4+ no, con scenari che dichiarano "V24" nei .feature, è sospetto un problema di tracciamento;</li>
        <li>Cerca il pattern nei .feature: se esistono step che chiaramente esercitano quell'endpoint, segnala il caso — il parser potrebbe non risolvere la catena (indirezioni &gt; 3 livelli, reflection, naming particolare);</li>
        <li>Se non trovi nulla nei .feature, il fantasma è <span class="hl">vero</span>: wrapper scritto (magari in previsione, o usato solo da setup interni) ma mai esercitato da un test.</li>
      </ul>
      <p>I sanity check di SCOPE aiutano: un salto anomalo degli endpoint reali tra due run genera un WARN, e i "fantasmi a famiglia asimmetrica" sono il primo posto dove guardare.</p>
    </div>
  </details>
</section>
<footer>
  Service Line Quality Assurance &amp; Operations - 2026
</footer>
<script>
__JS__
</script>
</body>
</html>
"""


def _crit_w(cls, crit):
    return crit.get("classes", {}).get(cls, 1)


def executive_suite_data(s, crit):
    """Metriche sintetiche per una suite (one-pager). Tutto derivato dai dati
    già riconciliati: KPI, pesata, trend, prossime mosse, rischi."""
    eps = s["endpoints"]
    total = len(eps)
    real = sum(1 for e in eps if e["category"] == "real")
    phantom = sum(1 for e in eps if e["category"] == "phantom")
    never = sum(1 for e in eps if e["category"] == "never")
    real_pct = real / total * 100 if total else 0
    wAll = wReal = 0
    for e in eps:
        w = _crit_w(e.get("crit", "standard"), crit)
        wAll += w
        if e["category"] == "real":
            wReal += w
    wpct = wReal / wAll * 100 if wAll else 0

    tr = s.get("trend") or []
    tdelta = round(tr[-1]["real_pct"] - tr[0]["real_pct"], 1) if len(tr) >= 2 else None
    tfrom = tr[0]["date"] if tr else None

    g = s.get("guide") or {}
    runs = g.get("runners") or []
    best_runner = (runs[0]["name"], runs[0]["pct"]) if runs else None

    # endpoint non coperti a maggior guadagno PESATO (il "fai questo per primo")
    unc = [e for e in eps if e["category"] in ("never", "phantom")]
    unc.sort(key=lambda e: (-_crit_w(e.get("crit", "standard"), crit), e["operation_id"]))
    top_unc = [{
        "op": e["operation_id"], "cat": e["category"], "svc": e.get("service", ""),
        "crit": e.get("crit", "standard"),
        "dw": round(_crit_w(e.get("crit", "standard"), crit) / wAll * 100, 2) if wAll else 0,
    } for e in unc[:4]]

    # spec interamente scoperte (0 reali, ≥1 endpoint): rischio di superficie morta
    byspec = {}
    for e in eps:
        k = e.get("spec_name", "?")
        r, t, svc = byspec.get(k, (0, 0, ""))
        byspec[k] = (r + (1 if e["category"] == "real" else 0), t + 1, e.get("service", "") or svc)
    dead = sorted([(svc, k, t) for k, (r, t, svc) in byspec.items() if r == 0 and t > 0],
                  key=lambda x: -x[2])[:4]

    exec_active = s.get("exec_active", False)
    exec_ok = s.get("exec_ok", 0)
    exec_ko = s.get("exec_ko", 0)
    exec_pct = exec_ok / real * 100 if (exec_active and real) else 0

    return {
        "suite": s["suite"], "label": "SEND" if s["suite"] == "send" else "Interop",
        "total": total, "real": real, "real_pct": real_pct, "wpct": wpct,
        "phantom": phantom, "never": never,
        "tdelta": tdelta, "tfrom": tfrom,
        "best_runner": best_runner, "top_unc": top_unc, "dead_specs": dead,
        "exec_active": exec_active, "exec_ok": exec_ok, "exec_ko": exec_ko, "exec_pct": exec_pct,
    }


def render_executive(suites, crit):
    """Vista 'Sintesi' (one-pager) in cima al dashboard. In stampa è l'unica
    visibile (@media print) → Cmd+P produce un PDF one-pager pulito."""
    cards = ""
    for s in suites:
        if not s.get("has_tag"):
            continue
        d = executive_suite_data(s, crit)
        trend = ""
        if d["tdelta"] is not None:
            arrow = "▲" if d["tdelta"] > 0 else ("▼" if d["tdelta"] < 0 else "▬")
            col = "var(--real)" if d["tdelta"] > 0 else ("var(--never)" if d["tdelta"] < 0 else "var(--muted)")
            trend = f"<span style='color:{col}'>{arrow} {d['tdelta']:+.1f} pt</span> <span class='detail-sub'>dal {d['tfrom']}</span>"
        else:
            trend = "<span class='detail-sub'>trend: dati insufficienti</span>"

        moves = ""
        if d["best_runner"]:
            moves += f"<li>Lancia il runner <b>{d['best_runner'][0]}</b> → copri {d['best_runner'][1]:.1f}% degli endpoint</li>"
        if d["top_unc"]:
            items = "".join(
                f"<li><code>{u['op']}</code> <span class='detail-sub'>({u['svc']}, {u['crit']})</span> "
                f"→ <b style='color:var(--real)'>+{u['dw']:.2f} pt</b> pesata</li>"
                for u in d["top_unc"])
            moves += f"<li>Implementa per primi gli endpoint a maggior peso:<ul>{items}</ul></li>"

        risks = ""
        risk_items = ""
        if d.get("exec_active") and d["exec_ko"]:
            risk_items += (f"<li><b>{d['exec_ko']}</b> endpoint reali <b style='color:var(--never)'>rossi</b> "
                           f"all'ultima esecuzione (coperti ma falliti)</li>")
        if d["dead_specs"]:
            risk_items += "".join(f"<li><b>{svc}</b> / {spec} — <b>{t}</b> endpoint, 0 testati</li>"
                                  for svc, spec, t in d["dead_specs"])
        if risk_items:
            risks = f"<div class='exec-h'>Rischi</div><ul>{risk_items}</ul>"

        exec_kpi = ""
        if d.get("exec_active"):
            exec_kpi = (f"<div class=\"exec-kpi\" title=\"Endpoint reali con ultimo esito OK nella finestra. eseguito ≠ asserito.\">"
                        f"<div class=\"exec-v\" style=\"color:var(--real)\">{d['exec_pct']:.1f}%</div>"
                        f"<div class=\"exec-k\">Eseguita ✓</div></div>")

        cards += f"""
        <div class="exec-card">
          <div class="exec-suite">{d['label']} <span class="detail-sub">{d['total']} endpoint</span></div>
          <div class="exec-kpis">
            <div class="exec-kpi"><div class="exec-v" style="color:var(--real)">{d['real_pct']:.1f}%</div><div class="exec-k">Reale</div></div>
            <div class="exec-kpi"><div class="exec-v" style="color:var(--accent)">{d['wpct']:.1f}%</div><div class="exec-k">Pesata ★</div></div>
            {exec_kpi}
            <div class="exec-kpi"><div class="exec-v" style="color:var(--phantom)">{d['phantom']}</div><div class="exec-k">Fantasma</div></div>
            <div class="exec-kpi"><div class="exec-v" style="color:var(--never)">{d['never']}</div><div class="exec-k">Mai impl.</div></div>
          </div>
          <div class="exec-trend">{trend}</div>
          <div class="exec-h">Prossime mosse</div><ul class="exec-moves">{moves or '<li>—</li>'}</ul>
          {risks}
        </div>"""

    return f"""
    <section class="exec">
      <div class="exec-title">📋 Sintesi esecutiva</div>
      <div class="exec-caveat">La copertura misura gli endpoint <b>invocati</b> da scenari eseguibili
        (catena tag→scenario→step→API), <b>non ancora la verifica</b> degli esiti. È un limite superiore
        tracciabile, non una garanzia di correttezza dei test.</div>
      <div class="exec-grid">{cards}</div>
    </section>"""


def render_guide(s):
    """Tab Guida: cosa lanciare per massimizzare la copertura (greedy set-cover)."""
    suite = s["suite"]
    g = s.get("guide")
    if not g:
        return ""
    total = g["total"]

    def steps_table(steps, label):
        if not steps:
            return '<div class="guide-empty">Nessun dato disponibile.</div>'
        is_tags = label.lower().startswith("tag")
        rows = ""
        for i, st in enumerate(steps, 1):
            tags = st.get("extra", {}).get("tags", [])
            tags_html = ("<span class='guide-tags'>" + ", ".join(tags[:6])
                         + (f" +{len(tags)-6}" if len(tags) > 6 else "") + "</span>") if tags else ""
            svcs = st.get("extra", {}).get("services", [])
            svc_cell = (f"<td><span class='svc-tags'>{', '.join(svcs[:4])}"
                        + (f" <span class='detail-sub'>+{len(svcs)-4}</span>" if len(svcs) > 4 else "")
                        + "</span></td>") if is_tags else ""
            rows += (f"<tr><td>{i}</td><td><code>{st['name']}</code> {tags_html}</td>"
                     f"<td style='text-align:right'>+{st['gain']}</td>"
                     f"<td style='text-align:right'>{st['cumulative']}/{total} "
                     f"<span class='detail-sub'>({st['pct']}%)</span></td>{svc_cell}</tr>")
        svc_th = "<th title='Microservizi coperti da questo tag'>Microservizi</th>" if is_tags else ""
        label_tip = ("Tag da includere nel nuovo runner, in ordine di scelta dell'algoritmo greedy"
                     if is_tags else "Runner da lanciare, in ordine: ad ogni passo quello che aggiunge più endpoint nuovi")
        return (f"<div class='tablewrap'><table><thead><tr>"
                f"<th title='Ordine di scelta (greedy set-cover)'>#</th><th title=\"{label_tip}\">{label}</th>"
                f"<th style='text-align:right' title='Endpoint NUOVI che questo passo aggiunge (non già coperti dai precedenti)'>+Nuovi</th>"
                f"<th style='text-align:right' title='Endpoint cumulativi coperti dopo questo passo, sul totale'>Cumulativo</th>{svc_th}"
                f"</tr></thead><tbody>{rows}</tbody></table></div>")

    # set minimo di tag come @IncludeTags pronto da copiare
    tag_names = [st["name"] for st in g["new_runner_tags"]]
    include = "@IncludeTags({" + ", ".join(f'"{t}"' for t in tag_names) + "})"

    # command line per lanciare i runner consigliati (surefire JUnit5).
    # Interop ha un pom proprio nel sotto-modulo.
    runner_names = [st["name"] for st in g["runners"]]
    pom_flag = " -f interop-qa-tests/pom.xml" if suite == "interop" else ""
    run_cmd = (f"mvn{pom_flag} test -Dtest=" + ",".join(runner_names)) if runner_names \
        else "# nessun runner disponibile"

    def gap_chips(ops, cls):
        return "".join(f"<span class='ep-chip {cls}'>{o}</span>" for o in ops)

    return f"""
      <div class="pane" data-pane="guide">
        <div class="guide-intro">Raccomandazioni per <b>massimizzare la copertura reale</b> con il minimo
          sforzo (algoritmo greedy set-cover: a ogni passo si sceglie ciò che aggiunge più endpoint nuovi).</div>

        <div class="detail-section-title">1 · Runner esistenti da lanciare
          <span class="detail-sub">— combinazione minima per arrivare a {g['runners_ceiling']}/{total} endpoint</span></div>
        {steps_table(g['runners'], 'Runner (in ordine di lancio)')}
        <div class="guide-copy">
          <code id="run-{suite}">{run_cmd}</code>
          <button class="back-link" onclick="copyText('run-{suite}')">📋 Copia</button>
        </div>

        <div class="detail-section-title">2 · Nuovo runner: set minimo di tag
          <span class="detail-sub">— {len(tag_names)} tag → {g['tags_ceiling']}/{total} endpoint</span></div>
        {steps_table(g['new_runner_tags'], 'Tag da includere')}
        <div class="guide-copy">
          <code id="inc-{suite}">{include}</code>
          <button class="back-link" onclick="copyText('inc-{suite}')">📋 Copia</button>
        </div>

        <div class="detail-section-title">3 · Gap: {g['gap_total']} endpoint non raggiunti da alcun tag
          <span class="detail-sub">— candidati a nuovi scenari</span></div>
        <div class="guide-gap-note"><b>{len(g['gap_phantom'])}</b> hanno già il wrapper (fantasma: basta scrivere lo scenario)
          · <b>{len(g['gap_never'])}</b> non hanno né codice né test (mai implementati)</div>
        <div class="detail-section-title" style="margin-top:10px">Fantasma ({len(g['gap_phantom'])})</div>
        <div class="ep-list">{gap_chips(g['gap_phantom'], 'phantom')}</div>
        <div class="detail-section-title">Mai implementati ({len(g['gap_never'])})</div>
        <div class="ep-list">{gap_chips(g['gap_never'], '')}</div>
      </div>""" if s["has_tag"] else ""


def render_suite(s):
    suite = s["suite"]
    total = s["total"]
    real, phantom, never = s["real"], s["phantom"], s["never"]
    static = s["static_covered"]

    real_w = real / total * 100 if total else 0
    phantom_w = phantom / total * 100 if total else 0
    never_w = never / total * 100 if total else 0

    label = "SEND" if suite == "send" else "Interop"
    tag_note = "" if s["has_tag"] else '<span class="badge">solo statico — tag non disponibile</span>'

    # Card esecuzione: solo se ci sono esiti di run nella finestra
    exec_cards = ""
    if s.get("exec_active"):
        e_ok, e_ko = s.get("exec_ok", 0), s.get("exec_ko", 0)
        exec_pct = e_ok / real * 100 if real else 0
        red_extra = "⚠ tutti i loro scenari falliti" if e_ko else "nessun endpoint rosso"
        exec_cards = f"""
      <div class="card execok" title="Endpoint REALI il cui ultimo esito noto (finestra esecuzione) è OK: uno scenario che li invoca è girato ed è passato. ATTENZIONE: passato ≠ asserito — lo scenario è verde, non è detto che verifichi la risposta dell'endpoint.">
        <div class="label">Reale eseguita ✓</div>
        <div class="value">{exec_pct:.1f}%</div>
        <div class="pct">{e_ok}/{real} reali verdi nell'ultima finestra</div>
      </div>
      <div class="card execko" title="Endpoint REALI invocati SOLO da scenari falliti nella finestra: coperti sulla carta ma rossi all'esecuzione. Priorità di triage.">
        <div class="label">Coperti ma rossi</div>
        <div class="value">{e_ko}</div>
        <div class="pct">{red_extra}</div>
      </div>"""

    # Cards
    cards = f"""
    <div class="cards">
      <div class="card weighted" title="KPI primario: copertura reale ponderata per criticità (criticality.yaml). Se coincide con la reale semplice, i pesi sono uniformi. Pesata < semplice = si testano bene gli endpoint sbagliati.">
        <div class="label">Reale pesata ★</div>
        <div class="value" id="wcov-{suite}">—</div>
        <div class="pct" id="wcov-note-{suite}"></div>
      </div>{exec_cards}
      <div class="card real" title="Endpoint REALI: esiste almeno uno scenario eseguibile che li invoca (invocato ≠ verificato: non analizziamo le asserzioni). È la copertura onesta, non pesata.">
        <div class="label">Copertura reale</div>
        <div class="value">{real/total*100:.1f}%</div>
        <div class="pct">{real}/{total} endpoint testati da scenari</div>
      </div>
      <div class="card phantom" title="Endpoint FANTASMA: esiste un wrapper nel codice client ma nessuno scenario lo raggiunge. Codice di test che esiste ma non viene mai esercitato — candidati a cleanup o a nuovi scenari.">
        <div class="label">Copertura fantasma</div>
        <div class="value">{phantom/total*100:.1f}%</div>
        <div class="pct">{phantom}/{total} codice ma 0 scenari</div>
      </div>
      <div class="card never" title="Endpoint MAI implementati: né wrapper nel client né scenario. Dichiarati nelle OpenAPI spec ma del tutto assenti dai test.">
        <div class="label">Mai implementati</div>
        <div class="value">{never/total*100:.1f}%</div>
        <div class="pct">{never}/{total} né codice né test</div>
      </div>
      <div class="card static" title="Copertura STATICA grezza: endpoint con un wrapper invocato da qualche parte nel codice, senza verificare che uno scenario eseguibile ci arrivi. Sovrastima (include i fantasma): mai citarla da sola.">
        <div class="label">Statico grezzo</div>
        <div class="value">{static/total*100:.1f}%</div>
        <div class="pct">{static}/{total} (sovrastima)</div>
      </div>
    </div>
    <div class="crit-banner" id="crit-banner-{suite}">
      ✏️ <span id="crit-count-{suite}"></span> — le modifiche vivono solo in questo browser finché non esporti e salvi il file in <code>data/criticality.yaml</code>
      <button onclick="exportCritYaml()">⬇ Esporta criticality.yaml</button>
      <button class="secondary" onclick="resetCrit('{suite}')">Azzera modifiche</button>
    </div>"""

    # Stacked bar
    stackbar = f"""
    <div class="stackbar">
      <div class="seg-real" style="width:{real_w}%">{f'{real_w:.0f}%' if real_w > 6 else ''}</div>
      <div class="seg-phantom" style="width:{phantom_w}%">{f'{phantom_w:.0f}%' if phantom_w > 6 else ''}</div>
      <div class="seg-never" style="width:{never_w}%">{f'{never_w:.0f}%' if never_w > 6 else ''}</div>
    </div>
    <div class="legend-row">
      <span><span class="dot real"></span> Reale {real}</span>
      <span><span class="dot phantom"></span> Fantasma {phantom}</span>
      <span><span class="dot never"></span> Mai impl. {never}</span>
    </div>"""

    # Build endpoint-to-category map for breakdown bars
    ep_cats = {}
    ep_svc = {}
    for ep in s["endpoints"]:
        ep_cats[ep["operation_id"]] = ep["category"]
        ep_svc[ep["operation_id"]] = ep.get("service", "")

    def services_of(ops):
        return sorted({ep_svc.get(o, "") for o in ops if ep_svc.get(o)})

    # Runner rows with breakdown bar and click handler
    runner_rows = ""
    for r in s["runners"][:60]:
        cov = r.get("endpoints_covered", 0)
        p = r.get("coverage_pct", 0)
        tags_str = ", ".join(r.get("include_tags", [])[:6])
        if len(r.get("include_tags", [])) > 6:
            tags_str += " …"
        name = r['name']
        ops = r.get("operation_ids", [])
        # Compute breakdown: real / phantom / never among covered ops
        r_real = sum(1 for o in ops if ep_cats.get(o) == "real")
        r_phantom = sum(1 for o in ops if ep_cats.get(o) == "phantom")
        r_never = sum(1 for o in ops if ep_cats.get(o) == "never")
        r_total = r_real + r_phantom + r_never
        pct_r = r_real / r_total * 100 if r_total else 0
        pct_p = r_phantom / r_total * 100 if r_total else 0
        pct_n = r_never / r_total * 100 if r_total else 0
        runner_rows += f"""<tr class="clickable" data-name="{name}" onclick="drillDown('{suite}','runner','{name}')">
          <td>{name}</td>
          <td>{r.get('scenarios',0)}</td>
          <td><div class="bar-cell">
            <div class="breakdown-bar">
              <div class="bb-real" style="width:{pct_r}%"></div>
              <div class="bb-phantom" style="width:{pct_p}%"></div>
              <div class="bb-never" style="width:{pct_n}%"></div>
            </div>
            <span class="count">{cov}/{total} ({p:.1f}%)</span>
          </div></td>
          <td class="tag-list">{tags_str}</td>
        </tr>"""

    # Tag rows with breakdown bar and click handler
    tag_rows = ""
    for t in s["tags"][:80]:
        cov = t.get("endpoints_covered", 0)
        p = t.get("coverage_pct", 0)
        tname = t["tag"]
        ops = t.get("operation_ids", [])
        t_real = sum(1 for o in ops if ep_cats.get(o) == "real")
        t_phantom = sum(1 for o in ops if ep_cats.get(o) == "phantom")
        t_never = sum(1 for o in ops if ep_cats.get(o) == "never")
        t_total = t_real + t_phantom + t_never
        pct_r = t_real / t_total * 100 if t_total else 0
        pct_p = t_phantom / t_total * 100 if t_total else 0
        pct_n = t_never / t_total * 100 if t_total else 0
        # Escape quotes in tag names for JS
        tname_js = tname.replace("'", "\\'").replace('"', '&quot;')
        svcs = services_of(ops)
        svcs_html = ("<span class='svc-tags'>" + ", ".join(svcs[:4])
                     + (f" <span class='detail-sub'>+{len(svcs)-4}</span>" if len(svcs) > 4 else "")
                     + "</span>") if svcs else "<span class='detail-sub'>—</span>"
        tag_rows += f"""<tr class="clickable" data-name="{tname}" onclick="openTagDetail('{suite}','{tname_js}')">
          <td><code>{tname}</code></td>
          <td>{t.get('scenarios',0)}</td>
          <td><div class="bar-cell">
            <div class="breakdown-bar">
              <div class="bb-real" style="width:{pct_r}%"></div>
              <div class="bb-phantom" style="width:{pct_p}%"></div>
              <div class="bb-never" style="width:{pct_n}%"></div>
            </div>
            <span class="count">{cov}/{total} ({p:.1f}%)</span>
          </div></td>
          <td>{svcs_html}</td>
        </tr>"""

    has_tag_tabs = s["has_tag"]
    runner_tab = '<div class="tab" data-tab="runners" title="Runner JUnit5 (suite eseguibili): quali tag includono/escludono e quanti endpoint reali coprono. La griglia di esecuzione." onclick="switchTab(\'%s\',\'runners\')">Runner</div>' % suite if has_tag_tabs else ""
    tag_tab = '<div class="tab" data-tab="tags" title="Tag Cucumber (master→detail): per ogni tag gli scenari, gli endpoint che invoca e i microservizi toccati. L\'unità con cui i runner selezionano i test." onclick="switchTab(\'%s\',\'tags\')">Tag</div>' % suite if has_tag_tabs else ""
    scenari_tab = '<div class="tab" data-tab="scenari" title="Scenari (reverse-lookup): per ogni scenario gli endpoint che tocca, i tag che lo includono e i runner che lo eseguono. Evidenzia gli orfani (nessun runner li lancia)." onclick="switchTab(\'%s\',\'scenari\');renderScenari(\'%s\')">Scenari</div>' % (suite, suite) if has_tag_tabs else ""
    guide_tab = '<div class="tab" data-tab="guide" title="Guida operativa: suggerimenti greedy su quali runner/tag aggiungere per massimizzare la copertura reale pesata dei gap." onclick="switchTab(\'%s\',\'guide\')">🎯 Guida</div>' % suite if has_tag_tabs else ""

    # Microservice tab: dropdown options sorted by endpoint count desc
    svc_counts = {}
    for ep in s["endpoints"]:
        svc = ep.get("service") or "(sconosciuto)"
        svc_counts[svc] = svc_counts.get(svc, 0) + 1
    svc_options = "".join(
        f'<option value="{svc}">{svc} ({n} endpoint)</option>'
        for svc, n in sorted(svc_counts.items(), key=lambda x: -x[1]))
    svc_tab = f'<div class="tab" data-tab="services" title="Microservizi: copertura reale aggregata per servizio. Dove concentrare lo sforzo a livello di sistema." onclick="switchTab(\'{suite}\',\'services\')">Microservizi</div>'

    # --- Trend pane ---
    trend = s.get("trend") or []
    if trend:
        trend_tab = f'<div class="tab" data-tab="trend" title="Trend storico: evoluzione della copertura reale e pesata nel tempo (1 punto = run tag+statico più recente per giorno). Una baseline opzionale esclude i run precedenti a una data impostata." onclick="switchTab(\'{suite}\',\'trend\')">Trend</div>'
        rows_html = ""
        prev = None
        for p in trend:
            def delta(key):
                if prev is None:
                    return '<span class="delta-flat">—</span>'
                d = p[key] - prev[key]
                if d > 0:
                    return f'<span class="delta-up">▲ +{d}</span>'
                if d < 0:
                    return f'<span class="delta-down">▼ {d}</span>'
                return '<span class="delta-flat">=</span>'
            sanity_badge = ('<span class="sanity-ok">✅</span>' if p["sanity_passed"]
                            else '<span class="sanity-fail">❌</span>' if p["sanity_passed"] is False
                            else '<span class="delta-flat">—</span>')
            rows_html += f"""<tr>
              <td>{p['date']} {p['time']}</td>
              <td><b style="color:var(--real)">{p['real']}</b> ({p['real_pct']}%)</td>
              <td>{delta('real')}</td>
              <td>{p['phantom']}</td>
              <td>{delta('phantom')}</td>
              <td>{p['never']}</td>
              <td>{p['total']}</td>
              <td>{p['depth1']}</td>
              <td style="text-align:center">{sanity_badge}</td>
            </tr>"""
            prev = p
        single_note = ("<div class='trend-note'>⏳ Un solo punto disponibile: il trend si popolerà con i prossimi run. "
                       "Una baseline opzionale in <code>reports/trend-baseline.txt</code> esclude dal trend i run "
                       "precedenti a una data impostata.</div>") if len(trend) == 1 else ""
        trend_pane = f"""
      <div class="pane" data-pane="trend">
        {single_note}
        <div class="chart-title">Copertura nel tempo <span class="sub">— linea verde = reale (%), tratteggio grigio = statico grezzo (%); il divario tra le due è la copertura fantasma</span></div>
        {svg_kpi_line(trend)}
        <div class="chart-title">Composizione assoluta <span class="sub">— verde = reale, giallo = fantasma, rosso = mai implementati; il bordo superiore è il totale endpoint dalle spec</span></div>
        {svg_stacked_area(trend)}
        <div class="chart-title">Salute della misura <span class="sub">— % step non matchati per run (linea rossa = soglia 30%): se sale, i punti non sono confrontabili</span></div>
        <div style="margin-bottom:18px">{svg_sparkline(trend)}</div>
        <div class="chart-title">Dettaglio run</div>
        <div class="tablewrap"><table>
          <thead><tr><th title="Data/ora del run (statico accoppiato al tag-coverage)">Run</th><th title="Copertura reale: endpoint invocati da uno scenario eseguibile, sul totale">Reale</th><th title="Variazione della reale rispetto al run precedente">Δ</th><th title="Endpoint con wrapper nel codice ma nessuno scenario li esercita">Fantasma</th><th title="Variazione dei fantasma rispetto al run precedente">Δ</th><th title="Endpoint senza né codice né test">Mai</th><th title="Totale endpoint nell'inventario delle spec">Totale</th><th title="Endpoint invocati da un solo scenario: copertura fragile">Invocati 1 volta</th><th title="Esito dei sanity check del run: ✓ misura affidabile, ✗ qualche segnale vitale fuori soglia">Sanity</th></tr></thead>
          <tbody>{rows_html}</tbody>
        </table></div>
      </div>"""
    else:
        trend_tab = ""
        trend_pane = ""

    # --- Tab Esecuzioni: elenco delle run ingerite ---
    run_summaries = s.get("run_summaries") or []
    if run_summaries:
        runs_tab = (f'<div class="tab" data-tab="runs" title="Report di esecuzione ingeriti '
                    f'(scope-runtime): quando, quanti scenari, esiti, copertura reale toccata." '
                    f'onclick="switchTab(\'{suite}\',\'runs\')">Esecuzioni</div>')
        rrows = ""
        for r in run_summaries:
            win = ('<span class="exec-badge ok" title="Dentro la finestra dello stato corrente">in finestra</span>'
                   if r["in_window"]
                   else '<span class="detail-sub" title="Fuori finestra: non contribuisce allo stato corrente">fuori finestra</span>')
            rrows += f"""<tr>
              <td>{r['date']}</td>
              <td class="opid">{r['file']}</td>
              <td style="text-align:right">{r['n_scen']}</td>
              <td style="text-align:right"><b style="color:var(--real)">{r['n_ok']}</b> · <b style="color:var(--never)">{r['n_ko']}</b> · {r['n_other']}</td>
              <td style="text-align:right">{r['cov_pct']}% <span class="detail-sub">({r['cov_n']})</span></td>
              <td>{win}</td>
            </tr>"""
        runs_pane = f"""
      <div class="pane" data-pane="runs">
        <div class="drilldown-hint">Report di esecuzione ingeriti da <code>runtime/inbox/{suite}/</code> via <code>scope-runtime</code>.
          Lo stato corrente usa solo le run <b>in finestra</b> (ultimi {(s.get('exec_meta') or {}).get('window_days', 0)} giorni); le altre restano in storia.</div>
        <div class="tablewrap"><table>
          <thead><tr>
            <th title="Data di esecuzione della run (dal timestamp degli scenari, o mtime del file)">Data</th>
            <th title="Nome del file droppato in inbox">File</th>
            <th style="text-align:right" title="Scenari nel report (esclusi i background)">Scenari</th>
            <th style="text-align:right" title="Esiti: OK · KO · incerti (skipped/undefined)">OK · KO · ?</th>
            <th style="text-align:right" title="Endpoint REALI esercitati da questa run, sul totale reali">Copertura reale</th>
            <th title="Se la run rientra nella finestra dello stato corrente">Finestra</th>
          </tr></thead>
          <tbody>{rrows}</tbody>
        </table></div>
      </div>"""
    else:
        runs_tab = ""
        runs_pane = ""

    svc_pane = f"""
      <div class="pane" data-pane="services">
        <div class="svc-header">
          <select id="svc-select-{suite}" onchange="renderService('{suite}')">{svc_options}</select>
          <div class="svc-summary" id="svc-summary-{suite}"></div>
        </div>
        <div class="tablewrap"><table>
          <thead><tr>
            <th onclick="sortSvc('{suite}','method')" title="Metodo HTTP dell'endpoint">Metodo</th>
            <th onclick="sortSvc('{suite}','path')" title="Path dell'endpoint nella spec OpenAPI">Path</th>
            <th onclick="sortSvc('{suite}','operation_id')" title="operationId della spec. Clicca per il dettaglio dell'endpoint">operationId</th>
            <th onclick="sortSvc('{suite}','category')" title="Reale = uno scenario lo invoca · Fantasma = wrapper ma nessuno scenario · Mai impl. = né codice né test">Stato</th>
            <th onclick="sortSvc('{suite}','depth')" style="text-align:right" title="Numero di scenari Cucumber distinti che CHIAMANO l'endpoint. Misura l'invocazione, non la verifica: non garantisce asserzioni sulla risposta">Scenari che lo invocano</th>
            <th title="Tag Cucumber i cui scenari raggiungono questo endpoint: lanciando uno di questi tag, l'endpoint viene invocato">Tag che lo raggiungono</th>
          </tr></thead>
          <tbody id="svc-tbody-{suite}"></tbody>
        </table></div>
      </div>"""

    runner_pane = f"""
      <div class="pane" data-pane="runners">
        <div class="drilldown-hint">Clicca su un runner per vedere gli endpoint che copre</div>
        <div class="tablewrap"><table>
          <thead><tr><th title="Classe runner JUnit5 (@Suite). Clicca una riga per filtrare gli endpoint che copre nel tab Endpoint">Runner</th><th title="Numero di scenari Cucumber eseguiti dal runner (tag @IncludeTags, meno @ExcludeTags)">Scenari</th><th title="Endpoint REALI coperti dagli scenari del runner, sul totale dell'inventario">Copertura</th><th title="Tag elencati in @IncludeTags del runner">Tag inclusi</th></tr></thead>
          <tbody id="runners-tbody-{suite}">{runner_rows}</tbody>
        </table></div>
      </div>""" if has_tag_tabs else ""

    tag_pane = f"""
      <div class="pane" data-pane="tags">
        <div id="tag-list-{suite}">
          <div class="drilldown-hint">Clicca su un tag per vederne gli scenari e gli endpoint che copre</div>
          <div class="tablewrap"><table>
            <thead><tr><th title="Tag Cucumber. Clicca per il dettaglio: scenari che lo portano + endpoint coperti raggruppati per microservizio">Tag</th><th title="Numero di scenari che portano questo tag">Scenari</th><th title="Endpoint REALI raggiunti dagli scenari del tag, sul totale">Endpoint coperti</th><th title="Microservizi toccati dagli endpoint coperti da questo tag (il nome del tag può essere fuorviante)">Microservizi</th></tr></thead>
            <tbody id="tags-tbody-{suite}">{tag_rows}</tbody>
          </table></div>
        </div>
        <div id="tag-detail-{suite}" style="display:none"></div>
      </div>""" if has_tag_tabs else ""

    # Banner sanity TC-ID: il TC-ID è la chiave di join coi report di esecuzione.
    # Scenari senza id non saranno mai joinabili; id duplicati danno join ambiguo.
    tcs = s.get("tc_id_sanity") or {}
    tc_alerts = []
    if tcs.get("without_id"):
        tc_alerts.append(
            f'<span class="tc-alert warn clickable" onclick="setScenTcFilter(\'{suite}\',\'noid\')" '
            f'title="Mostra solo questi scenari nella tabella">⚠ {tcs["without_id"]} scenari senza TC-ID '
            f'({tcs.get("without_id_pct", 0)}%) — non agganciabili ai report di esecuzione</span>')
    if tcs.get("duplicate_id_count"):
        tc_alerts.append(
            f'<span class="tc-alert err clickable" onclick="setScenTcFilter(\'{suite}\',\'dup\')" '
            f'title="Mostra solo questi scenari nella tabella">⛔ {tcs["duplicate_id_count"]} TC-ID duplicati '
            f'— join ambiguo coi report di esecuzione</span>')
    if tcs and not tc_alerts:
        tc_alerts.append(
            f'<span class="tc-alert ok">✓ tutti i {tcs.get("with_id", 0)} scenari hanno un TC-ID univoco</span>')
    tc_banner = ('<div class="tc-banner" title="Salute della chiave di join TC-ID '
                 '(identificatore [TC-…] nel titolo), preparatoria al merge degli esiti di run">'
                 + " ".join(tc_alerts) + "</div>") if tc_alerts else ""

    # Banner di contesto esecuzione: da quali run viene lo stato corrente.
    em = s.get("exec_meta") or {}
    if s.get("exec_active"):
        run_banner = (
            f'<div class="run-banner active" title="Stato corrente = ultimo esito per TC-ID '
            f'sulle run della finestra. eseguito ≠ asserito.">'
            f'📋 Esiti di esecuzione · {em.get("runs_in_window", 0)}/{em.get("runs_total", 0)} run '
            f'negli ultimi {em.get("window_days", 0)} giorni · '
            f'<b style="color:var(--real)">{s.get("exec_ok", 0)} reali verdi</b> · '
            f'<b style="color:var(--never)">{s.get("exec_ko", 0)} rossi</b> · '
            f'<span class="run-warn">⚠ report senza commit: drift col codice non verificabile</span></div>')
    elif em:
        run_banner = (
            f'<div class="run-banner stale" title="Nessuna run recente: la freschezza fa parte della salute del parco-test.">'
            f'📋 Nessuna run negli ultimi {em.get("window_days", 0)} giorni '
            f'(storia: {em.get("runs_total", 0)} run) → stato esecuzione non disponibile</div>')
    else:
        run_banner = (
            '<div class="run-banner none" title="Droppa un report Cucumber JSON in runtime/inbox/<suite>/ e lancia scope-runtime.">'
            '📋 Nessun report di esecuzione ingerito — livello esecuzione assente</div>')

    scenari_pane = f"""
      <div class="pane" data-pane="scenari">
        <div class="drilldown-hint">Ogni scenario: i tag che lo includono, gli endpoint che invoca, i runner che lo eseguono.
          Gli <b>orfani</b> hanno endpoint ma nessun runner li lancia → copertura illusoria.</div>
        {tc_banner}
        {run_banner}
        <div class="controls">
          <input type="search" id="scen-search-{suite}" placeholder="Cerca nome, file, tag o endpoint…" oninput="renderScenari('{suite}')">
          <label class="scen-orphan-toggle"><input type="checkbox" id="scen-orphan-{suite}" onchange="renderScenari('{suite}')"> solo orfani</label>
          <label class="scen-orphan-toggle"><input type="checkbox" id="scen-ko-{suite}" onchange="renderScenari('{suite}')"> solo KO</label>
          <span id="scen-count-{suite}" class="detail-sub"></span>
        </div>
        <div class="tablewrap"><table>
          <thead><tr>
            <th title="Nome dello scenario Cucumber e posizione nel .feature">Scenario</th>
            <th title="Esito dell'ultima esecuzione nota (finestra): ✓ OK, ✗ KO, ○ non eseguito di recente. eseguito ≠ asserito.">Esito</th>
            <th title="Tag che includono lo scenario. Clicca per il dettaglio del tag">Tag</th>
            <th style="text-align:right" title="Endpoint distinti invocati da questo scenario. Clicca per espanderli">Endpoint</th>
            <th title="Runner JUnit5 che eseguono lo scenario (match @IncludeTags meno @ExcludeTags). ⚠ orfano = nessuno">Runner che lo eseguono</th>
          </tr></thead>
          <tbody id="scenari-tbody-{suite}"></tbody>
        </table></div>
      </div>""" if has_tag_tabs else ""

    return f"""
  <div class="suite-section" data-suite="{suite}">
    <div class="suite-title">{label} <span class="badge">{total} endpoint</span> {tag_note}</div>
    {cards}
    {stackbar}
    <div class="tabs" id="tabs-{suite}">
      <div class="tab active" data-tab="endpoints" title="Endpoint (master→detail): l'inventario completo dalle OpenAPI spec, ogni endpoint classificato reale/fantasma/mai con drill-down su scenari, tag e runner che lo invocano." onclick="switchTab('{suite}','endpoints')">Endpoint (drill-down)</div>
      {svc_tab}
      {runner_tab}
      {tag_tab}
      {scenari_tab}
      {runs_tab}
      {guide_tab}
      {trend_tab}
    </div>
    <div id="panes-{suite}">
      <div class="pane active" data-pane="endpoints">
        <div id="ep-main-{suite}">
        <div class="filter-banner" id="filter-banner-{suite}" style="flex-wrap:wrap">
          <span id="filter-type-{suite}" class="filter-label"></span>
          <span id="filter-name-{suite}" class="filter-name"></span>
          <span id="filter-count-{suite}" class="filter-count"></span>
          <button class="filter-clear" onclick="clearFilter('{suite}')">✕ Rimuovi filtro</button>
          <div class="svc-breakdown" id="filter-svcs-{suite}"></div>
        </div>
        <div class="controls">
          <input type="search" id="search-{suite}" placeholder="Cerca path, operationId, spec…" oninput="updateTable('{suite}')">
          <div id="chips-{suite}" style="display:flex;gap:8px">
            <span class="chip real active" data-cat="real" onclick="toggleChip('{suite}',this)">Reale</span>
            <span class="chip phantom active" data-cat="phantom" onclick="toggleChip('{suite}',this)">Fantasma</span>
            <span class="chip never active" data-cat="never" onclick="toggleChip('{suite}',this)">Mai impl.</span>
          </div>
        </div>
        <div class="tablewrap"><table>
          <thead><tr>
            <th onclick="sortTable('{suite}','category')" title="Reale = invocato da uno scenario eseguibile · Fantasma = wrapper nel codice ma nessuno scenario lo esercita · Mai impl. = né codice né test. Clicca per ordinare">Categoria</th>
            <th onclick="sortTable('{suite}','exec')" title="Esito dell'ultima esecuzione nota (finestra): ✓ eseguita (almeno uno scenario passato), ✗ rossa (solo falliti), ○ non eseguita di recente. — se nessun report ingerito. eseguito ≠ asserito">Esecuzione</th>
            <th onclick="sortTable('{suite}','method')" title="Metodo HTTP dell'endpoint (GET/POST/PUT/DELETE/PATCH)">Metodo</th>
            <th onclick="sortTable('{suite}','path')" title="Path dell'endpoint come dichiarato nella spec OpenAPI">Path</th>
            <th onclick="sortTable('{suite}','operation_id')" title="operationId della spec = nome del metodo del client generato. Clicca su un valore per aprire il dettaglio dell'endpoint">operationId</th>
            <th onclick="sortTable('{suite}','spec_name')" title="File OpenAPI di provenienza (senza estensione), dichiarato nel pom.xml">Spec</th>
            <th onclick="sortTable('{suite}','visibility')" title="Visibilità (euristica sui nomi): public = API esposta a PA/consumatori · internal = API inter-microservizio">Vis.</th>
            <th onclick="sortTable('{suite}','depth')" style="text-align:right" title="Numero di scenari Cucumber distinti che CHIAMANO l'endpoint. Misura l'invocazione, non la verifica: non garantisce asserzioni sulla risposta">Scenari che lo invocano</th>
            <th title="Classe di criticità per la copertura pesata (criticality.yaml). Modificabile: le modifiche si esportano col bottone nel banner">Criticità</th>
          </tr></thead>
          <tbody id="tbody-{suite}"></tbody>
        </table></div>
        </div>
        <div id="ep-detail-{suite}" style="display:none"></div>
      </div>
      {svc_pane}
      {runner_pane}
      {tag_pane}
      {scenari_pane}
      {runs_pane}
      {render_guide(s)}
      {trend_pane}
    </div>
  </div>"""


def generate_html(suites, crit=None):
    crit = crit or dict(DEFAULT_CRIT)
    suites_html = "\n".join(render_suite(s) for s in suites)
    # Include runners and tags with operation_ids in the DATA object
    data = {}
    for s in suites:
        data[s["suite"]] = {
            "endpoints": s["endpoints"],
            "runners": [{
                "name": r.get("name", ""),
                "operation_ids": r.get("operation_ids", []),
            } for r in s["runners"]],
            "tags": [{
                "tag": t.get("tag", ""),
                "scenarios": t.get("scenarios", 0),
                "operation_ids": t.get("operation_ids", []),
            } for t in s["tags"]],
            "scen": s["scenarios_index"],
            "epScen": s["endpoint_scenarios"],
            "suggest": s.get("suggestions", {}),
            "root": s["repo_root"],
            "execActive": s.get("exec_active", False),
            "execMeta": s.get("exec_meta", {}),
        }
    # Suite switcher (only if more than one suite)
    if len(suites) > 1:
        buttons = "".join(
            f'<button data-suite="{s["suite"]}" onclick="switchSuite(\'{s["suite"]}\')">'
            f'{"SEND" if s["suite"] == "send" else "Interop"}</button>'
            for s in suites)
        switcher = f'<div class="suite-switch">{buttons}</div>'
    else:
        switcher = ""

    html = HTML_TEMPLATE
    # inline di CSS e JS (sorgenti separati per manutenibilità, ma l'OUTPUT
    # resta un unico file self-contained). Prima di tutto, così i placeholder
    # __DATA__/__CRIT__ dentro la JS vengano poi sostituiti dai replace sotto.
    html = html.replace("__CSS__", _read_asset("dashboard.css"))
    html = html.replace("__JS__", _read_asset("dashboard.js"))
    html = html.replace("__TIMESTAMP__", datetime.now().strftime("%Y-%m-%d %H:%M"))
    html = html.replace("__EXEC__", render_executive(suites, crit))
    html = html.replace("__SWITCHER__", switcher)
    html = html.replace("__SUITES__", suites_html)
    html = html.replace("__DATA__", json.dumps(data))
    html = html.replace("__CRIT__", json.dumps(crit))
    return html


def main():
    parser = argparse.ArgumentParser(description="Generate HTML coverage dashboard")
    parser.add_argument("--suite", choices=["send", "interop"], help="Single suite (default: both)")
    parser.add_argument("--open", action="store_true", help="Open in browser after generating")
    parser.add_argument("--output", help="Output HTML path")
    parser.add_argument("--all-runs", action="store_true",
                        help="Trend: un punto per ogni run (default: ultimo run del giorno)")
    args = parser.parse_args()

    target_suites = [args.suite] if args.suite else ["send", "interop"]
    crit = load_criticality()
    loaded = []
    for suite in target_suites:
        s = load_suite(suite)
        if s:
            s["trend"] = build_trend(suite, all_runs=args.all_runs)
            for e in s["endpoints"]:
                e["crit"] = resolve_crit_class(e, crit)
            loaded.append(s)
            tag_status = "statico+tag" if s["has_tag"] else "solo statico"
            print(f"  {suite}: {s['total']} endpoint · reale {s['real']} · fantasma {s['phantom']} · mai {s['never']} ({tag_status})")
            rns = s.get("reachable_not_static", [])
            if rns:
                print(f"    ⚠️  cross-layer: {len(rns)} endpoint reachable ma covered=False nello "
                      f"statico (scanner statico più debole del resolver → 'reale > statico'). "
                      f"Fix definitivo: parser unico (P2). Es.: {', '.join(rns[:5])}"
                      + (" …" if len(rns) > 5 else ""))
        else:
            print(f"  {suite}: nessun report trovato — esegui prima coverage.py --suite {suite}")

    if not loaded:
        print("\n  Nessun dato. Esegui prima coverage.py e tag-coverage.py.")
        return

    html = generate_html(loaded, crit)

    out_dir = REPORTS_DIR / "html"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(args.output) if args.output else out_dir / f"coverage-dashboard-{ts}.html"
    out_path.write_text(html)
    print(f"\n  📊 Dashboard HTML: {out_path}")

    if args.open:
        webbrowser.open(f"file://{out_path.resolve()}")


if __name__ == "__main__":
    main()
