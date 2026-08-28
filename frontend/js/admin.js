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
import { createCombobox } from './combobox.js';

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
// Recherche d'une personne — même composant que le sélecteur d'élu·e de
// l'onglet « Par élu·e » (voir combobox.js/elus.js initEluCombo) : choisir
// une option ouvre directement sa fiche d'édition (onMandatEditClick), comme
// la sélection y ouvre la fiche d'activité. Recréé à chaque rendu du panneau
// (mêmes éléments DOM détruits/reconstruits — voir renderAdminPanel), donc
// resynchronisé via mandatComboSelected plutôt que par un état interne.
let mandatCombo = null;
let mandatComboSelected = null;
// Puces de rôle (voir onMandatRoleChipClick) : filtrent le TABLEAU, ensemble
// cumulable comme eluRoleSel côté « Par élu·e » — vide = tous les rôles. Un
// rôle correspond ici à « a un jour occupé ce mandat » (champ renseigné),
// pas au rôle actuel (contrairement aux puces de la fiche Par élu·e, qui
// filtrent par rôle À LA DATE d'une action).
let mandatRoleSel = new Set();
// Puce de législature (voir onMandatLegislatureChipClick) : sélection UNIQUE
// (comme seanceTypeFilter), 'all' = aucun filtre. Une législature belge dure
// 6 ans, élection en octobre, nouveau conseil installé en novembre (voir
// mandatLegislatures ci-dessous).
let mandatLegislature = 'all';
// Tri des colonnes (voir onMandatSortClick) — 'nom'/'statut' triés comme du
// texte, les 3 colonnes de mandat par ANNÉE DE DÉBUT la plus ancienne
// (mandatSortKey), une entrée sans plage pour la colonne triée finissant
// toujours en dernier quel que soit le sens.
let mandatSort = { col: 'nom', dir: 'asc' };
// Entrée en cours d'édition (copie modifiable, voir onMandatEditClick) —
// null si aucun formulaire affiché. {nom: '', ...} vide = nouvelle personne
// (voir onMandatNewClick) : nom_original n'est envoyé que si non vide, pour
// distinguer création et modification côté backend (voir save_mandat).
let editingMandat = null;
// Bascule la fiche d'édition vers un écran de confirmation avant suppression
// (voir onMandatDeleteClick) — jamais de fenêtre confirm() native, hors
// style du reste du panneau (même logique que l'aperçu avant publication
// d'un PV : une action qui modifie le dépôt se confirme dans l'UI, jamais
// en un clic).
let mandatDeleteConfirm = false;

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
  if (adminSubTab === 'mandats' && mandatsData !== null) initMandatCombo();
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
  mandatComboSelected = null;
  mandatDeleteConfirm = false;
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
    <thead>${renderMandatsTableHead()}</thead>
    <tbody>${renderMandatsRows()}</tbody>
  </table></div>`;
  return `<section class="admin-mandats">
    <h4>Mandats élu·e·s</h4>
    <p class="yc-note">Rôle par plage de dates (voir aussi la fiche d'un·e élu·e dans l'onglet « Par élu·e »). Format attendu pour chaque champ : « AAAA-AAAA » (mandat clos) ou « AAAA-présent » (en cours), plusieurs plages séparées par une virgule. Colonnes triables (cliquer l'en-tête) ; puces pour retrouver un rôle ou une législature.</p>
    <div class="admin-mandats-toolbar">
      <div class="elu-combo" id="mandatSearchCombo">
        <svg class="icon elu-combo-icon" aria-hidden="true"><use href="#ico-search"/></svg>
        <input type="text" id="mandatSearch" class="elu-select elu-combo-input"
               role="combobox" aria-expanded="false" aria-controls="mandatSearchOptions"
               aria-autocomplete="list" aria-label="Rechercher une personne"
               placeholder="Rechercher une personne…" autocomplete="off"
               autocapitalize="off" spellcheck="false" enterkeyhint="search"
               data-form-type="other" data-lpignore="true" data-1p-ignore>
        <button type="button" class="elu-combo-clear" id="mandatSearchClear"
                aria-label="Effacer la recherche" title="Effacer la recherche" hidden>✕</button>
        <ul class="elu-combo-list" id="mandatSearchOptions" role="listbox" aria-label="Personnes" hidden></ul>
      </div>
      <button type="button" class="ask-btn" data-click="onMandatNewClick">+ Ajouter une personne</button>
    </div>
    <p class="sr-only" id="mandatSearchStatus" role="status" aria-live="polite"></p>
    ${renderMandatRoleChips()}
    ${renderMandatLegislatureChips()}
    ${table}
    ${editingMandat ? renderMandatEditForm() : ''}
  </section>`;
}

// ── Rôle « au sens du Collège » et législatures ─────────────────────────────
const MANDAT_ROLE_LABEL = { conseiller: 'Conseiller·ère', echevin: 'Échevin·e', bourgmestre: 'Bourgmestre' };
const MANDAT_ROLE_FIELD = { conseiller: 'conseiller_communal', echevin: 'echevin', bourgmestre: 'bourgmestre' };
// Ordre = précédence de classement (voir personRoleInPeriod), PAS l'ordre
// d'affichage des puces (rendu dans cet ordre aussi, du plus courant au plus
// rare, ce qui coïncide ici).
const MANDAT_ROLE_ORDER = ['conseiller', 'echevin', 'bourgmestre'];

// Fourchettes de 6 ans, élection en octobre, conseil installé en novembre
// (calendrier électoral communal belge — voir la discussion avec l'admin sur
// les dates précises des échevin·e·s Nimal, Decoux, Eraly, Haddioui, Bilge,
// Querton plus tôt cette session). Générées dynamiquement (aucune liste à
// maintenir à la main d'une législature à l'autre) depuis 1976 (repère
// électoral) jusqu'à l'année courante. `end` est EXCLU (voir
// rangesOverlapPeriod) : la législature « 2018-2024 » couvre les années
// 2018 à 2023, 2024 étant déjà l'année d'installation de la suivante.
const LEGISLATURE_ANCHOR = 1976;

function mandatLegislatures() {
  const nowYear = new Date().getFullYear();
  const periods = [];
  for (let start = LEGISLATURE_ANCHOR; start <= nowYear; start += 6) {
    const end = start + 6;
    periods.push({ key: `${start}-${end}`, start, end, actuel: start <= nowYear && nowYear < end });
  }
  return periods;
}

function legislatureLabel(p) {
  return p.actuel ? `nov. ${p.start} – aujourd'hui (actuel)` : `nov. ${p.start} – oct. ${p.end}`;
}

