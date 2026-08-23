// ── ADMIN (authentification + intégration d'un PV) ──
// Compte unique (voir backend/services/auth.py) : le cadenas dans l'en-tête
// ouvre une modale de connexion (même mécanique que le panneau Options — voir
// settings.js) ; une fois connecté·e, l'onglet Admin permet d'uploader le PDF
// d'un nouveau PV. Flux en 2 temps (voir services/pv_integration.py côté
// backend) : extraction + aperçu d'abord (submitAdminExtract), publication
// seulement après confirmation explicite (confirmAdminPublish) — jamais de
// publication automatique sur simple upload.
import { API_URL } from './config.js';
import { escapeHtml } from './utils.js';

let adminUsername = null;
// Résultat de /admin/seances/extract en attente de confirmation (voir
// confirmAdminPublish/cancelAdminExtract) — tant que non nul, le panneau
// affiche l'aperçu plutôt que le formulaire d'upload. Rien n'est persisté
// côté serveur entre extraction et publication : ce module en garde la trace.
let pendingSeance = null;
let pendingSourceUrl = null;
// Résumé de la dernière publication réussie — affiché une fois au-dessus du
// formulaire d'upload, effacé dès qu'une nouvelle extraction démarre.
let lastPublishResult = null;

// Même mécanique, pour le flux (indépendant) d'intégration d'une question
// écrite — voir services/questions_ecrites_integration.py côté backend.
// États distincts (jamais les deux formulaires en cours d'extraction/
// publication en même temps côté données, mais les deux sections restent
// affichées simultanément dans l'onglet Admin).
let pendingQuestion = null;
let pendingQeSourceUrl = null;
let lastQePublishResult = null;

// Session vérifiée via cookie httpOnly (jamais lu par ce script — juste
// renvoyé automatiquement par le navigateur, `credentials: 'include'`) : on
// interroge /admin/me pour savoir si une session valide existe déjà.
export async function checkAdminSession() {
  try {
    const res = await fetch(API_URL + '/admin/me', { credentials: 'include' });
    adminUsername = res.ok ? (await res.json()).username : null;
  } catch {
    adminUsername = null;
  }
  updateAdminUI();
}

function updateAdminUI() {
  const tabBtn = document.getElementById('tab-admin');
  if (tabBtn) tabBtn.hidden = !adminUsername;
  // Session absente/expirée alors que l'onglet Admin était actif : revient à
  // Question plutôt que de laisser un onglet vide/caché actif.
  const adminPanel = document.getElementById('panel-admin');
  if (!adminUsername && adminPanel && adminPanel.classList.contains('active')) {
    document.getElementById('tab-chat')?.click();
  }
  renderAdminPanel();
}

function renderAdminPanel() {
  const box = document.getElementById('adminPanelBody');
  if (!box) return;
  if (!adminUsername) { box.innerHTML = ''; return; }
  const head = `<p class="yc-note">Connecté·e en tant que <strong>${escapeHtml(adminUsername)}</strong>.</p>
    <button type="button" class="drill-reset" data-click="adminLogout">Se déconnecter</button>`;
  box.innerHTML = head
    + (pendingSeance ? renderPreview() : renderUploadForm())
    + (pendingQuestion ? renderQePreview() : renderQeUploadForm());
  const form = document.getElementById('adminUploadForm');
  if (form) form.addEventListener('submit', submitAdminExtract);
  const fileEl = document.getElementById('adminPdfFile');
  if (fileEl) fileEl.addEventListener('change', prefillSourceUrl);
  const qeForm = document.getElementById('qeUploadForm');
  if (qeForm) qeForm.addEventListener('submit', submitQeExtract);
}

