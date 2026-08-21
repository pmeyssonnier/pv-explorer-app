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
const APP_VERSION = '1.5.0';
let appVersion = APP_VERSION;
const SETTINGS_KEY = 'pv_settings';
const SETTINGS_DEFAULTS = {
  theme: 'auto', maxSources: 15, topK: 30, scoreMin: 0,
  model: 'claude-sonnet-4-6', order: 'relevance', cacheSize: 15,
};
function loadSettings() {
  try { return Object.assign({}, SETTINGS_DEFAULTS, JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}')); }
  catch (e) { return Object.assign({}, SETTINGS_DEFAULTS); }
}
let settings = loadSettings();
function saveSettings() { try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings)); } catch (e) {} }

// Icône thème séparée du ⚙️ (en-tête) : un tap change directement l'apparence
// — action fréquente — sans ouvrir le tiroir d'Options, qui ne porte plus
// que les réglages de recherche (plus rarement modifiés).
const THEME_ICONS = {
  light: '<circle cx="12" cy="12" r="4.2"/><path d="M12 3v2.4M12 18.6V21M4.9 4.9l1.7 1.7M17.4 17.4l1.7 1.7M3 12h2.4M18.6 12H21M4.9 19.1l1.7-1.7M17.4 6.6l1.7-1.7"/>',
  dark: '<path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a6.8 6.8 0 0 0 10.5 10.5Z"/>',
  auto: '<circle cx="12" cy="12" r="8.5"/><path d="M12 3.5a8.5 8.5 0 0 0 0 17Z" fill="currentColor" stroke="none"/>',
};
const THEME_LABELS = { light: 'clair', dark: 'sombre', auto: 'auto' };
const THEME_ORDER = ['light', 'dark', 'auto'];

function applyTheme() {
  if (settings.theme === 'light' || settings.theme === 'dark')
    document.documentElement.setAttribute('data-theme', settings.theme);
  else
    document.documentElement.removeAttribute('data-theme');   // « auto » → préférence OS
  const icon = document.getElementById('themeIcon');
  if (icon) icon.innerHTML = THEME_ICONS[settings.theme] || THEME_ICONS.auto;
  const btn = document.getElementById('themeBtn');
  if (btn) {
    const label = `Thème : ${THEME_LABELS[settings.theme] || 'auto'}`;
    btn.setAttribute('aria-label', label);
    btn.setAttribute('title', label + ' (appuyer pour changer)');
  }
}
function cycleTheme() {
  updateSetting('theme', THEME_ORDER[(THEME_ORDER.indexOf(settings.theme) + 1) % THEME_ORDER.length]);
}
function openSettings() { renderSettings(); document.getElementById('settingsOverlay').classList.add('open'); }
function closeSettings() {
  const panel = document.getElementById('settingsPanel');
  if (panel) panel.style.transform = '';   // efface un éventuel reliquat de glissement interrompu
  document.getElementById('settingsOverlay').classList.remove('open');
}
function updateSetting(key, val) {
  settings[key] = val; saveSettings();
  if (key === 'theme') applyTheme();
  if (key === 'cacheSize') { trimCaches(); renderHistory(); }
  renderSettings();
}
function resetSettings() {
  settings = Object.assign({}, SETTINGS_DEFAULTS);
  saveSettings(); applyTheme(); trimCaches(); renderHistory(); renderSettings();
}
// Reflète l'état courant dans les contrôles du panneau.
function renderSettings() {
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };
  const txt = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  set('setMaxSources', settings.maxSources); txt('valMaxSources', settings.maxSources);
  set('setTopK', settings.topK); txt('valTopK', settings.topK);
  set('setScoreMin', settings.scoreMin); txt('valScoreMin', (+settings.scoreMin).toFixed(2));
  set('setOrder', settings.order);
  set('setCacheSize', settings.cacheSize); txt('valCacheSize', settings.cacheSize);
  txt('appVersion', appVersion);
}

// Glisser la poignée du tiroir (mobile) vers le bas pour le fermer — pattern
// natif des bottom sheets. Sans effet sur tablette/PC (poignée masquée par
// CSS, donc jamais de pointerdown dessus).
(function initSettingsDrag() {
  const handle = document.getElementById('settingsHandle');
  const panel = document.getElementById('settingsPanel');
  if (!handle || !panel) return;
  const THRESHOLD = 90;
  let startY = null, dragging = false;

  handle.addEventListener('pointerdown', e => {
    dragging = true; startY = e.clientY;
    panel.classList.add('dragging');
    handle.setPointerCapture(e.pointerId);
  });
  handle.addEventListener('pointermove', e => {
    if (!dragging) return;
    panel.style.transform = `translateY(${Math.max(0, e.clientY - startY)}px)`;
  });
  const release = e => {
    if (!dragging) return;
    dragging = false;
    panel.classList.remove('dragging');
    const dy = Math.max(0, e.clientY - startY);
    panel.style.transform = '';
    if (dy > THRESHOLD) closeSettings();
  };
  handle.addEventListener('pointerup', release);
  handle.addEventListener('pointercancel', release);
})();

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