// Période de référence pour classer les rôles (voir personRoleInPeriod) :
// la législature sélectionnée, sinon l'année courante (rôle « à ce jour ») —
// même demi-intervalle [début, fin[ que mandatLegislatures.
function activeMandatPeriod() {
  if (mandatLegislature !== 'all') {
    const p = mandatLegislatures().find(x => x.key === mandatLegislature);
    if (p) return p;
  }
  const y = new Date().getFullYear();
  return { start: y, end: y + 1 };
}

// Chevauchement plage de mandat / période, en DEMI-INTERVALLES [début, fin[
// (granularité du fichier source — aucune date exacte de jour/mois n'y est
// stockée, voir services/people/mandats.py côté backend) : une plage close
// sur l'année charnière (ex. « …-2024 ») appartient à la législature qui
// FINIT en 2024, jamais à celle qui commence cette même année (élu·e jusqu'à
// l'élection d'octobre, remplacé·e par le conseil installé en novembre) —
// et symétriquement, une plage qui COMMENCE en 2024 n'appartient qu'à la
// nouvelle législature, jamais à celle qui vient de s'achever.
function rangesOverlapPeriod(raw, period) {
  if (!raw) return false;
  return raw.split(',').some(part => {
    const m = /^\s*(\d{4})\s*-\s*(\d{4}|pr[ée]sent)\s*(?:\(.*\))?\s*$/i.exec(part);
    if (!m) return false;
    const start = parseInt(m[1], 10);
    const end = /pr[ée]sent/i.test(m[2]) ? null : parseInt(m[2], 10);
    return start < period.end && (end === null || end > period.start);
  });
}

