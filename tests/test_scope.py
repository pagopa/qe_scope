#!/usr/bin/env python3
"""
Auto-test di SCOPE (golden tests su fixture sintetiche).

Esegue la catena di analisi di tag-coverage.py su un mini-repository finto
(tests/fixtures/) dove la risposta corretta è nota per costruzione.
Ogni test incarna un caso reale già incontrato (incluso il dispatch di versione a runtime).

Uso:
    python3 coverage-tool/tests/test_scope.py
    python3 -m unittest discover coverage-tool/tests
"""

import json
import tempfile
import unittest
import xml.etree.ElementTree as _ET
from datetime import datetime, timezone
from pathlib import Path

# Il package `scope` è importabile grazie a conftest.py (src/ su sys.path) o a
# `pip install -e .`. Niente più importlib-by-path: nomi di modulo veri.
from scope import config as scope_config
from scope import inventory as coverage_tool
from scope import java_analysis  # noqa: F401  (alcuni test lo usano via reflection)
from scope import prune as prune_reports
from scope import report as reporthtml
from scope import runtime as runtime_ingest
from scope import tag_coverage as tagcov

TESTS_DIR = Path(__file__).resolve().parent
TOOL_DIR = TESTS_DIR.parent          # project root (per fixtures/asset path)
FIXTURES = TESTS_DIR / "fixtures"
COV_FIX = FIXTURES / "coverage"


def _src_report():
    """Sorgente di report.py (per i test che verificano la presenza di funzioni)."""
    return (TOOL_DIR / "src" / "scope" / "report.py").read_text()

# Inventario endpoint finto (simula l'output di coverage.py)
KNOWN_OPS = {
    "createWidget",          # reale: step diretto
    "getWidget",             # reale: risoluzione multi-hop
    "listWidgets",           # reale: lambda multilinea
    "deleteWidget",          # fantasma: wrapper esiste, nessuno step
    "orphanWidget",          # mai implementato: solo nella spec
    "consumeFakeStream",     # famiglia versioni: base
    "consumeFakeStreamV1",   # famiglia versioni
    "consumeFakeStreamV2",   # famiglia versioni
    "uploadFile",            # reale: step chiama .uploadFileWithHttpInfo( (bug WithHttpInfo)
    "initUpload",            # reale: catena via chiamata senza dot prefix (bug unqualified)
    "archiveWidget",         # reale: step con custom parameter type {ruolo}
    "publishWidget",         # reale: step con annotazioni impilate (alias)
    "cloneWidget",           # reale: step invoca via method reference (client::cloneWidget)
    "noisyOp",               # collisione: chiamato SOLO da NoisyHomonym.fetchWidget
    "storeWidget",           # reale: via interfaccia IMiniStore → MiniStoreImpl
}


def run_pipeline():
    """Esegue l'intera catena di SCOPE sulle fixture. Risultato cacheato."""
    if hasattr(run_pipeline, "_cache"):
        return run_pipeline._cache

    name_to_ops = tagcov.build_operation_id_resolver([FIXTURES / "src"], KNOWN_OPS)
    step_defs = tagcov.parse_step_definitions([FIXTURES / "src"], name_to_ops, KNOWN_OPS)
    scenarios = tagcov.parse_features([FIXTURES / "features"])
    total_steps, unmatched = tagcov.match_steps(scenarios, step_defs, KNOWN_OPS)
    runners = tagcov.parse_runners([FIXTURES / "runners"])
    runner_results = tagcov.compute_runner_coverage(runners, scenarios, KNOWN_OPS)

    run_pipeline._cache = {
        "name_to_ops": name_to_ops,
        "step_defs": step_defs,
        "scenarios": {s["name"]: s for s in scenarios},
        "total_steps": total_steps,
        "unmatched": unmatched,
        "runner_results": {r["runner"]: r for r in runner_results},
    }
    return run_pipeline._cache


class TestResolver(unittest.TestCase):
    """Risoluzione metodo -> operationId (chiusura transitiva)."""

    def test_direct_wrapper_call(self):
        r = run_pipeline()["name_to_ops"]
        self.assertIn("createWidget", r.get("createWidget", set()))

    def test_multi_hop_resolution(self):
        """step -> fetchWidget -> loadWidget -> api.getWidget"""
        r = run_pipeline()["name_to_ops"]
        self.assertIn("getWidget", r.get("fetchWidget", set()),
                      "la chiusura transitiva a più hop non risolve getWidget")

    def test_multiline_lambda_call(self):
        """Regressione: chiamata dentro lambda multilinea (bug DOTALL della v1)."""
        r = run_pipeline()["name_to_ops"]
        self.assertIn("listWidgets", r.get("listWidgetsWrapper", set()),
                      "la chiamata in lambda multilinea non viene risolta")

    def test_phantom_wrapper_resolved_in_code(self):
        """deleteWidget ha il wrapper (esiste nel codice)..."""
        r = run_pipeline()["name_to_ops"]
        self.assertIn("deleteWidget", r.get("deleteWidgetWrapper", set()))

    def test_orphan_not_in_code(self):
        """orphanWidget non è chiamato da nessun metodo."""
        r = run_pipeline()["name_to_ops"]
        all_resolved = set()
        for ops in r.values():
            all_resolved |= ops
        self.assertNotIn("orphanWidget", all_resolved)


class TestStepMatching(unittest.TestCase):
    """Matching scenario step -> step definition."""

    def test_all_fixture_steps_match(self):
        p = run_pipeline()
        self.assertEqual(p["unmatched"], 0,
                         f"{p['unmatched']}/{p['total_steps']} step delle fixture non matchati")

    def test_cucumber_string_parameter(self):
        """{string} deve matchare un argomento quotato."""
        s = run_pipeline()["scenarios"]["Creazione e lettura widget"]
        self.assertIn("createWidget", s["api_calls"])

    def test_scenario_resolves_multi_hop_endpoint(self):
        s = run_pipeline()["scenarios"]["Creazione e lettura widget"]
        self.assertIn("getWidget", s["api_calls"])


class TestCategories(unittest.TestCase):
    """Le tre categorie: reale / fantasma / mai implementato."""

    def _reachable(self):
        scenarios = run_pipeline()["scenarios"].values()
        reach = set()
        for s in scenarios:
            reach |= s["api_calls"]
        return reach & KNOWN_OPS

    def test_real_endpoints_reachable(self):
        reach = self._reachable()
        for op in ("createWidget", "getWidget", "listWidgets"):
            self.assertIn(op, reach, f"{op} dovrebbe essere REALE")

    def test_phantom_endpoint_not_reachable(self):
        """deleteWidget: codice sì, scenari no -> FANTASMA."""
        self.assertNotIn("deleteWidget", self._reachable())

    def test_never_endpoint_not_reachable(self):
        self.assertNotIn("orphanWidget", self._reachable())


class TestTcId(unittest.TestCase):
    """TC-ID: chiave di join coi report di esecuzione (D2)."""

    def test_extract_basic(self):
        self.assertEqual(tagcov.extract_tc_id("[TC-PA_LEGALFACT_1] Invio notifica"), "TC-PA_LEGALFACT_1")

    def test_extract_first_token_only(self):
        # se ci sono più [], si prende il primo (il TC-ID è in testa al titolo)
        self.assertEqual(tagcov.extract_tc_id("[TC-A1] cosa [altro]"), "TC-A1")

    def test_extract_missing(self):
        self.assertEqual(tagcov.extract_tc_id("Scenario senza id"), "")
        self.assertEqual(tagcov.extract_tc_id(""), "")
        self.assertEqual(tagcov.extract_tc_id(None), "")

    def test_extract_ignores_lowercase_only(self):
        # i TC-ID sono maiuscoli; un [abc] minuscolo non è un id valido
        self.assertEqual(tagcov.extract_tc_id("[abc] roba"), "")