// ── MODE D'INTERROGATION (Rapide / Réflexion) ──
// Remplace le sélecteur de modèle (retiré des Options) : le mode PILOTE le
// paramètre `model` envoyé au backend. Rapide = modèle léger (Haiku), Réflexion
// = modèle précis (Sonnet). Persisté comme le reste des réglages.
const MODEL_RAPIDE = 'claude-haiku-4-5-20251001';
const MODEL_REFLEXION = 'claude-sonnet-4-6';
function currentMode() { return settings.model === MODEL_RAPIDE ? 'rapide' : 'reflexion'; }
function setMode(mode) {
  settings.model = (mode === 'rapide') ? MODEL_RAPIDE : MODEL_REFLEXION;
  saveSettings();
  syncModeUI();
}
function syncModeUI() {
  const m = currentMode();
  document.querySelectorAll('#modeSeg button').forEach(b =>
    b.classList.toggle('on', b.dataset.mode === m));
}
syncModeUI();

// Révèle le bouton d'envoi dès qu'il y a une question à envoyer (sinon caché ;
// le micro reste disponible). Appelé aussi après remplissage programmatique
// (dictée, reposer une question) et après envoi (champ vidé).
function onAskInput() {
  const input = document.getElementById('askInput');
  const bar = document.getElementById('askBar');
  if (input && bar) bar.classList.toggle('has-text', input.value.trim().length > 0);
}

// ── ONGLETS ──
function switchTab(tab) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.getElementById('tab-' + tab).classList.add('active');
  document.getElementById('panel-' + tab).classList.add('active');
  document.getElementById('askBar').style.display = (tab === 'chat') ? 'block' : 'none';
  if (tab === 'stats') loadStats();
  if (tab === 'elus') loadElus();
}

// ── SUGGESTIONS ──
function askSuggestion(el) {
  const text = el.querySelector('.suggestion-text');
  const question = (text ? text.textContent : el.textContent).trim();
  const cached = findCachedAnswer(question);
  if (cached) { showCachedAnswer(cached); return; }
  document.getElementById('askInput').value = question;
  submitQuestion();
}

// ── REPOSER UNE QUESTION DÉJÀ POSÉE ──
// Clic sur une bulle de question → on la remet dans le champ (à renvoyer/éditer).
function reuseQuestion(el) {
  const q = (el.textContent || '').trim();
  if (!q) return;
  const input = document.getElementById('askInput');
  input.value = q;
  onAskInput();
  input.focus();
  try { input.setSelectionRange(q.length, q.length); } catch (e) {}
  window.scrollTo(0, document.body.scrollHeight);
}

// ── HISTORIQUE PERSISTANT DES QUESTIONS (localStorage) ──
// Nombre de questions gardées (chips + réponses en cache, voir plus bas) :
// réglable dans Options ("Questions en cache"), settings.cacheSize.
const HIST_KEY = 'pv_explorer_history';

function getHistory() {
  try { return JSON.parse(localStorage.getItem(HIST_KEY) || '[]'); }
  catch (e) { return []; }
}
function saveHistory(q) {
  q = (q || '').trim();
  if (!q) return;
  let h = getHistory().filter(x => x.toLowerCase() !== q.toLowerCase());
  h.unshift(q);
  h = h.slice(0, settings.cacheSize);
  try { localStorage.setItem(HIST_KEY, JSON.stringify(h)); } catch (e) {}
  renderHistory();
}
function clearHistory() {
  try { localStorage.removeItem(HIST_KEY); } catch (e) {}
  renderHistory();
}
// Retire une seule question de l'historique (bouton « × » sur son chip),
// sans déclencher le clic du chip (qui la reposerait).
function removeHistoryItem(event, btn) {
  event.stopPropagation();
  const textEl = btn.closest('.suggestion-history')?.querySelector('.suggestion-text');
  const q = textEl ? textEl.textContent : '';
  if (!q) return;
  const h = getHistory().filter(x => x.toLowerCase() !== q.toLowerCase());
  try { localStorage.setItem(HIST_KEY, JSON.stringify(h)); } catch (e) {}
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
    `<span class="suggestion suggestion-history" onclick="askSuggestion(this)">` +
      `<span class="suggestion-text">${escapeHtml(q)}</span>` +
      `<button class="suggestion-remove" type="button" onclick="removeHistoryItem(event, this)" aria-label="Supprimer cette question">×</button>` +
    `</span>`
  ).join('');
}