// Rôle EFFECTIF d'une personne sur une période donnée : le Collège
// (bourgmestre puis échevin·e) l'emporte sur conseiller·ère — un échevin·e
// est conseiller·ère par défaut (son mandat de conseiller·ère la/le couvre
// toujours) mais son rôle affiché est le plus haut des deux, comme
// role_at() côté backend (services/people/mandats.py, même précédence).
// null si la personne n'a aucun mandat couvrant cette période.
function personRoleInPeriod(e, period) {
  if (rangesOverlapPeriod(e.bourgmestre, period)) return 'bourgmestre';
  if (rangesOverlapPeriod(e.echevin, period)) return 'echevin';
  if (rangesOverlapPeriod(e.conseiller_communal, period)) return 'conseiller';
  return null;
}

// ── Puces de rôle : partition (chaque personne comptée dans SON rôle le
// plus haut sur la période active, jamais deux fois) — voir mandatRoleSel.
// Compte parmi les personnes déjà retenues par la puce de législature
// (même convention que eluRoleCounts/seanceThemeFilterOptions : les compteurs
// reflètent l'AUTRE filtre, jamais le tableau final déjà réduit par celui-ci).
function renderMandatRoleChips() {
  const period = activeMandatPeriod();
  const base = (mandatsData || []).filter(matchesMandatLegislature);
  const chips = MANDAT_ROLE_ORDER.map(role => {
    const n = base.filter(e => personRoleInPeriod(e, period) === role).length;
    const on = mandatRoleSel.has(role);
    return `<button type="button" class="elu-chip${on ? ' elu-chip-active' : ''}" aria-pressed="${on}"
      data-click="onMandatRoleChipClick" data-arg="${role}">
      <strong>${escapeHtml(MANDAT_ROLE_LABEL[role])}</strong> · ${n} personne${n > 1 ? 's' : ''}</button>`;
  }).join('');
  return `<div class="elu-chips" id="mandatRoleChips" aria-label="Filtrer par rôle">${chips}</div>`;
}

export function onMandatRoleChipClick(role) {
  if (mandatRoleSel.has(role)) mandatRoleSel.delete(role); else mandatRoleSel.add(role);
  renderAdminPanel();
}

// Rôle le plus haut sur la période active (législature sélectionnée, ou
// aujourd'hui) — PAS « a un jour occupé ce rôle » : un·e ancien·ne échevin·e
// devenu·e simple conseiller·ère aujourd'hui n'apparaît plus dans « Échevin·e »
// une fois qu'aucune législature passée n'est sélectionnée.
function matchesMandatRole(e) {
  if (!mandatRoleSel.size) return true;
  const role = personRoleInPeriod(e, activeMandatPeriod());
  return role !== null && mandatRoleSel.has(role);
}

