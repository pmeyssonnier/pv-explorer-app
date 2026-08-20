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

// ── OPTIONS (menu ⚙️) — préférences par navigateur (localStorage) ──
// Version : source unique = le backend (GET /health → { version }). La constante
// locale n'est qu'un REPLI affiché si le backend est injoignable (hors-ligne, ou
// réveil du service Render). Garder cette valeur vaguement à jour, sans plus.
const APP_VERSION = '1.3.0';
let appVersion = APP_VERSION;
const SETTINGS_KEY = 'pv_settings';
const SETTINGS_DEFAULTS = {
  theme: 'auto', maxSources: 15, topK: 30, scoreMin: 0,
  model: 'claude-sonnet-4-6', order: 'relevance',
};
function loadSettings() {
  try { return Object.assign({}, SETTINGS_DEFAULTS, JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}')); }
  catch (e) { return Object.assign({}, SETTINGS_DEFAULTS); }
}
let settings = loadSettings();
function saveSettings() { try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings)); } catch (e) {} }

function applyTheme() {
  if (settings.theme === 'light' || settings.theme === 'dark')
    document.documentElement.setAttribute('data-theme', settings.theme);
  else
    document.documentElement.removeAttribute('data-theme');   // « auto » → préférence OS
}
function openSettings() { renderSettings(); document.getElementById('settingsOverlay').classList.add('open'); }
function closeSettings() { document.getElementById('settingsOverlay').classList.remove('open'); }
function updateSetting(key, val) {
  settings[key] = val; saveSettings();
  if (key === 'theme') applyTheme();
  renderSettings();
}
function resetSettings() {
  settings = Object.assign({}, SETTINGS_DEFAULTS);
  saveSettings(); applyTheme(); renderSettings();
}
// Reflète l'état courant dans les contrôles du panneau.
function renderSettings() {
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };
  const txt = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  document.querySelectorAll('#themeSeg button').forEach(b =>
    b.classList.toggle('on', b.dataset.v === settings.theme));
  set('setMaxSources', settings.maxSources); txt('valMaxSources', settings.maxSources);
  set('setTopK', settings.topK); txt('valTopK', settings.topK);
  set('setScoreMin', settings.scoreMin); txt('valScoreMin', (+settings.scoreMin).toFixed(2));
  set('setModel', settings.model);
  set('setOrder', settings.order);
  txt('appVersion', appVersion);
}

// Récupère la version auprès du backend (source unique). Silencieux en cas
// d'échec : on garde le repli local `APP_VERSION`. Met à jour l'affichage même
// si le panneau Options est déjà ouvert.
async function fetchVersion() {
  try {
    const r = await fetch(`${API_URL}/health`, { cache: 'no-store' });
    if (!r.ok) return;
    const d = await r.json();
    if (d && d.version) {
      appVersion = d.version;
      const el = document.getElementById('appVersion');
      if (el) el.textContent = appVersion;
    }
  } catch (e) { /* backend injoignable → repli local conservé */ }
}
fetchVersion();

applyTheme();   // le <head> a déjà posé le thème ; on confirme après chargement d'app.js

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
  hideChatTimer();
  const input = document.getElementById('askInput');
  if (input) input.value = '';
  renderHistory();
  window.scrollTo(0, 0);
}

// ── CHRONO du chat (petit message au-dessus de la conversation) ──
// Pendant l'appel : « En cours depuis X s » (rafraîchi). À la fin : fige sur
// « Durée d'exécution : X s ». Mesure le temps réel vu par le citoyen (réveil
// Render inclus), pas le temps serveur.
let chatTimerId = null;
let chatTimerStart = 0;
function startChatTimer() {
  const el = document.getElementById('chatTimer');
  if (!el) return;
  chatTimerStart = Date.now();
  el.style.display = '';
  el.classList.add('running');
  const tick = () => {
    const s = Math.floor((Date.now() - chatTimerStart) / 1000);
    el.textContent = `⏱ En cours depuis ${s} s`;
  };
  tick();
  chatTimerId = setInterval(tick, 250);
}
function stopChatTimer() {
  if (chatTimerId) { clearInterval(chatTimerId); chatTimerId = null; }
  const el = document.getElementById('chatTimer');
  if (!el) return;
  const s = ((Date.now() - chatTimerStart) / 1000).toFixed(1);
  el.classList.remove('running');
  el.textContent = `⏱ Durée d'exécution : ${s} s`;
}
function hideChatTimer() {
  if (chatTimerId) { clearInterval(chatTimerId); chatTimerId = null; }
  const el = document.getElementById('chatTimer');
  if (el) { el.style.display = 'none'; el.classList.remove('running'); el.textContent = ''; }
}