// ── CACHE DES RÉPONSES DÉJÀ OBTENUES (localStorage) ──
// Les chips « Vos questions récentes » ne sont visibles que quand le fil est
// vide (page d'accueil, ou après « Nouvelle recherche » qui le vide
// justement) : cliquer un chip ne peut donc jamais retrouver une réponse
// encore affichée à l'écran. On garde ici la RÉPONSE elle-même (pas
// seulement le libellé, contrairement à HIST_KEY) pour la restituer sans
// rappeler l'API quand on reclique une question déjà posée.
const ANSWER_CACHE_KEY = 'pv_explorer_answer_cache';

function getAnswerCache() {
  try { return JSON.parse(localStorage.getItem(ANSWER_CACHE_KEY) || '[]'); }
  catch (e) { return []; }
}
function cacheAnswer(question, answer, sources, duration) {
  const q = (question || '').trim();
  if (!q) return;
  let c = getAnswerCache().filter(t => t.question.toLowerCase() !== q.toLowerCase());
  c.unshift({ question: q, answer, sources, duration });
  c = c.slice(0, settings.cacheSize);
  try { localStorage.setItem(ANSWER_CACHE_KEY, JSON.stringify(c)); } catch (e) {}
}
// Réapplique la limite courante aux deux stockages (appelé quand on change
// le réglage « Questions en cache » : sans ça, une réduction ne prendrait
// effet qu'à la prochaine question posée, pas immédiatement).
function trimCaches() {
  const n = settings.cacheSize;
  try { localStorage.setItem(HIST_KEY, JSON.stringify(getHistory().slice(0, n))); } catch (e) {}
  try { localStorage.setItem(ANSWER_CACHE_KEY, JSON.stringify(getAnswerCache().slice(0, n))); } catch (e) {}
}
function findCachedAnswer(question) {
  const q = (question || '').trim().toLowerCase();
  if (!q) return null;
  return getAnswerCache().find(t => t.question.toLowerCase() === q) || null;
}
// Réaffiche une réponse déjà obtenue, sans requête réseau : même échange
// qu'avant, restitué instantanément (et reversé dans la conversation/
// l'historique, comme un échange normal).
function showCachedAnswer(turn) {
  document.getElementById('introCard').style.display = 'none';
  document.getElementById('newSearchBtn').style.display = '';
  const conv = document.getElementById('conversation');
  conv.insertAdjacentHTML('beforeend',
    renderQuestionMsg(turn.question) +
    renderAnswerMsg(turn.question, turn.answer, buildSourcesHtml(turn.sources || []), turn.duration || ''));
  saveTurn(turn);
  saveHistory(turn.question);
  window.scrollTo(0, document.body.scrollHeight);
}

// ── PERSISTANCE DE LA CONVERSATION (questions + réponses) ──
// L'historique de chips ne garde que les libellés de questions ; ici on
// persiste les ÉCHANGES complets (question, réponse, sources, durée) pour que
// le rechargement de la page restitue la conversation telle quelle, sans
// relancer d'appel. Stockage par navigateur (localStorage), borné à CONV_MAX
// échanges (les plus anciens sont écartés).
const CONV_KEY = 'pv_explorer_conversation';
const CONV_MAX = 30;

function getConversation() {
  try { return JSON.parse(localStorage.getItem(CONV_KEY) || '[]'); }
  catch (e) { return []; }
}
function saveTurn(turn) {
  let c = getConversation();
  c.push(turn);
  if (c.length > CONV_MAX) c = c.slice(c.length - CONV_MAX);
  try { localStorage.setItem(CONV_KEY, JSON.stringify(c)); }
  catch (e) {
    // Quota dépassé : on allège en repartant du seul dernier échange.
    try { localStorage.setItem(CONV_KEY, JSON.stringify([turn])); } catch (e2) {}
  }
}
function clearConversation() {
  try { localStorage.removeItem(CONV_KEY); } catch (e) {}
}
// Restaure la conversation persistée dans le fil (au chargement).
function restoreConversation() {
  const c = getConversation();
  const conv = document.getElementById('conversation');
  if (!conv || !c.length) return;
  conv.innerHTML = c.map(t =>
    renderQuestionMsg(t.question) +
    renderAnswerMsg(t.question, t.answer, buildSourcesHtml(t.sources || []), t.duration || '')
  ).join('');
  document.getElementById('introCard').style.display = 'none';
  document.getElementById('newSearchBtn').style.display = '';
}