// Puces de législature : compte, pour CHAQUE législature candidate (pas
// seulement celle sélectionnée), qui était conseiller·ère (donc membre du
// conseil, quel que soit son rôle exact) PENDANT CETTE période précise —
// et, si des puces de rôle sont cochées, dont le rôle sur CETTE MÊME période
// correspond (jamais celui d'une autre période, y compris la sélectionnée).
function renderMandatLegislatureChips() {
  const chips = mandatLegislatures().map(p => {
    const n = (mandatsData || []).filter(e => {
      if (!rangesOverlapPeriod(e.conseiller_communal, p)) return false;
      if (!mandatRoleSel.size) return true;
      const role = personRoleInPeriod(e, p);
      return role !== null && mandatRoleSel.has(role);
    }).length;
    const on = mandatLegislature === p.key;
    return `<button type="button" class="elu-chip${on ? ' elu-chip-active' : ''}" aria-pressed="${on}"
      data-click="onMandatLegislatureChipClick" data-arg="${p.key}">
      ${escapeHtml(legislatureLabel(p))} · ${n} personne${n > 1 ? 's' : ''}</button>`;
  }).join('');
  return `<div class="elu-chips" id="mandatLegislatureChips" aria-label="Filtrer par législature">${chips}</div>`;
}

export function onMandatLegislatureChipClick(key) {
  mandatLegislature = mandatLegislature === key ? 'all' : key;
  renderAdminPanel();
}

// Chevauchement sur le mandat de CONSEILLER·ÈRE uniquement, jamais échevin/
// bourgmestre : en droit communal belge, on doit d'abord être conseiller·ère
// pour accéder au Collège — un mandat échevinal/de bourgmestre est donc
// toujours couvert par le mandat de conseiller·ère de la même personne
// (sauf trou dans les données, voir Isabelle Durant 2006-2009 — signalé, pas
// corrigé ici faute de source). Vérifier aussi échevin/bourgmestre compterait
// deux fois la même législature pour une personne déjà comptée via son
// mandat de conseiller·ère.
function matchesMandatLegislature(e) {
  if (mandatLegislature === 'all') return true;
  const period = mandatLegislatures().find(p => p.key === mandatLegislature);
  if (!period) return true;
  return rangesOverlapPeriod(e.conseiller_communal, period);
}

function filteredMandats() {
  return (mandatsData || []).filter(e => matchesMandatRole(e) && matchesMandatLegislature(e)).sort(mandatComparator);
}

// ── Colonnes triables ────────────────────────────────────────────────────
const MANDAT_COLUMNS = [
  ['nom', 'Nom'],
  ['conseiller_communal', 'Conseiller·ère'],
  ['echevin', 'Échevin·e'],
  ['bourgmestre', 'Bourgmestre'],
  ['statut', 'Statut'],
];

// Clé de tri : texte pour nom/statut, ANNÉE DE DÉBUT LA PLUS ANCIENNE pour
// une colonne de mandat (« 2012-2018, 2024-présent » -> 2012) — null si le
// champ est vide, toujours relégué en fin de tri quel qu'en soit le sens.
function mandatSortKey(e, col) {
  if (col === 'nom' || col === 'statut') return (e[col] || '').toLowerCase() || null;
  const raw = e[col];
  if (!raw) return null;
  const starts = raw.split(',')
    .map(part => /^\s*(\d{4})\s*-/.exec(part))
    .filter(Boolean)
    .map(m => parseInt(m[1], 10));
  return starts.length ? Math.min(...starts) : null;
}

function mandatComparator(a, b) {
  const { col, dir } = mandatSort;
  const ka = mandatSortKey(a, col);
  const kb = mandatSortKey(b, col);
  if (ka === null && kb === null) return 0;
  if (ka === null) return 1;
  if (kb === null) return -1;
  const cmp = typeof ka === 'string' ? ka.localeCompare(kb, 'fr') : ka - kb;
  return dir === 'asc' ? cmp : -cmp;
}

export function onMandatSortClick(col) {
  if (mandatSort.col === col) mandatSort.dir = mandatSort.dir === 'asc' ? 'desc' : 'asc';
  else mandatSort = { col, dir: 'asc' };
  renderAdminPanel();
}

