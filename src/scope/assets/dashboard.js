const DATA = __DATA__;

// Per-suite state for drilldown filtering
const state = {};
Object.keys(DATA).forEach(s => {
  state[s] = { filterType: null, filterName: null, filterOps: null, sortCol: null, sortAsc: true,
               svcSortCol: "path", svcSortAsc: true };
});

// Inverted index: suite -> operationId -> [[tag, scenarios], ...] sorted by scenarios desc
const opTags = {};
Object.keys(DATA).forEach(s => {
  opTags[s] = {};
  (DATA[s].tags || []).forEach(t => {
    (t.operation_ids || []).forEach(op => {
      (opTags[s][op] = opTags[s][op] || []).push([t.tag, t.scenarios || 0]);
    });
  });
  Object.values(opTags[s]).forEach(a => a.sort((x,y) => y[1]-x[1]));
});

function pct(n, d) { return d ? (n/d*100).toFixed(1) : "0.0"; }

// Stringa JS sicura come argomento dentro un attributo HTML onclick="...".
// JSON.stringify userebbe doppi apici che troncano l'attributo: qui si usano
// apici singoli, con escaping di backslash/apice e doppio apice → entità.
function jsArg(s) {
  return "'" + String(s == null ? "" : s)
    .replace(/\\/g, "\\\\").replace(/'/g, "\\'").replace(/"/g, "&quot;") + "'";
}

function copyText(id) {
  const el = document.getElementById(id);
  if (!el) return;
  const t = el.textContent;
  navigator.clipboard?.writeText(t).then(() => {
    const b = el.nextElementSibling;
    if (b) { const o = b.textContent; b.textContent = "✓ Copiato"; setTimeout(() => b.textContent = o, 1500); }
  });
}

// Inserisce break-opportunity (<wbr>) dopo i separatori / e - così i path
// lunghi vanno a capo AI CONFINI DEI SEGMENTI invece che a metà parola.
function pathHtml(p) {
  return String(p == null ? "" : p)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/([/\-])/g, "$1<wbr>");
}

function renderEndpoints(suite, filterCat, search) {
  const st = state[suite];
  let rows = DATA[suite].endpoints;

  // Category chip filter
  if (filterCat.size) rows = rows.filter(e => filterCat.has(e.category));

  // Text search
  if (search) {
    const s = search.toLowerCase();
    rows = rows.filter(e =>
      e.operation_id.toLowerCase().includes(s) ||
      e.path.toLowerCase().includes(s) ||
      e.spec_name.toLowerCase().includes(s) ||
      e.service.toLowerCase().includes(s));
  }

  // Drill-down filter from runner/tag click
  if (st.filterOps) {
    const opSet = new Set(st.filterOps);
    rows = rows.filter(e => opSet.has(e.operation_id));
  }

  // Update filter banner (+ service breakdown for the drilled-down selection)
  const banner = document.getElementById(`filter-banner-${suite}`);
  if (st.filterOps) {
    banner.classList.add("active");
    document.getElementById(`filter-type-${suite}`).textContent =
      st.filterType === "runner" ? "Runner:" : "Tag:";
    document.getElementById(`filter-name-${suite}`).textContent = st.filterName;
    document.getElementById(`filter-count-${suite}`).textContent =
      `${rows.length} endpoint`;
    // Service breakdown: computed on the FULL op set of the selection (independent of chips/search)
    const opSet = new Set(st.filterOps);
    const svcCount = {};
    for (const e of DATA[suite].endpoints) {
      if (opSet.has(e.operation_id)) {
        const svc = e.service || "(sconosciuto)";
        svcCount[svc] = (svcCount[svc] || 0) + 1;
      }
    }
    const pills = Object.entries(svcCount)
      .sort((a,b) => b[1]-a[1])
      .map(([svc,n]) => `<span class="svc-pill">${escapeHtml(svc)} <b>${n}</b></span>`)
      .join("");
    document.getElementById(`filter-svcs-${suite}`).innerHTML = pills;
  } else {
    banner.classList.remove("active");
  }

  let html = "";
  for (const e of rows) {
    const catClass = "cat-" + e.category;
    const catLabel = {real:"reale", phantom:"fantasma", never:"mai impl."}[e.category];
    const depthHtml = depthCell(suite, e, "endpoints");
    html += `<tr>
      <td><span class="cat-pill ${catClass}">${catLabel}</span></td>
      <td>${epExecBadge(suite, e)}</td>
      <td><span class="method m-${e.method}">${e.method}</span></td>
      <td class="path">${pathHtml(e.path)}</td>
      <td class="opid opid-link" title="Apri il dettaglio dell'endpoint" onclick="showEndpoint('${suite}',${jsArg(e.operation_id)},'endpoints')">${escapeHtml(e.operation_id)}</td>
      <td class="spec">${escapeHtml(e.spec_name)}</td>
      <td class="vis">${escapeHtml(e.visibility)}</td>
      <td style="text-align:right">${depthHtml}</td>
      <td>${critSelect(suite, e)}</td>
    </tr>`;
  }
  return html || `<tr><td colspan="9" style="text-align:center;color:var(--muted);padding:24px">Nessun endpoint corrisponde ai filtri</td></tr>`;
}

function updateTable(suite) {
  const search = document.getElementById(`search-${suite}`).value;
  const chips = document.querySelectorAll(`#chips-${suite} .chip.active`);
  const filterCat = new Set([...chips].map(c => c.dataset.cat));
  document.getElementById(`tbody-${suite}`).innerHTML = renderEndpoints(suite, filterCat, search);
}

function toggleChip(suite, el) { el.classList.toggle("active"); updateTable(suite); }