// ── RENDU RÉUTILISABLE D'UN ÉCHANGE (live + restauration) ──
function renderQuestionMsg(question) {
  return `
    <div class="msg msg-question">
      <div class="msg-role">Votre question</div>
      <div class="msg-bubble" role="button" tabindex="0"
           title="Cliquer pour reposer ou modifier cette question"
           onclick="reuseQuestion(this)"
           onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();reuseQuestion(this);}">${escapeHtml(question)}</div>
    </div>`;
}
function renderAnswerMsg(question, answer, sourcesHtml, durationText) {
  const time = durationText ? `<div class="msg-time">${escapeHtml(durationText)}</div>` : '';
  return `
    <div class="msg">
      <div class="msg-role">Assistant</div>
      <div class="msg-bubble">${renderMarkdown(answer)}</div>
      ${sourcesHtml}
      ${time}
      <div class="msg-actions" data-md="${encodeURIComponent(answer || '')}" data-q="${encodeURIComponent(question || '')}">
        <button class="msg-act" type="button" onclick="copyAnswer(this)">Copier</button>
        <button class="msg-act" type="button" onclick="downloadAnswer(this)">Exporter (.md)</button>
        <button class="msg-act" type="button" onclick="shareAnswer(this)">Partager</button>
      </div>
    </div>`;
}
// Assemble le bloc « Sources » (groupes Débats filmés / Délibérations) à partir
// d'une liste de sources déjà ordonnée. '' si aucune source.
function buildSourcesHtml(srcs) {
  if (!srcs || !srcs.length) return '';
  const videoItem = s => {
    // Nombre d'extraits de transcript indexés pour ce point — donne une idée
    // de la longueur du débat. PAS un nombre d'intervenant·e·s (aucune
    // diarisation) : on l'affiche donc en « extraits », jamais « intervenants ».
    const extraits = (s.n_extraits && s.n_extraits > 1)
      ? ` <span class="video-extraits">(${s.n_extraits} extraits)</span>` : '';
    const ref = s.url
      ? `<a class="video-link" href="${s.url}" target="_blank" rel="noopener noreferrer" title="Voir le débat sur YouTube (au bon moment)"><svg class="icon" aria-hidden="true"><use href="#ico-video"/></svg>▶ Voir le débat · ${formatDate(s.date)}</a>${extraits}`
      : `<svg class="icon" aria-hidden="true"><use href="#ico-video"/></svg>Débat du ${formatDate(s.date)}${extraits}`;
    return `
    <div class="source-item source-video">
      <div class="source-ref">${ref}</div>
      <div class="source-titre">${escapeHtml(s.titre)}</div>
      ${s.decision ? `<div class="source-decision">${escapeHtml(s.decision)}</div>` : ''}
    </div>`;
  };
  const pvItem = s => {
    const seance = s.url
      ? `<a class="pv-pdf-link" href="${s.url}" target="_blank" rel="noopener noreferrer" title="Ouvrir le PV (PDF) sur 1030.be"><svg class="icon" aria-hidden="true"><use href="#ico-date"/></svg>Séance ${formatDate(s.date)}</a>`
      : `<svg class="icon" aria-hidden="true"><use href="#ico-date"/></svg>Séance ${formatDate(s.date)}`;
    return `
    <div class="source-item">
      <div class="source-ref">${seance}<br>Point SP ${s.sp}</div>
      <div class="source-titre">${escapeHtml(s.titre)}</div>
      <div class="source-decision"><svg class="icon" aria-hidden="true"><use href="#ico-decision"/></svg>${escapeHtml(s.decision)}</div>
    </div>`;
  };
  const vids = srcs.filter(s => s.source_type === 'video_conseil');
  const pvs = srcs.filter(s => s.source_type !== 'video_conseil');
  // La sélection des sources reste par pertinence (score, réglage « Ordre des
  // sources ») ; UNE FOIS sélectionnées, on affiche les délibérations dans un
  // ordre de lecture naturel — date décroissante, puis n° de point (SP)
  // croissant au sein d'une même séance — plutôt que dans l'ordre du score.
  pvs.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : (a.sp || 0) - (b.sp || 0)));
  let groups = '';
  if (vids.length) {
    groups += `<div class="src-group src-group-video"><svg class="icon" aria-hidden="true"><use href="#ico-video"/></svg>Débats filmés · ${vids.length}`
      + `<span class="src-group-note">— extraits des séances filmées (YouTube)</span></div>`
      + vids.map(videoItem).join('');
  }
  if (pvs.length) {
    groups += `<div class="src-group"><svg class="icon" aria-hidden="true"><use href="#ico-pv"/></svg>Délibérations · ${pvs.length}`
      + `<span class="src-group-note">— sur base des procès-verbaux officiels</span></div>`
      + pvs.map(pvItem).join('');
  }
  const dont = vids.length
    ? ` · dont ${vids.length} débat${vids.length > 1 ? 's' : ''} filmé${vids.length > 1 ? 's' : ''} 🎥`
    : '';
  return `<div class="sources">
    <div class="sources-title"><svg class="icon" aria-hidden="true"><use href="#ico-source"/></svg>Sources · ${srcs.length}${dont}</div>
    ${groups}</div>`;
}

// ── NOUVELLE RECHERCHE : vide le fil courant (et sa persistance) ; les chips
// de questions restent. ──
function newSearch() {
  document.getElementById('conversation').innerHTML = '';
  document.getElementById('introCard').style.display = '';
  document.getElementById('newSearchBtn').style.display = 'none';
  clearConversation();
  const input = document.getElementById('askInput');
  if (input) input.value = '';
  onAskInput();
  renderHistory();
  window.scrollTo(0, 0);
}

