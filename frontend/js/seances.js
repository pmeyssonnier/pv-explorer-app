// ── SÉANCES (vue complémentaire à « Par élu·e » : par PV, tous les points,
// pas seulement ceux d'une personne) ──
import { API_URL } from './config.js';
import {
  escapeHtml, formatDate, TYPE_ACTOR_LABEL, fmtMontant, renderThemeTags,
  renderTypeBadge, renderPvPdfLink, renderVideoLink, renderPersonLine, hasDebateLink,
} from './utils.js';
import { doShare, shareBaseUrl } from './share.js';
import { createCombobox } from './combobox.js';

let seancesData = null;       // liste complète [{date,n_points,url,video_url}]
let seancesLoaded = false;
let pendingSeanceDate = null; // séance à présélectionner depuis un lien partagé (?seance=)
let currentSeanceDetail = null;
// Année actuellement affichée en vue agrégée ("Toutes les séances"), ou null
// si une séance précise est affichée — sert à savoir si renderSeanceYearList
// doit garder "__all__" sélectionné plutôt que retomber sur la plus récente.
let currentAggregateYear = null;
// Filtres de la séance affichée (réinitialisés à chaque nouvelle séance
// chargée) : 'all', un type_label ("Motion", "Question orale", …), ou une
// FACETTE qui se superpose aux types — 'debat_filme' (a un lien vers le débat
// filmé, voir hasDebateLink) ou 'statut:<libellé>' (issue du point :
// « Approuvé », « Reporté », « Retiré »…). Détail dans seanceTypeChips /
// seanceFacetChips.
let seanceTypeFilter = 'all';
let seanceThemeFilter = 'all';
// Personne (demandeur·se OU répondant·e) impliquée dans le point — un même
// filtre couvre les deux rôles, pour retrouver tout ce qui concerne une
// personne dans la séance, peu importe son rôle sur chaque point.
let seancePersonFilter = 'all';
// Le combobox est recréé à chaque séance affichée : la barre de filtres est
// réécrite avec le reste du panneau (voir renderSeance), ses écouteurs avec.
let seancePersonCombo = null;
// Rôle (conseiller·ère / collège) de la personne demandeuse OU répondante —
// même principe que le filtre "Tous les rôles" de l'onglet Par élu·e (voir
// elus.js), mais ici en puces cliquables plutôt qu'un menu déroulant, et
// combiné aux autres filtres à facettes de cette séance.
let seanceRoleFilter = 'all';

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
  const year = yearSel.value;
  const list = seancesData.filter(s => s.date.startsWith(year));
  const allOpt = `<option value="__all__">Toutes les séances (${list.length})</option>`;
  sel.innerHTML = allOpt + list.map(s =>
    `<option value="${escapeHtml(s.date)}">${escapeHtml(formatDate(s.date))}</option>`
  ).join('');
  // Vue agrégée déjà affichée pour cette année : la garder plutôt que de
  // retomber sur la séance la plus récente (voir plus bas).
  if (currentAggregateYear === year) { sel.value = '__all__'; return; }
  const preselect = (currentSeanceDetail && list.some(s => s.date === currentSeanceDetail.date))
    ? currentSeanceDetail.date
    : (list[0] && list[0].date);
  if (!preselect) return;
  sel.value = preselect;
  if (!currentSeanceDetail || currentSeanceDetail.date !== preselect) loadSeance(preselect);
}
// Sélection manuelle dans le menu déroulant des séances — soit une date
// précise, soit "__all__" (toutes les séances de l'année sélectionnée).
export function onSeanceListChange(sel) {
  if (!sel.value) return;
  if (sel.value === '__all__') {
    const yearSel = document.getElementById('seanceYear');
    if (yearSel && yearSel.value) loadSeanceYearAll(yearSel.value);
    return;
  }
  loadSeance(sel.value, { scroll: true });
}

