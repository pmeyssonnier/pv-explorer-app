// ── SÉANCES (vue complémentaire à « Par élu·e » : par PV, tous les points,
// pas seulement ceux d'une personne) ──
import { API_URL } from './config.js';
import { escapeHtml, formatDate, TYPE_BADGE, TYPE_ACTOR_LABEL, fmtMontant } from './utils.js';
import { doShare, shareBaseUrl } from './share.js';

let seancesData = null;       // liste complète [{date,n_points,url,video_url}]
let seancesLoaded = false;
let pendingSeanceDate = null; // séance à présélectionner depuis un lien partagé (?seance=)
let currentSeanceDetail = null;
// Filtres de la séance affichée (réinitialisés à chaque nouvelle séance
// chargée) : 'all', un type_label ("Motion", "Question orale", …), ou
// 'reporte' (pseudo-type — un point reporté peut être de n'importe quel
// type réel, filtré séparément des types).
let seanceTypeFilter = 'all';
let seanceThemeFilter = 'all';

// Présélection appliquée depuis un lien partagé (?tab=seances&seance=…), voir handleDeepLink.
export function setPendingSeanceDate(date) { pendingSeanceDate = date; }

export async function loadSeances() {
  if (seancesLoaded) return;
  const yearSel = document.getElementById('seanceYear');
  if (!yearSel) return;
  try {
    const res = await fetch(API_URL + '/seances');
    if (!res.ok) throw new Error('Erreur ' + res.status);
    seancesData = (await res.json()).seances || [];
    seancesLoaded = true;
    const years = [...new Set(seancesData.map(s => s.date.slice(0, 4)))].sort((a, b) => b.localeCompare(a));
    yearSel.innerHTML = years.map(y => `<option value="${y}">${y}</option>`).join('');
    if (pendingSeanceDate) {
      const y = pendingSeanceDate.slice(0, 4);
      if (years.includes(y)) yearSel.value = y;
    }
    renderSeanceYearList();
    if (pendingSeanceDate && seancesData.some(s => s.date === pendingSeanceDate)) {
      loadSeance(pendingSeanceDate);
    }
    pendingSeanceDate = null;
  } catch (err) {
    yearSel.innerHTML = '<option value="">Indisponible</option>';
  }
}

// Liste cliquable des séances de l'année sélectionnée (la plus récente en premier).
export function renderSeanceYearList() {
  const listEl = document.getElementById('seanceYearList');
  const yearSel = document.getElementById('seanceYear');
  if (!listEl || !seancesData || !yearSel.value) return;
  const list = seancesData.filter(s => s.date.startsWith(yearSel.value));
  listEl.innerHTML = list.map(s => {
    const videoIcon = s.video_url ? '<svg class="icon" aria-hidden="true"><use href="#ico-video"/></svg>' : '';
    const active = s.date === (currentSeanceDetail && currentSeanceDetail.date) ? ' seance-row-active' : '';
    return `<div class="seance-row-wrap">
      <button type="button" class="seance-row${active}" data-click="loadSeance" data-arg="${escapeHtml(s.date)}">
        <span class="seance-row-date">${formatDate(s.date)}</span>
        <span class="seance-row-meta">${s.n_points} point${s.n_points > 1 ? 's' : ''}${videoIcon}</span>
      </button>
      <button type="button" class="seance-row-share" data-click="shareSeanceDate" data-arg="${escapeHtml(s.date)}" aria-label="Partager le lien vers cette séance" title="Partager le lien vers cette séance">
        <svg class="icon" aria-hidden="true"><use href="#ico-share"/></svg>
      </button>
    </div>`;
  }).join('');
}