// ── DICTÉE VOCALE (Web Speech API — côté navigateur, français) ──
// Amélioration progressive : le bouton micro n'est révélé que si le navigateur
// sait reconnaître la voix (Chrome/Edge/Safari ; pas Firefox). La transcription
// REMPLIT le champ (l'utilisateur relit puis clique « Demander ») — pas d'envoi
// automatique. HTTPS requis (sinon start() échoue → message).
const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
let dictation = null;      // instance en cours
let dictating = false;

if (SpeechRec) {
  const mb = document.getElementById('micBtn');
  if (mb) mb.style.display = '';
}

// Indice visible sous la barre de saisie (plus lisible sur mobile qu'un
// placeholder tronqué). kind : 'live' (écoute) ou 'err' (erreur actionnable).
let _micHintTimer = null;
function micHint(text, kind) {
  const el = document.getElementById('micHint');
  if (!el) return;
  if (_micHintTimer) { clearTimeout(_micHintTimer); _micHintTimer = null; }
  if (!text) { el.hidden = true; el.textContent = ''; el.className = 'mic-hint'; return; }
  el.textContent = text;
  el.className = 'mic-hint' + (kind ? ' ' + kind : '');
  el.hidden = false;
  if (kind === 'err') _micHintTimer = setTimeout(() => micHint(null), 7000);  // laisse le temps de lire
}

// Message d'erreur actionnable selon le code renvoyé par l'API.
function micErrorMessage(err) {
  switch (err) {
    case 'not-allowed':
    case 'service-not-allowed':
      return '🎤 Micro bloqué par le navigateur. Rechargez la page, puis autorisez le micro : ' +
             'icône 🔒 dans la barre d’adresse → Autorisations → Microphone → Autoriser.';
    case 'no-speech':
      return 'Aucune parole détectée — réappuyez sur le micro et parlez.';
    case 'audio-capture':
      return 'Aucun micro détecté sur l’appareil.';
    case 'network':
      return 'Reconnaissance vocale indisponible (problème réseau).';
    default:
      return 'Dictée indisponible (' + (err || 'erreur inconnue') + ').';
  }
}

function stopDictation() {
  if (dictation) { try { dictation.stop(); } catch (e) {} }
}

