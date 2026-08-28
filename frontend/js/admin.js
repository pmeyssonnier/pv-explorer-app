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

// Sous-onglet actif du panneau Admin : 'seances' | 'qe' | 'mandats' — voir
// renderAdminSubTabs. Indépendant des onglets principaux (#tab-*/#panel-*,
// voir app.js switchTab) : ce panneau se rend entièrement lui-même.
let adminSubTab = 'seances';
// Cache de GET /admin/mandats (voir services/people/mandats.py côté
// backend) — null tant que non chargé (chargement paresseux, seulement à la
// première visite du sous-onglet Mandats). Liste brute d'objets
// {nom, conseiller_communal, echevin, bourgmestre, statut}.
let mandatsData = null;
let mandatsFilter = '';
// Entrée en cours d'édition (copie modifiable, voir onMandatEditClick) —
// null si aucun formulaire affiché. {nom: '', ...} vide = nouvelle personne
// (voir onMandatNewClick) : nom_original n'est envoyé que si non vide, pour
// distinguer création et modification côté backend (voir save_mandat).
let editingMandat = null;

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
    <button type="button" class="drill-reset" data-click="adminLogout">Se déconnecter</button>
    ${renderAdminSubTabs()}`;
  let body;
  if (adminSubTab === 'mandats') {
    body = renderMandatsSection();
  } else if (adminSubTab === 'qe') {
    body = pendingQuestion ? renderQePreview() : renderQeUploadForm();
  } else {
    body = pendingSeance ? renderPreview() : renderUploadForm();
  }
  box.innerHTML = head + body;
  const form = document.getElementById('adminUploadForm');
  if (form) form.addEventListener('submit', submitAdminExtract);
  const fileEl = document.getElementById('adminPdfFile');
  if (fileEl) fileEl.addEventListener('change', prefillSourceUrl);
  const qeForm = document.getElementById('qeUploadForm');
  if (qeForm) qeForm.addEventListener('submit', submitQeExtract);
  const mandatFilterEl = document.getElementById('mandatFilter');
  if (mandatFilterEl) mandatFilterEl.addEventListener('input', onMandatFilterInput);
  const mandatForm = document.getElementById('mandatEditForm');
  if (mandatForm) mandatForm.addEventListener('submit', submitMandat);
}

function renderAdminSubTabs() {
  const tabs = [
    ['seances', 'Séances'],
    ['qe', 'Questions écrites'],
    ['mandats', 'Mandats élu·e·s'],
  ];
  return `<div class="admin-subtabs">${tabs.map(([key, label]) =>
    `<button type="button" class="admin-subtab${adminSubTab === key ? ' active' : ''}" data-click="switchAdminSubTab" data-arg="${key}">${escapeHtml(label)}</button>`
  ).join('')}</div>`;
}

export function switchAdminSubTab(tab) {
  if (adminSubTab === tab) return;
  adminSubTab = tab;
  editingMandat = null;
  if (tab === 'mandats' && mandatsData === null) { loadMandats(); return; }
  renderAdminPanel();
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

// ── Sous-onglet Mandats élu·e·s : voir/corriger les plages de dates
// (conseiller·ère communal·e/échevin·e/bourgmestre) déclarées dans
// backend/elus_mandats.json (voir services/people/mandats.py) — remplace
// l'édition manuelle du JSON par commit direct. Chargement paresseux (une
// seule fois par session admin, mandatsData sert de cache) ; l'écriture
// (submitMandat) met à jour ce cache localement plutôt que de tout
// recharger, pour ne pas perdre le filtre en cours.
async function loadMandats() {
  try {
    const res = await fetch(API_URL + '/admin/mandats', { credentials: 'include' });
    if (!res.ok) throw new Error(await _errorDetail(res));
    mandatsData = (await res.json()).mandats || [];
  } catch {
    mandatsData = [];
  }
  renderAdminPanel();
}

function renderMandatsSection() {
  if (mandatsData === null) {
    return `<section class="admin-mandats"><h4>Mandats élu·e·s</h4><p class="yc-note">Chargement…</p></section>`;
  }
  const table = `<div class="md-tablewrap"><table class="md-table admin-mandats-table">
    <thead><tr><th>Nom</th><th>Conseiller·ère</th><th>Échevin·e</th><th>Bourgmestre</th><th>Statut</th><th></th></tr></thead>
    <tbody id="mandatsTableBody">${renderMandatsRows()}</tbody>
  </table></div>`;
  return `<section class="admin-mandats">
    <h4>Mandats élu·e·s</h4>
    <p class="yc-note">Rôle par plage de dates (voir aussi la fiche d'un·e élu·e dans l'onglet « Par élu·e »). Format attendu pour chaque champ : « AAAA-AAAA » (mandat clos) ou « AAAA-présent » (en cours), plusieurs plages séparées par une virgule.</p>
    <div class="admin-mandats-toolbar">
      <input type="search" id="mandatFilter" placeholder="Filtrer par nom…" value="${escapeHtml(mandatsFilter)}">
      <button type="button" class="ask-btn" data-click="onMandatNewClick">+ Ajouter une personne</button>
    </div>
    ${table}
    ${editingMandat ? renderMandatEditForm() : ''}
  </section>`;
}

// Rendu isolé des lignes du tableau (voir onMandatFilterInput) : ne remplace
// QUE le <tbody>, jamais tout le panneau — sinon retaper dans #mandatFilter
// perdrait le focus à chaque frappe (innerHTML détruit et recrée l'input).
function renderMandatsRows() {
  const filter = mandatsFilter.trim().toLowerCase();
  const rows = (mandatsData || [])
    .filter(e => !filter || (e.nom || '').toLowerCase().includes(filter))
    .map(e => `<tr>
      <td>${escapeHtml(e.nom || '')}</td>
      <td>${escapeHtml(e.conseiller_communal || '—')}</td>
      <td>${escapeHtml(e.echevin || '—')}</td>
      <td>${escapeHtml(e.bourgmestre || '—')}</td>
      <td>${escapeHtml(e.statut || '—')}</td>
      <td><button type="button" class="drill-reset" data-click="onMandatEditClick" data-arg="${escapeHtml(e.nom || '')}">Modifier</button></td>
    </tr>`).join('');
  return rows || `<tr><td colspan="6">Aucun résultat.</td></tr>`;
}

function onMandatFilterInput(ev) {
  mandatsFilter = ev.target.value;
  const tbody = document.getElementById('mandatsTableBody');
  if (tbody) tbody.innerHTML = renderMandatsRows();
}

function renderMandatEditForm() {
  const e = editingMandat;
  const isNew = !e.nom;
  return `<section class="admin-mandat-form-wrap">
    <h4>${isNew ? 'Nouvelle personne' : `Modifier — ${escapeHtml(e.nom)}`}</h4>
    <form id="mandatEditForm" class="admin-login-form">
      <label for="mandatNom">Nom complet</label>
      <input type="text" id="mandatNom" value="${escapeHtml(e.nom || '')}" required>
      <label for="mandatConseiller">Conseiller·ère communal·e</label>
      <input type="text" id="mandatConseiller" value="${escapeHtml(e.conseiller_communal || '')}" placeholder="ex. 2012-présent">
      <label for="mandatEchevin">Échevin·e</label>
      <input type="text" id="mandatEchevin" value="${escapeHtml(e.echevin || '')}" placeholder="ex. 2018-2024">
      <label for="mandatBourgmestre">Bourgmestre</label>
      <input type="text" id="mandatBourgmestre" value="${escapeHtml(e.bourgmestre || '')}" placeholder="ex. 2001-présent">
      <label for="mandatStatut">Statut (libellé affiché sur la fiche)</label>
      <input type="text" id="mandatStatut" value="${escapeHtml(e.statut || '')}" placeholder="ex. Échevine">
      <p class="admin-login-error" id="mandatFormError" role="alert"></p>
      <div class="admin-preview-actions">
        <button type="button" class="drill-reset" data-click="cancelMandatEdit">Annuler</button>
        <button type="submit" class="ask-btn admin-login-submit" id="mandatFormSubmit">Enregistrer</button>
      </div>
    </form>
  </section>`;
}

export function onMandatEditClick(nom) {
  const entry = (mandatsData || []).find(e => e.nom === nom);
  if (!entry) return;
  editingMandat = { ...entry };
  renderAdminPanel();
  document.getElementById('mandatNom')?.focus();
}

export function onMandatNewClick() {
  editingMandat = { nom: '', conseiller_communal: '', echevin: '', bourgmestre: '', statut: '' };
  renderAdminPanel();
  document.getElementById('mandatNom')?.focus();
}

export function cancelMandatEdit() {
  editingMandat = null;
  renderAdminPanel();
}

// nom_original : nom AVANT modification (voir save_mandat côté backend) —
// permet de renommer une entrée sans en créer une seconde à côté de
// l'ancienne. Absent (undefined→null) pour une nouvelle personne.
export async function submitMandat(ev) {
  ev.preventDefault();
  const nomEl = document.getElementById('mandatNom');
  const conseillerEl = document.getElementById('mandatConseiller');
  const echevinEl = document.getElementById('mandatEchevin');
  const bourgmestreEl = document.getElementById('mandatBourgmestre');
  const statutEl = document.getElementById('mandatStatut');
  const errBox = document.getElementById('mandatFormError');
  const btn = document.getElementById('mandatFormSubmit');
  const nom = nomEl.value.trim();
  if (!nom) return;
  errBox.textContent = '';
  btn.disabled = true;
  try {
    const res = await fetch(API_URL + '/admin/mandats', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        nom,
        conseiller_communal: conseillerEl.value.trim() || null,
        echevin: echevinEl.value.trim() || null,
        bourgmestre: bourgmestreEl.value.trim() || null,
        statut: statutEl.value.trim() || null,
        nom_original: (editingMandat && editingMandat.nom) || null,
      }),
    });
    if (!res.ok) {
      errBox.textContent = await _errorDetail(res);
      return;
    }
    const { mandat } = await res.json();
    const idx = (mandatsData || []).findIndex(e => e.nom === (editingMandat && editingMandat.nom));
    if (idx >= 0) mandatsData[idx] = mandat;
    else (mandatsData || (mandatsData = [])).push(mandat);
    mandatsData.sort((a, b) => (a.nom || '').localeCompare(b.nom || '', 'fr'));
    editingMandat = null;
    renderAdminPanel();
  } catch (err) {
    errBox.textContent = err.message || 'Enregistrement impossible — réessayez.';
  } finally {
    btn.disabled = false;
  }
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
  adminSubTab = 'seances';
  mandatsData = null;
  mandatsFilter = '';
  editingMandat = null;
  updateAdminUI();
}
