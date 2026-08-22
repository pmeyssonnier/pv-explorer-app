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
  box.innerHTML = head + (pendingSeance ? renderPreview() : renderUploadForm());
  const form = document.getElementById('adminUploadForm');
  if (form) form.addEventListener('submit', submitAdminExtract);
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
      <label for="adminSourceUrl">URL du PV sur 1030.be (optionnel)</label>
      <input type="url" id="adminSourceUrl" placeholder="https://www.1030.be/...">
      <p class="admin-login-error" id="adminUploadError" role="alert"></p>
      <button type="submit" class="ask-btn admin-login-submit" id="adminUploadSubmit">Extraire</button>
    </form>
  </section>`;
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
    <p class="admin-login-error" id="adminPublishError" role="alert"></p>
    <div class="admin-preview-actions">
      <button type="button" class="drill-reset" data-click="cancelAdminExtract">Annuler</button>
      <button type="button" class="ask-btn" id="adminPublishBtn" data-click="confirmAdminPublish">Confirmer et publier</button>
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

// Sonde `${API_URL}${path}` toutes les 3s jusqu'à statut "done" (retourné
// tel quel) ou une erreur (HTTPException du backend → message réel, pas
// générique). Plafonné à 10 min pour ne jamais boucler indéfiniment si
// quelque chose reste bloqué côté serveur.
async function _pollJob(path, btn, label) {
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
      continue;
    }
    return data;
  }
  throw new Error(`${label} : délai dépassé (10 min) — réessayez plus tard.`);
}

export function cancelAdminExtract() {
  pendingSeance = null;
  pendingSourceUrl = null;
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
  updateAdminUI();
}