// Vue agrégée "Toutes les séances (année)" : récupère le détail de chaque
// séance de l'année en parallèle et fusionne leurs points dans une seule
// liste filtrable (mêmes filtres type/thématique/intervenant·e que pour une
// séance unique), chaque point gardant trace de sa date d'origine.
async function loadSeanceYearAll(year) {
  const box = document.getElementById('seanceResult');
  box.innerHTML = '<div class="loading"><span>Chargement</span><span class="dots"><span></span><span></span><span></span></span></div>';
  const dates = seancesData.filter(s => s.date.startsWith(year)).map(s => s.date);
  try {
    const results = await Promise.all(dates.map(async d => {
      const res = await fetch(API_URL + '/seance/' + encodeURIComponent(d));
      if (!res.ok) throw new Error('Erreur ' + res.status);
      return res.json();
    }));
    const points = results
      .flatMap(d => d.points.map(p => ({ ...p, _seanceDate: d.date })))
      .sort((a, b) => b._seanceDate.localeCompare(a._seanceDate) || a.sp - b.sp);
    renderSeance({
      isAggregate: true,
      year,
      n_seances: results.length,
      n_points: points.length,
      points,
    });
  } catch (err) {
    box.innerHTML = `<div class="error-box">Impossible de charger les séances de ${escapeHtml(year)}. ${escapeHtml(err.message)}</div>`;
  }
}

// Depuis la vue agrégée : reviens à la séance précise d'un point (bascule
// aussi le menu déroulant, via loadSeance → renderSeanceYearList).
export function jumpToSeance(date) {
  if (date) loadSeance(date, { scroll: false });
}