function switchTab(suite, tab) {
  document.querySelectorAll(`#tabs-${suite} .tab`).forEach(t => t.classList.toggle("active", t.dataset.tab===tab));
  document.querySelectorAll(`#panes-${suite} .pane`).forEach(p => p.classList.toggle("active", p.dataset.pane===tab));
  // cambiando tab si chiude qualsiasi dettaglio scenari aperto (torna alla vista lista)
  const td = document.getElementById(`tag-detail-${suite}`);
  const tl = document.getElementById(`tag-list-${suite}`);
  if (td && tab !== "tags") { td.style.display = "none"; if (tl) tl.style.display = "block"; }
  const ed = document.getElementById(`ep-detail-${suite}`);
  const em = document.getElementById(`ep-main-${suite}`);
  if (ed && tab !== "endpoints") { ed.style.display = "none"; if (em) em.style.display = "block"; }
}

function sortTable(suite, col) {
  const st = state[suite];
  if (st.sortCol === col) { st.sortAsc = !st.sortAsc; } else { st.sortCol = col; st.sortAsc = (col !== "depth"); }
  const d = DATA[suite].endpoints;
  const order = {real:0, phantom:1, never:2};
  const dir = st.sortAsc ? 1 : -1;
  if (col === "category") d.sort((a,b)=> dir * (order[a.category]-order[b.category]));
  else if (col === "depth") d.sort((a,b)=> dir * ((a.depth||0)-(b.depth||0)));
  else d.sort((a,b)=> dir * (a[col]||"").localeCompare(b[col]||""));
  updateTable(suite);
}

function switchSuite(suite) {
  document.querySelectorAll(".suite-section").forEach(d =>
    d.style.display = d.dataset.suite === suite ? "" : "none");
  document.querySelectorAll(".suite-switch button").forEach(b =>
    b.classList.toggle("active", b.dataset.suite === suite));
}

// ---------- Microservice view ----------
function renderService(suite) {
  const sel = document.getElementById(`svc-select-${suite}`);
  if (!sel) return;
  const svc = sel.value;
  const st = state[suite];
  let eps = DATA[suite].endpoints.filter(e => e.service === svc);

  // Summary
  const tot = eps.length;
  const c = { real: 0, phantom: 0, never: 0 };
  eps.forEach(e => c[e.category]++);
  const wr = tot ? c.real/tot*100 : 0, wp = tot ? c.phantom/tot*100 : 0, wn = tot ? c.never/tot*100 : 0;
  // Criticality of the whole service: current effective classes (may be mixed)
  const clsCount = {};
  eps.forEach(e => { const cc = effCrit(suite, e); clsCount[cc] = (clsCount[cc] || 0) + 1; });
  const mixed = Object.keys(clsCount).length > 1;
  const svcOvr = ((critOverrides[suite] || {}).svc || {})[svc];
  const curSvcCls = !mixed ? Object.keys(clsCount)[0] : "";
  const svcOpts = `<option value=""${!svcOvr && mixed ? " selected" : ""} disabled>${mixed ? "(misto)" : ""}</option>` +
    Object.keys(CRIT.classes).map(cc =>
      `<option value="${cc}"${cc === (svcOvr || curSvcCls) ? " selected" : ""}>${cc}</option>`).join("");

  document.getElementById(`svc-summary-${suite}`).innerHTML = `
    <div class="stackbar">
      <div class="seg-real" style="width:${wr}%"></div>
      <div class="seg-phantom" style="width:${wp}%"></div>
      <div class="seg-never" style="width:${wn}%"></div>
    </div>
    <span class="svc-stats">${tot} endpoint ·
      <b class="r">${c.real} reali (${wr.toFixed(0)}%)</b> ·
      <b class="p">${c.phantom} fantasma</b> ·
      <b class="n">${c.never} mai impl.</b></span>
    <span class="svc-stats">Criticità servizio:
      <select class="crit-sel${svcOvr ? " overridden" : ""}" title="Assegna la classe a TUTTI gli endpoint del servizio (gli override per singolo endpoint restano prioritari)"
        onchange="setSvcCrit('${suite}',${jsArg(svc)},this.value)">${svcOpts}</select></span>`;

  // Sort
  const dir = st.svcSortAsc ? 1 : -1;
  const order = { real: 0, phantom: 1, never: 2 };
  if (st.svcSortCol === "depth") eps.sort((a,b) => dir * ((a.depth||0)-(b.depth||0)));
  else if (st.svcSortCol === "category") eps.sort((a,b) => dir * (order[a.category]-order[b.category]) || (a.path||"").localeCompare(b.path||""));
  else eps.sort((a,b) => dir * (a[st.svcSortCol]||"").localeCompare(b[st.svcSortCol]||""));

  // Rows
  let html = "";
  let idx = 0;
  for (const e of eps) {
    const catClass = "cat-" + e.category;
    const catLabel = { real: "reale", phantom: "fantasma", never: "mai impl." }[e.category];
    const depthHtml = depthCell(suite, e, "services");

    // Tags: top 4 by scenario count + expandable rest
    const tags = opTags[suite][e.operation_id] || [];
    let tagsHtml = "—";
    if (tags.length) {
      const shown = tags.slice(0, 4).map(([t]) => `<span class="tag-chip">@${escapeHtml(t)}</span>`).join("");
      if (tags.length > 4) {
        const restId = `tagrest-${suite}-${idx}`;
        const rest = tags.slice(4).map(([t]) => `<span class="tag-chip">@${escapeHtml(t)}</span>`).join("");
        tagsHtml = `${shown}<span class="tag-more" onclick="toggleTags('${restId}', this, ${tags.length - 4})">+${tags.length - 4}</span><span class="tag-rest" id="${restId}">${rest}</span>`;
      } else {
        tagsHtml = shown;
      }
    }
    html += `<tr>
      <td><span class="method m-${e.method}">${e.method}</span></td>
      <td class="path">${pathHtml(e.path)}</td>
      <td class="opid opid-link" title="Apri il dettaglio dell'endpoint" onclick="showEndpoint('${suite}',${jsArg(e.operation_id)},'services')">${escapeHtml(e.operation_id)}</td>
      <td><span class="cat-pill ${catClass}">${catLabel}</span></td>
      <td style="text-align:right">${depthHtml}</td>
      <td style="max-width:380px">${tagsHtml}</td>
    </tr>`;
    idx++;
  }
  document.getElementById(`svc-tbody-${suite}`).innerHTML =
    html || `<tr><td colspan="6" style="text-align:center;color:var(--muted);padding:24px">Nessun endpoint</td></tr>`;
}

