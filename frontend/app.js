// ═══════════════════════════════════════════════════════════════
// CONFIGURATION — À MODIFIER APRÈS DÉPLOIEMENT DU BACKEND
// ═══════════════════════════════════════════════════════════════
// Remplace l'URL de production par celle de ton backend Railway/Render.
// En local (fichier ouvert directement), l'app utilise automatiquement localhost.
const API_PROD = "https://pv-explorer-api.onrender.com";  // backend Render
const API_LOCAL = "http://localhost:8000";

// Détection automatique : localhost en dev, URL prod sinon
const API_URL = (location.hostname === "localhost" || location.hostname === "127.0.0.1" || location.protocol === "file:")
  ? API_LOCAL
  : API_PROD;

// ── ÉTAT ──
let isLoading = false;

// ── ONGLETS ──
function switchTab(tab) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.getElementById('tab-' + tab).classList.add('active');
  document.getElementById('panel-' + tab).classList.add('active');
  document.getElementById('askBar').style.display = (tab === 'chat') ? 'block' : 'none';
  if (tab === 'stats') loadStats();
}

// ── SUGGESTIONS ──
function askSuggestion(el) {
  document.getElementById('askInput').value = el.textContent;
  submitQuestion();
}

// ── REPOSER UNE QUESTION DÉJÀ POSÉE ──
// Clic sur une bulle de question → on la remet dans le champ (à renvoyer/éditer).
function reuseQuestion(el) {
  const q = (el.textContent || '').trim();
  if (!q) return;
  const input = document.getElementById('askInput');
  input.value = q;
  input.focus();
  try { input.setSelectionRange(q.length, q.length); } catch (e) {}
  window.scrollTo(0, document.body.scrollHeight);
}

// ── HISTORIQUE PERSISTANT DES QUESTIONS (localStorage) ──
const HIST_KEY = 'pv_explorer_history';
const HIST_MAX = 12;

function getHistory() {
  try { return JSON.parse(localStorage.getItem(HIST_KEY) || '[]'); }
  catch (e) { return []; }
}
function saveHistory(q) {
  q = (q || '').trim();
  if (!q) return;
  let h = getHistory().filter(x => x.toLowerCase() !== q.toLowerCase());
  h.unshift(q);
  h = h.slice(0, HIST_MAX);
  try { localStorage.setItem(HIST_KEY, JSON.stringify(h)); } catch (e) {}
  renderHistory();
}
function clearHistory() {
  try { localStorage.removeItem(HIST_KEY); } catch (e) {}
  renderHistory();
}
function renderHistory() {
  const block = document.getElementById('historyBlock');
  const chips = document.getElementById('historyChips');
  if (!block || !chips) return;
  const h = getHistory();
  if (!h.length) { block.style.display = 'none'; chips.innerHTML = ''; return; }
  block.style.display = '';
  chips.innerHTML = h.map(q =>
    `<span class="suggestion" onclick="askSuggestion(this)">${escapeHtml(q)}</span>`
  ).join('');
}

// ── NOUVELLE RECHERCHE : revient à l'écran d'accueil (l'historique reste) ──
function newSearch() {
  document.getElementById('conversation').innerHTML = '';
  document.getElementById('introCard').style.display = '';
  document.getElementById('newSearchBtn').style.display = 'none';
  const input = document.getElementById('askInput');
  if (input) input.value = '';
  renderHistory();
  window.scrollTo(0, 0);
}

// Afficher l'historique au chargement
renderHistory();

