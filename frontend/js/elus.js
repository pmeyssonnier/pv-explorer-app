// ── INTERVENTIONS PAR ÉLU·E (agrégation structurée via /elus, /elu/{key}) ──
// La recherche sémantique du chat est sensible à la formulation et non
// exhaustive ; cette vue liste TOUTES les interventions d'une personne.
import { API_URL } from './config.js';
import {
  escapeHtml, formatDate, TYPE_ACTOR_LABEL, TYPE_COUNT_LABEL, renderThemeTags,
  renderTypeBadge, renderPvPdfLink, renderVideoLink, renderPersonLine, renderReponseDetails,
} from './utils.js';
import { doShare, shareBaseUrl } from './share.js';

let elusData = null;        // liste complète [{key,nom,role,depose,repond}]
let elusLoaded = false;
let pendingEluKey = null;   // élu·e à présélectionner depuis un lien partagé (?elu=)
let currentEluData = null;  // dernière fiche /elu/{key} chargée (pour reFiltrer sans refetch)
let eluYearFilter = 'all';  // année sélectionnée dans le filtre ("all" = toutes)
let eluTypeFilter = 'all';  // type d'intervention sélectionné ("all" = tous) — depose uniquement
let eluThemeFilter = 'all'; // thématique sélectionnée ("all" = toutes) — depose ET repond
let eluRoleFilter = 'all';  // rôle sélectionné ("all" = tous) — filtre le menu élu·e, voir puces
let eluSelectedKey = null;  // clé actuellement chargée

// Présélection appliquée depuis un lien partagé (?tab=elus&elu=…), voir handleDeepLink.
export function setPendingEluKey(key) { pendingEluKey = key; }

export async function loadElus() {
  if (elusLoaded) return;
  const input = document.getElementById('eluSelect');
  if (!input) return;
  try {
    const res = await fetch(API_URL + '/elus');
    if (!res.ok) throw new Error('Erreur ' + res.status);
    elusData = (await res.json()).elus || [];
    elusLoaded = true;
    renderEluRoleChips();
    populateElus();
  } catch (err) {
    input.disabled = true;
    input.placeholder = 'Indisponible';
  }
}

// Puces de rôle (Conseiller·ère / Collège) — même libellé/widget que les puces de
// l'onglet Séances (.elu-chip/.elu-chip-active) : reclique sur la puce déjà
// active → "Tous les rôles" ; comptent le nombre d'élu·e·s ayant ce rôle
// (indépendant du filtre lui-même, contrairement aux puces à facettes de
// l'onglet Séances — ici un seul filtre, pas de comptage croisé nécessaire).
function eluRoleChip(role, label, count) {
  const active = eluRoleFilter === role ? ' elu-chip-active' : '';
  return `<button type="button" class="elu-chip${active}" data-click="onEluRoleChipClick" data-arg="${role}">${escapeHtml(label)} (${count})</button>`;
}
function renderEluRoleChips() {
  const box = document.getElementById('eluRoleChips');
  if (!box || !elusData) return;
  const nConseiller = elusData.filter(e => e.role === 'conseiller').length;
  const nCollege = elusData.filter(e => e.role === 'college').length;
  box.innerHTML = eluRoleChip('conseiller', 'Conseiller·ère', nConseiller)
    + eluRoleChip('college', 'Collège', nCollege);
}
export function onEluRoleChipClick(role) {
  eluRoleFilter = eluRoleFilter === role ? 'all' : role;
  renderEluRoleChips();
  populateElus();
}

// (Re)remplit le menu déroulant selon le filtre de rôle courant.
export function populateElus() {
  const select = document.getElementById('eluSelect');
  if (!select || !elusData) return;
  const prevKey = eluSelectedKey;
  // Tri par nom de famille (la clé backend gère déjà les particules, ex.
  // « de Fierlant » → clé « fierlant »), pas par nombre d'interventions.
  const list = elusData.filter(e => eluRoleFilter === 'all' || e.role === eluRoleFilter)
    .slice()
    .sort((a, b) => a.key.localeCompare(b.key, 'fr'));
  select.innerHTML = list.map(e => `<option value="${escapeHtml(e.key)}">${escapeHtml(e.nom)}</option>`).join('');
  // Présélection : lien partagé (?elu=) prioritaire, sinon on conserve la
  // sélection courante si elle reste visible, sinon la 1re entrée.
  let nextKey = null;
  if (pendingEluKey && list.some(e => e.key === pendingEluKey)) {
    nextKey = pendingEluKey;
    pendingEluKey = null;
  } else if (list.some(e => e.key === prevKey)) {
    nextKey = prevKey;
  } else if (list.length) {
    nextKey = list[0].key;
  }
  selectElu(nextKey, list);
}