function renderMandatsTableHead() {
  const cells = MANDAT_COLUMNS.map(([col, label]) => {
    const active = mandatSort.col === col;
    const arrow = active ? (mandatSort.dir === 'asc' ? ' ▲' : ' ▼') : '';
    return `<th><button type="button" class="admin-mandats-sort${active ? ' active' : ''}"
      data-click="onMandatSortClick" data-arg="${col}">${escapeHtml(label)}${arrow}</button></th>`;
  }).join('');
  return `<tr>${cells}<th></th></tr>`;
}

// Pastille de rôle affichée à côté d'un nom — regroupe bourgmestre/échevin·e
// sous « Collège » (voir personRoleInPeriod, même précédence), 'Ancien·ne'
// quand la personne n'a aucun mandat couvrant la période donnée.
function mandatRoleBadge(e, period) {
  const role = personRoleInPeriod(e, period);
  if (role === 'bourgmestre' || role === 'echevin') return { cls: 'college', texte: 'Collège' };
  if (role === 'conseiller') return { cls: 'conseiller', texte: 'Conseil' };
  return { cls: 'ancien', texte: 'Ancien·ne' };
}

function renderMandatsRows() {
  const period = activeMandatPeriod();
  const rows = filteredMandats().map(e => {
    const badge = mandatRoleBadge(e, period);
    return `<tr>
      <td>${escapeHtml(e.nom || '')} <span class="elu-opt-role elu-opt-role-${badge.cls}">${badge.texte}</span></td>
      <td>${escapeHtml(e.conseiller_communal || '—')}</td>
      <td>${escapeHtml(e.echevin || '—')}</td>
      <td>${escapeHtml(e.bourgmestre || '—')}</td>
      <td>${escapeHtml(e.statut || '—')}</td>
      <td><button type="button" class="drill-reset" data-click="onMandatEditClick" data-arg="${escapeHtml(e.nom || '')}">Modifier</button></td>
    </tr>`;
  }).join('');
  return rows || `<tr><td colspan="6">Aucun résultat.</td></tr>`;
}

// ── Recherche : même combobox que le sélecteur d'élu·e (voir combobox.js) —
// une option choisie ouvre directement sa fiche d'édition, comme la
// sélection y ouvre la fiche d'activité (pas un filtre du tableau, qui reste
// géré par les puces de rôle ci-dessus, indépendamment). Rôle toujours À CE
// JOUR (pas la période active) : ce champ sert à retrouver une personne,
// indépendamment de la législature éventuellement sélectionnée pour filtrer
// le tableau.
function mandatOptionHtml(e, { id, active, selected, label }) {
  const y = new Date().getFullYear();
  const badge = mandatRoleBadge(e, { start: y, end: y + 1 });
  return `<li class="elu-opt${active ? ' elu-opt-active' : ''}" role="option" id="${id}"
      data-key="${escapeHtml(e.nom)}" aria-selected="${selected}">
    <span class="elu-opt-name">${label}</span>
    <span class="elu-opt-meta"><span class="elu-opt-role elu-opt-role-${badge.cls}">${badge.texte}</span></span>
  </li>`;
}

function initMandatCombo() {
  mandatCombo = createCombobox({
    input: document.getElementById('mandatSearch'),
    list: document.getElementById('mandatSearchOptions'),
    clear: document.getElementById('mandatSearchClear'),
    status: document.getElementById('mandatSearchStatus'),
    idPrefix: 'mandat-opt',
    itemKey: e => e.nom,
    itemLabel: e => e.nom,
    renderItem: mandatOptionHtml,
    emptyText: q => `Aucune personne ne correspond à « ${q} ».`,
    statusText: n => (n ? `${n} personne${n > 1 ? 's' : ''} — utilisez les flèches puis Entrée` : 'Aucun résultat'),
    placeholder: n => (n ? `Rechercher parmi ${n} personnes…` : 'Rechercher une personne…'),
    onSelect: key => { if (key) onMandatEditClick(key); },
  });
  mandatCombo.setItems((mandatsData || []).slice().sort((a, b) => (a.nom || '').localeCompare(b.nom || '', 'fr')));
  mandatCombo.setSelected(mandatComboSelected);
}