// Le lien "PDF officiel" (frontend/js/stats.js) ne s'affiche que si
// source_url est renseignée — laissée vide par défaut, elle l'est presque
// toujours restée (cause du correctif manuel de plusieurs PV de 2010). On
// pré-remplit donc dès le choix du fichier avec le nom tel quel sous le
// dossier 1030.be habituel : ce n'est qu'un point de départ (les nomenclatures
// réelles varient — voir scraping/pv_scraper_1030.py), l'admin corrige avant
// d'extraire si besoin. Champ toujours modifiable/vidable, pas obligatoire.
function prefillSourceUrl() {
  const fileEl = document.getElementById('adminPdfFile');
  const sourceUrlEl = document.getElementById('adminSourceUrl');
  const file = fileEl && fileEl.files[0];
  if (sourceUrlEl && file) {
    sourceUrlEl.value = `https://www.1030.be/data/media/import/${encodeURIComponent(file.name)}`;
  }
}

function renderUploadForm() {
  const banner = lastPublishResult
    ? `<p class="admin-check-ok">✅ Séance du ${escapeHtml(lastPublishResult.date)} publiée — ${lastPublishResult.n_points} point(s), ${lastPublishResult.indexed} indexé(s). Commit ${escapeHtml((lastPublishResult.commit_sha || '').slice(0, 7))}.</p>`
    : '';
  return `${banner}<section class="admin-upload">
    <h4>Intégrer un nouveau PV</h4>
    <p class="yc-note">Upload le PDF officiel : extraction automatique (Claude), avec un contrôle de complétude déterministe avant toute publication.</p>
    <form id="adminUploadForm" class="admin-login-form">
      <label for="adminPdfFile">Fichier PDF du PV</label>
      <input type="file" id="adminPdfFile" accept="application/pdf" required>
      <label for="adminSourceUrl">URL du PV sur 1030.be (pré-remplie depuis le fichier — à vérifier/corriger)</label>
      <input type="url" id="adminSourceUrl" placeholder="https://www.1030.be/...">
      ${renderProgressBox()}
      <p class="admin-login-error" id="adminUploadError" role="alert"></p>
      <button type="submit" class="ask-btn admin-login-submit" id="adminUploadSubmit">Extraire</button>
    </form>
  </section>`;
}

// Barre de progression partagée entre extraction et publication (jamais les
// deux en même temps — vues mutuellement exclusives) : masquée par défaut via
// style.display (pas l'attribut hidden — voir le bug .elus-bar/.tab plus
// haut dans l'historique du projet, une classe avec `display` définu ailleurs
// peut le rendre inopérant ; ici on manipule le style directement, aucune
// ambiguïté possible).
// `prefix` distingue les 2 formulaires indépendants (PV vs question écrite) :
// ids par défaut inchangés (adminProgress*) pour le PV, 'qe' pour l'autre —
// jamais de collision d'id si les deux étaient en cours en même temps.
function renderProgressBox(prefix = 'admin') {
  return `<div class="admin-progress" id="${prefix}ProgressBox" style="display:none">
    <div class="admin-progress-track"><div class="admin-progress-fill" id="${prefix}ProgressFill"></div></div>
    <p class="yc-note" id="${prefix}ProgressLabel"></p>
  </div>`;
}

function renderPreview() {
  const { seance, preview } = pendingSeance;
  const check = (seance.seance || {}).extraction_check || {};
  const points = seance.points || [];
  const titles = points.slice(0, 5).map(p => `<li>${escapeHtml(p.titre || '(sans titre)')}</li>`).join('');
  const more = points.length > 5 ? `<li>… et ${points.length - 5} de plus</li>` : '';
  const completeness = check.ok
    ? `<p class="admin-check-ok">✅ Complétude vérifiée : ${check.extracted}/${check.expected} points (comptage indépendant du texte).</p>`
    : `<p class="admin-check-warn">⚠️ Complétude incomplète : ${check.extracted ?? '?'}/${check.expected ?? '?'} points — SP manquants : ${(check.missing_sp || []).join(', ') || '?'}. Vérifiez le PDF avant de publier.</p>`;
  const mergeNote = preview.is_new
    ? `Nouvelle séance — ${preview.n_points} point(s).`
    : `Séance déjà présente (${preview.existing_points} point(s) existants) — fusion/enrichissement avec les ${preview.n_points} point(s) extraits.`;
  return `<section class="admin-preview">
    <h4>Aperçu — séance du ${escapeHtml(preview.date || '?')}</h4>
    <p class="yc-note">${escapeHtml(mergeNote)}</p>
    ${completeness}
    <ul class="admin-preview-titles">${titles}${more}</ul>
    ${renderProgressBox()}
    <p class="admin-login-error" id="adminPublishError" role="alert"></p>
    <div class="admin-preview-actions">
      <button type="button" class="drill-reset" data-click="cancelAdminExtract">Annuler</button>
      <button type="button" class="ask-btn" id="adminPublishBtn" data-click="confirmAdminPublish">Confirmer et publier</button>
    </div>
  </section>`;
}