// ── CHRONO du chat (attaché à CHAQUE réponse) ──
// Chaque question a son propre chrono : pendant l'appel, un compteur vit dans la
// bulle de chargement (« En cours… X s ») ; à la fin, la durée figée est
// insérée DANS le bloc-réponse correspondant (et non dans un bandeau global qui
// resterait épinglé en haut quand on enchaîne les questions). Mesure le temps
// réel vu par le citoyen (réveil Render inclus), pas le temps serveur.
function fmtDuration(t0) {
  return `⏱ Durée d'exécution : ${((Date.now() - t0) / 1000).toFixed(1)} s`;
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
      onAskInput();
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

// Afficher l'historique au chargement + restituer la conversation persistée
renderHistory();
restoreConversation();

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
  onAskInput();

  const conv = document.getElementById('conversation');

  // Afficher la question
  conv.insertAdjacentHTML('beforeend', renderQuestionMsg(question));

  // Afficher le chargement (chrono propre à cette question, vivant dans la bulle)
  const t0 = Date.now();
  const loadingId = 'loading-' + t0;
  const liveId = 'timer-' + t0;
  conv.insertAdjacentHTML('beforeend', `
    <div class="msg" id="${loadingId}">
      <div class="msg-role">Assistant</div>
      <div class="msg-bubble">
        <div class="loading"><span>Recherche dans les procès-verbaux</span>
        <span class="dots"><span></span><span></span><span></span></span></div>
        <div class="msg-time msg-time-live" id="${liveId}" role="status" aria-live="polite">⏱ En cours… 0 s</div>
      </div>
    </div>`);
  const timerId = setInterval(() => {
    const el = document.getElementById(liveId);
    if (el) el.textContent = `⏱ En cours… ${Math.floor((Date.now() - t0) / 1000)} s`;
  }, 250);
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
    clearInterval(timerId);
    document.getElementById(loadingId).remove();

    // Sources ordonnées selon les Options (pertinence [défaut] ou date).
    let srcs = data.sources || [];
    if (settings.order === 'date') srcs = srcs.slice().sort((a, b) => a.date < b.date ? 1 : -1);

    const durationText = fmtDuration(t0);
    conv.insertAdjacentHTML('beforeend',
      renderAnswerMsg(question, data.answer, buildSourcesHtml(srcs), durationText));

    // Persiste l'échange complet pour restitution au rechargement.
    saveTurn({ question, answer: data.answer, sources: srcs, duration: durationText });
    // Garde aussi la réponse en cache : recliquer cette question dans « Vos
    // questions récentes » la restituera sans rappeler l'API.
    cacheAnswer(question, data.answer, srcs, durationText);

  } catch (err) {
    clearInterval(timerId);
    document.getElementById(loadingId).remove();
    conv.insertAdjacentHTML('beforeend', `
      <div class="msg">
        <div class="msg-role">Assistant</div>
        <div class="error-box">
          Impossible d'obtenir une réponse. ${escapeHtml(err.message)}<br>
          <small>Vérifiez que le backend est démarré (${API_URL}).</small>
        </div>
        <div class="msg-time">${fmtDuration(t0)}</div>
      </div>`);
  }

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
  const dateBtn = `<button type="button" class="pv-pick" onclick="selectSeance('${s.date}')"
      title="Afficher les indicateurs et thématiques de cette séance">${label}</button>`;
  const icons = (s.url
      ? `<a class="pv-pdf" href="${s.url}" target="_blank" rel="noopener noreferrer"
           title="Ouvrir le PV (PDF) sur 1030.be"><svg class="icon" aria-hidden="true"><use href="#ico-pv"/></svg></a>`
      : '')
    + (s.video_url
      ? `<a class="pv-video" href="${s.video_url}" target="_blank" rel="noopener noreferrer"
           title="Voir la vidéo de la séance"><svg class="icon" aria-hidden="true"><use href="#ico-video"/></svg></a>`
      : '');
  // Colonnes fixes : date | icônes | méta → tout aligné à gauche, sans flottement.
  return `<div class="pv-row${sel ? ' pv-sel' : ''}">`
    + `<span class="pv-date">${dateBtn}</span>`
    + `<span class="pv-icons">${icons}</span>`
    + `<span class="pv-meta">${fmtInt(s.points)} points · ${fmtInt(s.votes)} votes · `
    + `<span class="pv-montant">${fmtMontant(s.montant)}</span></span></div>`;
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

