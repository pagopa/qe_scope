#!/usr/bin/env python3
"""Generate a self-contained HTML dashboard from the durable AI metrics dataset."""

from __future__ import annotations

import argparse
import html
import json
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def _load_dataset(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"executions": []}
    return value if isinstance(value, dict) else {"executions": []}


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _seconds_between(start: Any, end: Any) -> float | None:
    first = _datetime(start)
    second = _datetime(end)
    if first is None or second is None:
        return None
    return max(0.0, (second - first).total_seconds())


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _format_number(value: int | float | None, decimals: int = 0) -> str:
    if value is None:
        return "n/d"
    formatted = f"{value:,.{decimals}f}"
    return formatted.replace(",", "X").replace(".", ",").replace("X", ".")


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "n/d"
    rounded = int(round(seconds))
    if rounded < 60:
        return f"{rounded} s"
    minutes, remainder = divmod(rounded, 60)
    if minutes < 60:
        return f"{minutes} min {remainder:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"


def _format_percent(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "n/d"
    return f"{numerator / denominator * 100:.0f}%"


def _plural(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


def _format_date(value: Any) -> str:
    parsed = _datetime(value)
    if parsed is None:
        return "n/d"
    return parsed.strftime("%d/%m/%Y %H:%M UTC")


def _model(execution: dict[str, Any]) -> str:
    copilot = execution.get("copilot") or {}
    for key in ("resolved_models", "requested_models"):
        values = copilot.get(key)
        if isinstance(values, list) and values:
            return str(values[0])
    return "non disponibile"


def _safe_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    return value if parsed.scheme in ("http", "https") else None


def _link(value: Any, label: str) -> str:
    url = _safe_url(value)
    escaped_label = html.escape(label)
    if url is None:
        return escaped_label
    return (
        f'<a href="{html.escape(url, quote=True)}" target="_blank" '
        f'rel="noopener noreferrer">{escaped_label}</a>'
    )


def _badge(label: str, style: str) -> str:
    return f'<span class="badge badge-{style}">{html.escape(label)}</span>'


def _outcome(execution: dict[str, Any]) -> tuple[str, str]:
    run = execution.get("run") or {}
    pr = execution.get("pull_request") or {}
    conclusion = str(run.get("conclusion") or "").lower()
    state = str(pr.get("state") or "").upper()
    if conclusion and conclusion != "success":
        return "Esecuzione fallita", "danger"
    if state == "MERGED":
        return "Merged", "success"
    if pr:
        return ("Draft PR" if pr.get("is_draft") else "PR aperta"), "warning"
    if conclusion == "success":
        return "Nessuna PR", "warning"
    return "In corso", "neutral"


def _bar_rows(counter: Counter[str], total: int) -> str:
    if not counter:
        return '<p class="empty">Nessun dato disponibile.</p>'
    rows = []
    maximum = max(counter.values())
    for label, count in counter.most_common():
        width = 100 * count / maximum
        share = _format_percent(count, total)
        rows.append(
            "".join(
                [
                    '<div class="bar-row">',
                    f'<div class="bar-label" title="{html.escape(label, quote=True)}">'
                    f"{html.escape(label)}</div>",
                    '<div class="bar-track"><div class="bar-fill" '
                    f'style="width:{width:.2f}%"></div></div>',
                    f'<div class="bar-value">{count} · {share}</div>',
                    "</div>",
                ]
            )
        )
    return "".join(rows)


def _table_rows(executions: list[dict[str, Any]]) -> str:
    rows = []
    for execution in reversed(executions):
        run = execution.get("run") or {}
        issue = execution.get("issue") or {}
        pr = execution.get("pull_request") or {}
        copilot = execution.get("copilot") or {}
        model = _model(execution)
        outcome_label, outcome_style = _outcome(execution)
        jira_key = str(issue.get("jira_key") or "Senza Jira key")
        issue_number = issue.get("number")
        pr_number = pr.get("number")
        search = " ".join(
            [
                jira_key,
                str(issue.get("title") or ""),
                model,
                outcome_label,
                str(issue_number or ""),
                str(pr_number or ""),
            ]
        ).lower()
        lead_time = _seconds_between(run.get("created_at"), pr.get("created_at"))
        review_time = _seconds_between(pr.get("created_at"), pr.get("merged_at"))
        change_size = None
        additions = _number(pr.get("additions"))
        deletions = _number(pr.get("deletions"))
        if additions is not None or deletions is not None:
            change_size = int((additions or 0) + (deletions or 0))
        item_title = html.escape(str(issue.get("title") or "Titolo non disponibile"))
        issue_link = (
            _link(issue.get("url"), f"Issue #{issue_number}")
            if issue_number is not None
            else "n/d"
        )
        pr_link = (
            _link(pr.get("url"), f"PR #{pr_number}")
            if pr_number is not None
            else "n/d"
        )
        evidence_links = []
        if issue_number is not None and _safe_url(issue.get("url")):
            evidence_links.append(issue_link)
        if pr_number is not None and _safe_url(pr.get("url")):
            evidence_links.append(pr_link)
        if _safe_url(run.get("url")):
            evidence_links.append(_link(run.get("url"), "Run"))
        evidence = "".join(evidence_links) or "n/d"
        rows.append(
            f"""
            <tr data-search="{html.escape(search, quote=True)}"
                data-model="{html.escape(model, quote=True)}"
                data-outcome="{html.escape(outcome_label, quote=True)}">
              <td>
                <strong>{html.escape(jira_key)}</strong>
                <span class="secondary row-title">{item_title}</span>
              </td>
              <td>{_badge(outcome_label, outcome_style)}</td>
              <td>{html.escape(model)}</td>
              <td class="numeric">{_format_number(_number(copilot.get("input_tokens")))}</td>
              <td class="numeric">{_format_number(_number(copilot.get("output_tokens")))}</td>
              <td class="numeric">{_format_duration(_number(run.get("copilot_duration_seconds")))}</td>
              <td class="numeric">{_format_duration(lead_time)}</td>
              <td class="numeric">{_format_duration(review_time)}</td>
              <td class="numeric">{_format_number(change_size)}</td>
              <td><span class="links">{evidence}</span></td>
            </tr>
            """
        )
    return "".join(rows) or (
        '<tr><td colspan="10" class="empty table-empty">'
        "Nessuna esecuzione raccolta.</td></tr>"
    )


def render_dashboard(dataset: dict[str, Any]) -> str:
    executions = [
        item
        for item in dataset.get("executions", [])
        if isinstance(item, dict)
    ]
    executions.sort(
        key=lambda item: str((item.get("run") or {}).get("created_at") or "")
    )
    total = len(executions)
    successful = sum(
        str((item.get("run") or {}).get("conclusion") or "").lower()
        == "success"
        for item in executions
    )
    prs = [item for item in executions if item.get("pull_request")]
    merged = sum(
        str((item.get("pull_request") or {}).get("state") or "").upper()
        == "MERGED"
        for item in executions
    )
    input_tokens = sum(
        _number((item.get("copilot") or {}).get("input_tokens")) or 0
        for item in executions
    )
    output_tokens = sum(
        _number((item.get("copilot") or {}).get("output_tokens")) or 0
        for item in executions
    )
    cache_tokens = sum(
        _number((item.get("copilot") or {}).get("cache_read_input_tokens")) or 0
        for item in executions
    )
    ai_units_values = [
        value
        for item in executions
        if (value := _number((item.get("copilot") or {}).get("ai_units")))
        is not None
    ]
    agent_durations = [
        value
        for item in executions
        if (value := _number((item.get("run") or {}).get("copilot_duration_seconds")))
        is not None
    ]
    lead_times = [
        value
        for item in executions
        if (
            value := _seconds_between(
                (item.get("run") or {}).get("created_at"),
                (item.get("pull_request") or {}).get("created_at"),
            )
        )
        is not None
    ]
    review_times = [
        value
        for item in executions
        if (
            value := _seconds_between(
                (item.get("pull_request") or {}).get("created_at"),
                (item.get("pull_request") or {}).get("merged_at"),
            )
        )
        is not None
    ]
    models = Counter(_model(item) for item in executions)
    outcomes = Counter(_outcome(item)[0] for item in executions)
    missing_telemetry = sum(
        str((item.get("copilot") or {}).get("telemetry_status") or "")
        not in ("complete", "fallback_all_invocations")
        for item in executions
    )
    updated = _format_date(dataset.get("updated_at"))
    sample_message = (
        "Campione iniziale: leggere i valori come baseline tecnica, non come trend."
        if total < 10
        else "Campione sufficiente per iniziare a confrontare periodi e tipologie omogenee."
    )
    ai_units = sum(ai_units_values) if ai_units_values else None
    return f"""<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SCOPE · AI Coding Pilot</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #17233f;
      --muted: #62708a;
      --line: #dfe5ee;
      --canvas: #f3f6fa;
      --panel: #ffffff;
      --brand: #155eef;
      --brand-dark: #0d3eae;
      --success: #087443;
      --success-bg: #e7f6ee;
      --warning: #945f00;
      --warning-bg: #fff3d6;
      --danger: #b42318;
      --danger-bg: #feeceb;
      --neutral-bg: #eef2f7;
      --shadow: 0 8px 28px rgba(23, 35, 63, .08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--canvas);
      color: var(--ink);
      font: 15px/1.5 Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    header {{
      color: white;
      background: linear-gradient(125deg, #101b37 0%, #173b86 62%, #155eef 100%);
      padding: 42px max(24px, calc((100vw - 1240px) / 2));
    }}
    .eyebrow {{
      margin: 0 0 8px;
      color: #bcd1ff;
      font-size: 12px;
      font-weight: 800;
      letter-spacing: .13em;
      text-transform: uppercase;
    }}
    h1 {{ margin: 0; font-size: clamp(30px, 4vw, 48px); line-height: 1.08; letter-spacing: -.03em; }}
    .subtitle {{ max-width: 760px; margin: 14px 0 0; color: #dbe6ff; font-size: 17px; }}
    .updated {{ margin-top: 24px; color: #bcd1ff; font-size: 13px; }}
    main {{ max-width: 1288px; margin: 0 auto; padding: 28px 24px 56px; }}
    .notice {{
      display: flex;
      gap: 12px;
      align-items: flex-start;
      margin-bottom: 22px;
      padding: 15px 17px;
      border: 1px solid #bfd3ff;
      border-radius: 12px;
      background: #edf3ff;
      color: #173b86;
    }}
    .notice strong {{ display: block; margin-bottom: 2px; }}
    .notice-mark {{ font-size: 20px; line-height: 1.2; }}
    .kpis {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; }}
    .card {{
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: var(--panel);
      box-shadow: var(--shadow);
    }}
    .kpi {{ padding: 20px; }}
    .kpi-label {{ color: var(--muted); font-size: 13px; font-weight: 700; }}
    .kpi-value {{ margin-top: 6px; font-size: 31px; font-weight: 780; letter-spacing: -.03em; }}
    .kpi-detail {{ margin-top: 3px; color: var(--muted); font-size: 12px; }}
    .section {{ margin-top: 24px; }}
    .section-head {{ display: flex; justify-content: space-between; align-items: end; gap: 20px; margin-bottom: 12px; }}
    h2 {{ margin: 0; font-size: 21px; letter-spacing: -.015em; }}
    .section-note {{ margin: 3px 0 0; color: var(--muted); font-size: 13px; }}
    .split {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
    .chart {{ padding: 20px; }}
    .chart h3 {{ margin: 0 0 18px; font-size: 15px; }}
    .bar-row {{ display: grid; grid-template-columns: minmax(90px, 150px) 1fr 72px; gap: 12px; align-items: center; margin: 12px 0; }}
    .bar-label {{ overflow: hidden; font-size: 13px; font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }}
    .bar-track {{ height: 9px; overflow: hidden; border-radius: 99px; background: #e9eef6; }}
    .bar-fill {{ height: 100%; border-radius: inherit; background: linear-gradient(90deg, var(--brand-dark), #5f8ff5); }}
    .bar-value {{ color: var(--muted); font-size: 12px; text-align: right; }}
    .filters {{ display: flex; flex-wrap: wrap; gap: 10px; }}
    input, select {{
      min-height: 40px;
      border: 1px solid #cbd4e1;
      border-radius: 9px;
      background: white;
      color: var(--ink);
      font: inherit;
      padding: 8px 11px;
    }}
    input {{ width: min(320px, 100%); }}
    .table-wrap {{ overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; background: white; }}
    th, td {{ padding: 13px 12px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
    th {{ position: sticky; top: 0; background: #f8fafc; color: #4c5c76; font-size: 11px; letter-spacing: .04em; text-transform: uppercase; white-space: nowrap; }}
    tbody tr:last-child td {{ border-bottom: 0; }}
    tbody tr:hover {{ background: #f8fbff; }}
    .numeric {{ font-variant-numeric: tabular-nums; white-space: nowrap; }}
    .secondary {{ display: block; color: var(--muted); font-size: 12px; font-weight: 400; }}
    .row-title {{ max-width: 250px; margin-top: 3px; }}
    .badge {{ display: inline-flex; border-radius: 99px; padding: 3px 8px; font-size: 11px; font-weight: 750; white-space: nowrap; }}
    .badge-success {{ color: var(--success); background: var(--success-bg); }}
    .badge-warning {{ color: var(--warning); background: var(--warning-bg); }}
    .badge-danger {{ color: var(--danger); background: var(--danger-bg); }}
    .badge-neutral {{ color: #526078; background: var(--neutral-bg); }}
    .links {{ display: flex; flex-direction: column; gap: 3px; white-space: nowrap; }}
    a {{ color: var(--brand-dark); font-weight: 650; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .empty {{ color: var(--muted); }}
    .table-empty {{ padding: 32px; text-align: center; }}
    .footnotes {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 24px; }}
    .footnote {{ padding: 18px 20px; }}
    .footnote h3 {{ margin: 0 0 7px; font-size: 14px; }}
    .footnote p {{ margin: 0; color: var(--muted); font-size: 13px; }}
    .hidden {{ display: none; }}
    @media (max-width: 980px) {{ .kpis {{ grid-template-columns: repeat(2, 1fr); }} .split, .footnotes {{ grid-template-columns: 1fr; }} }}
    @media (max-width: 560px) {{ header {{ padding: 30px 20px; }} main {{ padding: 20px 14px 40px; }} .kpis {{ grid-template-columns: 1fr; }} .section-head {{ align-items: stretch; flex-direction: column; }} }}
    @media print {{ body {{ background: white; }} header {{ padding: 24px; }} main {{ max-width: none; padding: 20px 0; }} .card {{ box-shadow: none; }} .filters {{ display: none; }} }}
  </style>
</head>
<body>
  <header>
    <p class="eyebrow">SCOPE · Engineering effectiveness</p>
    <h1>AI Coding Pilot</h1>
    <p class="subtitle">Vista tecnica del flusso Jira → GitHub Copilot → Pull Request → review umana.</p>
    <div class="updated">Dati aggiornati: {html.escape(updated)}</div>
  </header>
  <main>
    <div class="notice">
      <div class="notice-mark" aria-hidden="true">ⓘ</div>
      <div><strong>{html.escape(sample_message)}</strong>La dashboard misura il flusso tecnico. Qualità, difetti evitati e tempo umano richiedono dati aggiuntivi e non sono dedotti da token o righe di codice.</div>
    </div>

    <section class="kpis" aria-label="Indicatori principali">
      <article class="card kpi"><div class="kpi-label">Esecuzioni AI</div><div class="kpi-value">{total}</div><div class="kpi-detail">Tentativi distinti acquisiti</div></article>
      <article class="card kpi"><div class="kpi-label">Workflow riusciti</div><div class="kpi-value">{_format_percent(successful, total)}</div><div class="kpi-detail">{successful} su {total} {_plural(total, "esecuzione", "esecuzioni")}</div></article>
      <article class="card kpi"><div class="kpi-label">PR prodotte</div><div class="kpi-value">{_format_percent(len(prs), total)}</div><div class="kpi-detail">{len(prs)} su {total} {_plural(total, "esecuzione", "esecuzioni")}</div></article>
      <article class="card kpi"><div class="kpi-label">PR mergiate</div><div class="kpi-value">{_format_percent(merged, len(prs))}</div><div class="kpi-detail">{merged} su {len(prs)} PR prodotte</div></article>
      <article class="card kpi"><div class="kpi-label">Mediana esecuzione agente</div><div class="kpi-value">{_format_duration(_median(agent_durations))}</div><div class="kpi-detail">Solo invocazione Copilot</div></article>
      <article class="card kpi"><div class="kpi-label">Mediana fino alla Draft PR</div><div class="kpi-value">{_format_duration(_median(lead_times))}</div><div class="kpi-detail">Avvio workflow → creazione PR</div></article>
      <article class="card kpi"><div class="kpi-label">Mediana review e merge</div><div class="kpi-value">{_format_duration(_median(review_times))}</div><div class="kpi-detail">Creazione PR → merge</div></article>
      <article class="card kpi"><div class="kpi-label">Token elaborati</div><div class="kpi-value">{_format_number(input_tokens + output_tokens)}</div><div class="kpi-detail">{_format_number(input_tokens)} input · {_format_number(output_tokens)} output</div></article>
    </section>

    <section class="section">
      <div class="section-head"><div><h2>Distribuzione del pilota</h2><p class="section-note">Modelli osservati ed esito corrente di ogni tentativo.</p></div></div>
      <div class="split">
        <article class="card chart"><h3>Modelli utilizzati</h3>{_bar_rows(models, total)}</article>
        <article class="card chart"><h3>Esito tecnico</h3>{_bar_rows(outcomes, total)}</article>
      </div>
    </section>

    <section class="section">
      <div class="section-head">
        <div><h2>Dettaglio esecuzioni</h2><p class="section-note">Risultati: <span id="visible-count">{total}</span> · link diretti alle evidenze GitHub.</p></div>
        <div class="filters">
          <input id="search" type="search" aria-label="Cerca esecuzioni" placeholder="Cerca Jira, issue, PR, modello…">
          <select id="model-filter" aria-label="Filtra per modello"><option value="">Tutti i modelli</option>{''.join(f'<option value="{html.escape(model, quote=True)}">{html.escape(model)}</option>' for model in sorted(models))}</select>
          <select id="outcome-filter" aria-label="Filtra per esito"><option value="">Tutti gli esiti</option>{''.join(f'<option value="{html.escape(outcome, quote=True)}">{html.escape(outcome)}</option>' for outcome in sorted(outcomes))}</select>
        </div>
      </div>
      <div class="card table-wrap">
        <table>
          <thead><tr><th>Attività</th><th>Esito</th><th>Modello</th><th>Input token</th><th>Output token</th><th>Agente</th><th>Fino a PR</th><th>Review</th><th>Righe ±</th><th>Evidenze</th></tr></thead>
          <tbody id="executions">{_table_rows(executions)}</tbody>
        </table>
      </div>
    </section>

    <section class="footnotes">
      <article class="card footnote"><h3>Consumo osservato</h3><p>Cache read: {_format_number(cache_tokens)} token · AI units: {_format_number(ai_units, 3)} · Telemetria incompleta: {missing_telemetry} {_plural(missing_telemetry, "esecuzione", "esecuzioni")}.</p></article>
      <article class="card footnote"><h3>Come leggere i tempi</h3><p>“Agente” misura l’invocazione Copilot. “Fino a PR” include validazioni e pubblicazione. “Review” include l’attesa e il lavoro umano fino al merge.</p></article>
    </section>
  </main>
  <script>
    (() => {{
      const rows = [...document.querySelectorAll('#executions tr[data-search]')];
      const search = document.querySelector('#search');
      const model = document.querySelector('#model-filter');
      const outcome = document.querySelector('#outcome-filter');
      const count = document.querySelector('#visible-count');
      const apply = () => {{
        const query = search.value.trim().toLowerCase();
        let visible = 0;
        rows.forEach(row => {{
          const show = (!query || row.dataset.search.includes(query)) &&
            (!model.value || row.dataset.model === model.value) &&
            (!outcome.value || row.dataset.outcome === outcome.value);
          row.classList.toggle('hidden', !show);
          if (show) visible += 1;
        }});
        count.textContent = visible;
      }};
      [search, model, outcome].forEach(control => control.addEventListener('input', apply));
    }})();
  </script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_dashboard(_load_dataset(args.input)))


if __name__ == "__main__":
    main()