function toggleTags(restId, btn, n) {
  const rest = document.getElementById(restId);
  const open = rest.classList.toggle("open");
  btn.textContent = open ? "− riduci" : `+${n}`;
}

function sortSvc(suite, col) {
  const st = state[suite];
  if (st.svcSortCol === col) { st.svcSortAsc = !st.svcSortAsc; }
  else { st.svcSortCol = col; st.svcSortAsc = (col !== "depth"); }
  renderService(suite);
}

// ---------- Scenario drill-down (detail in-pane, no dialog) ----------
// Stato del dettaglio aperto, per suite: { kind:'tag'|'endpoint', key, shown }
const detailState = {};
const DETAIL_PAGE = 200;

function getIde() { try { return localStorage.getItem("covdash-ide") || "idea"; } catch(e) { return "idea"; } }
function setIde(suite, v) { try { localStorage.setItem("covdash-ide", v); } catch(e) {} renderDetail(suite); }

function ideHref(suite, file, line) {
  const abs = (DATA[suite].root || "") + "/" + file;
  return getIde() === "vscode"
    ? `vscode://file${abs}:${line}`
    : `idea://open?file=${encodeURIComponent(abs)}&line=${line}`;
}

// togliendo il suffisso di versione (…V23) si ottiene l'endpoint LOGICO
function logicalBase(op) { return op.replace(/V\d+$/, ""); }
function countLogical(ops) { return new Set(ops.map(logicalBase)).size; }

// scenari (indici in DATA.scen) che portano un certo tag
function scenariosForTag(suite, tag) {
  const scen = DATA[suite].scen || [];
  const out = [];
  scen.forEach((s, i) => { if ((s.tags || []).includes(tag)) out.push(i); });
  return out;
}

// ---- Entrata 1: dal tab Tag, master → detail ----
function openTagDetail(suite, tag) {
  detailState[suite] = { kind: "tag", key: tag, shown: DETAIL_PAGE };
  document.getElementById(`tag-list-${suite}`).style.display = "none";
  const box = document.getElementById(`tag-detail-${suite}`);
  box.style.display = "block";
  renderDetail(suite);
}
function closeTagDetail(suite) {
  document.getElementById(`tag-detail-${suite}`).style.display = "none";
  document.getElementById(`tag-list-${suite}`).style.display = "block";
  detailState[suite] = null;
}

// ---- Entrata 2: dalla colonna "scenari che lo invocano" del tab Endpoint ----
function showEndpoint(suite, op, origin) {
  origin = origin || "endpoints";
  // il dettaglio vive nella pane Endpoint: assicuriamoci che sia attiva
  // (così funziona anche cliccando dal tab Microservizi)
  switchTab(suite, "endpoints");
  detailState[suite] = { kind: "endpoint", key: op, shown: DETAIL_PAGE, origin: origin };
  document.getElementById(`ep-main-${suite}`).style.display = "none";
  const box = document.getElementById(`ep-detail-${suite}`);
  box.style.display = "block";
  renderDetail(suite);
  box.scrollIntoView({ block: "start" });
}
const showScenarios = showEndpoint;  // retrocompatibilità: dalla colonna profondità
function closeEpDetail(suite) {
  const origin = (detailState[suite] || {}).origin || "endpoints";
  document.getElementById(`ep-detail-${suite}`).style.display = "none";
  document.getElementById(`ep-main-${suite}`).style.display = "block";
  detailState[suite] = null;
  if (origin !== "endpoints") switchTab(suite, origin);  // torna a Microservizi
}

function detailFilter(suite, v) { detailState[suite]._q = (v || "").toLowerCase(); renderDetail(suite); }
function detailMore(suite) { detailState[suite].shown += DETAIL_PAGE; renderDetail(suite); }

function toggleScenEps(id) {
  const el = document.getElementById(id);
  if (!el) return;
  const open = el.style.display === "none" || !el.style.display;
  el.style.display = open ? "flex" : "none";
  const badge = document.querySelector(`[data-eps="${id}"] .eps-arrow`);
  if (badge) badge.textContent = open ? "▴" : "▾";
}

// Filtro salute TC-ID dal banner: 'noid' | 'dup' | null (toggle/azzera).
function setScenTcFilter(suite, mode) {
  const st = state[suite];
  st._scenTc = (st._scenTc === mode) ? null : mode;
  switchTab(suite, "scenari");
  renderScenari(suite);
}

