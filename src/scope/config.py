#!/usr/bin/env python3
"""
Configurazione condivisa di SCOPE — un'unica fonte di verità per i percorsi.

Prima `coverage.py` e `tag-coverage.py` avevano ciascuno la propria copia di
`_resolve_target_repo()` e di `SUITE_CONFIG`, già divergenti nella forma
(src_dirs vs all_src_dirs/step_dirs/feature_dirs/runner_dirs): aggiungere una
suite o cambiare un percorso richiedeva due edit coordinati — la stessa classe
di bug che il parser unico (java_analysis.py) ha eliminato. Qui la config vive
in un posto solo; ogni script legge le chiavi che gli servono.

PROJECT_ROOT = root del repository SCOPE (dove stanno config.yaml, data/, reports/).
REPO_ROOT    = repo dei test da analizzare (mai modificato: SCOPE è read-only).
"""

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent          # src/scope


def _find_project_root() -> Path:
    """Root del progetto SCOPE: la cartella che contiene pyproject.toml,
    risalendo dal package. Fallback alla cwd se non trovata (es. wheel installata)."""
    for parent in [PACKAGE_DIR, *PACKAGE_DIR.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


PROJECT_ROOT = _find_project_root()
REPORTS_DIR = PROJECT_ROOT / "reports"
DATA_DIR = PROJECT_ROOT / "data"          # config versionata (criticality, spec-lock)

# Ingest dei report di esecuzione: cartella dove l'utente droppa i file
# per-suite; ledger + storia accumulata stanno in DATA_DIR (gitignored).
RUNTIME_INBOX_DIR = PROJECT_ROOT / "runtime" / "inbox"
# Finestra (giorni, da oggi) entro cui un esito di run conta per lo stato corrente.
# Analisi della salute del parco-test, non gate di rilascio → finestra generosa.
# Override con env SCOPE_RUNTIME_WINDOW (utile per rianalizzare storici).
RUNTIME_WINDOW_DAYS = int(os.environ.get("SCOPE_RUNTIME_WINDOW", "30"))


def _resolve_target_repo(project_root: Path = PROJECT_ROOT) -> Path:
    """Repo dei test da analizzare. Precedenza:
    env SCOPE_TARGET_REPO > config.yaml (target_repo) > cartella padre (legacy).
    Un config.yaml malformato emette un avviso esplicito invece di un fallback
    silenzioso sul repo sbagliato. `project_root` è una cucitura per i test."""
    import os
    import sys
    env = os.environ.get("SCOPE_TARGET_REPO")
    if env:
        return Path(env).expanduser().resolve()
    cfg = project_root / "config.yaml"
    if cfg.exists():
        try:
            import yaml
            data = yaml.safe_load(cfg.read_text()) or {}
            if data.get("target_repo"):
                return Path(data["target_repo"]).expanduser().resolve()
        except Exception as e:
            print(f"  ATTENZIONE: config.yaml illeggibile ({e}); "
                  f"fallback alla cartella padre di SCOPE", file=sys.stderr)
    return project_root.parent


REPO_ROOT = _resolve_target_repo()

# Namespace Maven (usato da coverage.py per il parsing del pom)
MAVEN_NS = {"m": "http://maven.apache.org/POM/4.0.0"}

# Config per suite — superset delle chiavi usate dai due script.
#   coverage.py    usa: pom, all_src_dirs, label
#   tag-coverage.py usa: pom, all_src_dirs, feature_dirs, runner_dirs, step_dirs, label
SUITE_CONFIG = {
    "send": {
        "pom": REPO_ROOT / "pom.xml",
        "all_src_dirs": [
            REPO_ROOT / "src" / "main" / "java",
            REPO_ROOT / "src" / "test" / "java",
        ],
        "feature_dirs": [REPO_ROOT / "src" / "test" / "resources"],
        "runner_dirs": [REPO_ROOT / "src" / "test" / "java" / "it" / "pagopa" / "pn" / "cucumber"],
        "step_dirs": [
            REPO_ROOT / "src" / "main" / "java",
            REPO_ROOT / "src" / "test" / "java",
        ],
        "label": "SEND",
    },
    "interop": {
        "pom": REPO_ROOT / "interop-qa-tests" / "pom.xml",
        "all_src_dirs": [
            REPO_ROOT / "interop-qa-tests" / "src" / "main" / "java",
            REPO_ROOT / "interop-qa-tests" / "src" / "test" / "java",
        ],
        "feature_dirs": [
            REPO_ROOT / "interop-qa-tests" / "src" / "test" / "resources",
            REPO_ROOT / "interop-qa-tests" / "src" / "main" / "resources",
        ],
        "runner_dirs": [
            REPO_ROOT / "interop-qa-tests" / "src" / "test" / "java" / "it" / "pagopa" / "pn" / "interop" / "cucumber",
        ],
        "step_dirs": [
            REPO_ROOT / "interop-qa-tests" / "src" / "test" / "java",
        ],
        "label": "Interop",
    },
}
