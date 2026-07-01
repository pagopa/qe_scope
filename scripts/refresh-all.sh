#!/usr/bin/env bash
# Rigenera tutti i report di copertura E2E e apre il dashboard HTML.
#
# Uso:
#   ./scripts/refresh-all.sh              # entrambe le suite + dashboard
#   ./scripts/refresh-all.sh send         # solo SEND + dashboard
#   ./scripts/refresh-all.sh interop      # solo Interop + dashboard
#   ./scripts/refresh-all.sh --no-open    # non aprire il browser
#
# Pipeline: scope.inventory (statico) -> scope.tag_coverage (reale) -> scope.report

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
cd "$ROOT"

SUITES=(send interop)
OPEN="--open"

for arg in "$@"; do
  case "$arg" in
    send|interop) SUITES=("$arg") ;;
    --no-open)    OPEN="" ;;
    *) echo "Argomento non riconosciuto: $arg"; exit 1 ;;
  esac
done

echo ""
echo "═══════════════════════════════════════════════════"
echo "  0 — Auto-test di SCOPE (pytest)"
echo "═══════════════════════════════════════════════════"
python3 -m pytest 2>&1 | tail -3

for s in "${SUITES[@]}"; do
  echo ""
  echo "═══════════════════════════════════════════════════"
  echo "  [$s] 1/2 — Inventario endpoint (scope.inventory)"
  echo "═══════════════════════════════════════════════════"
  python3 -m scope.inventory --suite "$s"

  echo ""
  echo "═══════════════════════════════════════════════════"
  echo "  [$s] 2/2 — Tracciamento tag (scope.tag_coverage)"
  echo "═══════════════════════════════════════════════════"
  python3 -m scope.tag_coverage --suite "$s"
done

echo ""
echo "═══════════════════════════════════════════════════"
echo "  Esiti di esecuzione (scope.runtime) — ingest inbox"
echo "═══════════════════════════════════════════════════"
python3 -m scope.runtime

echo ""
echo "═══════════════════════════════════════════════════"
echo "  Dashboard HTML (scope.report)"
echo "═══════════════════════════════════════════════════"
python3 -m scope.report $OPEN