// ---------- Tab Scenari (reverse-lookup + orfani) ----------
function renderScenari(suite) {
  const scen = DATA[suite].scen || [];
  const st = state[suite];
  const q = (document.getElementById(`scen-search-${suite}`).value || "").toLowerCase();
  const orphanOnly = document.getElementById(`scen-orphan-${suite}`).checked;
  const koEl = document.getElementById(`scen-ko-${suite}`);
  const koOnly = koEl ? koEl.checked : false;
  const tcMode = st._scenTc || null;   // null | 'noid' | 'dup'
  // reset paginazione se cambiano i filtri
  if (st._scenQ !== q || st._scenOrphan !== orphanOnly || st._scenKo !== koOnly || st._scenTcPrev !== tcMode || st._scenShown === undefined) {
    st._scenShown = 300; st._scenQ = q; st._scenOrphan = orphanOnly; st._scenKo = koOnly; st._scenTcPrev = tcMode;
  }
  const orphanTot = scen.filter(s => (s.runners || []).length === 0).length;

  let items = scen.map((s, i) => ({ i, ...s }));
  if (orphanOnly) items = items.filter(s => (s.runners || []).length === 0);
  if (koOnly) items = items.filter(s => s.exec && s.exec.status === "KO");
  if (tcMode === "noid") {
    items = items.filter(s => !s.tc_id);
  } else if (tcMode === "dup") {
    const counts = {};
    scen.forEach(s => { if (s.tc_id) counts[s.tc_id] = (counts[s.tc_id] || 0) + 1; });
    items = items.filter(s => s.tc_id && counts[s.tc_id] > 1);
    items.sort((a, b) => (a.tc_id || "").localeCompare(b.tc_id || ""));  // duplicati accostati
  }
  if (q) items = items.filter(s =>
    (s.name || "").toLowerCase().includes(q) ||
    (s.tc_id || "").toLowerCase().includes(q) ||
    (s.file || "").toLowerCase().includes(q) ||
    (s.tags || []).some(t => t.toLowerCase().includes(q)) ||
    (s.ops || []).some(o => o.toLowerCase().includes(q)));

  const tcLabel = tcMode === "noid" ? "senza TC-ID" : tcMode === "dup" ? "TC-ID duplicati" : "";
  const tcChip = tcMode
    ? ` · <span class="scen-tcfilter">filtro: ${tcLabel} <a onclick="setScenTcFilter('${suite}',null)">✕ mostra tutti</a></span>`
    : "";
  document.getElementById(`scen-count-${suite}`).innerHTML =
    `${items.length} scenari${(q || orphanOnly || tcMode) ? " (filtrati)" : ""} · `
    + `<b style="color:var(--never)">${orphanTot} orfani</b> in totale${tcChip}`;

  let html = "";
  for (const s of items.slice(0, st._scenShown)) {
    const ops = s.ops || [], nLog = countLogical(ops);
    const epId = `scenrow-${suite}-${s.i}`;
    const tags = (s.tags || []).map(t =>
      `<span class="ep-chip" style="cursor:pointer" title="apri il tag"
        onclick="switchTab('${suite}','tags');openTagDetail('${suite}',${jsArg(t)})">${escapeHtml(t)}</span>`).join("")
      || '<span class="detail-sub">—</span>';
    const runs = s.runners || [];
    const runnersHtml = runs.length
      ? runs.map(r => `<span class="ep-chip">${escapeHtml(r)}</span>`).join("")
      : `<span class="scen-orphan-badge">⚠ orfano</span>`;
    const tcId = s.tc_id
      ? `<span class="scen-tcid" title="TC-ID: chiave di join coi report di esecuzione">${escapeHtml(s.tc_id)}</span>`
      : `<span class="scen-tcid none" title="Senza TC-ID: non agganciabile ai report di esecuzione">⚠ senza TC-ID</span>`;
    const esito = execBadge(s.exec);
    const errLine = (s.exec && s.exec.status === "KO" && s.exec.error)
      ? `<div class="scen-err" title="Primo step fallito nell'ultima esecuzione">✗ ${escapeHtml(s.exec.error)}</div>` : "";
    html += `<tr>
        <td><div class="scen-name">${tcId}${escapeHtml(s.name || "(senza nome)")}</div>
            <a class="scen-file" href="${escapeHtml(ideHref(suite, s.file, s.line))}">${escapeHtml(s.file)}:${s.line} ↗</a>${errLine}</td>
        <td style="vertical-align:top">${esito}</td>
        <td class="cell-chips-col"><div class="cell-chips">${tags}</div></td>
        <td style="text-align:right;vertical-align:top"><span class="scen-eps" data-eps="${epId}"
          onclick="toggleScenEps('${epId}')" title="Endpoint invocati da questo scenario">${ops.length}${ops.length !== nLog ? ` · ${nLog} log.` : ""} <span class="eps-arrow">▾</span></span></td>
        <td class="cell-chips-col"><div class="cell-chips">${runnersHtml}</div></td>
      </tr>
      <tr><td colspan="5" style="padding:0"><div class="ep-list scen-eps-detail" id="${epId}" style="display:none">${epListHtml(suite, ops)}</div></td></tr>`;
  }
  if (items.length > st._scenShown) {
    const left = items.length - st._scenShown;
    html += `<tr><td colspan="5"><button class="scen-more" onclick="state['${suite}']._scenShown += 300; renderScenari('${suite}')">Mostra altri ${Math.min(300, left)} (${left} rimanenti)</button></td></tr>`;
  }
  document.getElementById(`scenari-tbody-${suite}`).innerHTML =
    html || `<tr><td colspan="5" style="text-align:center;color:var(--muted);padding:24px">Nessuno scenario corrisponde ai filtri</td></tr>`;
}

