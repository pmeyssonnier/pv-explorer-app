// ── INTERVENTIONS PAR ÉLU·E (agrégation structurée via /elus, /elu/{key}) ──
// La recherche sémantique du chat est sensible à la formulation et non
// exhaustive ; cette vue liste TOUTES les interventions d'une personne.
import { API_URL } from './config.js';
import {
  escapeHtml, formatDate, TYPE_ACTOR_LABEL, renderThemeTags,
  renderTypeBadge, renderPvPdfLink, renderVideoLink, renderPersonLine,
} from './utils.js';
import { doShare, shareBaseUrl } from './share.js';

let elusData = null;        // liste complète [{key,nom,role,depose,repond}]
let elusLoaded = false;
let pendingEluKey = null;   // élu·e à présélectionner depuis un lien partagé (?elu=)
let currentEluData = null;  // dernière fiche /elu/{key} chargée (pour reFiltrer sans refetch)
let eluYearFilter = 'all';  // année sélectionnée dans le filtre ("all" = toutes)
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
    populateElus();
  } catch (err) {
    input.disabled = true;
    input.placeholder = 'Indisponible';
  }
}

// (Re)remplit le menu déroulant selon le filtre de rôle courant.
export function populateElus() {
  const select = document.getElementById('eluSelect');
  const role = (document.getElementById('eluRole') || {}).value || 'all';
  if (!select || !elusData) return;
  const prevKey = eluSelectedKey;
  // Tri par nom de famille (la clé backend gère déjà les particules, ex.
  // « de Fierlant » → clé « fierlant »), pas par nombre d'interventions.
  const list = elusData.filter(e => role === 'all' || e.role === role)
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

export function onEluYearChange() {
  eluYearFilter = document.getElementById('eluYear').value;
  if (currentEluData) renderElu(currentEluData);
}

function filterByYear(items, year) {
  if (year === 'all') return items;
  return items.filter(it => (it.date || '').slice(0, 4) === year);
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
  const demandeur = renderPersonLine('elu-demandeur', actorLabel, nom);
  const rep = renderPersonLine('elu-rep', 'Répondant·e', it.repondant);
  const tags = renderThemeTags(it.thematiques);
  return `<div class="elu-item">
    <div class="elu-date">${formatDate(it.date)}</div>
    <div class="elu-body">
      ${badge}${sp}
      <div class="elu-titre">${escapeHtml(it.titre)}</div>
      ${demandeur}
      ${rep}
      ${tags}
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