// ── INTERVENTIONS PAR ÉLU·E (agrégation structurée via /elus, /elu/{key}) ──
// La recherche sémantique du chat est sensible à la formulation et non
// exhaustive ; cette vue liste TOUTES les interventions d'une personne.
let elusData = null;        // liste complète [{key,nom,role,depose,repond}]
let elusLoaded = false;
let pendingEluKey = null;   // élu·e à présélectionner depuis un lien partagé (?elu=)
let currentEluData = null;  // dernière fiche /elu/{key} chargée (pour reFiltrer sans refetch)
let eluYearFilter = 'all';  // année sélectionnée dans le filtre ("all" = toutes)

async function loadElus() {
  if (elusLoaded) return;
  const sel = document.getElementById('eluSelect');
  if (!sel) return;
  try {
    const res = await fetch(API_URL + '/elus');
    if (!res.ok) throw new Error('Erreur ' + res.status);
    elusData = (await res.json()).elus || [];
    elusLoaded = true;
    populateElus();
  } catch (err) {
    sel.innerHTML = '<option value="">Indisponible</option>';
  }
}

// (Re)remplit le sélecteur d'élu·e selon le filtre de rôle courant.
function populateElus() {
  const sel = document.getElementById('eluSelect');
  const role = (document.getElementById('eluRole') || {}).value || 'all';
  if (!sel || !elusData) return;
  const prev = sel.value;
  const list = elusData.filter(e => role === 'all' || e.role === role);
  sel.innerHTML = list.map(e => {
    const n = e.role === 'college' ? e.repond + e.depose : e.depose;
    return `<option value="${e.key}">${escapeHtml(e.nom)} (${n})</option>`;
  }).join('');
  // Présélection : lien partagé (?elu=) prioritaire, sinon on conserve la
  // sélection courante si elle reste visible, sinon la 1re entrée.
  if (pendingEluKey && list.some(e => e.key === pendingEluKey)) {
    sel.value = pendingEluKey;
    pendingEluKey = null;
  } else if (list.some(e => e.key === prev)) {
    sel.value = prev;
  }
  if (sel.value) loadElu(sel.value);
  else {
    document.getElementById('eluResult').innerHTML = '';
    document.getElementById('eluYear').hidden = true;
  }
}

// Partage : lien profond vers l'onglet Par élu·e, sur la fiche sélectionnée.
function shareElu(btn) {
  const sel = document.getElementById('eluSelect');
  const key = sel ? sel.value : '';
  const opt = sel && sel.selectedIndex >= 0 ? sel.options[sel.selectedIndex] : null;
  const nom = opt ? opt.textContent.replace(/\s*\(\d+\)\s*$/, '') : '';
  const url = key ? `${shareBaseUrl()}?tab=elus&elu=${encodeURIComponent(key)}` : `${shareBaseUrl()}?tab=elus`;
  const text = nom
    ? `Interventions de ${nom} au Conseil communal de Schaerbeek`
    : 'Interventions par élu·e — Conseil communal de Schaerbeek';
  doShare('PV Explorer — Interventions par élu·e', text, url, btn);
}

const TYPE_BADGE = {
  'Question orale': 'b-q',
  'Demande': 'b-d',
  'Motion': 'b-m',
  'Débat filmé': 'b-v',
};

// Terme utilisé pour désigner qui a soulevé le point, selon son type — affiché
// au-dessus du/de la répondant·e pour que chaque ligne se comprenne seule
// (ex. sur une capture d'écran, sans le contexte de la page).
const TYPE_ACTOR_LABEL = {
  'Question orale': 'Auteur·e de la question',
  'Demande': 'Demandeur·se',
  'Motion': 'Auteur·e de la motion',
  'Débat filmé': 'Intervenant·e',
};

async function loadElu(key) {
  const box = document.getElementById('eluResult');
  if (!key) { box.innerHTML = ''; document.getElementById('eluYear').hidden = true; return; }
  box.innerHTML = '<div class="loading"><span>Chargement</span><span class="dots"><span></span><span></span><span></span></span></div>';
  try {
    const res = await fetch(API_URL + '/elu/' + encodeURIComponent(key));
    if (!res.ok) throw new Error('Erreur ' + res.status);
    renderElu(await res.json());
  } catch (err) {
    box.innerHTML = `<div class="error-box">Impossible de charger cette fiche. ${escapeHtml(err.message)}</div>`;
  }
}

// Années distinctes (dépôts + réponses), triées récent → ancien.
function eluYears(d) {
  const ys = new Set();
  (d.depose || []).forEach(it => { if (it.date) ys.add(it.date.slice(0, 4)); });
  (d.repond || []).forEach(it => { if (it.date) ys.add(it.date.slice(0, 4)); });
  return [...ys].sort((a, b) => b.localeCompare(a));
}