function seancePointRow(it) {
  const badge = renderTypeBadge(it.type_label);
  // Deux statuts DISTINCTS (voir backend seances.py _is_reportee/_is_retire) :
  // « Reporté » (renvoyé à une séance ultérieure) vs « Retiré » (ôté de
  // l'ordre du jour) — jamais confondus.
  const statutBadge = it.reporte ? `<span class="elu-badge b-report">Reporté</span>`
    : it.retire ? `<span class="elu-badge b-retire">Retiré</span>` : '';
  const sp = it.sp ? `<span class="elu-sp">SP ${it.sp}</span>` : '';
  // Vue agrégée "Toutes les séances" uniquement : rappelle de quelle séance
  // vient ce point, et permet d'y revenir directement (voir jumpToSeance).
  const seanceDateBadge = it._seanceDate
    ? `<button type="button" class="elu-sp elu-sp-link" data-click="jumpToSeance" data-arg="${escapeHtml(it._seanceDate)}" title="Voir cette séance">${escapeHtml(formatDate(it._seanceDate))}</button>`
    : '';
  // Pas de lien "PV (PDF)" par point : c'est le même PDF de séance pour tous
  // les points (pas d'ancre par SP), déjà proposé une fois au-dessus de la
  // liste (voir renderSeance) — le répéter à chaque point suggérerait à tort
  // un accès direct à ce point précis dans le PDF. Même raisonnement pour la
  // vidéo générique (video_precise=false) : lien vers le DÉBUT de la séance,
  // pas ce point précis — déjà proposé une fois au-dessus (vidéo complète).
  const links = hasDebateLink(it)
    ? renderVideoLink(it.type === 'video' ? it.url : it.video_url, '▶ Voir le débat', 'Voir le débat sur YouTube (au bon moment)')
    : '';
  const actorLabel = TYPE_ACTOR_LABEL[it.type_label] || 'Auteur·e';
  const demandeur = renderPersonLine('elu-demandeur', actorLabel, it.demandeur);
  const rep = renderPersonLine('elu-rep', 'Répondant·e', it.repondant);
  // Déjà signalé via le badge « Reporté »/« Retiré » ci-dessus, pas besoin de répéter.
  const decision = (it.decision && !it.reporte && !it.retire) ? `<div class="elu-decision">${escapeHtml(it.decision)}</div>` : '';
  const montant = (it.montant_eur !== null && it.montant_eur !== undefined)
    ? `<div class="elu-montant">Montant engagé : ${fmtMontant(it.montant_eur)}</div>` : '';
  const tags = renderThemeTags(it.thematiques);
  return `<div class="elu-item">
    <div class="elu-body">
      ${seanceDateBadge}${badge}${statutBadge}${sp}
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
    : seanceTypeFilter === 'debat_filme' ? hasDebateLink(p)
    : seanceTypeFilter.startsWith('statut:') ? p.statut === seanceTypeFilter.slice(7)
    : p.type_label === seanceTypeFilter;
}
function matchesTheme(p) {
  return seanceThemeFilter === 'all' || (p.thematiques || []).includes(seanceThemeFilter);
}
// Intervenant·e·s d'un point, UNE PAR ENTRÉE (voir backend/services/seances.py,
// _people_list) : les deux côtés confondus, chacun·e avec SON rôle à la date du
// point. Un point à plusieurs répondant·e·s (« Audrey Henry et Justine Harzé »)
// donne donc deux entrées — c'est ce qui permet de le retrouver en cherchant
// l'un OU l'autre nom, alors que la forme recollée n'en faisait qu'une seule
// « personne » introuvable. L'affichage de la ligne, lui, garde cette forme
// recollée : un point montre toujours TOUS ses répondant·e·s.
const personnesDuPoint = p => [...(p.demandeurs || []), ...(p.repondants || [])];

function matchesPerson(p) {
  return seancePersonFilter === 'all'
    || personnesDuPoint(p).some(x => x.nom === seancePersonFilter);
}
// Rôle ("conseiller"/"college") DE CE POINT PRÉCIS, déjà résolu côté serveur
// à la date de la séance (mandats déclaratifs par date, voir
// services.people.mandats) : jamais un rôle unique figé par personne, un même
// nom peut être conseiller·ère sur un point ancien et échevin·e sur un point
// récent. Le rôle est lu par PERSONNE et non par point : un point où un·e
// conseiller·ère répond aux côtés d'un·e échevin·e compte pour les deux.
function matchesRole(p) {
  return seanceRoleFilter === 'all'
    || personnesDuPoint(p).some(x => x.role === seanceRoleFilter);
}
function pointMatchesFilters(p) { return matchesType(p) && matchesTheme(p) && matchesPerson(p) && matchesRole(p); }

// Types réellement atteignables compte tenu des filtres thématique/
// intervenant·e actifs, avec leur nombre de points, plus deux pseudo-types
// transversaux qui se superposent aux types réels plutôt que de les
// remplacer (un point compte dans son type ET dans les facettes qui le
// concernent — même logique que les compteurs de thématiques, où la somme
// peut dépasser le total) :
//   "debat_filme"     : tout point avec un lien vers le débat (voir
//                       hasDebateLink), quel que soit son type réel — pas
//                       seulement les chapitres vidéo autonomes.
//   "statut:<libellé>" : l'issue du point (« Approuvé », « Décidé », « Pris
//                       pour information », « Reporté », « Retiré »…), telle
//                       que le backend la normalise (voir _decision_label) —
//                       indépendante du type, d'où sa place en facette.
// La valeur actuellement sélectionnée reste toujours proposée (au besoin à
// 0) : un filtre ne doit jamais faire disparaître sa propre sélection.
// TYPES : une PARTITION du total — chaque point en porte un et un seul, donc
// la somme de ces puces égale le nombre de points annoncé. Le libellé d'une
// puce peut différer du type lui-même quand il prêterait à confusion avec une
// facette (ci-dessous).
//                     [type_label backend, libellé de la puce]
const _TYPE_FILTER_ORDER = [
  ['Point', 'Point'],
  ['Motion', 'Motion'],
  ['Question orale', 'Question orale'],
  ['Demande', 'Demande'],
  // Chapitre vidéo dont le point de PV n'a pas été retrouvé (PV pas encore
  // extrait, ou appariement non concluant) : un type à part entière, qui
  // compte dans le total. Il manquait à cette liste — ses points n'avaient
  // donc AUCUNE puce, et la somme des puces tombait sous le total (2025 :
  // 659 affichés pour 676 points). À ne pas confondre avec la facette
  // « Avec débat filmé », qui rassemble TOUS les points menant à un débat —
  // dont ceux-ci.
  ['Débat filmé', 'Débat filmé hors PV'],
];
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
// Personnes atteignables compte tenu des filtres type/thématique/rôle actifs
// (demandeur·se OU répondant·e de chaque point), triées, avec leur nombre de
// points (les deux rôles cumulés). Un rôle actif restreint aussi les NOMS
// eux-mêmes à celleux qui l'ont (pas seulement les points où il apparaît) —
// même principe que "Tous les rôles" dans l'onglet Par élu·e.
function seancePersonFilterOptions(points) {
  const counts = new Map();
  points.forEach(p => {
    // Dédoublonné PAR POINT : une personne qui y figure des deux côtés (ou
    // deux fois du même) ne le compte qu'une fois — le nombre affiché est
    // bien un nombre de points.
    const vus = new Set();
    personnesDuPoint(p).forEach(({ nom, role }) => {
      if (!nom || vus.has(nom)) return;
      if (seanceRoleFilter !== 'all' && role !== seanceRoleFilter) return;
      vus.add(nom);
      counts.set(nom, (counts.get(nom) || 0) + 1);
    });
  });
  if (seancePersonFilter !== 'all' && !counts.has(seancePersonFilter)) counts.set(seancePersonFilter, 0);
  const names = [...counts.keys()].sort((a, b) => a.localeCompare(b, 'fr'));
  // 1re entrée : revenir à tout le monde. La croix du champ fait la même
  // chose, mais un menu doit pouvoir se défaire sans connaître ce raccourci.
  return [{ nom: '', n: names.length, tous: true },
    ...names.map(n => ({ nom: n, n: counts.get(n) }))];
}

// Une option : le nom (partie trouvée surlignée) et son nombre de points.
function seancePersonOptionHtml(p, { id, active, selected, label }) {
  const texte = p.tous ? `Tou·te·s les intervenant·e·s` : label;
  return `<li class="elu-opt${active ? ' elu-opt-active' : ''}" role="option" id="${id}"
      data-key="${escapeHtml(p.nom)}" aria-selected="${selected}">
    <span class="elu-opt-name">${texte}</span>
    <span class="elu-opt-meta"><span class="elu-opt-count">${p.n} ${p.tous ? 'personnes' : `point${p.n > 1 ? 's' : ''}`}</span></span>
  </li>`;
}

// Puces de rôle (Conseiller·ère / Collège) — même widget que eluTypeChip côté
// Par élu·e (classes .elu-chip/.elu-chip-active) : reclique sur la puce déjà
// active → "Tous les rôles" ; jamais affichée à 0, sauf si déjà active (pour
// rester cliquable et permettre de revenir à "Tous les rôles").
function seanceRoleChip(role, label, count) {
  if (!count && seanceRoleFilter !== role) return '';
  const active = seanceRoleFilter === role ? ' elu-chip-active' : '';
  return `<button type="button" class="elu-chip${active}" data-click="onSeanceRoleChipClick" data-arg="${role}">${escapeHtml(label)} (${count})</button>`;
}
// Puces de type (Point/Motion/Question orale/Demande/Débat filmé/Reporté) —
// même widget que seanceRoleChip, remplace l'ancien menu déroulant #seanceTypeFilter
// (trop de clics pour un choix aussi fréquent que le rôle).
function seanceTypeChip(value, label, count, aide = null) {
  if (!count && seanceTypeFilter !== value) return '';
  const active = seanceTypeFilter === value ? ' elu-chip-active' : '';
  const facette = aide ? ' elu-chip-facette' : '';
  const title = aide ? ` title="${escapeHtml(aide)}"` : '';
  return `<button type="button" class="elu-chip${facette}${active}"${title} data-click="onSeanceTypeChipClick" data-arg="${escapeHtml(value)}">${escapeHtml(label)} (${count})</button>`;
}
function seanceTypeChips(points) {
  return _TYPE_FILTER_ORDER
    .map(([type, label]) => seanceTypeChip(type, label, points.filter(p => p.type_label === type).length))
    .join('');
}

// FACETTES : elles se superposent aux types au lieu de les partitionner — un
// point approuvé, reporté ou mené au débat filmé compte AUSSI dans son type.
// Leur somme ne s'ajoute donc pas au total ; d'où leur rangée à part et leur
// habillage distinct (voir .elu-chip-facette).

// Ordre d'affichage des STATUTS : issue du vote d'abord, prises d'acte
// ensuite, points non tranchés en dernier — jamais l'ordre alphabétique ni
// celui, instable, des données. Un statut hors liste (variante rare du PV)
// s'ajoute à la fin, par fréquence décroissante.
const _STATUT_ORDER = ['Approuvé', 'Décidé', 'Rejeté', 'Arrêté', 'Nommé', 'Admis',
  'Pris acte', 'Pris pour information', 'Débat', 'Reporté', 'Retiré'];

// Statuts présents dans le périmètre courant, avec leur nombre de points.
function seanceStatutCounts(points) {
  const counts = new Map();
  points.forEach(p => { if (p.statut) counts.set(p.statut, (counts.get(p.statut) || 0) + 1); });
  const connus = _STATUT_ORDER.filter(st => counts.has(st));
  const autres = [...counts.keys()].filter(st => !_STATUT_ORDER.includes(st))
    .sort((a, b) => counts.get(b) - counts.get(a));
  return [...connus, ...autres].map(st => [st, counts.get(st)]);
}

function seanceFacetChips(points) {
  const chips = [seanceTypeChip('debat_filme', 'Avec débat filmé', points.filter(hasDebateLink).length,
    'Points menant au débat filmé — chapitres vidéo autonomes ET points de PV appariés à leur chapitre. Se recoupe avec les types.')];
  // Statuts (« Approuvé », « Reporté »…) : l'issue d'un point est indépendante
  // de son type, d'où leur place ici plutôt que parmi les puces de type.
  seanceStatutCounts(points).forEach(([st, n]) => {
    chips.push(seanceTypeChip(`statut:${st}`, st, n,
      `Points dont l'issue est « ${st} ». Chacun compte aussi dans son type.`));
  });
  // Pas de puce « Autres issues » ici, contrairement au graphe de l'onglet
  // Statistiques : là-bas, quinze séries ne se lisent pas, donc les issues
  // rares sont repliées. Ici chaque statut a déjà SA puce, avec son compte
  // exact — un regroupement par-dessus les aurait fait lire comme « issues
  // non identifiées » alors qu'elles sont nommées à côté (27/05/2026 :
  // « Autres issues (27) » à côté de « Pris acte (3) », « Pris pour
  // information (12) » et « Débat (12) », qui en sont la somme).
  return chips.join('');
}
// Nombre de points où au moins une des deux personnes (demandeur·se OU
// répondant·e) a ce rôle — une personne peut cumuler les deux côtés d'un même
// point (rare, ex. un point autoporté), d'où deux compteurs indépendants.
function seanceRoleCounts(points) {
  let conseiller = 0, college = 0;
  points.forEach(p => {
    const roles = new Set(personnesDuPoint(p).map(x => x.role));
    if (roles.has('conseiller')) conseiller++;
    if (roles.has('college')) college++;
  });
  return { conseiller, college };
}