// Pas de pré-remplissage d'URL ici (contrairement au PV) : la nomenclature
// réelle des liens PDF sous 1030.be/fr/questions-ecrites n'est pas encore
// confirmée — mieux vaut un champ vide qu'une URL devinée et fausse.
function renderQeUploadForm() {
  const banner = lastQePublishResult
    ? `<p class="admin-check-ok">✅ Question écrite ${escapeHtml(lastQePublishResult.id)} de ${escapeHtml(lastQePublishResult.auteur)} publiée. Commit ${escapeHtml((lastQePublishResult.commit_sha || '').slice(0, 7))}.</p>`
    : '';
  return `${banner}<section class="admin-upload">
    <h4>Intégrer une nouvelle question écrite</h4>
    <p class="yc-note">Upload le PDF officiel (voir 1030.be/fr/questions-ecrites) : extraction automatique (Claude), aperçu avant publication. Canal indépendant des PV — jamais liée à une question orale.</p>
    <form id="qeUploadForm" class="admin-login-form">
      <label for="qePdfFile">Fichier PDF de la question écrite</label>
      <input type="file" id="qePdfFile" accept="application/pdf" required>
      <label for="qeSourceUrl">URL de la question sur 1030.be (optionnel)</label>
      <input type="url" id="qeSourceUrl" placeholder="https://www.1030.be/...">
      ${renderProgressBox('qe')}
      <p class="admin-login-error" id="qeUploadError" role="alert"></p>
      <button type="submit" class="ask-btn admin-login-submit" id="qeUploadSubmit">Extraire</button>
    </form>
  </section>`;
}

function renderQePreview() {
  const { question, preview } = pendingQuestion;
  const mergeNote = preview.is_new
    ? `Nouvelle question — n°${preview.numero}/${preview.annee}.`
    : `Question déjà publiée (n°${preview.numero}/${preview.annee}) — cette extraction la remplacera.`;
  // Le champ réponse est nullable côté extraction (voir normalize_question) :
  // une question tout juste posée peut ne pas encore avoir de réponse
  // publiée — signalé ici plutôt qu'affiché comme une omission.
  const reponse = question.reponse
    ? `<p class="yc-note"><strong>Réponse extraite :</strong> ${escapeHtml(question.reponse)}</p>`
    : `<p class="admin-check-warn">⚠️ Aucune réponse trouvée dans ce PDF — probablement une question encore sans réponse publiée.</p>`;
  return `<section class="admin-preview">
    <h4>Aperçu — question écrite n°${escapeHtml(String(question.numero ?? '?'))}/${escapeHtml(String(question.annee ?? '?'))}</h4>
    <p class="yc-note">${escapeHtml(mergeNote)}</p>
    <p class="yc-note"><strong>Auteur·e :</strong> ${escapeHtml(question.auteur || '?')} · <strong>Date :</strong> ${escapeHtml(question.date || '?')}</p>
    <p class="yc-note"><strong>Titre :</strong> ${escapeHtml(question.titre || '?')}</p>
    <p class="yc-note"><strong>Question :</strong> ${escapeHtml(question.question || '?')}</p>
    ${reponse}
    ${renderProgressBox('qe')}
    <p class="admin-login-error" id="qePublishError" role="alert"></p>
    <div class="admin-preview-actions">
      <button type="button" class="drill-reset" data-click="cancelQeExtract">Annuler</button>
      <button type="button" class="ask-btn" id="qePublishBtn" data-click="confirmQePublish">Confirmer et publier</button>
    </div>
  </section>`;
}