// ── POSER UNE QUESTION ──
async function submitQuestion() {
  if (isLoading) return;
  const input = document.getElementById('askInput');
  const question = input.value.trim();
  if (!question) return;

  isLoading = true;
  document.getElementById('askBtn').disabled = true;
  document.getElementById('introCard').style.display = 'none';
  document.getElementById('newSearchBtn').style.display = '';
  saveHistory(question);
  input.value = '';

  const conv = document.getElementById('conversation');

  // Afficher la question
  conv.insertAdjacentHTML('beforeend', `
    <div class="msg msg-question">
      <div class="msg-role">Votre question</div>
      <div class="msg-bubble" role="button" tabindex="0"
           title="Cliquer pour reposer ou modifier cette question"
           onclick="reuseQuestion(this)"
           onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();reuseQuestion(this);}">${escapeHtml(question)}</div>
    </div>`);

  // Afficher le chargement
  const loadingId = 'loading-' + Date.now();
  conv.insertAdjacentHTML('beforeend', `
    <div class="msg" id="${loadingId}">
      <div class="msg-role">Assistant</div>
      <div class="msg-bubble">
        <div class="loading"><span>Recherche dans les procès-verbaux</span>
        <span class="dots"><span></span><span></span><span></span></span></div>
      </div>
    </div>`);
  window.scrollTo(0, document.body.scrollHeight);

  // Filtre commune : "Toutes" (value vide) → on n'envoie rien (recherche croisée)
  const communeSel = document.getElementById('communeSelect');
  const commune = communeSel ? communeSel.value : '';
  const payload = { question };
  if (commune) payload.commune = commune;

  try {
    const res = await fetch(API_URL + '/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    if (!res.ok) {
      if (res.status === 429) {
        throw new Error("Trop de questions d'affilée. Patientez une minute avant de réessayer.");
      }
      const err = await res.json().catch(() => ({}));
      // slowapi renvoie {error:...}, FastAPI {detail:...}
      throw new Error(err.detail || err.error || 'Erreur ' + res.status);
    }

    const data = await res.json();
    document.getElementById(loadingId).remove();

    // Construire les sources
    let sourcesHtml = '';
    if (data.sources && data.sources.length) {
      const items = data.sources.map(s => `
        <div class="source-item">
          <div class="source-ref"><svg class="icon" aria-hidden="true"><use href="#ico-date"/></svg>Séance ${formatDate(s.date)}<br>Point SP ${s.sp}</div>
          <div class="source-titre">${escapeHtml(s.titre)}</div>
          <div class="source-decision"><svg class="icon" aria-hidden="true"><use href="#ico-decision"/></svg>${escapeHtml(s.decision)}</div>
        </div>`).join('');
      sourcesHtml = `<div class="sources">
        <div class="sources-title"><svg class="icon" aria-hidden="true"><use href="#ico-source"/></svg>Sources · ${data.sources.length} délibérations</div>
        ${items}</div>`;
    }

    conv.insertAdjacentHTML('beforeend', `
      <div class="msg">
        <div class="msg-role">Assistant</div>
        <div class="msg-bubble">${escapeHtml(data.answer)}</div>
        ${sourcesHtml}
      </div>`);

  } catch (err) {
    document.getElementById(loadingId).remove();
    conv.insertAdjacentHTML('beforeend', `
      <div class="msg">
        <div class="msg-role">Assistant</div>
        <div class="error-box">
          Impossible d'obtenir une réponse. ${escapeHtml(err.message)}<br>
          <small>Vérifiez que le backend est démarré (${API_URL}).</small>
        </div>
      </div>`);
  }

  isLoading = false;
  document.getElementById('askBtn').disabled = false;
  window.scrollTo(0, document.body.scrollHeight);
}

// ── STATISTIQUES ──
let statsLoaded = false;
let yearData = [];
let themesByYear = {};

// Rend les barres de thématiques pour l'année choisie ('toutes' ou 'YYYY').
function renderThemes(year) {
  const box = document.getElementById('themesBars');
  if (!box) return;
  const rows = themesByYear[year] || themesByYear['toutes'] || [];
  if (!rows.length) { box.innerHTML = '<p class="yc-note">Aucune donnée pour cette année.</p>'; return; }
  const max = Math.max(...rows.map(r => r[1]), 1);
  box.innerHTML = rows.map(([nom, n]) => `
    <div class="bar-row">
      <div class="bar-label">${escapeHtml(nom.replace(/_/g, ' '))}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${n/max*100}%"></div></div>
      <div class="bar-val">${n}</div>
    </div>`).join('');
  const sel = document.getElementById('themeYear');
  if (sel && sel.value !== year) sel.value = year;
}