// Badge esito esecuzione per uno scenario. Assente = nessun esito in finestra.
function execBadge(exec) {
  if (!exec) return '<span class="exec-badge none" title="Non eseguito nella finestra (assenza di dato, non un fallimento)">○</span>';
  const age = exec.age_days != null ? ` · ${exec.age_days}g fa` : "";
  const flaky = exec.flaky ? ` <span class="exec-flaky" title="Instabile: ${exec.ok} OK / ${exec.ko} KO nella finestra">~ instabile</span>` : "";
  if (exec.status === "OK") return `<span class="exec-badge ok" title="Ultimo esito OK${age}. eseguito ≠ asserito.">✓ OK</span>${flaky}`;
  if (exec.status === "KO") return `<span class="exec-badge ko" title="Ultimo esito KO${age}">✗ KO</span>${flaky}`;
  return `<span class="exec-badge other" title="Esito incerto (skipped/undefined)${age}">○ incerto</span>`;
}

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// Badge esito esecuzione per un ENDPOINT (stato derivato dagli scenari che lo invocano).
function epExecBadge(suite, e) {
  if (!DATA[suite].execActive || e.category !== "real")
    return '<span class="detail-sub">—</span>';
  const age = (e.exec_age != null) ? ` · <span class="exec-age">${e.exec_age}g fa</span>` : "";
  if (e.exec === "ok") return `<span class="exec-badge ok" title="Almeno uno scenario che la invoca è passato nell'ultima finestra. eseguito ≠ asserito.">✓ eseguita</span>${age}`;
  if (e.exec === "ko") return `<span class="exec-badge ko" title="Tutti gli scenari che la invocano sono falliti nella finestra. Priorità di triage.">✗ rossa</span>${age}`;
  return '<span class="exec-badge none" title="Nessuno scenario che la invoca è girato nella finestra (assenza di dato, non un fallimento)">○ non eseguita</span>';
}

function scenRowsHtml(suite, idxs, q, shown, prefix) {
  const scen = DATA[suite].scen || [];
  let items = idxs.map(i => ({ i, ...scen[i] }));
  if (q) items = items.filter(s =>
    (s.name || "").toLowerCase().includes(q) || (s.file || "").toLowerCase().includes(q));
  let html = "", n = 0;
  for (const s of items.slice(0, shown)) {
    n++;
    const fl = escapeHtml(`${s.file}:${s.line}`);
    const ops = s.ops || [];
    const nLog = countLogical(ops);
    const epId = `${prefix}-${s.i}`;
    // badge "quanti endpoint copre questo scenario" — espandibile
    const epBadge = `<span class="scen-eps" data-eps="${epId}" onclick="toggleScenEps('${epId}')"
        title="Endpoint coperti da questo scenario">${ops.length} ep${ops.length!==nLog?` · ${nLog} logici`:""} <span class="eps-arrow">▾</span></span>`;
    const esito = DATA[suite].execActive ? execBadge(s.exec) : "";
    html += `<div class="scen-row"><span class="scen-idx">${n}</span>`
      + `<span class="scen-name">${escapeHtml(s.name || "(senza nome)")}</span>`
      + esito
      + epBadge
      + `<span class="scen-file" title="${fl}">${fl}</span>`
      + `<a class="scen-open" href="${escapeHtml(ideHref(suite, s.file, s.line))}">Apri ↗</a></div>`
      + `<div class="scen-eps-detail ep-list" id="${epId}" style="display:none">${epListHtml(suite, ops)}</div>`;
  }
  if (items.length > shown) {
    const left = items.length - shown;
    html += `<button class="scen-more" onclick="detailMore('${suite}')">Mostra altri ${Math.min(DETAIL_PAGE, left)} (${left} rimanenti)</button>`;
  }
  return { html: html || `<div style="padding:16px;text-align:center;color:var(--muted)">Nessuno scenario corrisponde al filtro</div>`,
           total: items.length };
}

function ideSelectHtml(suite) {
  const ide = getIde();
  return `<span class="detail-sub">Apri in:</span>
    <select onchange="setIde('${suite}', this.value)">
      <option value="idea"${ide==="idea"?" selected":""}>IntelliJ IDEA</option>
      <option value="vscode"${ide==="vscode"?" selected":""}>VS Code</option>
    </select>`;
}

function epListHtml(suite, ops) {
  const cat = {};
  DATA[suite].endpoints.forEach(e => cat[e.operation_id] = e.category);
  return ops.slice().sort().map(o => {
    const c = cat[o] === "phantom" ? " phantom" : "";
    return `<span class="ep-chip${c}">${escapeHtml(o)}</span>`;
  }).join("");
}

// Variante raggruppata per microservizio, col totale tra parentesi nel titolo.
// Usata nel dettaglio tag, dove la lista di endpoint coperti è lunga.
function epListByServiceHtml(suite, ops) {
  const cat = {}, svc = {};
  DATA[suite].endpoints.forEach(e => { cat[e.operation_id] = e.category; svc[e.operation_id] = e.service || "(sconosciuto)"; });
  const groups = {};
  ops.forEach(o => { (groups[svc[o]] = groups[svc[o]] || []).push(o); });
  // microservizi ordinati per n. endpoint desc, poi alfabetico
  const names = Object.keys(groups).sort((a, b) => groups[b].length - groups[a].length || a.localeCompare(b));
  return names.map(s => {
    const chips = groups[s].sort().map(o =>
      `<span class="ep-chip${cat[o] === "phantom" ? " phantom" : ""}">${escapeHtml(o)}</span>`).join("");
    return `<div class="ep-group">
      <div class="ep-group-h">${escapeHtml(s)} <span class="detail-sub">(${groups[s].length})</span></div>
      <div class="ep-list">${chips}</div>
    </div>`;
  }).join("");
}