function seancePointRow(it) {
  const cls = TYPE_BADGE[it.type_label] || 'b-d';
  const badge = `<span class="elu-badge ${cls}">${escapeHtml(it.type_label)}</span>`;
  // Point reporté à une séance ultérieure : jamais débattu ce jour-là, donc
  // pas de lien vidéo générique à proposer (il n'y a rien à y voir sur ce
  // point précis).
  const reporteBadge = it.reporte ? `<span class="elu-badge b-report">Reporté</span>` : '';
  const sp = it.sp ? `<span class="elu-sp">SP ${it.sp}</span>` : '';
  // Pas de lien "PV (PDF)" par point : c'est le même PDF de séance pour tous
  // les points (pas d'ancre par SP), déjà proposé une fois au-dessus de la
  // liste (voir renderSeance) — le répéter à chaque point suggérerait à tort
  // un accès direct à ce point précis dans le PDF. Même raisonnement pour la
  // vidéo générique (video_precise=false) : lien vers le DÉBUT de la séance,
  // pas ce point précis — déjà proposé une fois au-dessus (vidéo complète).
  // Seul un lien vidéo réellement spécifique à ce point (deep-link précis,
  // ou chapitre vidéo autonome) est affiché ici.
  let links = '';
  if (it.type === 'video' && it.url) {
    links = `<a class="elu-link elu-link-video" href="${it.url}" target="_blank" rel="noopener noreferrer" title="Voir le débat sur YouTube (au bon moment)"><svg class="icon" aria-hidden="true"><use href="#ico-video"/></svg>▶ Voir le débat</a>`;
  } else if (it.video_url && it.video_precise && !it.reporte) {
    links = `<a class="elu-link elu-link-video" href="${it.video_url}" target="_blank" rel="noopener noreferrer" title="Voir le débat sur YouTube (au bon moment)"><svg class="icon" aria-hidden="true"><use href="#ico-video"/></svg>▶ Voir le débat</a>`;
  }
  const actorLabel = TYPE_ACTOR_LABEL[it.type_label] || 'Auteur·e';
  const demandeur = it.demandeur ? `<div class="elu-demandeur">${escapeHtml(actorLabel)} : ${escapeHtml(it.demandeur)}</div>` : '';
  const rep = it.repondant ? `<div class="elu-rep">Répondant·e : ${escapeHtml(it.repondant)}</div>` : '';
  // Déjà signalé via le badge « Reporté » ci-dessus, pas besoin de répéter.
  const decision = (it.decision && !it.reporte) ? `<div class="elu-decision">${escapeHtml(it.decision)}</div>` : '';
  const montant = (it.montant_eur !== null && it.montant_eur !== undefined)
    ? `<div class="elu-montant">Montant engagé : ${fmtMontant(it.montant_eur)}</div>` : '';
  const tags = (it.thematiques && it.thematiques.length)
    ? `<div class="elu-tags">${it.thematiques.map(t => `<span class="elu-tag">${escapeHtml(t)}</span>`).join('')}</div>` : '';
  return `<div class="elu-item">
    <div class="elu-body">
      ${badge}${reporteBadge}${sp}
      <div class="elu-titre">${escapeHtml(it.titre)}</div>
      ${demandeur}
      ${rep}
      ${decision}
      ${montant}
      ${tags}
      ${links ? `<div class="elu-links">${links}</div>` : ''}
    </div>
  </div>`;
}

export async function loadSeance(date) {
  const box = document.getElementById('seanceResult');
  box.innerHTML = '<div class="loading"><span>Chargement</span><span class="dots"><span></span><span></span><span></span></span></div>';
  try {
    const res = await fetch(API_URL + '/seance/' + encodeURIComponent(date));
    if (!res.ok) throw new Error('Erreur ' + res.status);
    renderSeance(await res.json());
    renderSeanceYearList();  // met en évidence la séance active dans la liste
  } catch (err) {
    box.innerHTML = `<div class="error-box">Impossible de charger cette séance. ${escapeHtml(err.message)}</div>`;
  }
}

// Types réellement présents dans la séance (jamais une liste figée : ex.
// "Débat filmé" n'apparaît que si un chapitre vidéo autonome existe) +
// "Reporté" si au moins un point l'est. Ordre stable, indépendant du tri
// d'apparition dans la liste.
const _TYPE_FILTER_ORDER = ['Point', 'Motion', 'Question orale', 'Demande', 'Débat filmé'];
function seanceTypeFilterOptions(points) {
  const present = new Set(points.map(p => p.type_label));
  const types = _TYPE_FILTER_ORDER.filter(t => present.has(t));
  const opts = types.map(t => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`);
  if (points.some(p => p.reporte)) opts.push('<option value="reporte">Reporté</option>');
  return opts.join('');
}
// Thématiques réellement présentes dans la séance, triées.
function seanceThemeFilterOptions(points) {
  const themes = [...new Set(points.flatMap(p => p.thematiques || []))].sort((a, b) => a.localeCompare(b, 'fr'));
  return themes.map(t => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`).join('');
}
function pointMatchesFilters(p) {
  const typeOk = seanceTypeFilter === 'all'
    || (seanceTypeFilter === 'reporte' ? p.reporte : p.type_label === seanceTypeFilter);
  const themeOk = seanceThemeFilter === 'all' || (p.thematiques || []).includes(seanceThemeFilter);
  return typeOk && themeOk;
}

