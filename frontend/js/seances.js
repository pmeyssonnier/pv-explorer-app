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
// chargée) : 'all', un type_label ("Motion", "Question orale", …), ou un
// pseudo-type transversal — 'reporte' (point reporté, peut être de
// n'importe quel type réel) ou 'debat_filme' (a un lien vers le débat filmé,
// voir hasDebateLink) — qui se superpose aux types réels (détail dans
// seanceTypeFilterOptions).
let seanceTypeFilter = 'all';
let seanceThemeFilter = 'all';
// Personne (demandeur·se OU répondant·e) impliquée dans le point — un même
// filtre couvre les deux rôles, pour retrouver tout ce qui concerne une
// personne dans la séance, peu importe son rôle sur chaque point.
let seancePersonFilter = 'all';

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
    // Lien partagé (?seance=…) : charge directement cette séance — son
    // propre chargement peuple ensuite la liste (voir loadSeance), pas
    // besoin d'un 2e appel réseau pour la présélection "plus récente".
    if (pendingSeanceDate && seancesData.some(s => s.date === pendingSeanceDate)) {
      await loadSeance(pendingSeanceDate, { scroll: true });
    } else {
      renderSeanceYearList();
    }
    pendingSeanceDate = null;
  } catch (err) {
    yearSel.innerHTML = '<option value="">Indisponible</option>';
  }
}

// Liste des séances de l'année sélectionnée (la plus récente en premier),
// en menu déroulant compact plutôt qu'une liste de lignes (gain de place).
// Présélectionne la séance déjà affichée si elle appartient à cette année,
// sinon la plus récente — et la charge si ce n'est pas déjà celle affichée
// (ex. après un changement d'année ; sans effet en boucle après un
// loadSeance() qui vient d'aboutir, puisqu'il correspond déjà).
export function renderSeanceYearList() {
  const sel = document.getElementById('seanceList');
  const yearSel = document.getElementById('seanceYear');
  if (!sel || !seancesData || !yearSel.value) return;
  const list = seancesData.filter(s => s.date.startsWith(yearSel.value));
  sel.innerHTML = list.map(s => {
    const video = s.video_url ? ' ▶' : '';
    return `<option value="${escapeHtml(s.date)}">${escapeHtml(formatDate(s.date))}${video}</option>`;
  }).join('');
  const preselect = (currentSeanceDetail && list.some(s => s.date === currentSeanceDetail.date))
    ? currentSeanceDetail.date
    : (list[0] && list[0].date);
  if (!preselect) return;
  sel.value = preselect;
  if (!currentSeanceDetail || currentSeanceDetail.date !== preselect) loadSeance(preselect);
}
// Sélection manuelle dans le menu déroulant des séances.
export function onSeanceListChange(sel) { if (sel.value) loadSeance(sel.value, { scroll: true }); }

// Un point a un lien vers le débat filmé quand c'est un chapitre vidéo
// autonome (type "video", pas de point PV associé), ou quand un chapitre
// vidéo a été apparié précisément à ce point (video_precise). Un point
// reporté n'a jamais rien été débattu ce jour-là. Fonction partagée entre
// le rendu de la ligne (lien affiché) et le filtre "Débat filmé" (voir
// seanceTypeFilterOptions/pointMatchesFilters) — même définition partout.
function hasDebateLink(it) {
  return (it.type === 'video' && !!it.url) || !!(it.video_url && it.video_precise && !it.reporte);
}