export function openAdminLogin() {
  document.getElementById('adminLoginOverlay').classList.add('open');
  document.getElementById('adminLoginError').textContent = '';
  document.getElementById('adminLoginUsername').focus();
}
export function closeAdminLogin() {
  document.getElementById('adminLoginOverlay').classList.remove('open');
}
// Clic sur le fond assombri (pas sur le panneau) → ferme, comme Options.
export function initAdminLoginOverlay() {
  const overlay = document.getElementById('adminLoginOverlay');
  if (overlay) overlay.addEventListener('click', e => { if (e.target === overlay) closeAdminLogin(); });
}

export async function submitAdminLogin(ev) {
  ev.preventDefault();
  const usernameEl = document.getElementById('adminLoginUsername');
  const passwordEl = document.getElementById('adminLoginPassword');
  const errBox = document.getElementById('adminLoginError');
  const btn = document.getElementById('adminLoginSubmit');
  const username = usernameEl.value.trim();
  const password = passwordEl.value;
  if (!username || !password) return;
  errBox.textContent = '';
  btn.disabled = true;
  try {
    const res = await fetch(API_URL + '/admin/login', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    if (!res.ok) {
      // 401 reste générique ("identifiants invalides") à dessein — mais
      // toute autre erreur (config serveur incomplète, rate limit, etc.)
      // affiche le vrai détail renvoyé par le backend, sinon un problème de
      // configuration (ex. ADMIN_JWT_SECRET manquant) serait indiscernable
      // d'un mot de passe erroné.
      let detail = 'Identifiants invalides.';
      if (res.status !== 401) {
        try {
          detail = (await res.json()).detail || `Erreur ${res.status}.`;
        } catch {
          detail = `Erreur ${res.status}.`;
        }
      }
      errBox.textContent = detail;
      return;
    }
    const data = await res.json();
    adminUsername = data.username;
    passwordEl.value = '';
    closeAdminLogin();
    updateAdminUI();
  } catch {
    errBox.textContent = 'Connexion impossible — réessayez.';
  } finally {
    btn.disabled = false;
  }
}

// Étape 1/2 : upload du PDF → démarre l'extraction en tâche de fond côté
// serveur (job_id), puis SONDE le statut plutôt que d'attendre une seule
// requête bloquante — une extraction dense (plusieurs appels Claude
// séquentiels côté serveur) dépasse le délai que tolère le proxy Render,
// qui coupe alors la connexion (observé en prod : se manifeste comme une
// erreur CORS trompeuse, pas un vrai souci de politique CORS).
export async function submitAdminExtract(ev) {
  ev.preventDefault();
  const fileEl = document.getElementById('adminPdfFile');
  const sourceUrlEl = document.getElementById('adminSourceUrl');
  const errBox = document.getElementById('adminUploadError');
  const btn = document.getElementById('adminUploadSubmit');
  const file = fileEl.files[0];
  if (!file) return;
  errBox.textContent = '';
  btn.disabled = true;
  try {
    const formData = new FormData();
    formData.append('file', file);
    const res = await fetch(API_URL + '/admin/seances/extract', {
      method: 'POST',
      credentials: 'include',
      body: formData,   // pas de Content-Type manuel : fetch pose le bon boundary multipart
    });
    if (!res.ok) {
      errBox.textContent = await _errorDetail(res);
      return;
    }
    const { job_id } = await res.json();
    const data = await _pollJob(`/admin/seances/extract/${job_id}`, btn, 'Extraction');
    pendingSeance = data;
    pendingSourceUrl = sourceUrlEl.value.trim() || null;
    lastPublishResult = null;
    renderAdminPanel();
  } catch (err) {
    errBox.textContent = err.message || 'Extraction impossible — réessayez.';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Extraire';
  }
}

// Même flux (étape 1/2) pour une question écrite — voir
// services/questions_ecrites_integration.py côté backend.
export async function submitQeExtract(ev) {
  ev.preventDefault();
  const fileEl = document.getElementById('qePdfFile');
  const sourceUrlEl = document.getElementById('qeSourceUrl');
  const errBox = document.getElementById('qeUploadError');
  const btn = document.getElementById('qeUploadSubmit');
  const file = fileEl.files[0];
  if (!file) return;
  errBox.textContent = '';
  btn.disabled = true;
  try {
    const formData = new FormData();
    formData.append('file', file);
    const res = await fetch(API_URL + '/admin/questions-ecrites/extract', {
      method: 'POST',
      credentials: 'include',
      body: formData,
    });
    if (!res.ok) {
      errBox.textContent = await _errorDetail(res);
      return;
    }
    const { job_id } = await res.json();
    const data = await _pollJob(`/admin/questions-ecrites/extract/${job_id}`, btn, 'Extraction', 'qe');
    pendingQuestion = data;
    pendingQeSourceUrl = sourceUrlEl.value.trim() || null;
    lastQePublishResult = null;
    renderAdminPanel();
  } catch (err) {
    errBox.textContent = err.message || 'Extraction impossible — réessayez.';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Extraire';
  }
}

// Sonde `${API_URL}${path}` toutes les 3s jusqu'à statut "done" (retourné
// tel quel) ou une erreur (HTTPException du backend → message réel, pas
// générique). Plafonné à 10 min pour ne jamais boucler indéfiniment si
// quelque chose reste bloqué côté serveur. Affiche l'avancement RÉEL renvoyé
// par le backend (pages/chunks/points pour l'extraction, étape commit/
// indexation pour la publication — voir services/pv_integration.py) plutôt
// que de simuler une progression avec le temps écoulé.
async function _pollJob(path, btn, label, prefix = 'admin') {
  const started = Date.now();
  const MAX_MS = 10 * 60 * 1000;
  while (Date.now() - started < MAX_MS) {
    await new Promise(r => setTimeout(r, 3000));
    const res = await fetch(API_URL + path, { credentials: 'include' });
    if (!res.ok) throw new Error(await _errorDetail(res));
    const data = await res.json();
    if (data.status === 'pending') {
      const elapsed = Math.round((Date.now() - started) / 1000);
      if (btn) btn.textContent = `${label} en cours (${elapsed}s)…`;
      updateProgressBox(data.progress, prefix);
      continue;
    }
    updateProgressBox(null, prefix);   // masque la barre : le job est terminé (done/error géré par l'appelant)
    return data;
  }
  throw new Error(`${label} : délai dépassé (10 min) — réessayez plus tard.`);
}

// `progress` vient tel quel de process_pdf/publish_seance (voir
// pipeline/pv_extraction_pipeline.py et services/pv_integration.py) — pas de
// donnée simulée. Les étapes "commit"/"indexing" (publication) n'ont pas de
// granularité fine (une seule opération atomique pour le commit, peu de
// points à indexer pour une séance) : pourcentage approximatif par étape,
// juste pour donner un signal visuel de progression, pas une mesure exacte.
function updateProgressBox(progress, prefix = 'admin') {
  const box = document.getElementById(`${prefix}ProgressBox`);
  const fill = document.getElementById(`${prefix}ProgressFill`);
  const label = document.getElementById(`${prefix}ProgressLabel`);
  if (!box) return;
  if (!progress) { box.style.display = 'none'; return; }
  box.style.display = 'block';
  let pct = 0;
  let text = '';
  if (progress.stage === 'extraction' && progress.total_chunks) {
    pct = Math.round((progress.chunk / progress.total_chunks) * 100);
    text = `Bloc de pages ${progress.chunk}/${progress.total_chunks} — ${progress.points_so_far} point(s) trouvé(s) jusqu'ici (${progress.pages} pages au total)`;
  } else if (progress.stage === 'extraction') {
    // Question écrite : pas de découpage en blocs (document court, un seul
    // appel Claude — voir pipeline/questions_ecrites_extraction_pipeline.py).
    pct = 40;
    text = 'Extraction du texte…';
  } else if (progress.stage === 'verification') {
    pct = 90;
    // points_so_far absent pour une question écrite (voir process_pdf de
    // questions_ecrites_extraction_pipeline.py — verification y suit
    // directement l'appel Claude, pas d'audit de complétude par points).
    text = progress.points_so_far != null
      ? `Vérification de la complétude — ${progress.points_so_far} point(s)`
      : 'Vérification…';
  } else if (progress.stage === 'commit') {
    pct = 50;
    // n_points absent pour une question écrite (une seule question, pas de
    // liste de points — voir publish_question côté backend).
    text = progress.n_points
      ? `Fusion et commit sur GitHub — ${progress.n_points} point(s)…`
      : 'Fusion et commit sur GitHub…';
  } else if (progress.stage === 'indexing') {
    pct = 90;
    text = `Indexation dans Pinecone — ${progress.n_points} point(s)…`;
  }
  if (fill) fill.style.width = `${pct}%`;
  if (label) label.textContent = text;
}

export function cancelAdminExtract() {
  pendingSeance = null;
  pendingSourceUrl = null;
  renderAdminPanel();
}

export function cancelQeExtract() {
  pendingQuestion = null;
  pendingQeSourceUrl = null;
  renderAdminPanel();
}

// Étape 2/2 : publie EXACTEMENT ce que l'extraction a renvoyé (voir
// services/pv_integration.py côté backend) — fusion réelle, commit GitHub,
// réindexation Pinecone. Même sondage qu'à l'extraction : un commit sur un
// fichier de plusieurs Mo + un upsert Pinecone (retry pouvant attendre
// jusqu'à 65s sur un 429 Pinecone) dépassent aussi facilement le délai
// toléré par le proxy Render.
export async function confirmAdminPublish() {
  const errBox = document.getElementById('adminPublishError');
  const btn = document.getElementById('adminPublishBtn');
  if (!pendingSeance) return;
  errBox.textContent = '';
  btn.disabled = true;
  try {
    const res = await fetch(API_URL + '/admin/seances/publish', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ seance: pendingSeance.seance, source_url: pendingSourceUrl }),
    });
    if (!res.ok) {
      errBox.textContent = await _errorDetail(res);
      return;
    }
    const { job_id } = await res.json();
    lastPublishResult = await _pollJob(`/admin/seances/publish/${job_id}`, btn, 'Publication');
    pendingSeance = null;
    pendingSourceUrl = null;
    renderAdminPanel();
  } catch (err) {
    errBox.textContent = err.message || 'Publication impossible — réessayez.';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Confirmer et publier';
  }
}