// (Re)remplit le filtre d'année pour la fiche courante ; conserve l'année
// sélectionnée si elle existe encore pour cette personne, sinon "Toutes".
function populateEluYearSelect(d) {
  const sel = document.getElementById('eluYear');
  if (!sel) return;
  const years = eluYears(d);
  if (!years.length) { sel.innerHTML = ''; sel.hidden = true; return; }
  if (!years.includes(eluYearFilter)) eluYearFilter = 'all';
  sel.hidden = false;
  sel.innerHTML = '<option value="all">Toutes les années</option>' +
    years.map(y => `<option value="${y}">${y}</option>`).join('');
  sel.value = eluYearFilter;
}

function onEluYearChange() {
  eluYearFilter = document.getElementById('eluYear').value;
  if (currentEluData) renderElu(currentEluData);
}

function filterByYear(items, year) {
  if (year === 'all') return items;
  return items.filter(it => (it.date || '').slice(0, 4) === year);
}

function eluDeposeRow(it, nom) {
  const cls = TYPE_BADGE[it.type_label] || 'b-d';
  const badge = `<span class="elu-badge ${cls}">${escapeHtml(it.type_label)}</span>`;
  const sp = it.sp ? `<span class="elu-sp">SP ${it.sp}</span>` : '';
  // Lien : deep-link « ▶ Voir le débat » pour un point filmé (débat filmé
  // pur, ou point PV fusionné avec son chapitre vidéo — video_precise), sinon
  // PDF du PV et, si la séance a été filmée sans chapitre précis pour CE
  // point, un lien léger « ▶ vidéo » (généraliste, début de séance).
  let links = '';
  if (it.type === 'video' && it.url) {
    links = `<a class="elu-link elu-link-video" href="${it.url}" target="_blank" rel="noopener noreferrer" title="Voir le débat sur YouTube (au bon moment)"><svg class="icon" aria-hidden="true"><use href="#ico-video"/></svg>▶ Voir le débat</a>`;
  } else {
    if (it.url) links += `<a class="elu-link" href="${it.url}" target="_blank" rel="noopener noreferrer" title="Ouvrir le PV (PDF) sur 1030.be"><svg class="icon" aria-hidden="true"><use href="#ico-date"/></svg>PV (PDF)</a>`;
    if (it.video_url) {
      const label = it.video_precise ? '▶ Voir le débat' : '▶ vidéo';
      const title = it.video_precise
        ? 'Voir le débat sur YouTube (au bon moment)'
        : 'Voir la séance filmée sur YouTube (début de séance, pas de moment précis identifié pour ce point)';
      links += `<a class="elu-link elu-link-video" href="${it.video_url}" target="_blank" rel="noopener noreferrer" title="${title}"><svg class="icon" aria-hidden="true"><use href="#ico-video"/></svg>${label}</a>`;
    }
  }
  const actorLabel = TYPE_ACTOR_LABEL[it.type_label] || 'Auteur·e';
  const demandeur = nom ? `<div class="elu-demandeur">${escapeHtml(actorLabel)} : ${escapeHtml(nom)}</div>` : '';
  const rep = it.repondant ? `<div class="elu-rep">Répondant·e : ${escapeHtml(it.repondant)}</div>` : '';
  return `<div class="elu-item">
    <div class="elu-date">${formatDate(it.date)}</div>
    <div class="elu-body">
      ${badge}${sp}
      <div class="elu-titre">${escapeHtml(it.titre)}</div>
      ${demandeur}
      ${rep}
      ${links ? `<div class="elu-links">${links}</div>` : ''}
    </div>
  </div>`;
}

function eluRepondRow(it, nom) {
  const sp = it.sp ? `<span class="elu-sp">SP ${it.sp}</span>` : '';
  const link = it.url ? `<a class="elu-link" href="${it.url}" target="_blank" rel="noopener noreferrer" title="Ouvrir le PV (PDF) sur 1030.be"><svg class="icon" aria-hidden="true"><use href="#ico-date"/></svg>PV (PDF)</a>` : '';
  const demandeur = it.demandeur ? `<div class="elu-demandeur">Demandé par : ${escapeHtml(it.demandeur)}</div>` : '';
  const rep = nom ? `<div class="elu-rep">Répondant·e : ${escapeHtml(nom)}</div>` : '';
  return `<div class="elu-item">
    <div class="elu-date">${formatDate(it.date)}</div>
    <div class="elu-body">
      ${sp}
      <div class="elu-titre">${escapeHtml(it.titre)}</div>
      ${demandeur}
      ${rep}
      ${link ? `<div class="elu-links">${link}</div>` : ''}
    </div>
  </div>`;
}

// Regroupe une liste d'items (triés récent→ancien) par année et produit le HTML.
function groupByYear(items, rowFn) {
  const groups = [];
  let cur = null;
  items.forEach(it => {
    const y = (it.date || '').slice(0, 4) || '—';
    if (!cur || cur.year !== y) { cur = { year: y, rows: [] }; groups.push(cur); }
    cur.rows.push(it);
  });
  return groups.map(g =>
    `<div class="elu-year">${g.year}</div>` + g.rows.map(rowFn).join('')
  ).join('');
}