// Renderer unico del dettaglio (tag o endpoint)
function renderDetail(suite) {
  const st = detailState[suite];
  if (!st) return;
  const q = st._q || "";

  if (st.kind === "tag") {
    const tag = st.key;
    const item = (DATA[suite].tags || []).find(t => t.tag === tag) || { operation_ids: [], scenarios: 0 };
    const ops = item.operation_ids || [];
    const idxs = scenariosForTag(suite, tag);
    const r = scenRowsHtml(suite, idxs, q, st.shown, `${suite}-tagdet`);
    const nLog = countLogical(ops);
    const variantsNote = ops.length !== nLog ? ` <span class="detail-sub">(${ops.length} con varianti di versione)</span>` : "";
    document.getElementById(`tag-detail-${suite}`).innerHTML = `
      <div class="detail-head">
        <button class="back-link" onclick="closeTagDetail('${suite}')">← Tutti i tag</button>
        <span class="detail-title">${escapeHtml(tag)}</span>
        <span class="detail-sub">${item.scenarios} scenari · ${nLog} endpoint logici${variantsNote}</span>
      </div>
      <div class="detail-section-title">Endpoint coperti (${nLog} logici), raggruppati per microservizio
        · <a class="scen-open" style="cursor:pointer" onclick="drillDown('${suite}','tag',${jsArg(tag)})">filtra nel tab Endpoint →</a></div>
      ${epListByServiceHtml(suite, ops)}
      <div class="detail-section-title">Scenari che portano questo tag (${idxs.length})</div>
      <div class="detail-controls">
        <input type="search" placeholder="Filtra per nome o file…" value="${q}" oninput="detailFilter('${suite}', this.value)">
        ${ideSelectHtml(suite)}
      </div>
      <div>${r.html}</div>`;
  } else {
    const op = st.key;
    const e = DATA[suite].endpoints.find(x => x.operation_id === op) || {operation_id: op, category: "never"};
    const idxs = (DATA[suite].epScen || {})[op] || [];
    const catLabel = {real:"reale", phantom:"fantasma", never:"mai impl."}[e.category] || e.category;
    const catClass = {real:"cat-real", phantom:"cat-phantom", never:"cat-never"}[e.category] || "";
    // tag che raggiungono l'endpoint (ordinati per n° scenari, cap a 40)
    const tags = (opTags[suite][op] || []);
    const TAGCAP = 40;
    const shownTags = tags.slice(0, TAGCAP);
    const tagsHtml = tags.length
      ? shownTags.map(([t,n]) => `<span class="ep-chip" style="cursor:pointer" title="${n} scenari"
          onclick="switchTab('${suite}','tags');openTagDetail('${suite}',${jsArg(t)})">${escapeHtml(t)}</span>`).join("")
        + (tags.length > TAGCAP ? `<span class="detail-sub">+${tags.length - TAGCAP} altri</span>` : "")
      : `<span class="detail-sub">nessun tag lo raggiunge</span>`;

    // sezione scenari (se coperto) oppure box "non coperto" con delta copertura
    let body;
    if (idxs.length > 0) {
      const r = scenRowsHtml(suite, idxs, q, st.shown, `${suite}-epdet`);
      body = `<div class="detail-section-title">Scenari che lo invocano (${idxs.length})</div>
        <div class="detail-controls">
          <input type="search" placeholder="Filtra per nome o file…" value="${q}" oninput="detailFilter('${suite}', this.value)">
          ${ideSelectHtml(suite)}
        </div><div>${r.html}</div>`;
    } else {
      body = notCoveredHtml(suite, e);
    }

    document.getElementById(`ep-detail-${suite}`).innerHTML = `
      <div class="detail-head">
        <button class="back-link" onclick="closeEpDetail('${suite}')">← Torna agli endpoint</button>
        <span class="method m-${e.method}">${e.method||""}</span>
        <span class="detail-title">${escapeHtml(e.path||op)}</span>
        <span class="cat-pill ${catClass}">${catLabel}</span>
        ${epExecBadge(suite, e)}
      </div>
      <div class="detail-sub" style="margin-bottom:10px">
        <b>${escapeHtml(op)}</b> · ${escapeHtml(e.service||"?")} / ${escapeHtml(e.spec_name||"?")} · ${escapeHtml(e.visibility||"")} · criticità: ${effCrit(suite,e)} (peso ${critWeight(effCrit(suite,e))})
      </div>
      ${(DATA[suite].execActive && e.category === "real" && e.exec_run)
        ? `<div class="run-banner active" style="margin-bottom:10px" title="Run più recente che ha esercitato questo endpoint">📋 Ultima esecuzione: <b>${escapeHtml(e.exec_run)}</b>${e.exec_age != null ? ` · ${e.exec_age}g fa` : ""}</div>`
        : ""}
      <div class="detail-section-title">Tag che lo raggiungono (${tags.length})</div>
      <div class="ep-list">${tagsHtml}</div>
      ${body}`;
  }
}

// Box per endpoint NON coperto da scenari: di quanto crescerebbe la copertura
// se venisse esercitato, + spazio per i suggerimenti futuri (come/dove implementarlo).
function notCoveredHtml(suite, e) {
  const eps = DATA[suite].endpoints;
  const total = eps.length;
  let real = 0, wAll = 0, wReal = 0;
  for (const x of eps) {
    const w = critWeight(effCrit(suite, x));
    wAll += w;
    if (x.category === "real") { real += 1; wReal += w; }
  }
  const w = critWeight(effCrit(suite, e));
  const sNow = total ? real/total*100 : 0, sNew = total ? (real+1)/total*100 : 0;
  const wNow = wAll ? wReal/wAll*100 : 0, wNew = wAll ? (wReal+w)/wAll*100 : 0;
  const why = e.category === "phantom"
    ? "Il wrapper/client esiste già nel codice: basta scrivere uno scenario che lo eserciti."
    : "Nessun codice né scenario lo tocca: va implementato sia il supporto sia lo scenario.";
  return `
    <div class="detail-section-title">Non coperto da alcuno scenario</div>
    <div class="guide-intro">${why}</div>
    <div class="delta-grid">
      <div class="delta-card"><div class="delta-k">Copertura reale</div>
        <div class="delta-v">${sNow.toFixed(1)}% → <b>${sNew.toFixed(1)}%</b></div>
        <div class="delta-d">+${(sNew-sNow).toFixed(2)} pt esercitandolo</div></div>
      <div class="delta-card"><div class="delta-k">Copertura pesata ★</div>
        <div class="delta-v">${wNow.toFixed(1)}% → <b>${wNew.toFixed(1)}%</b></div>
        <div class="delta-d">+${(wNew-wNow).toFixed(2)} pt (peso ${w})</div></div>
    </div>
    <div class="detail-section-title">In quale tag inserirlo</div>
    ${suggestTagsHtml(suite, e)}`;
}