// Reconstruit les options des 3 sélecteurs + les puces de rôle (pas les
// éléments eux-mêmes, pour garder leurs écouteurs) à partir des filtres
// CROISÉS — chacun exclut son propre filtre mais applique les autres.
function renderSeanceFilterOptions() {
  if (!currentSeanceDetail) return;
  const all = currentSeanceDetail.points;
  const typeBox = document.getElementById('seanceTypeChips');
  const themeSel = document.getElementById('seanceThemeFilter');
  const roleBox = document.getElementById('seanceRoleChips');
  const facetBox = document.getElementById('seanceFacetChips');
  const pourLesPuces = all.filter(p => matchesTheme(p) && matchesPerson(p) && matchesRole(p));
  if (typeBox) typeBox.innerHTML = seanceTypeChips(pourLesPuces);
  if (facetBox) facetBox.innerHTML = seanceFacetChips(pourLesPuces);
  if (themeSel) {
    themeSel.innerHTML = '<option value="all">Toutes les thématiques</option>'
      + seanceThemeFilterOptions(all.filter(p => matchesType(p) && matchesPerson(p) && matchesRole(p)));
    themeSel.value = seanceThemeFilter;
  }
  if (seancePersonCombo) {
    seancePersonCombo.setItems(
      seancePersonFilterOptions(all.filter(p => matchesType(p) && matchesTheme(p) && matchesRole(p))));
    // '' est la clé de l'entrée « tou·te·s » — le filtre, lui, dit 'all'.
    seancePersonCombo.setSelected(seancePersonFilter === 'all' ? '' : seancePersonFilter);
  }
  if (roleBox) {
    const { conseiller, college } = seanceRoleCounts(all.filter(p => matchesType(p) && matchesTheme(p) && matchesPerson(p)));
    roleBox.innerHTML = seanceRoleChip('conseiller', 'Conseiller·ère', conseiller)
      + seanceRoleChip('college', 'Collège', college);
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
  const filtering = seanceTypeFilter !== 'all' || seanceThemeFilter !== 'all'
    || seancePersonFilter !== 'all' || seanceRoleFilter !== 'all';
  if (count) {
    count.textContent = filtering ? `${filtered.length} / ${currentSeanceDetail.points.length} point(s) affiché(s)` : '';
  }
  const reset = document.getElementById('seanceFilterReset');
  if (reset) reset.hidden = !filtering;
}
// Un changement de filtre recalcule à la fois les options des AUTRES
// filtres (facettes) et la liste de points affichée.
function refreshSeanceFilteredView() { renderSeanceFilterOptions(); renderSeancePoints(); }
function onSeanceThemeFilterChange(sel) { seanceThemeFilter = sel.value; refreshSeanceFilteredView(); }
function onSeancePersonSelect(key) { seancePersonFilter = key || 'all'; refreshSeanceFilteredView(); }
// Reclique sur la puce déjà active → "Tous les types" ; sinon sélectionne ce
// type (même geste que les puces de rôle).
export function onSeanceTypeChipClick(type) {
  seanceTypeFilter = seanceTypeFilter === type ? 'all' : type;
  refreshSeanceFilteredView();
}
// Reclique sur la puce déjà active → "Tous les rôles" ; sinon sélectionne ce
// rôle. Une personne déjà choisie qui n'a plus ce rôle est désélectionnée
// (elle disparaîtrait sinon du menu sans que rien n'explique pourquoi).
// Un même nom peut avoir des rôles différents selon le point (voir
// matchesRole) : "cette personne a-t-elle CE rôle sur au moins un point du
// périmètre actuel" remplace donc un simple lookup par nom.
function personHasRole(name, role) {
  if (!currentSeanceDetail) return false;
  return currentSeanceDetail.points.some(p =>
    personnesDuPoint(p).some(x => x.nom === name && x.role === role));
}
export function onSeanceRoleChipClick(role) {
  seanceRoleFilter = seanceRoleFilter === role ? 'all' : role;
  if (seanceRoleFilter !== 'all' && seancePersonFilter !== 'all' && !personHasRole(seancePersonFilter, seanceRoleFilter)) {
    seancePersonFilter = 'all';
  }
  refreshSeanceFilteredView();
}
function onSeanceFilterReset() {
  seanceTypeFilter = 'all';
  seanceThemeFilter = 'all';
  seancePersonFilter = 'all';
  seanceRoleFilter = 'all';
  refreshSeanceFilteredView();
}

function renderSeance(d, scroll) {
  currentSeanceDetail = d;
  currentAggregateYear = d.isAggregate ? d.year : null;
  seanceTypeFilter = 'all';
  seanceThemeFilter = 'all';
  seancePersonFilter = 'all';
  seanceRoleFilter = 'all';
  const box = document.getElementById('seanceResult');
  // Vue agrégée : pas de PV/vidéo unique à lier (chaque point garde le sien
  // via seanceDateBadge → jumpToSeance), ni de partage dédié pour l'instant.
  let links = '';
  if (!d.isAggregate) {
    links += renderPvPdfLink(d.url);
    links += renderVideoLink(d.video_url, '▶ vidéo (séance complète)', 'Voir la séance filmée sur YouTube');
  }
  const titleLabel = d.isAggregate ? `Toutes les séances ${escapeHtml(d.year)}` : formatDate(d.date);
  const countLabel = d.isAggregate
    ? `${d.n_points} point${d.n_points > 1 ? 's' : ''} · ${d.n_seances} séance${d.n_seances > 1 ? 's' : ''}`
    : `${d.n_points} point${d.n_points > 1 ? 's' : ''}`;
  const shareBtn = d.isAggregate ? '' : `<button type="button" class="seance-share-btn" data-click="shareSeance" aria-label="Partager le lien vers cette séance" title="Partager le lien vers cette séance">
      <svg class="icon" aria-hidden="true"><use href="#ico-share"/></svg>
    </button>`;

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
    <div class="elu-chips" id="seanceRoleChips" aria-label="Filtrer par rôle"></div>
    <div class="elu-chips" id="seanceTypeChips" aria-label="Filtrer par type de sujet"></div>
    <div class="elu-chips" id="seanceFacetChips" aria-label="Filtrer par particularité (se recoupe avec les types)"></div>
    <select id="seanceThemeFilter" class="elu-select" aria-label="Filtrer par thématique"></select>
    <div class="elu-combo" id="seancePersonCombo">
      <svg class="icon elu-combo-icon" aria-hidden="true"><use href="#ico-search"/></svg>
      <input type="text" id="seancePersonFilter" class="elu-select elu-combo-input"
             role="combobox" aria-expanded="false" aria-controls="seancePersonOptions"
             aria-autocomplete="list" aria-label="Filtrer par intervenant·e"
             placeholder="Filtrer par intervenant·e…" autocomplete="off"
             autocapitalize="off" spellcheck="false" enterkeyhint="search"
             data-form-type="other" data-lpignore="true" data-1p-ignore>
      <button type="button" class="elu-combo-clear" id="seancePersonClear"
              aria-label="Effacer la recherche" title="Effacer la recherche" hidden>✕</button>
      <ul class="elu-combo-list" id="seancePersonOptions" role="listbox" aria-label="Intervenant·e·s" hidden></ul>
    </div>
    <p class="sr-only" id="seancePersonStatus" role="status" aria-live="polite"></p>
  </div>
  <p class="yc-note" id="seanceFilterCount"></p>
  <div class="elu-head">
    <div class="elu-name">${titleLabel}</div>
    <span class="elu-role elu-role-conseiller">${countLabel}</span>
    ${shareBtn}
  </div>`;
  if (links) html += `<div class="elu-links seance-head-links">${links}</div>`;
  html += `<div class="elu-list" id="seancePointsList"></div>`;
  html += d.isAggregate
    ? `<p class="elu-note">Points de toutes les séances de ${escapeHtml(d.year)} fusionnés dans une seule liste filtrable ; la date sous chaque point permet de revenir à sa séance d'origine (PV, vidéo).</p>`
    : `<p class="elu-note">Agrégation déterministe depuis le procès-verbal officiel de cette séance et le chapitrage vidéo correspondant, quand la séance a été filmée. Liste exhaustive des points à l'ordre du jour ; demandeur·se/répondant·e non affiché·e quand non attribuable individuellement (points collectifs/administratifs).</p>`;

  box.innerHTML = html;
  const themeSel = document.getElementById('seanceThemeFilter');
  const resetBtn = document.getElementById('seanceFilterReset');
  const filterToggle = document.getElementById('seanceFilterToggle');
  const filterBar = document.getElementById('seanceFilterBar');
  if (themeSel) themeSel.addEventListener('change', () => onSeanceThemeFilterChange(themeSel));
  // Même composant que le sélecteur d'élu·e (voir combobox.js) : un menu natif
  // ne tenait pas ici non plus — jusqu'à 115 personnes sur une séance, et sa
  // frappe rapide ne cherche que dans le prénom.
  seancePersonCombo = createCombobox({
    input: document.getElementById('seancePersonFilter'),
    list: document.getElementById('seancePersonOptions'),
    clear: document.getElementById('seancePersonClear'),
    status: document.getElementById('seancePersonStatus'),
    idPrefix: 'seance-personne',
    itemKey: x => x.nom,
    itemLabel: x => x.nom,
    renderItem: seancePersonOptionHtml,
    emptyText: q => `Aucun·e intervenant·e ne correspond à « ${q} ».`,
    statusText: n => (n ? `${n} intervenant·e${n > 1 ? 's' : ''} — utilisez les flèches puis Entrée`
      : 'Aucun résultat'),
    placeholder: n => (n > 1 ? `Filtrer parmi ${n - 1} intervenant·e·s…` : 'Filtrer par intervenant·e…'),
    onSelect: onSeancePersonSelect,
  });
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