function renderElu(d) {
  currentEluData = d;
  populateEluYearSelect(d);

  const box = document.getElementById('eluResult');
  const depose = filterByYear(d.depose || [], eluYearFilter);
  const repond = filterByYear(d.repond || [], eluYearFilter);
  const roleLabel = d.role === 'college'
    ? 'Collège (échevin·e / bourgmestre)'
    : 'Conseiller·ère';
  const nQuestions = depose.filter(it => it.type === 'question_orale').length;
  const nDemandes = depose.filter(it => it.type === 'demande_habitant').length;
  const nMotions = depose.filter(it => it.type === 'motion').length;
  const nVideos = depose.filter(it => it.type === 'video').length;
  const parts = [];
  if (nQuestions) parts.push(`${nQuestions} question${nQuestions > 1 ? 's' : ''} orale${nQuestions > 1 ? 's' : ''}`);
  if (nDemandes) parts.push(`${nDemandes} demande${nDemandes > 1 ? 's' : ''}`);
  if (nMotions) parts.push(`${nMotions} motion${nMotions > 1 ? 's' : ''}`);
  if (nVideos) parts.push(`${nVideos} débat${nVideos > 1 ? 's' : ''} filmé${nVideos > 1 ? 's' : ''}`);

  let html = `<div class="elu-head">
    <div class="elu-name">${escapeHtml(d.nom)}</div>
    <span class="elu-role elu-role-${d.role}">${roleLabel}</span>
  </div>`;

  if (depose.length) {
    html += `<div class="elu-summary"><strong>${depose.length}</strong> intervention${depose.length > 1 ? 's' : ''} déposée${depose.length > 1 ? 's' : ''}${parts.length ? ' · ' + parts.join(' · ') : ''}</div>`;
    html += `<div class="elu-list">${groupByYear(depose, it => eluDeposeRow(it, d.nom))}</div>`;
  }

  if (repond.length) {
    html += `<details class="elu-repond"${depose.length ? '' : ' open'}>
      <summary><strong>${repond.length}</strong> réponse${repond.length > 1 ? 's' : ''} en séance <span class="elu-repond-hint">(activité de Collège)</span></summary>
      <div class="elu-list">${groupByYear(repond, it => eluRepondRow(it, d.nom))}</div>
    </details>`;
  }

  if (!depose.length && !repond.length) {
    html += eluYearFilter === 'all'
      ? `<div class="trend-empty">Aucune intervention identifiée.</div>`
      : `<div class="trend-empty">Aucune intervention en ${escapeHtml(eluYearFilter)}.</div>`;
  }

  html += `<p class="elu-note">Agrégation déterministe depuis les procès-verbaux (2012–2026) et le chapitrage des séances filmées. Attribution : question orale → 1er intervenant ; demande → auteur du titre ou 1er intervenant ; motion → auteur nommé dans le titre. Liste indicative, non exhaustive des prises de parole en débat.</p>`;

  box.innerHTML = html;
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

// Partage de LA RÉPONSE (son contenu), jamais un lien qui relancerait le chat.
// UN SEUL appel navigator.share, synchrone dans le clic (exactement comme le
// bouton des statistiques, qui fonctionne). On NE tente PAS le partage de
// fichier : sur certains mobiles il « échoue » puis notre 2e appel share() était
// refusé (le geste utilisateur est déjà consommé) → repli sur la copie, d'où le
// bug « ça copie au lieu d'ouvrir la feuille ». Le fichier .md reste dispo via
// le bouton « Exporter ». On partage donc le TEXTE de la réponse.
function shareAnswer(btn) {
  const actions = btn.closest('.msg-actions');
  const md = decodeURIComponent(actions.dataset.md || '');
  const q = decodeURIComponent(actions.dataset.q || '');
  const orig = btn.textContent;
  const flash = (msg) => { btn.textContent = msg; setTimeout(() => { btn.textContent = orig; }, 1800); };
  if (!md) { flash('Rien à partager'); return; }
  const title = 'PV Explorer — réponse du Conseil communal de Schaerbeek';
  const intro = q ? `Réponse aux procès-verbaux du Conseil communal de Schaerbeek — question : « ${q} »\n\n` : '';
  const text = intro + md;
  const url = shareBaseUrl();                // page d'accueil (sans ?q= → aucune relance)

  if (navigator.share) {
    navigator.share({ title, text, url })    // un seul appel, dans le geste → la feuille s'ouvre
      .then(() => flash('Partagé ✓'))
      .catch((err) => { if (!err || err.name !== 'AbortError') copyShare(md, () => flash('Réponse copiée ✓')); });
    return;
  }
  copyShare(md, () => flash('Réponse copiée ✓'));   // desktop sans Web Share → copie
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
  if (params.get('tab') === 'elus') {
    pendingEluKey = params.get('elu') || null;   // appliqué quand la liste est chargée
    switchTab('elus');
  }
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