// Étape 2/2 pour une question écrite : publie EXACTEMENT ce que l'extraction
// a renvoyé, commit GitHub — pas d'indexation Pinecone (voir docstring de
// services/questions_ecrites_integration.py).
export async function confirmQePublish() {
  const errBox = document.getElementById('qePublishError');
  const btn = document.getElementById('qePublishBtn');
  if (!pendingQuestion) return;
  errBox.textContent = '';
  btn.disabled = true;
  try {
    const res = await fetch(API_URL + '/admin/questions-ecrites/publish', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: pendingQuestion.question, source_url: pendingQeSourceUrl }),
    });
    if (!res.ok) {
      errBox.textContent = await _errorDetail(res);
      return;
    }
    const { job_id } = await res.json();
    lastQePublishResult = await _pollJob(`/admin/questions-ecrites/publish/${job_id}`, btn, 'Publication', 'qe');
    pendingQuestion = null;
    pendingQeSourceUrl = null;
    renderAdminPanel();
  } catch (err) {
    errBox.textContent = err.message || 'Publication impossible — réessayez.';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Confirmer et publier';
  }
}

async function _errorDetail(res) {
  try {
    return (await res.json()).detail || `Erreur ${res.status}.`;
  } catch {
    return `Erreur ${res.status}.`;
  }
}

export async function adminLogout() {
  try {
    await fetch(API_URL + '/admin/logout', { method: 'POST', credentials: 'include' });
  } catch { /* la session cookie expirera de toute façon côté serveur */ }
  adminUsername = null;
  pendingSeance = null;
  pendingSourceUrl = null;
  lastPublishResult = null;
  pendingQuestion = null;
  pendingQeSourceUrl = null;
  lastQePublishResult = null;
  updateAdminUI();
}