function toggleDictation(btn) {
  if (!SpeechRec) return;
  if (dictating) { stopDictation(); return; }
  const input = document.getElementById('askInput');
  if (!input) return;
  const base = input.value.trim() ? input.value.trim() + ' ' : '';
  let errored = false;

  try {
    dictation = new SpeechRec();
    dictation.lang = 'fr-FR';
    dictation.interimResults = true;
    dictation.continuous = false;

    dictation.onstart = () => {
      dictating = true;
      btn.classList.add('listening');
      btn.setAttribute('aria-label', 'Arrêter la dictée');
      micHint('🔴 Écoute… parlez maintenant', 'live');
    };
    dictation.onresult = (e) => {
      let txt = '';
      for (let i = 0; i < e.results.length; i++) txt += e.results[i][0].transcript;
      input.value = base + txt;
    };
    dictation.onerror = (e) => {
      errored = true;
      micHint(micErrorMessage(e && e.error), 'err');
    };
    dictation.onend = () => {
      dictating = false; dictation = null;
      btn.classList.remove('listening');
      btn.setAttribute('aria-label', 'Dicter la question');
      if (!errored) micHint(null);          // efface l'indice « Écoute… » ; garde le message d'erreur
      if (input.value.trim()) input.focus();
    };
    dictation.start();
  } catch (e) {
    dictating = false; dictation = null;
    btn.classList.remove('listening');
    // start() lève surtout hors HTTPS (contexte non sécurisé).
    micHint('Dictée indisponible ici — le micro nécessite une connexion sécurisée (HTTPS).', 'err');
  }
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
  startChatTimer();
  window.scrollTo(0, document.body.scrollHeight);

  // Filtre commune : "Toutes" (value vide) → on n'envoie rien (recherche croisée)
  const communeSel = document.getElementById('communeSelect');
  const commune = communeSel ? communeSel.value : '';
  const payload = { question };
  if (commune) payload.commune = commune;
  // Réglages du menu Options (re-bornés côté serveur).
  payload.top_k = settings.topK;
  payload.max_sources = settings.maxSources;
  payload.score_min = settings.scoreMin;
  payload.model = settings.model;

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

    // Construire les sources (ordre selon les Options : pertinence [défaut] ou date)
    let sourcesHtml = '';
    let srcs = data.sources || [];
    if (settings.order === 'date') srcs = srcs.slice().sort((a, b) => a.date < b.date ? 1 : -1);
    if (srcs.length) {
      // Rendu d'un débat filmé (lien « ▶ voir le débat » vers l'instant exact).
      const videoItem = s => {
        const ref = s.url
          ? `<a class="video-link" href="${s.url}" target="_blank" rel="noopener noreferrer" title="Voir le débat sur YouTube (au bon moment)"><svg class="icon" aria-hidden="true"><use href="#ico-video"/></svg>▶ Voir le débat · ${formatDate(s.date)}</a>`
          : `<svg class="icon" aria-hidden="true"><use href="#ico-video"/></svg>Débat du ${formatDate(s.date)}`;
        return `
        <div class="source-item source-video">
          <div class="source-ref">${ref}</div>
          <div class="source-titre">${escapeHtml(s.titre)}</div>
          ${s.decision ? `<div class="source-decision">${escapeHtml(s.decision)}</div>` : ''}
        </div>`;
      };
      // Rendu d'une délibération (lien vers le PDF officiel du PV).
      const pvItem = s => {
        const seance = s.url
          ? `<a class="pv-pdf-link" href="${s.url}" target="_blank" rel="noopener noreferrer" title="Ouvrir le PV (PDF) sur 1030.be"><svg class="icon" aria-hidden="true"><use href="#ico-date"/></svg>Séance ${formatDate(s.date)}</a>`
          : `<svg class="icon" aria-hidden="true"><use href="#ico-date"/></svg>Séance ${formatDate(s.date)}`;
        // Séance filmée (sans chapitrage précis) → lien vers la vidéo (début),
        // sur sa propre ligne pour ne pas élargir la colonne (débordement mobile).
        const vid = s.video_url
          ? `<br><a class="video-session-link" href="${s.video_url}" target="_blank" rel="noopener noreferrer" title="Voir la vidéo de la séance"><svg class="icon" aria-hidden="true"><use href="#ico-video"/></svg>▶ vidéo</a>`
          : '';
        return `
        <div class="source-item">
          <div class="source-ref">${seance}<br>Point SP ${s.sp}${vid}</div>
          <div class="source-titre">${escapeHtml(s.titre)}</div>
          <div class="source-decision"><svg class="icon" aria-hidden="true"><use href="#ico-decision"/></svg>${escapeHtml(s.decision)}</div>
        </div>`;
      };

      // Groupes visibles, débats filmés d'abord (pour les mettre en avant).
      const vids = srcs.filter(s => s.source_type === 'video_conseil');
      const pvs = srcs.filter(s => s.source_type !== 'video_conseil');
      let groups = '';
      if (vids.length) {
        groups += `<div class="src-group src-group-video"><svg class="icon" aria-hidden="true"><use href="#ico-video"/></svg>Débats filmés · ${vids.length}</div>`
          + vids.map(videoItem).join('');
      }
      if (pvs.length) {
        groups += `<div class="src-group"><svg class="icon" aria-hidden="true"><use href="#ico-pv"/></svg>Délibérations · ${pvs.length}</div>`
          + pvs.map(pvItem).join('');
      }
      const dont = vids.length
        ? ` · dont ${vids.length} débat${vids.length > 1 ? 's' : ''} filmé${vids.length > 1 ? 's' : ''} 🎥`
        : '';
      sourcesHtml = `<div class="sources">
        <div class="sources-title"><svg class="icon" aria-hidden="true"><use href="#ico-source"/></svg>Sources · ${srcs.length}${dont}</div>
        ${groups}</div>`;
    }

    conv.insertAdjacentHTML('beforeend', `
      <div class="msg">
        <div class="msg-role">Assistant</div>
        <div class="msg-bubble">${renderMarkdown(data.answer)}</div>
        ${sourcesHtml}
        <div class="msg-actions" data-md="${encodeURIComponent(data.answer || '')}" data-q="${encodeURIComponent(question || '')}">
          <button class="msg-act" type="button" onclick="copyAnswer(this)">Copier</button>
          <button class="msg-act" type="button" onclick="downloadAnswer(this)">Exporter (.md)</button>
          <button class="msg-act" type="button" onclick="shareAnswer(this)">Partager</button>
        </div>
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

  stopChatTimer();
  isLoading = false;
  document.getElementById('askBtn').disabled = false;
  window.scrollTo(0, document.body.scrollHeight);
}

// ── STATISTIQUES — drill-down Année → Mois → Séance ──
// KPI et thématiques se recalculent pour le périmètre courant, à partir d'un
// résumé compact par séance (s.seances_resume). Navigation 100 % côté client :
// un clic = un niveau plus bas, aucun nouvel appel serveur.
let statsLoaded = false;
let seancesResume = [];                       // [{date, points, votes, montant, themes:[[t,n]]}]
let drill = { level: 'year', year: null, month: null };
let drillMetric = 'points';
let latestYear = '';
let selectedSeance = null;   // date d'un PV choisi dans la liste (affine KPI + thèmes)
let selectedTheme = null;    // thème choisi → ne garde dans la liste que les PV concernés
let expandedYears = new Set();  // années dépliées dans la vue « tous les PV » groupée

const MOIS_FR = ['', 'janvier', 'février', 'mars', 'avril', 'mai', 'juin',
  'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre'];
// Entier groupé « # ### ##0 » avec des espaces (déterministe, indépendant de la
// locale du navigateur — évite « 8718 » quand fr-BE n'est pas disponible).
const fmtInt = n => Math.round(n || 0).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
// Montant « # ### ##0 € ».
const fmtMontant = n => fmtInt(n) + ' €';

// Séances du périmètre du GRAPHE (toutes / une année / un mois) — pilote la liste.
function scopeSeances() {
  return seancesResume.filter(s => {
    if (drill.year && s.date.slice(0, 4) !== drill.year) return false;
    if (drill.month && s.date.slice(0, 7) !== drill.year + '-' + drill.month) return false;
    return true;
  });
}

// Périmètre EFFECTIF pour KPI + thématiques : un PV précis s'il est sélectionné
// dans la liste, sinon le périmètre du graphe.
function activeScope() {
  return selectedSeance ? seancesResume.filter(s => s.date === selectedSeance) : scopeSeances();
}

// Agrège une liste de séances → KPI + thématiques (canoniques, top 12).
// Chaque thème cumule son nombre de points ET son montant engagé.
function aggregate(list) {
  const th = {}; let points = 0, votes = 0, montant = 0;
  list.forEach(s => {
    points += s.points; votes += s.votes; montant += s.montant;
    (s.themes || []).forEach(([t, n, m]) => {
      const e = th[t] || (th[t] = { n: 0, m: 0 });
      e.n += n; e.m += (m || 0);
    });
  });
  const top = Object.entries(th).sort((a, b) => b[1].n - a[1].n).slice(0, 12)
    .map(([t, e]) => [t, e.n, e.m]);
  return { nb: list.length, points, votes, montant, themes: top };
}

// Libellé lisible du périmètre courant.
function scopeLabel() {
  if (drill.level === 'year') return 'Toutes les années';
  if (drill.month) return MOIS_FR[+drill.month].replace(/^./, c => c.toUpperCase()) + ' ' + drill.year;
  return drill.year;
}

// KPI (séances / points / votes / montant) recalculés pour le périmètre.
function renderKPIs() {
  const box = document.getElementById('statsKPIs');
  if (!box) return;
  const a = aggregate(activeScope());
  const card = (ico, num, lbl, small) =>
    `<div class="stat-card"><svg class="stat-ico" aria-hidden="true"><use href="#${ico}"/></svg>`
    + `<div class="stat-num"${small ? ' style="font-size:22px"' : ''}>${num}</div>`
    + `<div class="stat-label">${lbl}</div></div>`;
  box.innerHTML =
    card('ico-date', fmtInt(a.nb), 'Séances') +
    card('ico-pv', fmtInt(a.points), 'Points traités') +
    card('ico-vote', fmtInt(a.votes), 'Votes disputés') +
    card('ico-montant', fmtMontant(a.montant), 'Montants engagés', true);
  const lbl = document.getElementById('scopeLabel');
  if (lbl) lbl.textContent = selectedSeance ? 'Séance du ' + formatDate(selectedSeance) : scopeLabel();
  const reset = document.getElementById('drillReset');
  if (reset) reset.hidden = (drill.level === 'year' && !selectedSeance);
}

// Barres du niveau courant : années, ou mois de l'année sélectionnée.
// (Pas de niveau « séance » : ~1 séance/mois → le mois EST la séance.)
function drillBuckets() {
  if (drill.level === 'year') {
    const m = {};
    seancesResume.forEach(s => { const y = s.date.slice(0, 4); (m[y] = m[y] || []).push(s); });
    return Object.keys(m).sort().map(k => ({ key: k, label: k, list: m[k] }));
  }
  // Niveau mois : tous les mois de l'année (indépendant du mois éventuellement focalisé).
  const m = {};
  seancesResume.filter(s => s.date.slice(0, 4) === drill.year)
    .forEach(s => { const mo = s.date.slice(5, 7); (m[mo] = m[mo] || []).push(s); });
  return Object.keys(m).sort().map(k => ({ key: k, label: MOIS_FR[+k].slice(0, 4) + '.', list: m[k] }));
}
const bucketVal = b => drillMetric === 'pv' ? b.list.length : aggregate(b.list).points;

// Graphe du niveau courant + titre + fil d'Ariane.
function renderDrill() {
  const plot = document.getElementById('drillPlot');
  if (!plot) return;
  const bs = drillBuckets();
  const max = Math.max(...bs.map(bucketVal), 1);
  plot.innerHTML = bs.map(b => {
    const v = bucketVal(b), h = Math.max(4, Math.round(v / max * 130)), a = aggregate(b.list);
    const sel = (drill.level === 'month' && b.key === drill.month);
    const tip = `${b.label} · ${fmtInt(a.points)} points · ${fmtInt(a.nb)} séance(s) · ${fmtMontant(a.montant)}`;
    return `<div class="yc-col yc-clic${sel ? ' yc-sel' : ''}" onclick="drillInto('${b.key}')" title="${tip}">`
      + `<span class="yc-val">${drillMetric === 'points' ? fmtInt(v) : v}</span>`
      + `<div class="yc-bar" style="height:${h}px"></div>`
      + `<span class="yc-yr">${b.label}</span></div>`;
  }).join('');

  const t = document.getElementById('drillTitle');
  if (t) t.textContent = drill.level === 'year' ? 'Activité par année' : 'Activité par mois — ' + drill.year;
  const hint = drill.level === 'year'
    ? 'Cliquez une année pour voir ses mois.'
    : 'Cliquez un mois pour affiner les indicateurs et les thématiques à cette séance.';
  const hn = document.getElementById('drillHint'); if (hn) hn.textContent = hint;
  document.querySelectorAll('.yc-toggle button').forEach(btn =>
    btn.classList.toggle('active', btn.dataset.metric === drillMetric));
  renderCrumb();
}

// Fil d'Ariane cliquable pour remonter d'un niveau.
function renderCrumb() {
  const bc = document.getElementById('drillCrumb');
  if (!bc) return;
  let p;
  if (drill.level === 'year') {
    p = [selectedSeance
      ? '<a onclick="drillTo(\'year\')">Toutes les années</a>'
      : '<span class="crumb-here">Toutes les années</span>'];
  } else {
    p = ['<a onclick="drillTo(\'year\')">Toutes les années</a>'];
    const yearHere = !drill.month && !selectedSeance;
    p.push(yearHere
      ? `<span class="crumb-here">${drill.year}</span>`
      : `<a onclick="drillTo('month','${drill.year}')">${drill.year}</a>`);
    if (drill.month) {
      const monthHere = !selectedSeance;
      p.push(monthHere
        ? `<span class="crumb-here">${MOIS_FR[+drill.month]}</span>`
        : `<a onclick="clearSeance()">${MOIS_FR[+drill.month]}</a>`);
    }
  }
  if (selectedSeance) p.push(`<span class="crumb-here">Séance du ${formatDate(selectedSeance)}</span>`);
  bc.innerHTML = p.join('<span class="crumb-sep">›</span>');
}

// Thématiques recalculées pour le périmètre courant.
function renderThemes() {
  const box = document.getElementById('themesBars');
  if (!box) return;
  const rows = aggregate(activeScope()).themes;
  const sc = document.getElementById('themesScope');
  if (sc) sc.textContent = selectedSeance ? 'séance du ' + formatDate(selectedSeance) : scopeLabel().toLowerCase();
  if (!rows.length) { box.innerHTML = '<p class="yc-note">Aucune donnée pour cette vue.</p>'; return; }
  const max = Math.max(...rows.map(r => r[1]), 1);
  box.innerHTML = rows.map(([nom, n, m]) => `
    <div class="bar-row bar-clic${nom === selectedTheme ? ' bar-sel' : ''}"
         onclick="selectTheme('${nom.replace(/'/g, "\\'")}')"
         title="Ne montrer que les PV traitant « ${escapeHtml(nom.replace(/_/g, ' '))} »">
      <div class="bar-label">${escapeHtml(nom.replace(/_/g, ' '))}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${n / max * 100}%"></div></div>
      <div class="bar-val">${fmtInt(n)}</div>
      <div class="bar-eur" title="Montant engagé sur les points de ce thème">${m ? fmtMontant(m) : '—'}</div>
    </div>`).join('')
    + '<p class="yc-note">Chiffre = points traités portant la thématique. Un point peut relever de '
    + 'plusieurs thématiques : la somme des barres (points et montants) dépasse donc les totaux, '
    + 'et seules les 12 thématiques principales sont affichées.</p>';
}

// Séances à lister : celles du périmètre courant ; au niveau « toutes les
// années », on retombe sur la dernière année (défaut demandé) plutôt que 144.
function listSeances() {
  // « Toutes les années » → tous les PV (regroupés par année à l'affichage) ;
  // sinon les PV du périmètre du graphe (année ou mois).
  let base = (drill.level === 'year') ? seancesResume.slice() : scopeSeances();
  if (selectedTheme) base = base.filter(s => (s.themes || []).some(([t]) => t === selectedTheme));
  return base;
}

// HTML d'une ligne PV (titre cliquable pour affiner + lien PDF si disponible).
function pvRowHtml(s) {
  const sel = s.date === selectedSeance;
  const label = `Séance du ${formatDate(s.date)}`;
  const head = `<button type="button" class="pv-pick" onclick="selectSeance('${s.date}')"
      title="Afficher les indicateurs et thématiques de cette séance">${label}</button>`
    + (s.url
      ? `<a class="pv-pdf" href="${s.url}" target="_blank" rel="noopener noreferrer"
           title="Ouvrir le PV (PDF) sur 1030.be"><svg class="icon" aria-hidden="true"><use href="#ico-pv"/></svg></a>`
      : '')
    + (s.video_url
      ? `<a class="pv-video" href="${s.video_url}" target="_blank" rel="noopener noreferrer"
           title="Voir la vidéo de la séance"><svg class="icon" aria-hidden="true"><use href="#ico-video"/></svg></a>`
      : '');
  return `<div class="pv-row${sel ? ' pv-sel' : ''}">`
    + `<span class="pv-head">${head}</span>`
    + `<span class="pv-meta">${fmtInt(s.points)} points · ${fmtInt(s.votes)} votes · ${fmtMontant(s.montant)}</span></div>`;
}

// Liste des PV du périmètre. « Toutes les années » → groupée par année (repliable) ;
// une année/un mois → liste chronologique simple.
function renderPvList() {
  const box = document.getElementById('pvList');
  if (!box) return;
  const cap = document.getElementById('pvScope');
  if (cap) {
    const base = drill.level === 'year' ? 'Toutes les années' : scopeLabel();
    cap.innerHTML = selectedTheme
      ? `${base} · thème « ${escapeHtml(selectedTheme.replace(/_/g, ' '))} » `
        + `<button type="button" class="pv-clear" onclick="selectTheme('${selectedTheme.replace(/'/g, "\\'")}')">✕</button>`
      : base;
  }
  const all = listSeances();
  if (!all.length) { box.innerHTML = '<p class="yc-note">Aucune séance.</p>'; return; }

  if (drill.level === 'year') {
    // Regroupement par année, plus récente en premier, chaque groupe repliable.
    const byYear = {};
    all.forEach(s => { const y = s.date.slice(0, 4); (byYear[y] = byYear[y] || []).push(s); });
    const years = Object.keys(byYear).sort().reverse();
    box.innerHTML = years.map(y => {
      const items = byYear[y].slice().sort((a, b) => a.date < b.date ? 1 : -1);
      const open = expandedYears.has(y);
      return `<div class="pv-group">
        <button type="button" class="pv-group-head" onclick="toggleYear('${y}')">
          <span class="pv-caret">${open ? '▾' : '▸'}</span>
          <span class="pv-group-year">${y}</span>
          <span class="pv-group-count">${items.length} séance${items.length > 1 ? 's' : ''}</span>
        </button>
        ${open ? `<div class="pv-group-body">${items.map(pvRowHtml).join('')}</div>` : ''}
      </div>`;
    }).join('');
  } else {
    const list = all.slice().sort((a, b) => a.date < b.date ? 1 : -1);
    box.innerHTML = list.map(pvRowHtml).join('')
      + (list.length > 1 ? '<p class="yc-note">Cliquez une séance pour n\'afficher que ses indicateurs et thématiques.</p>' : '');
  }
}

