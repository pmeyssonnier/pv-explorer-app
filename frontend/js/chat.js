// ── CHAT : saisie, historique, cache de réponses, conversation persistée,
// rendu des échanges, dictée vocale, envoi de la question ──
import { API_URL } from './config.js';
import { settings } from './settings.js';
import { escapeHtml, renderMarkdown, formatDate, renderThemeTags, renderReponseDetails } from './utils.js';
import { copyShare, shareBaseUrl } from './share.js';

let isLoading = false;

// Révèle le bouton d'envoi dès qu'il y a une question à envoyer (sinon caché ;
// le micro reste disponible). Appelé aussi après remplissage programmatique
// (dictée, reposer une question) et après envoi (champ vidé).
export function onAskInput() {
  const input = document.getElementById('askInput');
  const bar = document.getElementById('askBar');
  if (input && bar) bar.classList.toggle('has-text', input.value.trim().length > 0);
}

// ── SUGGESTIONS ──
export function askSuggestion(el) {
  const text = el.querySelector('.suggestion-text');
  const question = (text ? text.textContent : el.textContent).trim();
  const cached = findCachedAnswer(question);
  if (cached) { showCachedAnswer(cached); return; }
  document.getElementById('askInput').value = question;
  submitQuestion();
}

// ── REPOSER UNE QUESTION DÉJÀ POSÉE ──
// Clic sur une bulle de question → on la remet dans le champ (à renvoyer/éditer).
export function reuseQuestion(el) {
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

export function getHistory() {
  try { return JSON.parse(localStorage.getItem(HIST_KEY) || '[]'); }
  catch (e) { return []; }
}
export function saveHistory(q) {
  q = (q || '').trim();
  if (!q) return;
  let h = getHistory().filter(x => x.toLowerCase() !== q.toLowerCase());
  h.unshift(q);
  h = h.slice(0, settings.cacheSize);
  try { localStorage.setItem(HIST_KEY, JSON.stringify(h)); } catch (e) {}
  renderHistory();
}
export function clearHistory() {
  try { localStorage.removeItem(HIST_KEY); } catch (e) {}
  renderHistory();
}
// Retire une seule question de l'historique (bouton « × » sur son chip),
// sans déclencher le clic du chip (qui la reposerait).
export function removeHistoryItem(btn, event) {
  event.stopPropagation();
  const textEl = btn.closest('.suggestion-history')?.querySelector('.suggestion-text');
  const q = textEl ? textEl.textContent : '';
  if (!q) return;
  const h = getHistory().filter(x => x.toLowerCase() !== q.toLowerCase());
  try { localStorage.setItem(HIST_KEY, JSON.stringify(h)); } catch (e) {}
  renderHistory();
}
export function renderHistory() {
  const block = document.getElementById('historyBlock');
  const chips = document.getElementById('historyChips');
  if (!block || !chips) return;
  const h = getHistory();
  if (!h.length) { block.style.display = 'none'; chips.innerHTML = ''; return; }
  block.style.display = '';
  chips.innerHTML = h.map(q =>
    `<span class="suggestion suggestion-history" data-click="askSuggestion">` +
      `<span class="suggestion-text">${escapeHtml(q)}</span>` +
      `<button class="suggestion-remove" type="button" data-click="removeHistoryItem" aria-label="Supprimer cette question">×</button>` +
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

export function getAnswerCache() {
  try { return JSON.parse(localStorage.getItem(ANSWER_CACHE_KEY) || '[]'); }
  catch (e) { return []; }
}
export function cacheAnswer(question, answer, sources, duration) {
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
export function trimCaches() {
  const n = settings.cacheSize;
  try { localStorage.setItem(HIST_KEY, JSON.stringify(getHistory().slice(0, n))); } catch (e) {}
  try { localStorage.setItem(ANSWER_CACHE_KEY, JSON.stringify(getAnswerCache().slice(0, n))); } catch (e) {}
}
export function findCachedAnswer(question) {
  const q = (question || '').trim().toLowerCase();
  if (!q) return null;
  return getAnswerCache().find(t => t.question.toLowerCase() === q) || null;
}
// Réaffiche une réponse déjà obtenue, sans requête réseau : même échange
// qu'avant, restitué instantanément (et reversé dans la conversation/
// l'historique, comme un échange normal).
export function showCachedAnswer(turn) {
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

export function getConversation() {
  try { return JSON.parse(localStorage.getItem(CONV_KEY) || '[]'); }
  catch (e) { return []; }
}
export function saveTurn(turn) {
  let c = getConversation();
  c.push(turn);
  if (c.length > CONV_MAX) c = c.slice(c.length - CONV_MAX);
  try { localStorage.setItem(CONV_KEY, JSON.stringify(c)); }
  catch (e) {
    // Quota dépassé : on allège en repartant du seul dernier échange.
    try { localStorage.setItem(CONV_KEY, JSON.stringify([turn])); } catch (e2) {}
  }
}
export function clearConversation() {
  try { localStorage.removeItem(CONV_KEY); } catch (e) {}
}
// Restaure la conversation persistée dans le fil (au chargement).
export function restoreConversation() {
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
export function renderQuestionMsg(question) {
  return `
    <div class="msg msg-question">
      <div class="msg-role">Votre question</div>
      <div class="msg-bubble" role="button" tabindex="0"
           title="Cliquer pour reposer ou modifier cette question"
           data-click="reuseQuestion" data-keyclick>${escapeHtml(question)}</div>
    </div>`;
}
export function renderAnswerMsg(question, answer, sourcesHtml, durationText) {
  const time = durationText ? `<div class="msg-time">${escapeHtml(durationText)}</div>` : '';
  return `
    <div class="msg">
      <div class="msg-role">Assistant</div>
      <div class="msg-bubble">${renderMarkdown(answer)}</div>
      ${sourcesHtml}
      ${time}
      <div class="msg-actions" data-md="${encodeURIComponent(answer || '')}" data-q="${encodeURIComponent(question || '')}">
        <button class="msg-act" type="button" data-click="copyAnswer">Copier</button>
        <button class="msg-act" type="button" data-click="downloadAnswer">Exporter (.md)</button>
        <button class="msg-act" type="button" data-click="shareAnswer">Partager</button>
      </div>
    </div>`;
}
// Assemble le bloc « Sources » (groupes Débats filmés / Délibérations) à partir
// d'une liste de sources déjà ordonnée. '' si aucune source.
export function buildSourcesHtml(srcs) {
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
      ${renderThemeTags(s.thematiques)}
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
      ${renderThemeTags(s.thematiques)}
    </div>`;
  };
  const qeItem = s => {
    // Jamais liée à une séance (question adressée au Collège hors séance,
    // voir services/questions_ecrites*.py) : pas de "Séance"/"Point SP", juste
    // la date de la question et son lien PDF si connu (souvent absent — voir
    // le même module).
    const ref = s.url
      ? `<a class="pv-pdf-link" href="${s.url}" target="_blank" rel="noopener noreferrer" title="Ouvrir la question écrite (PDF) sur 1030.be"><svg class="icon" aria-hidden="true"><use href="#ico-date"/></svg>Question du ${formatDate(s.date)}</a>`
      : `<svg class="icon" aria-hidden="true"><use href="#ico-date"/></svg>Question du ${formatDate(s.date)}`;
    return `
    <div class="source-item">
      <div class="source-ref">${ref}</div>
      <div class="source-titre">${escapeHtml(s.titre)}</div>
      ${s.decision ? `<div class="source-decision"><svg class="icon" aria-hidden="true"><use href="#ico-decision"/></svg>${escapeHtml(s.decision)}</div>` : ''}
      ${renderThemeTags(s.thematiques)}
      ${renderReponseDetails(s.reponse)}
    </div>`;
  };
  const vids = srcs.filter(s => s.source_type === 'video_conseil');
  const qes = srcs.filter(s => s.source_type === 'question_ecrite');
  const pvs = srcs.filter(s => s.source_type !== 'video_conseil' && s.source_type !== 'question_ecrite');
  // La sélection des sources reste par pertinence (score, réglage « Ordre des
  // sources ») ; UNE FOIS sélectionnées, on affiche les délibérations dans un
  // ordre de lecture naturel — date décroissante, puis n° de point (SP)
  // croissant au sein d'une même séance — plutôt que dans l'ordre du score.
  pvs.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : (a.sp || 0) - (b.sp || 0)));
  qes.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
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
  if (qes.length) {
    groups += `<div class="src-group"><svg class="icon" aria-hidden="true"><use href="#ico-pv"/></svg>Questions écrites · ${qes.length}`
      + `<span class="src-group-note">— adressées au Collège hors séance</span></div>`
      + qes.map(qeItem).join('');
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
export function newSearch() {
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

export function toggleDictation(btn) {
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
export async function submitQuestion() {
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

// Export d'une réponse (Markdown brut stocké dans data-md).
export function copyAnswer(btn) {
  const md = decodeURIComponent(btn.closest('.msg-actions').dataset.md || '');
  const done = () => { btn.textContent = 'Copié ✓'; setTimeout(() => btn.textContent = 'Copier', 1500); };
  (navigator.clipboard ? navigator.clipboard.writeText(md).then(done) : Promise.reject()).catch(() => {
    const t = document.createElement('textarea'); t.value = md; document.body.appendChild(t);
    t.select(); try { document.execCommand('copy'); } catch (e) {} t.remove(); done();
  });
}
export function downloadAnswer(btn) {
  const md = decodeURIComponent(btn.closest('.msg-actions').dataset.md || '');
  const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = 'reponse-pv-schaerbeek.md';
  document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(a.href);
}

// Partage de LA RÉPONSE (son contenu), jamais un lien qui relancerait le chat.
// UN SEUL appel navigator.share, synchrone dans le clic (exactement comme le
// bouton des statistiques, qui fonctionne). On NE tente PAS le partage de
// fichier : sur certains mobiles il « échoue » puis notre 2e appel share() était
// refusé (le geste utilisateur est déjà consommé) → repli sur la copie, d'où le
// bug « ça copie au lieu d'ouvrir la feuille ». Le fichier .md reste dispo via
// le bouton « Exporter ». On partage donc le TEXTE de la réponse.
export function shareAnswer(btn) {
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