// Applique une sélection par clé et charge la fiche. `list` (optionnel)
// évite de re-chercher dans elusData quand l'appelant l'a déjà filtrée/triée.
function selectElu(key, list) {
  eluSelectedKey = key;
  const select = document.getElementById('eluSelect');
  const entry = key ? (list || elusData || []).find(e => e.key === key) : null;
  if (entry) {
    if (select) select.value = key;
    loadElu(key);
  } else {
    if (select) select.value = '';
    document.getElementById('eluResult').innerHTML = '';
    document.getElementById('eluYear').hidden = true;
  }
}

export function onEluSelectChange(sel) {
  if (sel.value) selectElu(sel.value);
}

// Partage : lien profond vers l'onglet Par élu·e, sur la fiche sélectionnée.
export function shareElu(btn) {
  const key = eluSelectedKey;
  const entry = key ? (elusData || []).find(e => e.key === key) : null;
  const nom = entry ? entry.nom : '';
  const url = key ? `${shareBaseUrl()}?tab=elus&elu=${encodeURIComponent(key)}` : `${shareBaseUrl()}?tab=elus`;
  const text = nom
    ? `Interventions de ${nom} au Conseil communal de Schaerbeek`
    : 'Interventions par élu·e — Conseil communal de Schaerbeek';
  doShare('PV Explorer — Interventions par élu·e', text, url, btn);
}