// Plier/déplier une année dans la vue « tous les PV ».
function toggleYear(y) {
  if (expandedYears.has(y)) expandedYears.delete(y); else expandedYears.add(y);
  renderPvList();
}

function refreshStats() { renderKPIs(); renderDrill(); renderThemes(); renderPvList(); }

// Navigation graphe : Année → mois ; mois → focalise ce mois. Toute navigation
// dans le graphe annule la sélection d'un PV précis.
function drillInto(key) {
  selectedSeance = null; selectedTheme = null;
  if (drill.level === 'year') drill = { level: 'month', year: key, month: null };
  else drill = { level: 'month', year: drill.year, month: key };
  refreshStats();
}
// Remonter via le fil d'Ariane ou le bouton « Toutes les années ».
function drillTo(level, year) {
  selectedSeance = null; selectedTheme = null;
  if (level === 'year') drill = { level: 'year', year: null, month: null };
  else if (level === 'month') drill = { level: 'month', year: year || drill.year, month: null };
  refreshStats();
}
// Sélection d'un PV dans la liste : affine KPI + thématiques à cette séance
// (re-cliquer désélectionne). clearSeance() revient au périmètre du graphe.
function selectSeance(date) {
  selectedSeance = (selectedSeance === date) ? null : date;
  refreshStats();
}
function clearSeance() { selectedSeance = null; refreshStats(); }
// Clic sur un thème : ne garde dans la liste que les PV concernés (toggle).
// Change le filtre → annule la sélection d'un PV précis.
function selectTheme(nom) {
  selectedTheme = (selectedTheme === nom) ? null : nom;
  selectedSeance = null;
  refreshStats();
}
function setMetric(m) { drillMetric = m; renderDrill(); }

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
    seancesResume = s.seances_resume || [];
    latestYear = seancesResume.reduce((mx, x) => x.date.slice(0, 4) > mx ? x.date.slice(0, 4) : mx, '');
    expandedYears = new Set([latestYear]);   // année la plus récente dépliée par défaut
    drill = { level: 'year', year: null, month: null };
    drillMetric = 'points';

    container.innerHTML = `
      <div class="scope-line">
        <div class="scope-title">Vue&nbsp;: <b id="scopeLabel">Toutes les années</b></div>
        <button class="drill-reset" id="drillReset" hidden onclick="drillTo('year')">↩ Toutes les années</button>
      </div>
      <div class="stats-grid" id="statsKPIs"></div>
      <div class="stat-section">
        <div class="yc-head">
          <h3><svg class="icon" aria-hidden="true"><use href="#ico-stats"/></svg><span id="drillTitle">Activité par année</span></h3>
          <div class="yc-toggle">
            <button data-metric="pv" onclick="setMetric('pv')">PV</button>
            <button data-metric="points" class="active" onclick="setMetric('points')">Points</button>
          </div>
        </div>
        <div class="drill-crumb" id="drillCrumb"></div>
        <div class="yc-scroll"><div class="yc-plot" id="drillPlot"></div></div>
        <p class="yc-note" id="drillHint"></p>
      </div>
      <div class="stat-section" id="themesSection">
        <div class="yc-head">
          <h3><svg class="icon" aria-hidden="true"><use href="#ico-thematique"/></svg>Thématiques — <span id="themesScope">toutes les années</span></h3>
        </div>
        <div id="themesBars"></div>
      </div>
      <div class="stat-section" id="pvSection">
        <div class="yc-head">
          <h3><svg class="icon" aria-hidden="true"><use href="#ico-pv"/></svg>Procès-verbaux — <span id="pvScope"></span></h3>
        </div>
        <div id="pvList" class="pv-list"></div>
      </div>`;
    refreshStats();
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
function fmtEUR(n) { return fmtMontant(n); }   // même format « # ### ##0 € » que les KPI
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
    const items = (d.top_items || []).map(it => {
      const dateHtml = it.url
        ? `<a class="pv-pdf-link" href="${it.url}" target="_blank" rel="noopener noreferrer" title="Ouvrir le PV (PDF) sur 1030.be"><svg class="icon" aria-hidden="true"><use href="#ico-date"/></svg>${formatDate(it.date)}</a>`
        : `<svg class="icon" aria-hidden="true"><use href="#ico-date"/></svg>${formatDate(it.date)}`;
      return `
      <div class="source-item">
        <div class="source-ref">${dateHtml}<br>SP ${it.sp}</div>
        <div class="source-titre">${escapeHtml(it.titre)}</div>
        <div class="source-decision">${fmtEUR(it.montant_eur)}</div>
      </div>`;
    }).join('');
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