function seancePointRow(it) {
  const cls = TYPE_BADGE[it.type_label] || 'b-d';
  const badge = `<span class="elu-badge ${cls}">${escapeHtml(it.type_label)}</span>`;
  const reporteBadge = it.reporte ? `<span class="elu-badge b-report">Reporté</span>` : '';
  const sp = it.sp ? `<span class="elu-sp">SP ${it.sp}</span>` : '';
  // Pas de lien "PV (PDF)" par point : c'est le même PDF de séance pour tous
  // les points (pas d'ancre par SP), déjà proposé une fois au-dessus de la
  // liste (voir renderSeance) — le répéter à chaque point suggérerait à tort
  // un accès direct à ce point précis dans le PDF. Même raisonnement pour la
  // vidéo générique (video_precise=false) : lien vers le DÉBUT de la séance,
  // pas ce point précis — déjà proposé une fois au-dessus (vidéo complète).
  const links = hasDebateLink(it)
    ? `<a class="elu-link elu-link-video" href="${it.type === 'video' ? it.url : it.video_url}" target="_blank" rel="noopener noreferrer" title="Voir le débat sur YouTube (au bon moment)"><svg class="icon" aria-hidden="true"><use href="#ico-video"/></svg>▶ Voir le débat</a>`
    : '';
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

// `opts.scroll` : fait défiler jusqu'au résultat — uniquement pour une
// action délibérée (choix explicite dans le menu, lien partagé), jamais
// pour une présélection automatique (ouverture de l'onglet, changement
// d'année) qui ne doit pas faire sauter la page sous l'utilisateur·rice.
export async function loadSeance(date, opts = {}) {
  const box = document.getElementById('seanceResult');
  box.innerHTML = '<div class="loading"><span>Chargement</span><span class="dots"><span></span><span></span><span></span></span></div>';
  try {
    const res = await fetch(API_URL + '/seance/' + encodeURIComponent(date));
    if (!res.ok) throw new Error('Erreur ' + res.status);
    renderSeance(await res.json(), !!opts.scroll);
    renderSeanceYearList();  // synchronise le menu déroulant (option/année sélectionnées)
  } catch (err) {
    box.innerHTML = `<div class="error-box">Impossible de charger cette séance. ${escapeHtml(err.message)}</div>`;
  }
}

// ── Filtres à facettes : chaque sélecteur ne propose que les valeurs encore
// atteignables compte tenu des AUTRES filtres actifs (pas du sien) — choisir
// un type recalcule les thématiques/intervenant·e·s disponibles, et vice
// versa. Chaque prédicat matchesX ignore délibérément seanceXFilter lui-même :
// c'est ce qui permet de calculer "les points qui passeraient le filtre X
// si on l'ignorait" pour peupler les options de X.
function matchesType(p) {
  return seanceTypeFilter === 'all' ? true
    : seanceTypeFilter === 'reporte' ? p.reporte
    : seanceTypeFilter === 'debat_filme' ? hasDebateLink(p)
    : p.type_label === seanceTypeFilter;
}
function matchesTheme(p) {
  return seanceThemeFilter === 'all' || (p.thematiques || []).includes(seanceThemeFilter);
}
function matchesPerson(p) {
  return seancePersonFilter === 'all' || p.demandeur === seancePersonFilter || p.repondant === seancePersonFilter;
}
function pointMatchesFilters(p) { return matchesType(p) && matchesTheme(p) && matchesPerson(p); }

// Types réellement atteignables compte tenu des filtres thématique/
// intervenant·e actifs, avec leur nombre de points, plus deux pseudo-types
// transversaux qui se superposent aux types réels plutôt que de les
// remplacer (un point compte dans son type ET dans "Débat filmé"/"Reporté"
// s'il l'est aussi — même logique que les compteurs de thématiques, où la
// somme peut dépasser le total) :
//   "debat_filme" : tout point avec un lien vers le débat (voir
//                   hasDebateLink), quel que soit son type réel — pas
//                   seulement les chapitres vidéo autonomes.
//   "reporte"     : un point reporté peut être de n'importe quel type réel.
// La valeur actuellement sélectionnée reste toujours proposée (au besoin à
// 0) : un filtre ne doit jamais faire disparaître sa propre sélection.
const _TYPE_FILTER_ORDER = ['Point', 'Motion', 'Question orale', 'Demande'];
function seanceTypeFilterOptions(points) {
  const opts = [];
  _TYPE_FILTER_ORDER.forEach(t => {
    const n = points.filter(p => p.type_label === t).length;
    if (n || seanceTypeFilter === t) opts.push(`<option value="${escapeHtml(t)}">${escapeHtml(t)} (${n})</option>`);
  });
  const nDebat = points.filter(hasDebateLink).length;
  if (nDebat || seanceTypeFilter === 'debat_filme') opts.push(`<option value="debat_filme">Débat filmé (${nDebat})</option>`);
  const nReporte = points.filter(p => p.reporte).length;
  if (nReporte || seanceTypeFilter === 'reporte') opts.push(`<option value="reporte">Reporté (${nReporte})</option>`);
  return opts.join('');
}
// Thématiques atteignables compte tenu des filtres type/intervenant·e actifs,
// triées, avec le nombre de points concernés (un point peut porter plusieurs
// thématiques, donc la somme des compteurs peut dépasser le nombre de points).
function seanceThemeFilterOptions(points) {
  const counts = new Map();
  points.forEach(p => (p.thematiques || []).forEach(t => counts.set(t, (counts.get(t) || 0) + 1)));
  if (seanceThemeFilter !== 'all' && !counts.has(seanceThemeFilter)) counts.set(seanceThemeFilter, 0);
  const themes = [...counts.keys()].sort((a, b) => a.localeCompare(b, 'fr'));
  return themes.map(t => `<option value="${escapeHtml(t)}">${escapeHtml(t)} (${counts.get(t)})</option>`).join('');
}
// Personnes atteignables compte tenu des filtres type/thématique actifs
// (demandeur·se OU répondant·e de chaque point), triées, avec leur nombre de
// points (les deux rôles cumulés).
function seancePersonFilterOptions(points) {
  const counts = new Map();
  points.forEach(p => {
    [p.demandeur, p.repondant].forEach(name => {
      if (name) counts.set(name, (counts.get(name) || 0) + 1);
    });
  });
  if (seancePersonFilter !== 'all' && !counts.has(seancePersonFilter)) counts.set(seancePersonFilter, 0);
  const names = [...counts.keys()].sort((a, b) => a.localeCompare(b, 'fr'));
  return names.map(n => `<option value="${escapeHtml(n)}">${escapeHtml(n)} (${counts.get(n)})</option>`).join('');
}

// Reconstruit les options des 3 sélecteurs (pas les éléments <select>
// eux-mêmes, pour garder leurs écouteurs) à partir des filtres CROISÉS —
// chacun exclut son propre filtre mais applique les deux autres.
function renderSeanceFilterOptions() {
  if (!currentSeanceDetail) return;
  const all = currentSeanceDetail.points;
  const typeSel = document.getElementById('seanceTypeFilter');
  const themeSel = document.getElementById('seanceThemeFilter');
  const personSel = document.getElementById('seancePersonFilter');
  if (typeSel) {
    typeSel.innerHTML = '<option value="all">Tous les types</option>'
      + seanceTypeFilterOptions(all.filter(p => matchesTheme(p) && matchesPerson(p)));
    typeSel.value = seanceTypeFilter;
  }
  if (themeSel) {
    themeSel.innerHTML = '<option value="all">Toutes les thématiques</option>'
      + seanceThemeFilterOptions(all.filter(p => matchesType(p) && matchesPerson(p)));
    themeSel.value = seanceThemeFilter;
  }
  if (personSel) {
    personSel.innerHTML = '<option value="all">Tou·te·s les intervenant·e·s</option>'
      + seancePersonFilterOptions(all.filter(p => matchesType(p) && matchesTheme(p)));
    personSel.value = seancePersonFilter;
  }
}

// (Re)rend la liste des points selon les filtres courants — pas besoin de
// reconstruire l'en-tête/les liens de la séance à chaque changement.
function renderSeancePoints() {
  const list = document.getElementById('seancePointsList');
  const count = document.getElementById('seanceFilterCount');
  if (!list || !currentSeanceDetail) return;
  const filtered = currentSeanceDetail.points.filter(pointMatchesFilters);
  list.innerHTML = filtered.length
    ? filtered.map(seancePointRow).join('')
    : '<p class="trend-empty">Aucun point ne correspond à ces filtres.</p>';
  const filtering = seanceTypeFilter !== 'all' || seanceThemeFilter !== 'all' || seancePersonFilter !== 'all';
  if (count) {
    count.textContent = filtering ? `${filtered.length} / ${currentSeanceDetail.points.length} point(s) affiché(s)` : '';
  }
  const reset = document.getElementById('seanceFilterReset');
  if (reset) reset.hidden = !filtering;
}
// Un changement de filtre recalcule à la fois les options des 2 AUTRES
// sélecteurs (filtres à facettes) et la liste de points affichée.
function refreshSeanceFilteredView() { renderSeanceFilterOptions(); renderSeancePoints(); }
function onSeanceTypeFilterChange(sel) { seanceTypeFilter = sel.value; refreshSeanceFilteredView(); }
function onSeanceThemeFilterChange(sel) { seanceThemeFilter = sel.value; refreshSeanceFilteredView(); }
function onSeancePersonFilterChange(sel) { seancePersonFilter = sel.value; refreshSeanceFilteredView(); }
function onSeanceFilterReset() {
  seanceTypeFilter = 'all';
  seanceThemeFilter = 'all';
  seancePersonFilter = 'all';
  refreshSeanceFilteredView();
}

function renderSeance(d, scroll) {
  currentSeanceDetail = d;
  seanceTypeFilter = 'all';
  seanceThemeFilter = 'all';
  seancePersonFilter = 'all';
  const box = document.getElementById('seanceResult');
  let links = '';
  if (d.url) links += `<a class="elu-link" href="${d.url}" target="_blank" rel="noopener noreferrer" title="Ouvrir le PV (PDF) sur 1030.be"><svg class="icon" aria-hidden="true"><use href="#ico-date"/></svg>PV (PDF)</a>`;
  if (d.video_url) links += `<a class="elu-link elu-link-video" href="${d.video_url}" target="_blank" rel="noopener noreferrer" title="Voir la séance filmée sur YouTube"><svg class="icon" aria-hidden="true"><use href="#ico-video"/></svg>▶ vidéo (séance complète)</a>`;

  // Filtres repliés par défaut (comme les valeurs, remises à "all" ci-dessus) :
  // l'écran par défaut reste léger (choisir une séance → lire ses points),
  // le filtrage à facettes reste dispo à la demande sans peser sur tout le
  // monde. Le bouton reset reste visible même replié (hors du bloc masqué)
  // pour qu'un filtre actif reste toujours annulable sans rouvrir le panneau.
  let html = `<div class="seance-filter-toggle-row">
    <button type="button" class="drill-reset" id="seanceFilterToggle" aria-expanded="false">Filtrer ▾</button>
    <button type="button" class="drill-reset" id="seanceFilterReset" hidden>↩ Réinitialiser les filtres</button>
  </div>
  <div class="elus-bar seance-filter-bar" id="seanceFilterBar" hidden>
    <select id="seanceTypeFilter" class="elu-select" aria-label="Filtrer par type de sujet"></select>
    <select id="seanceThemeFilter" class="elu-select" aria-label="Filtrer par thématique"></select>
    <select id="seancePersonFilter" class="elu-select" aria-label="Filtrer par intervenant·e"></select>
  </div>
  <p class="yc-note" id="seanceFilterCount"></p>
  <div class="elu-head">
    <div class="elu-name">${formatDate(d.date)}</div>
    <span class="elu-role elu-role-conseiller">${d.n_points} point${d.n_points > 1 ? 's' : ''}</span>
    <button type="button" class="seance-share-btn" data-click="shareSeance" aria-label="Partager le lien vers cette séance" title="Partager le lien vers cette séance">
      <svg class="icon" aria-hidden="true"><use href="#ico-share"/></svg>
    </button>
  </div>`;
  if (links) html += `<div class="elu-links seance-head-links">${links}</div>`;
  html += `<div class="elu-list" id="seancePointsList"></div>`;
  html += `<p class="elu-note">Agrégation déterministe depuis le procès-verbal officiel de cette séance et le chapitrage vidéo correspondant, quand la séance a été filmée. Liste exhaustive des points à l'ordre du jour ; demandeur·se/répondant·e non affiché·e quand non attribuable individuellement (points collectifs/administratifs).</p>`;

  box.innerHTML = html;
  const typeSel = document.getElementById('seanceTypeFilter');
  const themeSel = document.getElementById('seanceThemeFilter');
  const personSel = document.getElementById('seancePersonFilter');
  const resetBtn = document.getElementById('seanceFilterReset');
  const filterToggle = document.getElementById('seanceFilterToggle');
  const filterBar = document.getElementById('seanceFilterBar');
  if (typeSel) typeSel.addEventListener('change', () => onSeanceTypeFilterChange(typeSel));
  if (themeSel) themeSel.addEventListener('change', () => onSeanceThemeFilterChange(themeSel));
  if (personSel) personSel.addEventListener('change', () => onSeancePersonFilterChange(personSel));
  if (resetBtn) resetBtn.addEventListener('click', onSeanceFilterReset);
  if (filterToggle && filterBar) {
    filterToggle.addEventListener('click', () => {
      const nowExpanded = filterBar.hidden;   // vrai une fois basculé
      filterBar.hidden = !nowExpanded;
      filterToggle.setAttribute('aria-expanded', String(nowExpanded));
      filterToggle.textContent = nowExpanded ? 'Filtrer ▴' : 'Filtrer ▾';
    });
  }
  refreshSeanceFilteredView();
  if (scroll) box.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function seanceShareUrl(date) {
  return date ? `${shareBaseUrl()}?tab=seances&seance=${encodeURIComponent(date)}` : `${shareBaseUrl()}?tab=seances`;
}
function seanceShareText(date) {
  return date
    ? `Séance du Conseil communal de Schaerbeek du ${formatDate(date)}`
    : 'Séances du Conseil communal de Schaerbeek';
}

// Partage la séance actuellement affichée (bouton dans son en-tête).
export function shareSeance(btn) {
  const date = currentSeanceDetail ? currentSeanceDetail.date : '';
  doShare('PV Explorer — Séances', seanceShareText(date), seanceShareUrl(date), btn);
}
