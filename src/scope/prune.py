#!/usr/bin/env python3
"""
Prune di reports/ — tiene la crescita sotto controllo SENZA rovinare il trend.

Il trend (report-html.py) di default usa "l'ultimo run del giorno" per suite:
quindi possiamo eliminare i run intra-giornata dei giorni PASSATI (i tanti run
di sviluppo) tenendo solo l'ultimo di ogni giorno, e lasciare intatto OGGI per
il debug corrente. La granularità del trend by-day resta identica.

Mai toccati: reports/html/ (i dashboard) e reports/trend-baseline.txt.

Default: DRY-RUN (mostra cosa eliminerebbe). Con --apply elimina davvero.

Uso:
    python3 prune-reports.py            # dry-run
    python3 prune-reports.py --apply    # elimina
"""

import argparse
import re
import shutil
import sys
from collections import defaultdict
from datetime import datetime

from .config import REPORTS_DIR

# 20260613_175809_send  /  20260612_091115_send_tags
RUN_RE = re.compile(r"^(\d{8})_(\d{6})_(.+)$")
# coverage-dashboard-20260614_072741.html
DASH_RE = re.compile(r"^coverage-dashboard-(\d{8}_\d{6})\.html$")

KEEP_DASHBOARDS = 3   # i dashboard sono snapshot self-contained: basta tenere i più recenti


def select_dirs_to_prune(names, today):
    """Funzione PURA. Dato l'elenco dei nomi di directory in reports/ e il
    giorno corrente (YYYYMMDD), ritorna i nomi da eliminare.

    Regola: per ogni (giorno, tipo) tiene solo il run più recente; elimina gli
    altri. Eccezione: i run di OGGI si tengono tutti. Nomi non-run (html, file)
    non vengono mai toccati.
    """
    groups = defaultdict(list)   # (day, kind) → [(ts, name)]
    for name in names:
        m = RUN_RE.match(name)
        if not m:
            continue                       # html, trend-baseline.txt, ecc.
        day, time, kind = m.group(1), m.group(2), m.group(3)
        if day == today:
            continue                       # oggi: si tiene tutto
        groups[(day, kind)].append((day + time, name))

    to_delete = []
    for (_day, _kind), runs in groups.items():
        runs.sort()                        # per timestamp crescente
        keep = runs[-1][1]                 # l'ultimo del giorno
        to_delete += [name for _ts, name in runs if name != keep]
    return sorted(to_delete)


def select_dashboards_to_prune(names, keep=KEEP_DASHBOARDS):
    """Funzione PURA. Dei dashboard HTML in reports/html/ tiene i `keep` più
    recenti (per timestamp nel nome) ed elimina i rimanenti."""
    dated = []
    for name in names:
        m = DASH_RE.match(name)
        if m:
            dated.append((m.group(1), name))
    dated.sort()                           # timestamp crescente
    # NB: dated[:-keep] è SBAGLIATO per keep=0 (diventa dated[:0]=vuoto).
    cut = len(dated) - keep
    return sorted(name for _ts, name in dated[:cut]) if cut > 0 else []


def main():
    ap = argparse.ArgumentParser(description="Prune dei report intra-giornata")
    ap.add_argument("--apply", action="store_true",
                    help="Elimina davvero (default: dry-run)")
    args = ap.parse_args()

    if not REPORTS_DIR.exists():
        sys.exit(f"  reports/ non esiste: {REPORTS_DIR}")

    names = [p.name for p in REPORTS_DIR.iterdir() if p.is_dir()]
    today = datetime.now().strftime("%Y%m%d")
    victims = select_dirs_to_prune(names, today)

    html_dir = REPORTS_DIR / "html"
    dash_names = [p.name for p in html_dir.iterdir()] if html_dir.exists() else []
    dash_victims = select_dashboards_to_prune(dash_names)

    if not victims and not dash_victims:
        print("  Niente da fare: nessun run intra-giornata né dashboard in eccesso.")
        return

    print(f"  reports/: {len(names)} directory di run, {len(victims)} da eliminare "
          f"(intra-giornata di giorni passati; tenuto l'ultimo di ogni giorno + oggi)")
    for v in victims:
        if args.apply:
            shutil.rmtree(REPORTS_DIR / v)
            print(f"    🗑  {v}")
        else:
            print(f"    (dry-run) {v}")

    if dash_victims:
        print(f"  reports/html/: {len(dash_names)} dashboard, {len(dash_victims)} da eliminare "
              f"(tenuti i {KEEP_DASHBOARDS} più recenti)")
        for v in dash_victims:
            if args.apply:
                (html_dir / v).unlink()
                print(f"    🗑  html/{v}")
            else:
                print(f"    (dry-run) html/{v}")

    n = len(victims) + len(dash_victims)
    if args.apply:
        print(f"\n  ✅ Eliminati {n} elementi. Trend by-day e ultimi dashboard preservati.")
    else:
        print("\n  Dry-run: nessuna eliminazione. Rilancia con --apply per procedere.")


if __name__ == "__main__":
    main()