async function loadElu(key) {
  const box = document.getElementById('eluResult');
  if (!key) {
    box.innerHTML = '';
    document.getElementById('eluYear').hidden = true;
    return;
  }
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

export function onEluYearChange() {
  eluYearFilter = document.getElementById('eluYear').value;
  if (currentEluData) renderElu(currentEluData);
}

function filterByYear(items, year) {
  if (year === 'all') return items;
  return items.filter(it => (it.date || '').slice(0, 4) === year);
}

// Thématiques distinctes (dépôts + réponses de l'année sélectionnée), triées
// alphabétiquement — indépendant du filtre de type, pour que la liste des
// thématiques disponibles ne se réduise pas selon le type déjà choisi.
function eluThemes(deposeForYear, repondForYear) {
  const s = new Set();
  deposeForYear.forEach(it => (it.thematiques || []).forEach(t => s.add(t)));
  repondForYear.forEach(it => (it.thematiques || []).forEach(t => s.add(t)));
  return [...s].sort((a, b) => a.localeCompare(b, 'fr'));
}

// (Re)remplit le filtre de thématique pour la fiche courante ; conserve la
// thématique sélectionnée si elle existe encore, sinon "Toutes".
function populateEluThemeSelect(themes) {
  const sel = document.getElementById('eluTheme');
  if (!sel) return;
  if (!themes.length) { sel.innerHTML = ''; sel.hidden = true; eluThemeFilter = 'all'; return; }
  if (!themes.includes(eluThemeFilter)) eluThemeFilter = 'all';
  sel.hidden = false;
  sel.innerHTML = '<option value="all">Toutes les thématiques</option>' +
    themes.map(t => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`).join('');
  sel.value = eluThemeFilter;
}

export function onEluThemeChange() {
  eluThemeFilter = document.getElementById('eluTheme').value;
  if (currentEluData) renderElu(currentEluData);
}

function filterByTheme(items, theme) {
  if (theme === 'all') return items;
  return items.filter(it => (it.thematiques || []).includes(theme));
}

// Ordre d'affichage fixe (pas l'ordre d'apparition dans les données) : même
// ordre que TYPE_BADGE côté utils.js, pour une liste toujours dans le même
// sens quel que soit l'élu·e sélectionné·e.
const TYPE_FILTER_ORDER = ['Question orale', 'Demande', 'Motion', 'Débat filmé', 'Question écrite'];

function filterByType(items, typeLabel) {
  if (typeLabel === 'all') return items;
  return items.filter(it => it.type_label === typeLabel);
}

// Puce cliquable par type présent — seul filtre de type de l'onglet (plus de
// menu déroulant redondant, voir onEluChipClick). '' si aucune intervention
// de ce type cette année (jamais de puce à 0).
function eluTypeChip(typeLabel, count) {
  if (!count) return '';
  const active = eluTypeFilter === typeLabel ? ' elu-chip-active' : '';
  const text = TYPE_COUNT_LABEL[typeLabel](count);
  return `<button type="button" class="elu-chip${active}" data-click="onEluChipClick" data-arg="${escapeHtml(typeLabel)}">${escapeHtml(text)}</button>`;
}

// Reclique sur la puce déjà active → bascule vers "Tous les types" ; sinon
// sélectionne ce type.
export function onEluChipClick(typeLabel) {
  eluTypeFilter = eluTypeFilter === typeLabel ? 'all' : typeLabel;
  if (currentEluData) renderElu(currentEluData);
}

function eluDeposeRow(it, nom) {
  const badge = renderTypeBadge(it.type_label);
  const sp = it.sp ? `<span class="elu-sp">SP ${it.sp}</span>` : '';
  // Lien : deep-link « ▶ Voir le débat » pour un point filmé (débat filmé
  // pur, ou point PV fusionné avec son chapitre vidéo — video_precise), sinon
  // PDF du PV et, si la séance a été filmée sans chapitre précis pour CE
  // point, un lien léger « ▶ vidéo » (généraliste, début de séance).
  let links = '';
  if (it.type === 'video' && it.url) {
    links = renderVideoLink(it.url, '▶ Voir le débat', 'Voir le débat sur YouTube (au bon moment)');
  } else if (it.type === 'question_ecrite') {
    links += renderPvPdfLink(it.url, 'Voir la question (PDF)', 'Ouvrir la question écrite (PDF) sur 1030.be');
  } else {
    links += renderPvPdfLink(it.url);
    if (it.video_url) {
      const label = it.video_precise ? '▶ Voir le débat' : '▶ vidéo';
      const title = it.video_precise
        ? 'Voir le débat sur YouTube (au bon moment)'
        : 'Voir la séance filmée sur YouTube (début de séance, pas de moment précis identifié pour ce point)';
      links += renderVideoLink(it.video_url, label, title);
    }
  }
  const actorLabel = TYPE_ACTOR_LABEL[it.type_label] || 'Auteur·e';
  // Question écrite cosignée : les cosignataires sont affichés côte à côte
  // avec le nom de la fiche consultée, quelle que soit celle-ci — jamais
  // masqués simplement parce qu'on a filtré sur l'un d'eux (voir registry.py,
  // co_auteurs résolu pour chaque auteur·e depuis les autres signataires).
  const auteurs = it.co_auteurs ? `${nom} et ${it.co_auteurs}` : nom;
  const demandeur = renderPersonLine('elu-demandeur', actorLabel, auteurs);
  const rep = renderPersonLine('elu-rep', 'Répondant·e', it.repondant);
  const tags = renderThemeTags(it.thematiques);
  const reponse = it.type === 'question_ecrite' ? renderReponseDetails(it.reponse) : '';
  return `<div class="elu-item">
    <div class="elu-date">${formatDate(it.date)}</div>
    <div class="elu-body">
      ${badge}${sp}
      <div class="elu-titre">${escapeHtml(it.titre)}</div>
      ${demandeur}
      ${rep}
      ${tags}
      ${reponse}
      ${links ? `<div class="elu-links">${links}</div>` : ''}
    </div>
  </div>`;
}

function eluRepondRow(it, nom) {
  const sp = it.sp ? `<span class="elu-sp">SP ${it.sp}</span>` : '';
  const link = renderPvPdfLink(it.url);
  const demandeur = renderPersonLine('elu-demandeur', 'Demandé par', it.demandeur);
  const rep = renderPersonLine('elu-rep', 'Répondant·e', nom);
  const tags = renderThemeTags(it.thematiques);
  return `<div class="elu-item">
    <div class="elu-date">${formatDate(it.date)}</div>
    <div class="elu-body">
      ${sp}
      <div class="elu-titre">${escapeHtml(it.titre)}</div>
      ${demandeur}
      ${rep}
      ${tags}
      ${link ? `<div class="elu-links">${link}</div>` : ''}
    </div>
  </div>`;
}

// Historique de mandats lisible (voir /elu/{key} "mandats", services.people.
// mandats côté backend) : « Conseiller·ère communal·e (2012–2019, 2024–présent)
// · Échevin·e (2025–présent) » — un même élu·e peut avoir occupé plusieurs
// rôles successifs, jamais résumé en un seul. null si absent des données
// déclaratives (repli sur le libellé simple "role", voir renderElu).
const MANDAT_LABEL = {
  conseiller: 'Conseiller·ère communal·e',
  echevin: 'Échevin·e',
  bourgmestre: 'Bourgmestre',
};
const MANDAT_ORDER = ['conseiller', 'echevin', 'bourgmestre'];
function formatMandats(mandats) {
  if (!mandats) return null;
  const parts = MANDAT_ORDER
    .filter(k => (mandats[k] || []).length)
    .map(k => {
      const ranges = mandats[k].map(r => `${r.debut}–${r.fin ?? 'présent'}`).join(', ');
      return `${MANDAT_LABEL[k]} (${ranges})`;
    });
  return parts.length ? parts.join(' · ') : null;
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
  // Base pour les puces/compteurs : filtrée par ANNÉE seulement, pas par type
  // — sinon sélectionner un type ferait disparaître les puces des autres
  // types (chacune doit rester visible/cliquable pour changer de filtre).
  const deposeForYear = filterByYear(d.depose || [], eluYearFilter);
  const repondForYear = filterByYear(d.repond || [], eluYearFilter);
  // Un type resté sélectionné en changeant d'élu·e/d'année peut ne plus être
  // présent (sa puce ne s'affiche alors plus, voir eluTypeChip) : retombe sur
  // "Tous les types" plutôt que de filtrer silencieusement sans puce active.
  if (eluTypeFilter !== 'all' && !deposeForYear.some(it => it.type_label === eluTypeFilter)) {
    eluTypeFilter = 'all';
  }
  // Thématiques disponibles pour l'année courante — indépendantes du type et
  // de la thématique déjà choisis, sinon le menu se viderait au filtrage.
  populateEluThemeSelect(eluThemes(deposeForYear, repondForYear));
  const depose = filterByType(filterByTheme(deposeForYear, eluThemeFilter), eluTypeFilter);
  const repond = filterByTheme(repondForYear, eluThemeFilter);
  const roleLabel = formatMandats(d.mandats) || (d.role === 'college'
    ? 'Collège (échevin·e / bourgmestre)'
    : 'Conseiller·ère');
  const chips = TYPE_FILTER_ORDER
    .map(t => eluTypeChip(t, deposeForYear.filter(it => it.type_label === t).length))
    .join('');

  let html = `<div class="elu-head">
    <div class="elu-name">${escapeHtml(d.nom)}</div>
    <span class="elu-role elu-role-${d.role}">${roleLabel}</span>
  </div>`;

  if (deposeForYear.length) {
    html += `<div class="elu-summary"><strong>${depose.length}</strong> intervention${depose.length > 1 ? 's' : ''} déposée${depose.length > 1 ? 's' : ''}</div>`;
    if (chips) html += `<div class="elu-chips">${chips}</div>`;
  }
  if (depose.length) {
    html += `<div class="elu-list">${groupByYear(depose, it => eluDeposeRow(it, d.nom))}</div>`;
  }

  if (repond.length) {
    html += `<details class="elu-repond"${depose.length ? '' : ' open'}>
      <summary><strong>${repond.length}</strong> réponse${repond.length > 1 ? 's' : ''} en séance <span class="elu-repond-hint">(activité de Collège)</span></summary>
      <div class="elu-list">${groupByYear(repond, it => eluRepondRow(it, d.nom))}</div>
    </details>`;
  }

  if (!depose.length && !repond.length) {
    const filters = [];
    if (eluTypeFilter !== 'all') filters.push(`de type « ${eluTypeFilter} »`);
    if (eluThemeFilter !== 'all') filters.push(`sur la thématique « ${eluThemeFilter} »`);
    if (eluYearFilter !== 'all') filters.push(`en ${eluYearFilter}`);
    html += filters.length
      ? `<div class="trend-empty">Aucune intervention ${filters.join(' ')}.</div>`
      : `<div class="trend-empty">Aucune intervention identifiée.</div>`;
  }

  html += `<p class="elu-note">Agrégation déterministe depuis les procès-verbaux (2012–2026) et le chapitrage des séances filmées. Attribution : question orale → 1er intervenant ; demande → auteur du titre ou 1er intervenant ; motion → auteur nommé dans le titre. Liste indicative, non exhaustive des prises de parole en débat.</p>`;

  box.innerHTML = html;
}