// Metà A: suggerimento del tag, fondato sull'evidenza (dati da DATA.suggest,
// calcolati server-side). Mai una proposta "inventata": ogni riga dice PERCHÉ.
function suggestTagsHtml(suite, e) {
  const sugg = (DATA[suite].suggest || {})[e.operation_id] || [];
  if (!sugg.length)
    return `<div class="guide-empty">Nessun tag affine: non ci sono altre versioni
      di questo endpoint né altri endpoint dello stesso microservizio già coperti.
      Probabile candidato a un nuovo tag/runner dedicato.</div>`;
  const kindLabel = {family: "stessa famiglia di versione", service: "stesso microservizio"};
  const rows = sugg.map(s => `<div class="sugg-row">
      <span class="ep-chip" style="cursor:pointer" title="apri il tag"
        onclick="switchTab('${suite}','tags');openTagDetail('${suite}',${jsArg(s.tag)})">${escapeHtml(s.tag)}</span>
      <span class="sugg-kind">${kindLabel[s.kind] || s.kind}</span>
      <span class="detail-sub">${escapeHtml(s.reason)}</span>
    </div>`).join("");
  return `<div class="sugg-note">Suggerimento <b>basato sull'evidenza</b> nel grafo
    (non una proposta generata): ecco i tag più affini.</div>${rows}`;
}

function depthCell(suite, e, origin) {
  const d = e.depth || 0;
  if (d === 0) return `<span class="depth depth-zero">—</span>`;
  const cls = d === 1 ? "depth depth-low" : (d >= 10 ? "depth depth-hi" : "depth");
  return `<span class="${cls} depth-link" title="Clicca per vedere gli scenari"
    onclick="showEndpoint('${suite}',${jsArg(e.operation_id)},'${origin || "endpoints"}')">${d}</span>`;
}

// ---------- Criticality (copertura pesata) ----------
const CRIT = __CRIT__;
let critOverrides = {};
try { critOverrides = JSON.parse(localStorage.getItem("scope-crit") || "{}"); } catch(e) {}

// Auto-pulizia: se un override coincide con la classe già risolta dal
// criticality.yaml (perché l'export è stato salvato e il dashboard rigenerato),
// l'override è ridondante e viene rimosso. Il banner resta solo per le
// modifiche NON ancora recepite dal yaml.
function pruneCritOverrides() {
  let changed = false;
  for (const suite of Object.keys(critOverrides)) {
    if (!DATA[suite]) continue;
    const o = critOverrides[suite];
    const baseByOp = {}, svcBase = {};
    for (const e of DATA[suite].endpoints) {
      baseByOp[e.operation_id] = { base: e.crit, svc: e.service };
      (svcBase[e.service] = svcBase[e.service] || new Set()).add(e.crit);
    }
    // 1. override per endpoint: ridondante se = (override servizio attivo, altrimenti base yaml)
    if (o.op) for (const [op, cls] of Object.entries(o.op)) {
      const info = baseByOp[op];
      if (!info) continue;
      const eff = (o.svc && o.svc[info.svc]) || info.base;
      if (cls === eff) { delete o.op[op]; changed = true; }
    }
    // 2. override per servizio: ridondante se TUTTI gli endpoint del servizio
    //    hanno già quella classe dal yaml
    if (o.svc) for (const [svc, cls] of Object.entries(o.svc)) {
      const set = svcBase[svc];
      if (set && set.size === 1 && set.has(cls)) { delete o.svc[svc]; changed = true; }
    }
    if (o.op && !Object.keys(o.op).length) delete o.op;
    if (o.svc && !Object.keys(o.svc).length) delete o.svc;
    if (!Object.keys(o).length) { delete critOverrides[suite]; changed = true; }
  }
  if (changed) saveCrit();
}

function effCrit(suite, e) {
  const o = critOverrides[suite] || {};
  if (o.op && o.op[e.operation_id]) return o.op[e.operation_id];
  if (o.svc && o.svc[e.service]) return o.svc[e.service];
  return e.crit || CRIT.default;
}

function isOverridden(suite, e) {
  const o = critOverrides[suite] || {};
  return !!((o.op && o.op[e.operation_id]) || (o.svc && o.svc[e.service]));
}

function critWeight(cls) {
  const w = CRIT.classes[cls];
  return (w === undefined || w === null) ? 1 : w;
}

function critSelect(suite, e) {
  const cur = effCrit(suite, e);
  const ovr = isOverridden(suite, e) ? " overridden" : "";
  const opts = Object.keys(CRIT.classes).map(c =>
    `<option value="${c}"${c === cur ? " selected" : ""}>${c}</option>`).join("");
  return `<select class="crit-sel crit-${cur}${ovr}" title="Classe di criticità (peso ${critWeight(cur)})"
    onchange="setOpCrit('${suite}',${jsArg(e.operation_id)},this.value)">${opts}</select>`;
}