// (Re)rend uniquement la liste des points selon les filtres courants — pas
// besoin de reconstruire l'en-tête/les liens de la séance à chaque changement.
function renderSeancePoints() {
  const list = document.getElementById('seancePointsList');
  const count = document.getElementById('seanceFilterCount');
  if (!list || !currentSeanceDetail) return;
  const filtered = currentSeanceDetail.points.filter(pointMatchesFilters);
  list.innerHTML = filtered.length
    ? filtered.map(seancePointRow).join('')
    : '<p class="trend-empty">Aucun point ne correspond à ces filtres.</p>';
  if (count) {
    const filtering = seanceTypeFilter !== 'all' || seanceThemeFilter !== 'all';
    count.textContent = filtering ? `${filtered.length} / ${currentSeanceDetail.points.length} point(s) affiché(s)` : '';
  }
}
function onSeanceTypeFilterChange(sel) { seanceTypeFilter = sel.value; renderSeancePoints(); }
function onSeanceThemeFilterChange(sel) { seanceThemeFilter = sel.value; renderSeancePoints(); }

function renderSeance(d) {
  currentSeanceDetail = d;
  seanceTypeFilter = 'all';
  seanceThemeFilter = 'all';
  const box = document.getElementById('seanceResult');
  let links = '';
  if (d.url) links += `<a class="elu-link" href="${d.url}" target="_blank" rel="noopener noreferrer" title="Ouvrir le PV (PDF) sur 1030.be"><svg class="icon" aria-hidden="true"><use href="#ico-date"/></svg>PV (PDF)</a>`;
  if (d.video_url) links += `<a class="elu-link elu-link-video" href="${d.video_url}" target="_blank" rel="noopener noreferrer" title="Voir la séance filmée sur YouTube"><svg class="icon" aria-hidden="true"><use href="#ico-video"/></svg>▶ vidéo (séance complète)</a>`;

  let html = `<div class="elus-bar seance-filter-bar">
    <select id="seanceTypeFilter" class="elu-select" aria-label="Filtrer par type de sujet">
      <option value="all">Tous les types</option>
      ${seanceTypeFilterOptions(d.points)}
    </select>
    <select id="seanceThemeFilter" class="elu-select" aria-label="Filtrer par thématique">
      <option value="all">Toutes les thématiques</option>
      ${seanceThemeFilterOptions(d.points)}
    </select>
  </div>
  <p class="yc-note" id="seanceFilterCount"></p>
  <div class="elu-head">
    <div class="elu-name">${formatDate(d.date)}</div>
    <span class="elu-role elu-role-conseiller">${d.n_points} point${d.n_points > 1 ? 's' : ''}</span>
  </div>`;
  if (links) html += `<div class="elu-links seance-head-links">${links}</div>`;
  html += `<div class="elu-list" id="seancePointsList"></div>`;
  html += `<p class="elu-note">Agrégation déterministe depuis le procès-verbal officiel de cette séance et le chapitrage vidéo correspondant, quand la séance a été filmée. Liste exhaustive des points à l'ordre du jour ; demandeur·se/répondant·e non affiché·e quand non attribuable individuellement (points collectifs/administratifs).</p>`;

  box.innerHTML = html;
  const typeSel = document.getElementById('seanceTypeFilter');
  const themeSel = document.getElementById('seanceThemeFilter');
  if (typeSel) typeSel.addEventListener('change', () => onSeanceTypeFilterChange(typeSel));
  if (themeSel) themeSel.addEventListener('change', () => onSeanceThemeFilterChange(themeSel));
  renderSeancePoints();
  box.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function seanceShareUrl(date) {
  return date ? `${shareBaseUrl()}?tab=seances&seance=${encodeURIComponent(date)}` : `${shareBaseUrl()}?tab=seances`;
}
function seanceShareText(date) {
  return date
    ? `Séance du Conseil communal de Schaerbeek du ${formatDate(date)}`
    : 'Séances du Conseil communal de Schaerbeek';
}

// Partage depuis le bandeau du haut : la séance actuellement ouverte, s'il y en a une.
export function shareSeance(btn) {
  const date = currentSeanceDetail ? currentSeanceDetail.date : '';
  doShare('PV Explorer — Séances', seanceShareText(date), seanceShareUrl(date), btn);
}

// Partage depuis la liste : une séance donnée, sans avoir à l'ouvrir d'abord.
export function shareSeanceDate(date, btn) {
  doShare('PV Explorer — Séances', seanceShareText(date), seanceShareUrl(date), btn);
}