// Rendu Markdown minimal et SÛR (échappe le texte d'abord, puis n'insère que nos
// propres balises) : tableaux, titres, gras/italique, code, listes, séparateurs.
function renderMarkdown(src) {
  const inline = s => escapeHtml(s)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*(?!\s)([^*]+?)\*/g, '$1<em>$2</em>')
    .replace(/`([^`]+?)`/g, '<code>$1</code>');
  const lines = String(src || '').replace(/\r\n/g, '\n').split('\n');
  const out = [];
  let i = 0;
  const isRow = l => /^\s*\|.*\|\s*$/.test(l);
  const cells = l => l.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
  while (i < lines.length) {
    const l = lines[i];
    if (isRow(l) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      const head = cells(l); i += 2; const rows = [];
      while (i < lines.length && isRow(lines[i])) { rows.push(cells(lines[i])); i++; }
      const thead = '<tr>' + head.map(h => `<th>${inline(h)}</th>`).join('') + '</tr>';
      const tbody = rows.map(r => '<tr>' + head.map((_, j) => `<td>${inline(r[j] || '')}</td>`).join('') + '</tr>').join('');
      out.push(`<div class="md-tablewrap"><table class="md-table"><thead>${thead}</thead><tbody>${tbody}</tbody></table></div>`);
      continue;
    }
    const mh = l.match(/^(#{1,6})\s+(.*)$/);
    if (mh) { out.push(`<h4 class="md-h">${inline(mh[2])}</h4>`); i++; continue; }
    if (/^\s*---+\s*$/.test(l)) { out.push('<hr class="md-hr">'); i++; continue; }
    if (/^\s*[-*]\s+/.test(l)) {
      const items = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) { items.push(`<li>${inline(lines[i].replace(/^\s*[-*]\s+/, ''))}</li>`); i++; }
      out.push(`<ul class="md-ul">${items.join('')}</ul>`); continue;
    }
    if (/^\s*$/.test(l)) { i++; continue; }
    const para = [];
    while (i < lines.length && !/^\s*$/.test(lines[i]) && !isRow(lines[i])
           && !/^#{1,6}\s/.test(lines[i]) && !/^\s*[-*]\s+/.test(lines[i]) && !/^\s*---+\s*$/.test(lines[i])) {
      para.push(inline(lines[i])); i++;
    }
    if (para.length) out.push(`<p>${para.join('<br>')}</p>`);
  }
  return out.join('');
}

// Export d'une réponse (Markdown brut stocké dans data-md).
function copyAnswer(btn) {
  const md = decodeURIComponent(btn.closest('.msg-actions').dataset.md || '');
  const done = () => { btn.textContent = 'Copié ✓'; setTimeout(() => btn.textContent = 'Copier', 1500); };
  (navigator.clipboard ? navigator.clipboard.writeText(md).then(done) : Promise.reject()).catch(() => {
    const t = document.createElement('textarea'); t.value = md; document.body.appendChild(t);
    t.select(); try { document.execCommand('copy'); } catch (e) {} t.remove(); done();
  });
}
function downloadAnswer(btn) {
  const md = decodeURIComponent(btn.closest('.msg-actions').dataset.md || '');
  const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = 'reponse-pv-schaerbeek.md';
  document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(a.href);
}

// ── PARTAGE ──
// URL de l'app sans query/fragment (repère de base pour les liens partagés).
function shareBaseUrl() { return location.href.split('#')[0].split('?')[0]; }

// Copie « texte + lien » dans le presse-papier (repli si navigator.share absent).
function copyShare(payload, cb) {
  (navigator.clipboard ? navigator.clipboard.writeText(payload).then(cb) : Promise.reject()).catch(() => {
    const t = document.createElement('textarea'); t.value = payload; document.body.appendChild(t);
    t.select(); try { document.execCommand('copy'); } catch (e) {} t.remove(); cb();
  });
}

// Partage via la feuille native (mobile) si dispo, sinon copie du lien. Le
// bouton confirme brièvement (« Partagé ✓ » / « Lien copié ✓ »).
function doShare(title, text, url, btn) {
  const orig = btn ? btn.textContent : '';
  const flash = (msg) => { if (btn) { btn.textContent = msg; setTimeout(() => { btn.textContent = orig; }, 1800); } };
  if (navigator.share) {
    navigator.share({ title, text, url }).then(() => flash('Partagé ✓')).catch((err) => {
      if (err && err.name === 'AbortError') return;           // annulé par l'utilisateur
      copyShare(`${text}\n${url}`, () => flash('Lien copié ✓'));
    });
  } else {
    copyShare(`${text}\n${url}`, () => flash('Lien copié ✓'));
  }
}

function shareAnswer(btn) {
  const q = decodeURIComponent(btn.closest('.msg-actions').dataset.q || '');
  const url = q ? `${shareBaseUrl()}?q=${encodeURIComponent(q)}` : shareBaseUrl();
  const text = q ? `Question aux procès-verbaux du Conseil communal de Schaerbeek : « ${q} »`
                 : 'PV Explorer — procès-verbaux du Conseil communal de Schaerbeek';
  doShare('PV Explorer — Conseil communal de Schaerbeek', text, url, btn);
}

function shareStats(btn) {
  doShare('PV Explorer — Statistiques du Conseil communal de Schaerbeek',
          'Statistiques des décisions du Conseil communal de Schaerbeek',
          `${shareBaseUrl()}?tab=stats`, btn);
}

// Liens partagés : ?tab=stats ouvre l'onglet Statistiques ; ?q=… ré-ouvre la
// question et la relance automatiquement (au chargement).
function handleDeepLink() {
  const params = new URLSearchParams(location.search);
  if (params.get('tab') === 'stats') switchTab('stats');
  const q = params.get('q');
  if (q) {
    const input = document.getElementById('askInput');
    if (input) { input.value = q; submitQuestion(); }
  }
}
handleDeepLink();
function formatDate(iso) {
  if (!iso) return '?';
  const parts = iso.split('-');
  if (parts.length === 3) return `${parts[2]}/${parts[1]}/${parts[0]}`;
  return iso;
}