class TestRuntimeIngest(unittest.TestCase):
    """D2: adapter cucumber, stato corrente overlay 'ultimo vince', finestra, flaky."""

    def _scn(self, name, *statuses):
        return {"type": "scenario", "name": name,
                "steps": [{"result": {"status": s}} for s in statuses]}

    def test_aggregate_status(self):
        self.assertEqual(runtime_ingest.aggregate_status(self._scn("x", "passed", "passed")), "OK")
        self.assertEqual(runtime_ingest.aggregate_status(self._scn("x", "passed", "failed")), "KO")
        self.assertEqual(runtime_ingest.aggregate_status(self._scn("x", "passed", "skipped")), "OTHER")
        self.assertEqual(runtime_ingest.aggregate_status(self._scn("x")), "OTHER")

    def test_first_error(self):
        el = {"steps": [{"result": {"status": "passed"}},
                        {"result": {"status": "failed", "error_message": "boom\nstack"}}]}
        self.assertEqual(runtime_ingest.first_error(el), "boom")

    def test_parse_cucumber_filters_background_and_extracts_tcid(self):
        data = [{"elements": [
            {"type": "background", "name": "setup", "steps": [{"result": {"status": "passed"}}]},
            self._scn("[TC-1] alfa", "passed"),
            self._scn("senza id", "failed", "passed"),
        ]}]
        fb = datetime(2026, 1, 1, tzinfo=timezone.utc)
        res, run_ts = runtime_ingest.parse_cucumber(data, fb)
        self.assertEqual(len(res), 2)  # background scartato
        self.assertEqual(res[0]["tc_id"], "TC-1")
        self.assertEqual(res[0]["status"], "OK")
        self.assertEqual(res[1]["tc_id"], "")
        self.assertEqual(res[1]["status"], "KO")
        self.assertEqual(run_ts, fb)  # nessun start_timestamp → fallback mtime

    def test_run_ts_from_min_start_timestamp(self):
        data = [{"elements": [
            {"type": "scenario", "name": "[TC-1] a", "start_timestamp": "2026-03-05T10:00:00.000Z",
             "steps": [{"result": {"status": "passed"}}]},
            {"type": "scenario", "name": "[TC-2] b", "start_timestamp": "2026-03-05T08:00:00.000Z",
             "steps": [{"result": {"status": "passed"}}]},
        ]}]
        _, run_ts = runtime_ingest.parse_cucumber(data, datetime(2000, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(run_ts.hour, 8)  # il più vecchio

    def _run(self, day, *pairs):
        return {"run_ts": datetime(2026, 6, 1 + day, tzinfo=timezone.utc).isoformat(),
                "results": [{"tc_id": tc, "status": st} for tc, st in pairs]}

    def test_latest_wins(self):
        now = datetime(2026, 6, 10, tzinfo=timezone.utc)
        runs = [self._run(0, ("TC-1", "OK")), self._run(3, ("TC-1", "KO"))]
        state, meta = runtime_ingest.compute_current_state(runs, now=now, window_days=30)
        self.assertEqual(state["TC-1"]["last_status"], "KO")  # la run più recente vince
        self.assertEqual(state["TC-1"]["ok"], 1)
        self.assertEqual(state["TC-1"]["ko"], 1)

    def test_flaky_is_separate_signal(self):
        now = datetime(2026, 6, 10, tzinfo=timezone.utc)
        runs = [self._run(0, ("TC-1", "OK")), self._run(2, ("TC-1", "KO")), self._run(4, ("TC-1", "OK"))]
        state, _ = runtime_ingest.compute_current_state(runs, now=now, window_days=30)
        self.assertEqual(state["TC-1"]["last_status"], "OK")  # stato = foto attuale
        self.assertTrue(state["TC-1"]["flaky"])               # ma instabile nella finestra

    def test_window_excludes_old_runs(self):
        now = datetime(2026, 6, 10, tzinfo=timezone.utc)
        old = {"run_ts": datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(),
               "results": [{"tc_id": "TC-1", "status": "OK"}]}
        state, meta = runtime_ingest.compute_current_state([old], now=now, window_days=30)
        self.assertTrue(meta["empty"])
        self.assertEqual(state, {})

    def test_join_runtime_endpoint_states(self):
        # epX toccato da TC-1(OK) e TC-2(KO) → ok (basta un verde)
        # epY toccato solo da TC-3(KO) → ko;  epZ da TC-4(non in stato) → none
        scen = [
            {"tc_id": "TC-1", "ops": ["epX"]},
            {"tc_id": "TC-2", "ops": ["epX"]},
            {"tc_id": "TC-3", "ops": ["epY"]},
            {"tc_id": "TC-4", "ops": ["epZ"]},
        ]
        epScen = {"epX": [0, 1], "epY": [2], "epZ": [3]}
        rt = {
            "TC-1": {"last_status": "OK", "age_days": 1, "flaky": False, "ok": 1, "ko": 0},
            "TC-2": {"last_status": "KO", "age_days": 1, "flaky": False, "ok": 0, "ko": 1},
            "TC-3": {"last_status": "KO", "age_days": 2, "flaky": False, "ok": 0, "ko": 1, "last_error": "boom"},
        }
        ep_exec, ep_run = reporthtml.join_runtime(scen, epScen, rt)
        self.assertEqual(ep_exec["epX"], "ok")
        self.assertEqual(ep_exec["epY"], "ko")
        self.assertEqual(ep_exec["epZ"], "none")
        self.assertEqual(scen[0]["exec"]["status"], "OK")   # scenario arricchito
        self.assertNotIn("exec", scen[3])                    # TC-4 senza stato

    def test_sanity_unmatched_fail(self):
        now = datetime(2026, 6, 10, tzinfo=timezone.utc)
        runs = [self._run(0, ("TC-1", "OK"), ("TC-2", "OK"), ("TC-GHOST", "KO"), ("TC-X", "OK"))]
        # inventario conosce TC-1, TC-2, TC-X ma non TC-GHOST → 25% unmatched > FAIL
        known = {"TC-1", "TC-2", "TC-X"}
        issues, sanity = runtime_ingest.run_sanity(runs, "send", now=now, known_tc_ids=known)
        self.assertFalse(sanity["passed"])
        self.assertTrue(any(sev == "FAIL" for sev, _ in issues))

    def test_sanity_passes_when_all_matched(self):
        now = datetime(2026, 6, 10, tzinfo=timezone.utc)
        runs = [self._run(0, ("TC-1", "OK"), ("TC-2", "KO"))]
        issues, sanity = runtime_ingest.run_sanity(runs, "send", now=now,
                                                   known_tc_ids={"TC-1", "TC-2"})
        self.assertTrue(sanity["passed"])
        self.assertEqual(sanity["unmatched"], 0)

    def test_sanity_stale_window_warns_not_fails(self):
        now = datetime(2026, 6, 10, tzinfo=timezone.utc)
        old = {"run_ts": datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(),
               "results": [{"tc_id": "TC-1", "status": "OK"}]}
        issues, sanity = runtime_ingest.run_sanity([old], "send", now=now, known_tc_ids={"TC-1"})
        self.assertTrue(sanity["passed"])  # stantio = WARN, non FAIL
        self.assertTrue(any("nessuna run" in m for _, m in issues))

    def test_sanity_duplicate_tc_warns(self):
        now = datetime(2026, 6, 10, tzinfo=timezone.utc)
        runs = [self._run(0, ("TC-1", "OK"), ("TC-1", "KO"))]
        issues, sanity = runtime_ingest.run_sanity(runs, "send", now=now, known_tc_ids={"TC-1"})
        self.assertEqual(sanity["duplicate_tc"], 1)
        self.assertTrue(sanity["passed"])  # duplicato = WARN

    def test_ingest_idempotent_by_content(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            inbox = Path(tmp) / "inbox"
            (inbox / "send").mkdir(parents=True)
            report = [{"elements": [self._scn("[TC-1] a", "passed")]}]
            (inbox / "send" / "r1.json").write_text(json.dumps(report))
            ledger = {"version": 1, "entries": {}}
            results = {"version": 1, "runs": []}
            n_new, n_skip, _ = runtime_ingest.ingest(inbox, ledger, results)
            self.assertEqual((n_new, n_skip), (1, 0))
            # secondo giro: stesso contenuto → skip
            n_new2, n_skip2, _ = runtime_ingest.ingest(inbox, ledger, results)
            self.assertEqual((n_new2, n_skip2), (0, 1))
            self.assertEqual(len(results["runs"]), 1)


class TestVersionRefinement(unittest.TestCase):
    """Dispatch a runtime per versione (attribuzione per famiglia)."""

    def test_family_detected(self):
        families, op_to_base = tagcov.build_version_families(KNOWN_OPS)
        self.assertIn("consumeFakeStream", families)
        fam = families["consumeFakeStream"]
        self.assertEqual(set(fam.keys()), {0, 1, 2})  # base + V1 + V2

    def test_lone_versioned_op_is_not_a_family(self):
        """Un op isolato che finisce in V1 non deve formare una famiglia."""
        families, _ = tagcov.build_version_families({"deleteApiKeyV1", "other"})
        self.assertEqual(families, {})

    def test_explicit_version_restricts(self):
        """Scenario con versione "V2": solo V2 accreditata."""
        s = run_pipeline()["scenarios"]["Stream con versione esplicita V2"]
        self.assertIn("consumeFakeStreamV2", s["api_calls"])
        self.assertNotIn("consumeFakeStreamV1", s["api_calls"],
                         "V1 accreditata a uno scenario che dichiara V2")
        self.assertNotIn("consumeFakeStream", s["api_calls"])

    def test_generic_step_inherits_scenario_version(self):
        """Lo step generico nello scenario V2 non deve riaprire la famiglia."""
        s = run_pipeline()["scenarios"]["Stream con versione esplicita V2"]
        self.assertEqual(
            s["api_calls"] & {"consumeFakeStream", "consumeFakeStreamV1", "consumeFakeStreamV2"},
            {"consumeFakeStreamV2"})

    def test_no_version_keeps_whole_family(self):
        """Fallback conservativo: senza token, tutta la famiglia."""
        s = run_pipeline()["scenarios"]["Stream senza versione dichiarata"]
        self.assertTrue(
            {"consumeFakeStream", "consumeFakeStreamV1", "consumeFakeStreamV2"} <= s["api_calls"])

    def test_most_recent_token(self):
        """'più recente' -> versione massima della famiglia."""
        families, op_to_base = tagcov.build_version_families(KNOWN_OPS)
        ops = {"consumeFakeStream", "consumeFakeStreamV1", "consumeFakeStreamV2"}
        refined = tagcov.refine_ops_by_version(ops, ["MAX"], families, op_to_base)
        self.assertEqual(refined & ops, {"consumeFakeStreamV2"})

    def test_unknown_version_maps_to_base(self):
        """Token senza membro dedicato (es. V10) -> variante non versionata."""
        families, op_to_base = tagcov.build_version_families(KNOWN_OPS)
        ops = {"consumeFakeStream", "consumeFakeStreamV1", "consumeFakeStreamV2"}
        refined = tagcov.refine_ops_by_version(ops, [10], families, op_to_base)
        self.assertEqual(refined & ops, {"consumeFakeStream"})


class TestRunnerLogic(unittest.TestCase):
    """Logica JUnit5 @IncludeTags (OR) / @ExcludeTags (OR)."""

    def test_runner_parsed(self):
        r = run_pipeline()["runner_results"]
        self.assertIn("MiniRunner", r)

    def test_exclude_tags_win(self):
        """@ignored è sia in include che in exclude: exclude vince."""
        r = run_pipeline()["runner_results"]["MiniRunner"]
        self.assertEqual(r["scenarios"], 4, "lo scenario @ignored non è stato escluso")

    def test_runner_real_coverage(self):
        r = run_pipeline()["runner_results"]["MiniRunner"]
        expected = {"createWidget", "getWidget", "listWidgets",
                    "consumeFakeStream", "consumeFakeStreamV1", "consumeFakeStreamV2"}
        self.assertEqual(r["covered"], expected)


class TestCriticality(unittest.TestCase):
    """Risoluzione dei pesi di criticità (report-html.py)."""

    EP = {"operation_id": "createWidget", "path": "/widgets",
          "spec_name": "mini-api", "service": "mini-service"}

    def test_no_rules_gives_default(self):
        crit = {"classes": {"core": 3, "standard": 1}, "default": "standard", "rules": []}
        self.assertEqual(reporthtml.resolve_crit_class(self.EP, crit), "standard")

    def test_precedence_operation_over_service(self):
        crit = {"classes": {"core": 3, "standard": 1, "marginal": 0.3}, "default": "standard",
                "rules": [
                    {"service": "mini-service", "class": "core"},
                    {"operation": "createWidget", "class": "marginal"},
                ]}
        self.assertEqual(reporthtml.resolve_crit_class(self.EP, crit), "marginal",
                         "la regola operation deve vincere su service")

    def test_path_glob(self):
        crit = {"classes": {"core": 3, "standard": 1}, "default": "standard",
                "rules": [{"path": "/widg*", "class": "core"}]}
        self.assertEqual(reporthtml.resolve_crit_class(self.EP, crit), "core")

    def test_uniform_weights_equal_simple_coverage(self):
        """Con rules vuote, pesata == semplice (verifica matematica dell'impianto)."""
        crit = {"classes": {"core": 3, "standard": 1}, "default": "standard", "rules": []}
        eps = [
            {"operation_id": f"op{i}", "path": f"/p{i}", "spec_name": "s", "service": "x",
             "category": "real" if i < 3 else "never"}
            for i in range(10)
        ]
        w_all = w_real = 0
        for e in eps:
            w = crit["classes"][reporthtml.resolve_crit_class(e, crit)]
            w_all += w
            if e["category"] == "real":
                w_real += w
        self.assertAlmostEqual(w_real / w_all, 3 / 10)


class TestCustomParameterType(unittest.TestCase):
    """Regressione: custom parameter type ({delegationRole} & co.) non convertito
    → step mai matchato → endpoint persi quando il fallback file_ops non scatta
    (rejectConsumerDelegation/revokeConsumerDelegation su Interop)."""

    def test_custom_param_step_matches(self):
        s = run_pipeline()["scenarios"]["Archiviazione con custom parameter type"]
        self.assertIn("archiveWidget", s["api_calls"],
                      "lo step con {ruolo} (custom @ParameterType) non viene matchato")


class TestStackedAnnotations(unittest.TestCase):
    """Regressione: annotazioni step impilate (@When + @When sullo stesso metodo).
    La v1 registrava solo l'ultima annotazione adiacente alla firma: gli scenari
    che usavano il primo alias risultavano non matchati (revokeConsumerDelegation)."""

    def test_first_stacked_alias_matches(self):
        s = run_pipeline()["scenarios"]["Pubblicazione via prima annotazione impilata"]
        self.assertIn("publishWidget", s["api_calls"],
                      "il primo alias di un blocco di annotazioni impilate non matcha")


class TestScopedResolution(unittest.TestCase):
    """Risoluzione import-scoped: il tipo del receiver delimita i candidati.

    Caso anti-collisione: NoisyHomonym.fetchWidget (→ noisyOp) è OMONIMO di
    MiniService.fetchWidget (→ getWidget). Lo step chiama service.fetchWidget
    con service dichiarato MiniService: NON deve ereditare noisyOp."""

    def test_homonym_does_not_pollute_scenario(self):
        s = run_pipeline()["scenarios"]["Creazione e lettura widget"]
        self.assertIn("getWidget", s["api_calls"])
        self.assertNotIn("noisyOp", s["api_calls"],
                         "collisione di omonimi: il receiver tipizzato non delimita i candidati")

    def test_scoped_node_isolation(self):
        r = run_pipeline()["name_to_ops"]  # è l'OpResolver
        self.assertEqual(r.ops_for("MiniService", "fetchWidget") & {"noisyOp", "getWidget"},
                         {"getWidget"})
        self.assertEqual(r.ops_for("NoisyHomonym", "fetchWidget") & {"noisyOp", "getWidget"},
                         {"noisyOp"})

    def test_interface_expands_to_impl_transitively(self):
        """Campo IMiniStore → impl 2 livelli sotto (MiniStoreImpl implements
        IMiniStoreV2 extends IMiniStore): la discesa deve essere transitiva
        (regressione getPurposeTemplateEvents su Interop)."""
        s = run_pipeline()["scenarios"]["Salvataggio via interfaccia"]
        self.assertIn("storeWidget", s["api_calls"],
                      "l'interfaccia non viene espansa transitivamente alle implementazioni")

    def test_static_import_cross_file(self):
        """Chiamata unqualified definita in altra classe via import static."""
        s = run_pipeline()["scenarios"]["Upload profondo via static import"]
        self.assertIn("initUpload", s["api_calls"],
                      "lo static import non entra nel perimetro delle chiamate unqualified")


class TestMethodReference(unittest.TestCase):
    """Regressione: chiamate via method reference (obj::metodo) non tracciate
    → falsi fantasmi createProducerDelegation e getStatus su Interop."""

    def test_method_reference_resolves(self):
        r = run_pipeline()["name_to_ops"]
        self.assertIn("cloneWidget", r.get("cloneWidget", set()) | r.get("cloneWidgetStep", set()),
                      "il method reference client::cloneWidget non viene risolto")

    def test_step_via_method_reference_reaches_endpoint(self):
        s = run_pipeline()["scenarios"]["Clonazione via method reference"]
        self.assertIn("cloneWidget", s["api_calls"],
                      "lo step che usa :: non risolve l'operationId")


class TestWithHttpInfo(unittest.TestCase):
    """Regressione: .opIdWithHttpInfo( deve essere riconosciuto come chiamata a opId.

    Bug osservato su SEND: 7 fantasmi falsi (addCoverage, additionalFileTagsMassiveUpdate,
    additionalFileTagsSearch, createActOperation, createActOperationV2, getOperationStatus,
    getOperationV2) — il codice chiama .opIdWithHttpInfo( ma il resolver cercava solo .opId(.
    """

    def test_with_http_info_resolves_operation_id(self):
        """Il resolver deve mappare uploadFileWithHttpInfo -> uploadFile."""
        r = run_pipeline()["name_to_ops"]
        self.assertIn("uploadFile", r.get("uploadFileWithHttpInfo", set()),
                      "WithHttpInfo non riconosciuto come proxy dell'operationId base")

    def test_step_calling_with_http_info_reaches_endpoint(self):
        """Lo scenario che invoca WithHttpInfo deve avere uploadFile come api_call."""
        s = run_pipeline()["scenarios"]["Upload file via WithHttpInfo"]
        self.assertIn("uploadFile", s["api_calls"],
                      "lo step con .uploadFileWithHttpInfo( non risolve l'operationId")


class TestUnqualifiedCall(unittest.TestCase):
    """Regressione: chiamate di metodo senza dot prefix (static o same-class) devono
    essere seguite nella chiusura transitiva.

    Bug osservato su SEND: presignedUploadRequest — catena spezzata perché
    preloadGeneric(...) e getPreLoadResponse(...) sono chiamati senza prefisso oggetto.
    """

    def test_unqualified_call_in_resolver(self):
        """prepareUpload chiama resolveUpload( senza dot: initUpload deve essere risolvibile."""
        r = run_pipeline()["name_to_ops"]
        self.assertIn("initUpload", r.get("prepareUpload", set()),
                      "la chiamata senza dot prefix non propagata nella chiusura transitiva")

    def test_deep_chain_fixpoint(self):
        """Regressione: catena a 5 hop (deepPrepareUpload -> hop3 -> hop2 ->
        prepareUpload -> resolveUpload -> api.initUpload). I 3 hop fissi della v1
        la troncavano: changeStatusVirtualKeysV1/deleteVirtualKeyV1 persi su SEND.
        Il fixpoint deve risolverla a qualsiasi profondità."""
        r = run_pipeline()["name_to_ops"]
        self.assertIn("initUpload", r.get("deepPrepareUpload", set()),
                      "la chiusura transitiva non raggiunge il fixpoint (catena >3 hop troncata)")

    def test_step_via_unqualified_chain_reaches_endpoint(self):
        """Lo step 'viene preparato l'upload' deve risolvere initUpload via catena unqualified."""
        s = run_pipeline()["scenarios"]["Upload file via WithHttpInfo"]
        self.assertIn("initUpload", s["api_calls"],
                      "la catena con chiamate senza dot non porta all'operationId")


# ===========================================================================
# P1 — Golden test del livello STATICO (coverage.py)
# Prima questa metà della pipeline (l'inventario + il denominatore di ogni
# metrica) era priva di test. Tutte le fixture sono locali: nessuna rete.
# ===========================================================================


class TestCoveragePom(unittest.TestCase):
    """Parsing pom.xml: properties, variabili, executions openapi-generator."""

    def _tree(self):
        return _ET.parse(COV_FIX / "pom.xml")

    def test_properties_and_var_resolution(self):
        props = coverage_tool.parse_maven_properties(self._tree())
        self.assertEqual(props.get("pn.spec.branch"), "main")
        resolved = coverage_tool.resolve_vars(
            "https://x/${pn.spec.branch}/y", props)
        self.assertEqual(resolved, "https://x/main/y")

    def test_extract_executions(self):
        tree = self._tree()
        props = coverage_tool.parse_maven_properties(tree)
        ex = coverage_tool.extract_executions(tree, props)
        # Solo le 2 execution del plugin openapi (surefire ignorato)
        self.assertEqual(len(ex), 2)
        by_id = {e["id"]: e for e in ex}
        w = by_id["generate-widget-client"]
        self.assertEqual(w["service"], "pn-widget")        # group(2) dell'URL GH
        self.assertEqual(w["spec_name"], "widget-api")     # filename senza ext
        self.assertNotIn("${", w["input_spec"])            # var risolta
        self.assertTrue(w["api_package"].endswith("clients.widget.api"))

    def test_version_classification(self):
        self.assertIsNone(coverage_tool.classify_version("generate-widget-client"))
        self.assertTrue(coverage_tool.is_latest_execution("generate-widget-client"))
        self.assertEqual(coverage_tool.classify_version("generate-store-v1"), "v1")
        self.assertFalse(coverage_tool.is_latest_execution("generate-store-v1"))


class TestCoverageSpec(unittest.TestCase):
    """extract_endpoints: parsing della spec OpenAPI (da stringa, niente rete)."""

    def _eps(self):
        text = (COV_FIX / "specs" / "widget-api.yaml").read_text()
        return coverage_tool.extract_endpoints(text)

    def test_endpoints_extracted(self):
        eps = self._eps()
        ids = {(e["method"], e["path"], e["operation_id"]) for e in eps}
        self.assertIn(("POST", "/widgets", "createWidget"), ids)
        self.assertIn(("GET", "/widgets", "listWidgets"), ids)
        self.assertIn(("DELETE", "/widgets/{id}", "deleteWidget"), ids)

    def test_no_operation_id_endpoint(self):
        """PUT /widgets/{id} non ha operationId: viene estratto con op_id vuoto."""
        eps = self._eps()
        put = [e for e in eps if e["method"] == "PUT"]
        self.assertEqual(len(put), 1)
        self.assertEqual(put[0]["operation_id"], "")

    def test_non_http_key_ignored(self):
        """La chiave 'parameters' sotto /widgets non è un metodo HTTP."""
        eps = self._eps()
        self.assertFalse(any(e["method"] == "PARAMETERS" for e in eps))

    def test_malformed_spec_returns_empty(self):
        self.assertEqual(coverage_tool.extract_endpoints(": : not yaml ["), [])
        self.assertEqual(coverage_tool.extract_endpoints("foo: bar"), [])


class TestCoverageVisibility(unittest.TestCase):
    """classify_visibility: euristiche public/internal."""

    def test_public_default(self):
        self.assertEqual(
            coverage_tool.classify_visibility("https://x/widget-api.yaml", "/widgets"),
            "public")

    def test_internal_by_spec_name(self):
        self.assertEqual(
            coverage_tool.classify_visibility("https://x/api-internal-v1.yaml", "/s"),
            "internal")

    def test_internal_by_path(self):
        self.assertEqual(
            coverage_tool.classify_visibility("https://x/widget-api.yaml", "/internal/x"),
            "internal")

    def test_b2b_override_wins(self):
        """api-internal-b2b è internal nel nome ma esposta alle PA → public."""
        self.assertEqual(
            coverage_tool.classify_visibility("https://x/api-internal-b2b-pa.yaml", "/s"),
            "public")


class TestCoverageStaticScan(unittest.TestCase):
    """compute_static_covered: usa il resolver condiviso (P2).

    Il file WidgetSteps.java chiama widgetApi.createWidget(...),
    widgetApi.listWidgetsWithHttpInfo() e service.deleteWidget(...).
    Tutti e tre gli operationId devono risultare invocati — incluso
    listWidgets via la variante WithHttpInfo (era il blind spot pre-P2)."""

    def _covered(self):
        all_ops = {"createWidget", "listWidgets", "deleteWidget", "neverCalled"}
        return coverage_tool.compute_static_covered([COV_FIX / "src"], all_ops)

    def test_direct_call_covered(self):
        self.assertIn("createWidget", self._covered())

    def test_withhttpinfo_now_covered(self):
        """P2: lo scanner statico ora segue .listWidgetsWithHttpInfo( →
        l'operationId base listWidgets è coperto (prima era il blind spot)."""
        self.assertIn("listWidgets", self._covered())

    def test_dotted_call_covered(self):
        self.assertIn("deleteWidget", self._covered())

    def test_uncalled_not_covered(self):
        self.assertNotIn("neverCalled", self._covered())


class TestCoverageBuildReport(unittest.TestCase):
    """build_report: il cuore non testato — covered + match_source."""

    def setUp(self):
        # Costruiti a mano: pura logica, nessun file/rete
        self.executions = [{
            "id": "generate-widget-client",
            "input_spec": "https://x/widget-api.yaml",
            "api_package": "it.pagopa...clients.widget.api",
            "service": "pn-widget",
            "spec_name": "widget-api",
            "endpoints": [
                {"method": "POST", "path": "/widgets", "operation_id": "createWidget"},
                {"method": "GET", "path": "/widgets", "operation_id": "listWidgets"},
                {"method": "DELETE", "path": "/widgets/{id}", "operation_id": "deleteWidget"},
                {"method": "PUT", "path": "/widgets/{id}", "operation_id": ""},
            ],
        }, {
            "id": "generate-store-v1",
            "input_spec": "https://x/api-internal-v1.yaml",
            "api_package": "it.pagopa...clients.store.api",
            "service": "pn-store",
            "spec_name": "api-internal-v1",
            "endpoints": [
                {"method": "GET", "path": "/store/{id}", "operation_id": "getStore"},
            ],
        }]
        # P2: build_report riceve l'insieme degli operationId invocati
        # (da compute_static_covered / resolver), non più i due scan separati.
        self.covered_ops = {"createWidget", "deleteWidget", "listWidgets"}

    def _report(self, latest_only=False):
        return {r["operation_id"]: r for r in coverage_tool.build_report(
            self.executions, self.covered_ops, latest_only=latest_only)}

    def test_covered_invoked(self):
        r = self._report()["createWidget"]
        self.assertTrue(r["covered"])
        self.assertEqual(r["match_source"], "invoked")

    def test_no_operation_id(self):
        r = self._report()[""]
        self.assertFalse(r["covered"])
        self.assertEqual(r["match_source"], "no-operationId")

    def test_uncovered(self):
        r = self._report()["getStore"]
        self.assertFalse(r["covered"])
        self.assertEqual(r["match_source"], "")

    def test_latest_only_filters_versioned(self):
        rep = self._report(latest_only=True)
        self.assertIn("createWidget", rep)   # execution latest
        self.assertNotIn("getStore", rep)    # execution _v1 esclusa


# ===========================================================================
# P4 — Robustezza dell'inventario: lock-file + sanity del download spec
# ===========================================================================


def _fp(count, sha="aaaa", downloaded=True):
    """Helper: una entry di impronta spec."""
    return {"url": "https://x/s.yaml", "service": "svc", "spec_name": "s",
            "endpoint_count": count, "sha256": sha, "downloaded": downloaded}


class TestInventoryLock(unittest.TestCase):
    """check_inventory: confronto impronta corrente vs baseline lockata."""

    def _sev(self, issues):
        return {s for s, _ in issues}

    def test_clean_no_issues(self):
        fp = {"e1": _fp(10), "e2": _fp(5)}
        issues, summary = coverage_tool.check_inventory("send", fp, dict(fp))
        self.assertEqual(issues, [])
        self.assertTrue(summary["passed"])

    def test_no_lock_is_info(self):
        fp = {"e1": _fp(10)}
        issues, summary = coverage_tool.check_inventory("send", fp, {})
        self.assertEqual(self._sev(issues), {"INFO"})
        self.assertTrue(summary["passed"])

    def test_download_failure_fails(self):
        fp = {"e1": _fp(0, sha=None, downloaded=False)}
        issues, summary = coverage_tool.check_inventory("send", fp, {"e1": _fp(10)})
        self.assertIn("FAIL", self._sev(issues))
        self.assertFalse(summary["passed"])

    def test_vanished_spec_fails(self):
        lock = {"e1": _fp(10), "e2": _fp(5)}
        cur = {"e1": _fp(10)}                      # e2 sparita
        issues, summary = coverage_tool.check_inventory("send", cur, lock)
        self.assertTrue(any("SPARITA" in m for s, m in issues if s == "FAIL"))
        self.assertFalse(summary["passed"])

    def test_collapsed_to_zero_fails(self):
        issues, _ = coverage_tool.check_inventory(
            "send", {"e1": _fp(0)}, {"e1": _fp(10)})
        self.assertTrue(any("collassata" in m for s, m in issues if s == "FAIL"))

    def test_drift_warns(self):
        # e1: 10 → 7 = -30% > soglia 15% → WARN. e2 stabile (100) diluisce il
        # totale (110→107, -2,7% < 10%) così NON scatta il FAIL aggregato.
        lock = {"e1": _fp(10), "e2": _fp(100)}
        cur = {"e1": _fp(7), "e2": _fp(100)}
        issues, summary = coverage_tool.check_inventory("send", cur, lock)
        self.assertIn("WARN", self._sev(issues))
        self.assertNotIn("FAIL", self._sev(issues))
        self.assertTrue(summary["passed"])      # WARN non blocca

    def test_content_change_same_count_is_info(self):
        issues, _ = coverage_tool.check_inventory(
            "send", {"e1": _fp(10, sha="bbbb")}, {"e1": _fp(10, sha="aaaa")})
        self.assertEqual(self._sev(issues), {"INFO"})

    def test_new_spec_is_info(self):
        issues, _ = coverage_tool.check_inventory(
            "send", {"e1": _fp(10), "e2": _fp(3)}, {"e1": _fp(10)})
        self.assertTrue(any("nuova execution" in m for s, m in issues if s == "INFO"))

    def test_total_drop_fails(self):
        lock = {"e1": _fp(100), "e2": _fp(100)}
        cur = {"e1": _fp(100), "e2": _fp(70)}      # -15% totale > soglia 10%
        issues, summary = coverage_tool.check_inventory("send", cur, lock)
        self.assertTrue(any("ristretto" in m for s, m in issues if s == "FAIL"))
        self.assertFalse(summary["passed"])


class TestInventoryLockIO(unittest.TestCase):
    """load_lock / save_lock: round-trip e isolamento tra suite."""

    def test_roundtrip_and_suite_isolation(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "spec-lock.json"
            coverage_tool.save_lock("send", {"e1": _fp(10)}, path=p)
            coverage_tool.save_lock("interop", {"e2": _fp(20)}, path=p)
            # salvare interop non deve perdere send
            self.assertEqual(coverage_tool.load_lock("send", path=p)["e1"]["endpoint_count"], 10)
            self.assertEqual(coverage_tool.load_lock("interop", path=p)["e2"]["endpoint_count"], 20)

    def test_missing_lock_is_empty(self):
        self.assertEqual(
            coverage_tool.load_lock("send", path=Path("/nonesistente/spec-lock.json")), {})


class TestSharedConfig(unittest.TestCase):
    """F2: scope_config è l'unica fonte di verità per percorsi e suite."""

    def test_both_scripts_share_the_same_config_object(self):
        # coverage.py e tag-coverage.py importano lo STESSO oggetto, non copie
        self.assertIs(coverage_tool.SUITE_CONFIG, scope_config.SUITE_CONFIG)
        self.assertIs(tagcov.SUITE_CONFIG, scope_config.SUITE_CONFIG)

    def test_suite_config_has_keys_used_by_both(self):
        for suite in ("send", "interop"):
            cfg = scope_config.SUITE_CONFIG[suite]
            # chiavi usate da coverage.py + tag-coverage.py
            for k in ("pom", "all_src_dirs", "feature_dirs", "runner_dirs", "step_dirs", "label"):
                self.assertIn(k, cfg, f"{suite}.{k} mancante")

    def test_env_var_has_precedence(self):
        import os
        prev = os.environ.get("SCOPE_TARGET_REPO")
        # path già canonico: .resolve() su macOS espande /tmp → /private/tmp
        target = str(Path("/tmp/scope-test-target").resolve())
        os.environ["SCOPE_TARGET_REPO"] = target
        try:
            self.assertEqual(scope_config._resolve_target_repo(), Path(target))
        finally:
            if prev is None:
                del os.environ["SCOPE_TARGET_REPO"]
            else:
                os.environ["SCOPE_TARGET_REPO"] = prev

    def test_malformed_config_warns_not_silent(self):
        """F6: un config.yaml illeggibile deve emettere un avviso su stderr e
        ripiegare sulla cartella padre — NON un except...pass silenzioso che
        analizzerebbe il repo sbagliato senza dirlo."""
        import contextlib
        import io
        import os
        import tempfile
        prev = os.environ.pop("SCOPE_TARGET_REPO", None)
        try:
            with tempfile.TemporaryDirectory() as d:
                td = Path(d)
                (td / "config.yaml").write_text("target_repo: [questo: non: è: yaml valido")
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    result = scope_config._resolve_target_repo(project_root=td)
                self.assertIn("ATTENZIONE", err.getvalue(),
                              "il fallback su config malformato è silenzioso")
                self.assertEqual(result, td.parent)   # fallback alla cartella padre
        finally:
            if prev is not None:
                os.environ["SCOPE_TARGET_REPO"] = prev


# ===========================================================================
# F5 — Smoke test della generazione HTML (report-html.py)
# Il modulo è ~64% template: prima i test coprivano riconciliazione e criticità
# ma NON la generazione del dashboard. Un refuso nel template non veniva
# intercettato. Questi test verificano che generate_html() produca un HTML
# completo (placeholder tutti sostituiti) e ben formato, da dati sintetici.
# ===========================================================================


def _fake_suite(suite="send"):
    """Una suite minima ma completa per render_suite + generate_html."""
    endpoints = [
        {"operation_id": "createWidget", "method": "POST", "path": "/widgets",
         "service": "pn-widget", "spec_name": "widget-api", "visibility": "public",
         "category": "real", "static": True, "tag": True, "depth": 5, "crit": "core"},
        {"operation_id": "deleteWidget", "method": "DELETE", "path": "/widgets/{id}",
         "service": "pn-widget", "spec_name": "widget-api", "visibility": "internal",
         "category": "phantom", "static": True, "tag": False, "depth": 0, "crit": "standard"},
    ]
    return {
        "suite": suite, "total": 3, "static_covered": 2,
        "real": 1, "phantom": 1, "never": 1,
        "has_tag": True, "endpoints": endpoints,
        "runners": [{"name": "MiniRunner", "operation_ids": ["createWidget"]}],
        "tags": [{"tag": "happy", "scenarios": 3, "operation_ids": ["createWidget"]}],
        "scenarios_index": [{"name": "Scenario X", "file": "x.feature", "line": 1,
                             "tags": ["happy"], "ops": ["createWidget"]}],
        "endpoint_scenarios": {"createWidget": [0]},
        "repo_root": "/repo", "trend": [],
    }


class TestGuide(unittest.TestCase):
    """compute_guide: greedy set-cover per runner, nuovo runner (tag), gap."""

    def setUp(self):
        self.all_ops = {"a", "b", "c", "d"}     # d = mai raggiunto da alcun tag
        self.runners = [
            {"name": "R1", "operation_ids": ["a", "b"], "include_tags": ["t1"]},
            {"name": "R2", "operation_ids": ["b", "c"], "include_tags": ["t2"]},
        ]
        self.tags = [
            {"tag": "t1", "operation_ids": ["a"]},
            {"tag": "t2", "operation_ids": ["b", "c"]},
            {"tag": "t3", "operation_ids": ["c"]},
        ]
        self.op_cat = {"a": "real", "b": "real", "c": "phantom", "d": "never"}
        self.op_svc = {"a": "svc-A", "b": "svc-B", "c": "svc-A", "d": "svc-Z"}

    def _g(self):
        return reporthtml.compute_guide(self.all_ops, self.runners, self.tags,
                                        self.op_cat, self.op_svc)

    def test_new_runner_tags_carry_services(self):
        """Ogni tag del nuovo runner porta i microservizi che copre (Task 1)."""
        g = self._g()
        t2 = next(s for s in g["new_runner_tags"] if s["name"] == "t2")
        self.assertEqual(t2["extra"]["services"], ["svc-A", "svc-B"])  # b→B, c→A

    def test_runner_greedy_order_and_ceiling(self):
        g = self._g()
        names = [s["name"] for s in g["runners"]]
        self.assertEqual(names, ["R1", "R2"])        # R1 (+2) poi R2 (+1)
        self.assertEqual(g["runners"][0]["gain"], 2)
        self.assertEqual(g["runners"][1]["gain"], 1)
        self.assertEqual(g["runners_ceiling"], 3)    # a,b,c (non d)

    def test_new_runner_minimal_tags(self):
        g = self._g()
        names = [s["name"] for s in g["new_runner_tags"]]
        self.assertEqual(names[0], "t2")             # t2 copre 2 (b,c) → primo
        self.assertIn("t1", names)                   # poi t1 per 'a'
        self.assertNotIn("t3", names)                # t3 ridondante (c già preso)
        self.assertEqual(g["tags_ceiling"], 3)

    def test_gap_split_by_category(self):
        g = self._g()
        self.assertEqual(g["gap_total"], 1)          # solo 'd'
        self.assertEqual(g["gap_never"], ["d"])
        self.assertEqual(g["gap_phantom"], [])

    def test_control_tags_excluded_from_new_runner(self):
        """Un tag che i runner ESCLUDONO (es. @ignore) non va raccomandato come
        @IncludeTags, anche se 'coprirebbe' molti endpoint (scenari disabilitati)."""
        runners = self.runners + [{"name": "R3", "operation_ids": ["a"],
                                   "include_tags": ["t1"], "exclude_tags": ["ignore"]}]
        tags = self.tags + [{"tag": "ignore", "operation_ids": ["a", "b", "c"]}]
        g = reporthtml.compute_guide(self.all_ops, runners, tags, self.op_cat)
        names = [s["name"] for s in g["new_runner_tags"]]
        self.assertNotIn("ignore", names, "il control-tag @ignore è stato raccomandato")

    def test_guide_tab_rendered(self):
        s = _fake_suite("send")
        s["guide"] = self._g()
        html = reporthtml.render_guide(s)
        self.assertIn("Runner esistenti da lanciare", html)
        self.assertIn("@IncludeTags", html)
        self.assertIn("Gap:", html)


class TestScenarioRunners(unittest.TestCase):
    """compute_scenario_runners: per scenario i runner che lo eseguono + orfani."""

    def test_runner_matching_and_orphans(self):
        runners = [
            {"name": "R1", "include_tags": ["a"], "exclude_tags": []},
            {"name": "R2", "include_tags": ["b"], "exclude_tags": ["skip"]},
        ]
        scen = [
            {"name": "s1", "tags": ["a"]},            # solo R1
            {"name": "s2", "tags": ["a", "b"]},       # R1 e R2
            {"name": "s3", "tags": ["b", "skip"]},    # R2 esclude 'skip' → nessuno
            {"name": "s4", "tags": ["z"]},            # nessun include → orfano
        ]
        orphans = reporthtml.compute_scenario_runners(scen, runners)
        self.assertEqual(scen[0]["runners"], ["R1"])
        self.assertEqual(scen[1]["runners"], ["R1", "R2"])
        self.assertEqual(scen[2]["runners"], [])      # escluso da R2
        self.assertEqual(scen[3]["runners"], [])
        self.assertEqual(orphans, 2)                  # s3 e s4

    def test_include_empty_matches_all(self):
        """Un runner senza @IncludeTags esegue tutti (tranne gli esclusi)."""
        runners = [{"name": "All", "include_tags": [], "exclude_tags": ["ignore"]}]
        scen = [{"name": "s1", "tags": ["x"]}, {"name": "s2", "tags": ["ignore"]}]
        orphans = reporthtml.compute_scenario_runners(scen, runners)
        self.assertEqual(scen[0]["runners"], ["All"])
        self.assertEqual(scen[1]["runners"], [])      # escluso
        self.assertEqual(orphans, 1)


class TestTagSuggestions(unittest.TestCase):
    """compute_tag_suggestions: in quale tag mettere un endpoint NON coperto,
    fondato sull'evidenza (famiglia di versione + microservizio)."""

    def _eps(self, *specs):
        # spec: (op, service, category)
        return [{"operation_id": o, "service": s, "category": c} for o, s, c in specs]

    def test_version_family_suggestion(self):
        eps = self._eps(
            ("getFooV20", "svc-A", "never"),   # non coperto (target)
            ("getFooV21", "svc-A", "real"),    # versione sorella coperta
            ("getFooV23", "svc-A", "real"),
        )
        tags = [{"tag": "tA", "operation_ids": ["getFooV21", "getFooV23"]}]
        out = reporthtml.compute_tag_suggestions(eps, tags)
        self.assertIn("getFooV20", out)
        top = out["getFooV20"][0]
        self.assertEqual(top["tag"], "tA")
        self.assertEqual(top["kind"], "family")
        self.assertIn("famiglia", top["reason"])

    def test_service_fallback_when_no_family(self):
        eps = self._eps(
            ("createBar", "svc-B", "phantom"),  # non coperto, nessuna versione sorella
            ("getBar", "svc-B", "real"),        # stesso servizio, coperto
            ("listBar", "svc-B", "real"),
        )
        tags = [{"tag": "tB", "operation_ids": ["getBar", "listBar"]}]
        out = reporthtml.compute_tag_suggestions(eps, tags)
        top = out["createBar"][0]
        self.assertEqual(top["tag"], "tB")
        self.assertEqual(top["kind"], "service")
        self.assertIn("svc-B", top["reason"])

    def test_control_tags_excluded(self):
        eps = self._eps(("getFooV20", "svc-A", "never"), ("getFooV21", "svc-A", "real"))
        tags = [{"tag": "ignore", "operation_ids": ["getFooV21"]}]
        out = reporthtml.compute_tag_suggestions(eps, tags, control_tags={"ignore"})
        self.assertNotIn("getFooV20", out)  # nessun suggerimento da control-tag

    def test_only_uncovered_get_suggestions(self):
        eps = self._eps(("getFooV21", "svc-A", "real"))
        out = reporthtml.compute_tag_suggestions(eps, [{"tag": "tA", "operation_ids": ["getFooV21"]}])
        self.assertEqual(out, {})  # i coperti non ricevono suggerimenti


class TestHtmlSmoke(unittest.TestCase):

    def _placeholders(self, html):
        return [p for p in ("__TIMESTAMP__", "__SWITCHER__", "__SUITES__",
                            "__DATA__", "__CRIT__", "__EXEC__") if p in html]

    def test_executive_summary_present(self):
        html = reporthtml.generate_html([_fake_suite("send")])
        self.assertIn("Sintesi esecutiva", html)
        self.assertIn("class=\"exec\"", html)
        self.assertIn("@media print", html)        # one-pager via stampa
        self.assertIn("Pesata", html)

    def test_scenari_tab_present(self):
        s = _fake_suite("send")
        s["scen_orphans"] = 0
        html = reporthtml.generate_html([s])
        self.assertIn('data-tab="scenari"', html)        # il tab esiste
        self.assertIn("function renderScenari", html)    # il renderer è inlinato
        self.assertIn("compute_scenario_runners", _src_report())  # il dato è calcolato

    def test_output_is_self_contained(self):
        """L'output resta UN file auto-consistente: CSS/JS inlinati, nessuna
        risorsa esterna, nessun placeholder residuo (anche dopo l'estrazione
        di dashboard.js/dashboard.css come sorgenti separati)."""
        html = reporthtml.generate_html([_fake_suite("send")])
        self.assertIn("<style>", html)
        self.assertIn("<script>", html)
        self.assertNotIn("__CSS__", html)
        self.assertNotIn("__JS__", html)
        self.assertNotIn("<link ", html.lower())          # niente CSS esterno
        self.assertNotIn("<script src", html.lower())      # niente JS esterno

    def test_js_syntax_valid(self):
        """dashboard.js deve avere sintassi valida. Se `node` è disponibile lo
        usa (controllo vero); altrimenti verifica almeno il bilanciamento di
        graffe/parentesi (catch dei refusi grossolani, es. una regex rotta)."""
        import shutil
        import subprocess
        js_path = TOOL_DIR / "src" / "scope" / "assets" / "dashboard.js"
        self.assertTrue(js_path.exists(), "dashboard.js mancante")
        src = js_path.read_text()
        node = shutil.which("node")
        if node:
            r = subprocess.run([node, "--check", str(js_path)],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"node --check: {r.stderr}")
        else:
            # fallback pure-Python: graffe/parentesi/quadre bilanciate
            # (ignora il contenuto di stringhe/regex con un mini-scanner)
            depth = {"{": 0, "(": 0, "[": 0}
            pairs = {"}": "{", ")": "(", "]": "["}
            i, n = 0, len(src)
            while i < n:
                c = src[i]
                if c in "\"'`":
                    q = c
                    i += 1
                    while i < n and src[i] != q:
                        i += 2 if src[i] == "\\" else 1
                elif c in "{([":
                    depth[c] += 1
                elif c in pairs:
                    depth[pairs[c]] -= 1
                i += 1
            self.assertEqual([d for d in depth.values() if d != 0], [],
                             f"delimitatori sbilanciati in dashboard.js: {depth}")

    def test_every_column_header_has_tooltip(self):
        """Vision 'ogni perché ha risposta': ogni intestazione di colonna (<th>)
        delle tabelle deve avere un title (tooltip). Vale anche per le tabelle
        renderizzate client-side: si verifica il sorgente report.py."""
        import re as _re
        src = (TOOL_DIR / "src" / "scope" / "report.py").read_text()
        # <th ...> di colonna (esclude <thead>); deve contenere title=
        ths = _re.findall(r'<th(?![ae])[^>]*>', src)
        no_title = [th for th in ths if "title=" not in th]
        self.assertEqual(no_title, [], f"colonne senza tooltip: {no_title}")

    def test_every_kpi_card_has_tooltip(self):
        """Ogni KPI card (<div class="card ...">) deve avere un title (tooltip):
        l'utente deve poter capire ogni metrica senza leggere la FAQ."""
        import re as _re
        src = (TOOL_DIR / "src" / "scope" / "report.py").read_text()
        cards = _re.findall(r'<div class="card [^"]*"[^>]*>', src)
        no_title = [c for c in cards if "title=" not in c]
        self.assertEqual(no_title, [], f"KPI card senza tooltip: {no_title}")

    def test_every_tab_has_tooltip(self):
        """Ogni tab di navigazione (<div class="tab ...">) deve avere un title."""
        import re as _re
        src = (TOOL_DIR / "src" / "scope" / "report.py").read_text()
        tabs = _re.findall(r'<div class="tab(?: active)?"[^>]*>', src)
        no_title = [t for t in tabs if "title=" not in t]
        self.assertEqual(no_title, [], f"tab senza tooltip: {no_title}")

    def test_no_json_stringify_inside_onclick(self):
        """Regressione: JSON.stringify dentro onclick="..." produce doppi apici
        che TRONCANO l'attributo HTML (bug del link 'filtra nel tab Endpoint').
        Per gli argomenti in onclick si usa jsArg() (apici singoli + escaping)."""
        import re as _re
        src = (TOOL_DIR / "src" / "scope" / "assets" / "dashboard.js").read_text()
        bad = _re.findall(r'onclick="[^"]*JSON\.stringify', src)
        self.assertEqual(bad, [], f"JSON.stringify dentro onclick (usa jsArg): {bad}")

    def test_single_suite_html_is_complete(self):
        html = reporthtml.generate_html([_fake_suite("send")])
        self.assertGreater(len(html), 5000, "HTML sospettosamente corto")
        self.assertIn("<html", html.lower())
        self.assertIn("</html>", html.lower())
        self.assertEqual(self._placeholders(html), [],
                         "placeholder non sostituiti nel template")

    def test_endpoint_data_embedded(self):
        html = reporthtml.generate_html([_fake_suite("send")])
        self.assertIn("createWidget", html)   # l'op finisce nel DATA JSON inline

    def test_embedded_data_is_valid_json(self):
        """Il blocco __DATA__ deve essere JSON valido: un refuso lo romperebbe."""
        import re as _re
        html = reporthtml.generate_html([_fake_suite("send")])
        m = _re.search(r'const DATA\s*=\s*(\{.*?\});', html, _re.DOTALL)
        self.assertIsNotNone(m, "blocco DATA non trovato nel template")
        data = json.loads(m.group(1))   # solleva se non è JSON valido
        self.assertIn("send", data)

    def test_multi_suite_has_switcher(self):
        html = reporthtml.generate_html([_fake_suite("send"), _fake_suite("interop")])
        self.assertIn("switchSuite", html)
        self.assertEqual(self._placeholders(html), [])

    def test_scenario_detail_replaces_modal(self):
        """Il drill-down scenari è una vista in-pane (master→detail), non un dialog:
        il modal è rimosso e ci sono le funzioni del nuovo modello."""
        html = reporthtml.generate_html([_fake_suite("send")])
        self.assertNotIn("modal-backdrop", html)       # niente più overlay dialog
        self.assertIn("function openTagDetail", html)  # tab Tag master→detail
        self.assertIn("function showEndpoint", html)   # dettaglio endpoint (master→detail)
        self.assertIn("function notCoveredHtml", html) # box delta copertura per non coperti
        self.assertIn("function logicalBase", html)    # endpoint logici vs varianti
        self.assertIn('"tags"', html)                  # i tag degli scenari nel DATA
        self.assertIn("function toggleScenEps", html)  # contributo endpoint per scenario
        self.assertIn('"ops"', html)                   # gli ops per scenario nel DATA

    def test_long_column_wrapping_present(self):
        """Le colonne path/operationId lunghe devono andare a capo: il template
        deve contenere il CSS (overflow-wrap + min-width) e l'helper pathHtml
        che inserisce <wbr> ai separatori. Guardia anti-rimozione accidentale."""
        html = reporthtml.generate_html([_fake_suite("send")])
        self.assertIn("overflow-wrap: break-word", html)
        self.assertIn(".path", html)
        self.assertIn(".opid", html)
        self.assertIn("function pathHtml", html)
        self.assertIn("<wbr>", html)   # nel corpo della funzione di rendering


# ===========================================================================
# F4 — Prune di reports/: la logica di selezione (pura) non deve MAI eliminare
# l'ultimo run del giorno (preserva il trend), né i run di oggi, né html/file.
# ===========================================================================



class TestPruneSelection(unittest.TestCase):
    TODAY = "20260614"

    def test_keeps_last_of_day_per_kind(self):
        names = [
            "20260612_090000_send", "20260612_091500_send", "20260612_093000_send",  # 3 run
            "20260612_090000_send_tags", "20260612_095000_send_tags",                # 2 run
        ]
        victims = prune_reports.select_dirs_to_prune(names, self.TODAY)
        # tenuto l'ultimo di ogni (giorno, tipo); eliminati i precedenti
        self.assertEqual(set(victims), {
            "20260612_090000_send", "20260612_091500_send",
            "20260612_090000_send_tags",
        })

    def test_today_is_untouched(self):
        names = [f"{self.TODAY}_080000_send", f"{self.TODAY}_090000_send",
                 f"{self.TODAY}_100000_send"]
        self.assertEqual(prune_reports.select_dirs_to_prune(names, self.TODAY), [])

    def test_static_and_tags_are_separate_kinds(self):
        # send e send_tags dello stesso giorno NON competono tra loro
        names = ["20260601_080000_send", "20260601_090000_send",
                 "20260601_080000_send_tags"]
        victims = prune_reports.select_dirs_to_prune(names, self.TODAY)
        self.assertEqual(victims, ["20260601_080000_send"])  # solo il send più vecchio

    def test_interop_and_send_separate(self):
        names = ["20260601_080000_send", "20260601_090000_interop"]
        # uno per tipo, ciascuno ultimo del suo giorno → niente da eliminare
        self.assertEqual(prune_reports.select_dirs_to_prune(names, self.TODAY), [])

    def test_non_run_dirs_never_touched(self):
        names = ["html", "trend-baseline.txt", "random_folder",
                 "20260601_080000_send", "20260601_090000_send"]
        victims = prune_reports.select_dirs_to_prune(names, self.TODAY)
        self.assertNotIn("html", victims)
        self.assertNotIn("trend-baseline.txt", victims)
        self.assertNotIn("random_folder", victims)
        self.assertEqual(victims, ["20260601_080000_send"])

    def test_multi_day_keeps_one_per_day(self):
        names = [
            "20260610_080000_send", "20260610_090000_send",   # giorno 10
            "20260611_080000_send", "20260611_090000_send",   # giorno 11
        ]
        victims = prune_reports.select_dirs_to_prune(names, self.TODAY)
        # tenuto l'ultimo di ciascun giorno → 2 punti di trend preservati
        self.assertEqual(set(victims),
                         {"20260610_080000_send", "20260611_080000_send"})


class TestPruneDashboards(unittest.TestCase):

    def test_keeps_n_most_recent(self):
        names = [f"coverage-dashboard-2026061{i}_120000.html" for i in range(6)]
        victims = prune_reports.select_dashboards_to_prune(names, keep=3)
        # tenuti i 3 con timestamp maggiore (i3,i4,i5), eliminati i3 più vecchi
        self.assertEqual(victims, [
            "coverage-dashboard-20260610_120000.html",
            "coverage-dashboard-20260611_120000.html",
            "coverage-dashboard-20260612_120000.html",
        ])

    def test_under_keep_threshold_noop(self):
        names = ["coverage-dashboard-20260614_120000.html"]
        self.assertEqual(prune_reports.select_dashboards_to_prune(names, keep=3), [])

    def test_ignores_non_dashboard_files(self):
        names = ["index.html", "note.txt", "coverage-dashboard-20260614_120000.html"]
        self.assertEqual(prune_reports.select_dashboards_to_prune(names, keep=0),
                         ["coverage-dashboard-20260614_120000.html"])


# ===========================================================================
# F7 — Invariante reachable ⊆ static (directly_invoked) anche con le FAMIGLIE
# di versioni. L'attribuzione conservativa "famiglia intera senza token" è il
# punto in cui un op potrebbe risultare reachable senza essere invocato nel
# codice. Qui si verifica che il pipeline mantenga l'invariante sulle fixture
# (che includono lo scenario "Stream senza versione dichiarata" → intera
# famiglia). Il guard a runtime su dati veri è il warning cross-layer di
# report-html (reachable_not_static), già presente.
# ===========================================================================


class TestReachSubsetStaticInvariant(unittest.TestCase):

    def test_family_scenario_keeps_invariant(self):
        p = run_pipeline()
        resolver = p["name_to_ops"]            # è l'OpResolver
        directly = resolver.directly_invoked()
        reach = set()
        for s in p["scenarios"].values():
            reach |= (s["api_calls"] & KNOWN_OPS)
        # la fixture "Stream senza versione dichiarata" accredita l'intera
        # famiglia consumeFakeStream*: tutti i membri devono essere invocati
        # nel codice (MiniApiClient li chiama) → invariante rispettato
        self.assertTrue({"consumeFakeStream", "consumeFakeStreamV1",
                         "consumeFakeStreamV2"} <= reach,
                        "la famiglia non è attribuita come atteso")
        violations = reach - directly
        self.assertEqual(violations, set(),
                         f"reach⊄static: op reachable ma non invocati nel codice: {violations}")

    def test_report_html_exposes_violations(self):
        """Il guard permanente: report-html calcola reachable_not_static. Una
        violazione (anche da famiglia) NON resta silenziosa."""
        suite = {
            "operation_id": "x", "method": "GET", "path": "/x",
            "service": "s", "spec_name": "sp", "visibility": "public",
        }
        # costruisco un cov con 1 op covered e un tag che ne raggiunge 2:
        # il secondo (reachable ma non static) deve finire in reachable_not_static
        cov = [dict(suite, operation_id="a", covered=True),
               dict(suite, operation_id="b", covered=False)]
        tag = {"runners": [{"name": "R", "operation_ids": ["a", "b"]}],
               "tags": [], "endpoint_depth": {}, "scenarios_index": [],
               "endpoint_scenarios": {"a": [0], "b": [0]}, "repo_root": ""}
        res = reporthtml.reconcile("send", cov, tag, "", "")
        self.assertIn("b", res["reachable_not_static"],
                      "il guard cross-layer non espone la violazione")


if __name__ == "__main__":
    unittest.main(verbosity=2)