// Synchro : clic sur une barre d'année → thématiques de cette année.
function selectThemeYear(y) {
  renderThemes(String(y));
  const sec = document.getElementById('themesSection');
  if (sec) sec.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// Rend le graphe « activité par année » pour la métrique choisie (pv ou points).
function renderYearChart(metric) {
  const plot = document.getElementById('ycPlot');
  if (!plot || !yearData.length) return;
  const years = yearData.map(a => +a.annee);
  const lo = Math.min(...years), hi = Math.max(...years);
  const maxVal = Math.max(...yearData.map(a => a[metric] || 0), 1);
  const byYear = {};
  yearData.forEach(a => { byYear[+a.annee] = a; });

  let html = '';
  for (let y = lo; y <= hi; y++) {
    const a = byYear[y];
    if (!a || !(a[metric] > 0)) {
      html += `<div class="yc-col" title="${y} · non extraite">`
        + `<span class="yc-val yc-muted">—</span>`
        + `<div class="yc-gap"></div>`
        + `<span class="yc-yr">${y}</span></div>`;
      continue;
    }
    const val = a[metric];
    const h = Math.max(4, Math.round(val / maxVal * 130));
    const shown = metric === 'points' ? val.toLocaleString('fr-BE') : val;
    html += `<div class="yc-col yc-clic" onclick="selectThemeYear(${y})" `
      + `title="${y} · ${a.pv} PV · ${a.points.toLocaleString('fr-BE')} points — voir les thématiques">`
      + `<span class="yc-val">${shown}</span>`
      + `<div class="yc-bar" style="height:${h}px"></div>`
      + `<span class="yc-yr">${y}</span></div>`;
  }
  plot.innerHTML = html;

  document.querySelectorAll('.yc-toggle button').forEach(b => {
    b.classList.toggle('active', b.dataset.metric === metric);
  });
}

async function loadStats() {
  if (statsLoaded) return;
  const container = document.getElementById('statsContent');
  try {
    const res = await fetch(API_URL + '/stats');
    if (!res.ok) {
      if (res.status === 429) throw new Error("Trop de requêtes. Patientez un instant.");
      throw new Error('Erreur ' + res.status);
    }
    const s = await res.json();

    // Thématiques par année — options du sélecteur synchronisé (voir renderThemes).
    themesByYear = s.themes_par_annee || { toutes: s.top_thematiques || [] };
    const themeYears = Object.keys(themesByYear)
      .filter(y => y !== 'toutes').sort().reverse();
    const themeOptions = ['<option value="toutes">Toutes les années</option>']
      .concat(themeYears.map(y => `<option value="${y}">${y}</option>`)).join('');

    const montantFmt = new Intl.NumberFormat('fr-BE', {
      style: 'currency', currency: 'EUR', maximumFractionDigits: 0
    }).format(s.montant_total_eur);

    // Graphe « activité par année » (bascule PV ↔ points) — voir renderYearChart().
    yearData = s.pv_par_annee || [];
    const yearChart = yearData.length ? `<div class="stat-section">
      <div class="yc-head">
        <h3><svg class="icon" aria-hidden="true"><use href="#ico-date"/></svg>Activité par année</h3>
        <div class="yc-toggle">
          <button data-metric="pv" onclick="renderYearChart('pv')">PV</button>
          <button data-metric="points" onclick="renderYearChart('points')">Points</button>
        </div>
      </div>
      <div class="yc-scroll"><div class="yc-plot" id="ycPlot"></div></div>
      <p class="yc-note">Hauteur = valeur de l'année · pointillé = année non extraite · survol pour le détail.</p>
    </div>` : '';

    container.innerHTML = `
      <div class="stats-grid">
        <div class="stat-card"><svg class="stat-ico" aria-hidden="true"><use href="#ico-date"/></svg><div class="stat-num">${s.nb_seances}</div><div class="stat-label">Séances</div></div>
        <div class="stat-card"><svg class="stat-ico" aria-hidden="true"><use href="#ico-pv"/></svg><div class="stat-num">${s.nb_points}</div><div class="stat-label">Points traités</div></div>
        <div class="stat-card"><svg class="stat-ico" aria-hidden="true"><use href="#ico-vote"/></svg><div class="stat-num">${s.votes_non_unanimes}</div><div class="stat-label">Votes disputés</div></div>
        <div class="stat-card"><svg class="stat-ico" aria-hidden="true"><use href="#ico-montant"/></svg><div class="stat-num" style="font-size:22px">${montantFmt}</div><div class="stat-label">Montants engagés</div></div>
      </div>
      ${yearChart}
      <div class="stat-section" id="themesSection">
        <div class="yc-head">
          <h3><svg class="icon" aria-hidden="true"><use href="#ico-thematique"/></svg>Thématiques les plus fréquentes</h3>
          <label class="th-year">Année&nbsp;
            <select id="themeYear" onchange="renderThemes(this.value)">${themeOptions}</select>
          </label>
        </div>
        <div id="themesBars"></div>
      </div>`;
    if (yearData.length) renderYearChart('pv');
    renderThemes('toutes');
    statsLoaded = true;
  } catch (err) {
    container.innerHTML = `<div class="error-box">Impossible de charger les statistiques. ${escapeHtml(err.message)}</div>`;
  }
}

// ── ÉVOLUTION D'UN THÈME (agrégation exhaustive via /trend) ──
function trendSuggestion(el) {
  document.getElementById('trendInput').value = el.textContent;
  loadTrend();
}
function fmtEUR(n) {
  return new Intl.NumberFormat('fr-BE', {
    style: 'currency', currency: 'EUR', maximumFractionDigits: 0
  }).format(n || 0);
}
async function loadTrend() {
  const theme = document.getElementById('trendInput').value.trim();
  const box = document.getElementById('trendResult');
  if (!theme) { box.innerHTML = ''; return; }
  box.innerHTML = '<div class="loading"><span>Calcul en cours</span><span class="dots"><span></span><span></span><span></span></span></div>';
  try {
    const res = await fetch(API_URL + '/trend?theme=' + encodeURIComponent(theme));
    if (!res.ok) {
      if (res.status === 429) throw new Error("Trop de requêtes. Patientez un instant.");
      const e = await res.json().catch(() => ({}));
      throw new Error(e.detail || e.error || ('Erreur ' + res.status));
    }
    const d = await res.json();
    if (!d.annees || !d.annees.length) {
      box.innerHTML = `<div class="trend-empty">Aucun point trouvé pour « ${escapeHtml(theme)} ». Essayez un autre mot-clé.</div>`;
      return;
    }
    const max = Math.max(...d.annees.map(a => a.total_eur), 1);
    const bars = d.annees.map(a => `
      <div class="bar-row">
        <div class="bar-label">${escapeHtml(a.annee)}</div>
        <div class="bar-track"><div class="bar-fill" style="width:${a.total_eur / max * 100}%"></div></div>
        <div class="bar-val">${fmtEUR(a.total_eur)}</div>
      </div>`).join('');
    const items = (d.top_items || []).map(it => `
      <div class="source-item">
        <div class="source-ref"><svg class="icon" aria-hidden="true"><use href="#ico-date"/></svg>${formatDate(it.date)}<br>SP ${it.sp}</div>
        <div class="source-titre">${escapeHtml(it.titre)}</div>
        <div class="source-decision">${fmtEUR(it.montant_eur)}</div>
      </div>`).join('');
    box.innerHTML = `
      <div class="trend-summary"><strong>${fmtEUR(d.total_eur)}</strong> cumulés · ${d.points_total} points liés à « ${escapeHtml(d.theme)} »</div>
      <div class="stat-section"><h3>Montants par année</h3>${bars}</div>
      <div class="stat-section"><h3>Plus grosses dépenses</h3>${items}</div>
      <p class="trend-note">${escapeHtml(d.note || '')}</p>`;
  } catch (err) {
    box.innerHTML = `<div class="error-box">Impossible de calculer l'évolution. ${escapeHtml(err.message)}</div>`;
  }
}

// ── HELPERS ──
function escapeHtml(t) {
  return String(t).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function formatDate(iso) {
  if (!iso) return '?';
  const parts = iso.split('-');
  if (parts.length === 3) return `${parts[2]}/${parts[1]}/${parts[0]}`;
  return iso;
}