function renderMandatEditForm() {
  const e = editingMandat;
  const isNew = !e.nom;
  if (mandatDeleteConfirm) return renderMandatDeleteConfirm(e);
  const deleteBtn = isNew ? '' :
    `<button type="button" class="admin-mandat-danger" data-click="onMandatDeleteClick">Supprimer</button>`;
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
        ${deleteBtn}
        <button type="submit" class="ask-btn admin-login-submit" id="mandatFormSubmit">Enregistrer</button>
      </div>
    </form>
  </section>`;
}

// Écran de confirmation avant suppression — jamais un simple clic : la
// suppression commite dans le dépôt (voir confirmMandatDelete), même degré
// de prudence que la publication d'un PV (aperçu, confirmation explicite).
function renderMandatDeleteConfirm(e) {
  return `<section class="admin-mandat-form-wrap">
    <h4>Supprimer — ${escapeHtml(e.nom)}</h4>
    <p class="admin-check-warn">⚠️ Êtes-vous sûr·e de vouloir supprimer le mandat de <strong>${escapeHtml(e.nom)}</strong> ? Cette action est irréversible.</p>
    <p class="admin-login-error" id="mandatDeleteError" role="alert"></p>
    <div class="admin-preview-actions">
      <button type="button" class="drill-reset" data-click="cancelMandatDelete">Annuler</button>
      <button type="button" class="admin-mandat-danger" id="mandatDeleteConfirmBtn" data-click="confirmMandatDelete">Oui, supprimer</button>
    </div>
  </section>`;
}

export function onMandatEditClick(nom) {
  const entry = (mandatsData || []).find(e => e.nom === nom);
  if (!entry) return;
  editingMandat = { ...entry };
  mandatComboSelected = nom;
  mandatDeleteConfirm = false;
  renderAdminPanel();
  document.getElementById('mandatNom')?.focus();
}

export function onMandatNewClick() {
  editingMandat = { nom: '', conseiller_communal: '', echevin: '', bourgmestre: '', statut: '' };
  mandatComboSelected = null;
  mandatDeleteConfirm = false;
  renderAdminPanel();
  document.getElementById('mandatNom')?.focus();
}

export function cancelMandatEdit() {
  editingMandat = null;
  mandatComboSelected = null;
  mandatDeleteConfirm = false;
  renderAdminPanel();
}

export function onMandatDeleteClick() {
  mandatDeleteConfirm = true;
  renderAdminPanel();
}

export function cancelMandatDelete() {
  mandatDeleteConfirm = false;
  renderAdminPanel();
}

export async function confirmMandatDelete() {
  if (!editingMandat || !editingMandat.nom) return;
  const errBox = document.getElementById('mandatDeleteError');
  const btn = document.getElementById('mandatDeleteConfirmBtn');
  errBox.textContent = '';
  btn.disabled = true;
  try {
    const res = await fetch(API_URL + '/admin/mandats?nom=' + encodeURIComponent(editingMandat.nom), {
      method: 'DELETE',
      credentials: 'include',
    });
    if (!res.ok) {
      errBox.textContent = await _errorDetail(res);
      return;
    }
    const nom = editingMandat.nom;
    mandatsData = (mandatsData || []).filter(e => e.nom !== nom);
    editingMandat = null;
    mandatComboSelected = null;
    mandatDeleteConfirm = false;
    renderAdminPanel();
  } catch (err) {
    errBox.textContent = err.message || 'Suppression impossible — réessayez.';
  } finally {
    btn.disabled = false;
  }
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
    mandatComboSelected = null;
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
  mandatComboSelected = null;
  mandatRoleSel = new Set();
  mandatLegislature = 'all';
  mandatSort = { col: 'nom', dir: 'asc' };
  editingMandat = null;
  mandatDeleteConfirm = false;
  updateAdminUI();
}