function saveCrit() {
  try { localStorage.setItem("scope-crit", JSON.stringify(critOverrides)); } catch(e) {}
}

function setOpCrit(suite, op, cls) {
  const o = critOverrides[suite] = critOverrides[suite] || {};
  (o.op = o.op || {})[op] = cls;
  saveCrit(); refreshCrit(suite);
}

function setSvcCrit(suite, svc, cls) {
  const o = critOverrides[suite] = critOverrides[suite] || {};
  (o.svc = o.svc || {})[svc] = cls;
  saveCrit(); refreshCrit(suite);
}

function resetCrit(suite) {
  delete critOverrides[suite];
  saveCrit(); refreshCrit(suite);
}

function refreshCrit(suite) {
  recomputeWeighted(suite);
  updateCritBanner(suite);
  updateTable(suite);
  renderService(suite);
}

function recomputeWeighted(suite) {
  let wAll = 0, wReal = 0, all = 0, real = 0;
  for (const e of DATA[suite].endpoints) {
    const w = critWeight(effCrit(suite, e));
    wAll += w; all += 1;
    if (e.category === "real") { wReal += w; real += 1; }
  }
  const wPct = wAll ? wReal / wAll * 100 : 0;
  const sPct = all ? real / all * 100 : 0;
  const el = document.getElementById(`wcov-${suite}`);
  if (!el) return;
  el.textContent = wPct.toFixed(1) + "%";
  const note = document.getElementById(`wcov-note-${suite}`);
  const d = wPct - sPct;
  if (Math.abs(d) < 0.05) {
    note.textContent = "= reale semplice (pesi uniformi)";
  } else if (d > 0) {
    note.textContent = `+${d.toFixed(1)} pt vs semplice: i critici sono coperti meglio della media`;
  } else {
    note.textContent = `${d.toFixed(1)} pt vs semplice: i critici sono coperti PEGGIO della media`;
  }
}

function updateCritBanner(suite) {
  const o = critOverrides[suite] || {};
  const n = Object.keys(o.op || {}).length + Object.keys(o.svc || {}).length;
  const banner = document.getElementById(`crit-banner-${suite}`);
  if (!banner) return;
  banner.classList.toggle("active", n > 0);
  if (n > 0) {
    document.getElementById(`crit-count-${suite}`).textContent =
      `${n} modifiche di criticità non esportate`;
  }
}

function exportCritYaml() {
  let y = "# criticality.yaml — esportato dal dashboard SCOPE il " + new Date().toISOString().slice(0,16) + "\n";
  y += "# Salvare come coverage-tool/criticality.yaml e rigenerare il dashboard.\n\nclasses:\n";
  for (const [k, v] of Object.entries(CRIT.classes)) y += `  ${k}: ${v}\n`;
  y += `\ndefault: ${CRIT.default}\n\n`;

  // base rules (passthrough) + overrides (service prima, operation dopo = precedenza)
  const rules = [];
  for (const r of (CRIT.rules || [])) rules.push({...r});
  for (const suite of Object.keys(critOverrides)) {
    const o = critOverrides[suite] || {};
    for (const [svc, cls] of Object.entries(o.svc || {}))
      rules.push({ service: svc, class: cls });
    for (const [op, cls] of Object.entries(o.op || {}))
      rules.push({ operation: op, class: cls });
  }
  // dedup: l'ultima assegnazione per chiave vince
  const seen = {};
  for (const r of rules) {
    const kind = r.operation ? "operation" : r.path ? "path" : r.spec ? "spec" : "service";
    seen[kind + ":" + r[kind]] = r;
  }
  const final = Object.values(seen);
  if (!final.length) {
    y += "rules: []\n";
  } else {
    y += "rules:\n";
    for (const r of final) {
      const kind = r.operation ? "operation" : r.path ? "path" : r.spec ? "spec" : "service";
      const val = r[kind];
      y += `  - ${kind}: ${/[*{}\[\]:#]/.test(val) ? JSON.stringify(val) : val}\n    class: ${r.class}\n`;
    }
  }
  const blob = new Blob([y], { type: "text/yaml" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "criticality.yaml";
  a.click();
  URL.revokeObjectURL(a.href);
}

function drillDown(suite, type, name) {
  // Find the operation_ids for this runner or tag
  const list = type === "runner" ? DATA[suite].runners : DATA[suite].tags;
  const key = type === "runner" ? "name" : "tag";
  const item = list.find(x => x[key] === name);
  if (!item || !item.operation_ids) return;

  const st = state[suite];
  st.filterType = type;
  st.filterName = name;
  st.filterOps = item.operation_ids;

  // Highlight selected row
  document.querySelectorAll(`#${type}s-tbody-${suite} tr`).forEach(tr => {
    tr.classList.toggle("selected", tr.dataset.name === name);
  });

  // Switch to endpoints tab and refresh
  switchTab(suite, "endpoints");
  // Enable all category chips so drill-down shows everything
  document.querySelectorAll(`#chips-${suite} .chip`).forEach(c => c.classList.add("active"));
  document.getElementById(`search-${suite}`).value = "";
  updateTable(suite);
}

function clearFilter(suite) {
  state[suite].filterType = null;
  state[suite].filterName = null;
  state[suite].filterOps = null;
  // Deselect rows in all sub-tables
  document.querySelectorAll(`#panes-${suite} tr.selected`).forEach(tr => tr.classList.remove("selected"));
  updateTable(suite);
}

// Init
pruneCritOverrides();
Object.keys(DATA).forEach(s => { updateTable(s); renderService(s); recomputeWeighted(s); updateCritBanner(s); });
// Show only the first suite at startup
const suiteKeys = Object.keys(DATA);
if (suiteKeys.length > 1) switchSuite(suiteKeys[0]);
