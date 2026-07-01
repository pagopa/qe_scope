#!/usr/bin/env bash
# CI guard di SCOPE — rende automatiche le guardie che già esistono.
#
# Esegue, in sequenza e in modalità "blocco":
#   0. golden test (pytest)
#   1. scope.inventory --strict   → integrità dell'inventario (lock-file)
#   2. scope.tag_coverage --strict → sanity della copertura reale
# per ogni suite richiesta. Exit non-zero se UNA QUALSIASI guardia fallisce:
# pensato per uno scheduler/CI, che l'utente aggancia (SCOPE non fa mai
# operazioni remote).
#
# Esegue TUTTE le guardie anche in presenza di fallimenti (non si ferma alla
# prima) per dare un quadro completo in un solo giro.
#
# Uso:
#   ./ci-check.sh                 # entrambe le suite
#   ./ci-check.sh send            # solo SEND
#   SCOPE_TARGET_REPO=/path ./ci-check.sh
#
# Prerequisito: SCOPE_TARGET_REPO o config.yaml con target_repo.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
cd "$ROOT"

if [ "$#" -gt 0 ]; then
  SUITES=("$@")
else
  SUITES=(send interop)
fi

fail=0

guard() {
  # guard "etichetta" comando args...
  local label="$1"; shift
  local out
  if out="$("$@" 2>&1)"; then
    echo "  ✅ $label"
  else
    echo "  ❌ $label — FALLITO"
    echo "$out" | grep -E "FAIL|❌|--strict|Error|Traceback|Assertion" | sed 's/^/        /' | head -25
    fail=1
  fi
}

echo "═══════════════════════════════════════════════════"
echo "  SCOPE CI — guardie di qualità (--strict)"
echo "═══════════════════════════════════════════════════"

echo ""
echo "[0] Golden test"
guard "golden test" python3 -m pytest

for s in "${SUITES[@]}"; do
  echo ""
  echo "[$s] Inventario + copertura reale"
  # inventory prima (tag_coverage legge il suo report come inventario)
  guard "[$s] inventario (scope.inventory --strict)"    python3 -m scope.inventory    --suite "$s" --strict
  guard "[$s] sanity reale (scope.tag_coverage --strict)" python3 -m scope.tag_coverage --suite "$s" --strict
done

echo ""
echo "[esecuzione] Sanity D2 (scope.runtime --strict)"
guard "sanity esecuzione (scope.runtime --strict)" python3 -m scope.runtime --strict

echo ""
echo "═══════════════════════════════════════════════════"
if [ "$fail" -eq 0 ]; then
  echo "  ✅ CI OK — tutte le guardie passate"
else
  echo "  ❌ CI FALLITA — almeno una guardia non è passata (vedi sopra)"
fi
echo "═══════════════════════════════════════════════════"
exit "$fail"
