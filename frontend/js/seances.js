// ── SÉANCES (vue complémentaire à « Par élu·e » : par PV, tous les points,
// pas seulement ceux d'une personne) ──
import { API_URL } from './config.js';
import {
  escapeHtml, formatDate, TYPE_ACTOR_LABEL, renderThemeTags,
  renderTypeBadge, renderStatutBadge, renderDecision, renderMontant, renderSpBadge,
  renderPvPdfLink, renderVideoLink, renderPersonLine, hasDebateLink,
  renderLoadingReveil, renderRubrique, renderResume,
} from './utils.js';
import { doShare, shareBaseUrl } from './share.js';
import { createCombobox } from './combobox.js';

let seancesData = null;       // liste complète [{date,n_points,url,video_url}]
let seancesLoaded = false;
let pendingSeanceDate = null; // séance à présélectionner depuis un lien partagé (?seance=)
let currentSeanceDetail = null;
// Compteur de requête : loadSeance/loadSeanceYearAll s'enchaînent parfois
// (présélection de la dernière séance de l'année au changement d'année,
// puis choix explicite d'une autre séance avant que la 1re réponse soit
// arrivée) — sans garde, une réponse ARRIVÉE EN RETARD écrase l'affichage
// avec des données périmées, et sa propre synchronisation du menu
// (renderSeanceYearList) relance même un rechargement sur la présélection
// par défaut, effaçant le choix explicite de l'utilisateur·rice (race
// observée en CI : sélectionner une séance précise après le changement
// d'année retombait sur la plus récente si la 1re requête traînait).
// Chaque appel capture le compteur AVANT son fetch ; s'il a changé à son
// retour, une requête plus récente a pris le dessus, et celle-ci s'efface.
let seanceRequestSeq = 0;
// Année actuellement affichée en vue agrégée ("Toutes les séances"), ou null
// si une séance précise est affichée — sert à savoir si renderSeanceYearList
// doit garder "__all__" sélectionné plutôt que retomber sur la plus récente.
let currentAggregateYear = null;
// Filtres de la séance affichée (réinitialisés à chaque nouvelle séance
// chargée). Type et facette sont deux variables DISTINCTES, pas une seule :
// une motion rejetée doit pouvoir se filtrer par les deux à la fois, et
// choisir l'un doit recalculer les valeurs ATTEIGNABLES de l'autre (voir
// renderSeanceFilterOptions) — deux rangées de puces qui se croisent, plutôt
// qu'un choix qui en efface un autre.
//   seanceTypeFilter  : 'all' ou un type_label ("Motion", "Question orale"…) —
//                       PARTITION des points, un seul par point.
//   seanceFacetFilter : 'all', 'debat_filme' (lien vers le débat filmé, voir
//                       hasDebateLink), 'statut:<libellé>' (issue du point :
//                       « Approuvé », « Rejeté », « Reporté »…), ou l'un des
//                       deux MANQUES ('sans_decision', 'intervenant_inconnu').
//                       Se superpose aux types plutôt que de les partitionner.
let seanceTypeFilter = 'all';
let seanceFacetFilter = 'all';
let seanceThemeFilter = 'all';
// Personne (demandeur·se OU répondant·e) impliquée dans le point — un même
// filtre couvre les deux rôles, pour retrouver tout ce qui concerne une
// personne dans la séance, peu importe son rôle sur chaque point.
let seancePersonFilter = 'all';
// Le combobox est recréé à chaque séance affichée : la barre de filtres est
// réécrite avec le reste du panneau (voir renderSeance), ses écouteurs avec.
let seancePersonCombo = null;
// Idem pour les thématiques : recréé avec la barre à chaque séance affichée.
let seanceThemeCombo = null;
// Pas de filtre par RÔLE ici, contrairement à l'onglet Par élu·e : le rôle est
// résolu à la date du point (voir services.people.mandats), donc une personne
// n'en a qu'un seul le soir d'une séance. Vérifié sur tout le corpus — aucune
// des 179 séances, ni aucune des 17 années en vue agrégée, ne voit quelqu'un
// siéger des deux côtés. Des puces « Conseiller·ère »/« Collège » y filtraient
// donc les POINTS (« tout point où quelqu'un a ce rôle »), ce qui ne séparait
// à peu près rien : 67 % des points nommant quelqu'un en portaient les deux.

// Présélection appliquée depuis un lien partagé (?tab=seances&seance=…), voir handleDeepLink.
export function setPendingSeanceDate(date) { pendingSeanceDate = date; }

export async function loadSeances() {
  if (seancesLoaded) return;
  const yearSel = document.getElementById('seanceYear');
  if (!yearSel) return;
  // Premier appel réseau de l'onglet : le plus exposé au réveil du service
  // (voir utils.REVEIL_DELAI_MS). #seanceResult est vide tant qu'aucune
  // séance n'est chargée — sans ce message, cette attente s'y lisait comme
  // un panneau resté blanc plutôt qu'un chargement en cours.
  const box = document.getElementById('seanceResult');
  const finReveil = box ? renderLoadingReveil(box, 'Chargement des séances') : () => {};
  try {
    const res = await fetch(API_URL + '/seances');
    finReveil();   // réponse arrivée : le message de réveil n'a plus lieu d'être
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
    finReveil();   // idempotent — utile si le fetch lui-même a échoué (réseau) avant la ligne ci-dessus
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
  const seq = ++seanceRequestSeq;
  const box = document.getElementById('seanceResult');
  box.innerHTML = '<div class="loading"><span>Chargement</span><span class="dots"><span></span><span></span><span></span></span></div>';
  const dates = seancesData.filter(s => s.date.startsWith(year)).map(s => s.date);
  try {
    const results = await Promise.all(dates.map(async d => {
      const res = await fetch(API_URL + '/seance/' + encodeURIComponent(d));
      if (!res.ok) throw new Error('Erreur ' + res.status);
      return res.json();
    }));
    if (seq !== seanceRequestSeq) return;   // une sélection plus récente a pris le dessus
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
    if (seq !== seanceRequestSeq) return;
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
  const statutBadge = renderStatutBadge(it);
  const sp = renderSpBadge(it);
  // Vue agrégée "Toutes les séances" uniquement : rappelle de quelle séance
  // vient ce point, et permet d'y revenir directement (voir jumpToSeance).
  const seanceDateBadge = it._seanceDate
    ? `<button type="button" class="elu-sp elu-sp-link" data-click="jumpToSeance" data-arg="${escapeHtml(it._seanceDate)}" title="Voir cette séance">${escapeHtml(formatDate(it._seanceDate))}</button>`
    : '';
  // Pas de lien "PV (PDF)" générique par point : c'est le même PDF de séance
  // pour tous les points, déjà proposé une fois au-dessus de la liste (voir
  // renderSeance) — le répéter partout suggérerait à tort un accès direct à
  // CE point précis. Seule exception : quand la page d'extraction est connue
  // (voir it.page, ~27% du corpus), un lien SUPPLÉMENTAIRE pointe dessus via
  // l'ancre #page=N — un vrai accès direct, cette fois. Même raisonnement
  // pour la vidéo générique (video_precise=false) : lien vers le DÉBUT de la
  // séance, pas ce point précis — déjà proposé une fois au-dessus (vidéo complète).
  let links = hasDebateLink(it)
    ? renderVideoLink(it.type === 'video' ? it.url : it.video_url, '▶ Voir le débat', 'Voir le débat sur YouTube (au bon moment)')
    : '';
  if (it.page !== null && it.page !== undefined) {
    links += renderPvPdfLink(it.url, it.page, `PV (PDF) — page ${it.page}`,
      `Ouvrir le PV à la page ${it.page} sur 1030.be`);
  }
  const actorLabel = TYPE_ACTOR_LABEL[it.type_label] || 'Auteur·e';
  const rubrique = renderRubrique(it);
  const resume = renderResume(it);
  const demandeur = renderPersonLine('elu-demandeur', actorLabel, it.demandeur);
  const rep = renderPersonLine('elu-rep', 'Répondant·e', it.repondant);
  const decision = renderDecision(it);
  const montant = renderMontant(it);
  const tags = renderThemeTags(it.thematiques);
  return `<div class="elu-item">
    <div class="elu-body">
      ${seanceDateBadge}${badge}${statutBadge}${sp}
      ${rubrique}
      <div class="elu-titre">${escapeHtml(it.titre)}</div>
      ${resume}
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
  const seq = ++seanceRequestSeq;
  const box = document.getElementById('seanceResult');
  box.innerHTML = '<div class="loading"><span>Chargement</span><span class="dots"><span></span><span></span><span></span></span></div>';
  try {
    const res = await fetch(API_URL + '/seance/' + encodeURIComponent(date));
    if (!res.ok) throw new Error('Erreur ' + res.status);
    const data = await res.json();
    if (seq !== seanceRequestSeq) return;   // une sélection plus récente a pris le dessus entre-temps
    renderSeance(data, !!opts.scroll);
    renderSeanceYearList();  // synchronise le menu déroulant (option/année sélectionnées)
  } catch (err) {
    if (seq !== seanceRequestSeq) return;
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
  return seanceTypeFilter === 'all' || p.type_label === seanceTypeFilter;
}
function matchesFacet(p) {
  return seanceFacetFilter === 'all' ? true
    : seanceFacetFilter === 'debat_filme' ? hasDebateLink(p)
    : seanceFacetFilter === 'sans_decision' ? estSansDecision(p)
    : seanceFacetFilter === 'intervenant_inconnu' ? estIntervenantInconnu(p)
    : p.statut === seanceFacetFilter.slice(7);   // 'statut:<libellé>'
}
// Point du PV dont on ne sait PAS ce qu'il est devenu — le solde entre ce que
// la séance compte de points et ce que les puces d'issue savent en dire. Même
// définition que la série du graphe des issues.
//
// « Issue » et non « décision » : une question orale ou une demande ne se
// tranche pas par un vote, son issue relevée est le plus souvent « Débat » ou
// « Pris pour information ». Elles n'en sont pas exclues pour autant — 95 %
// d'entre elles en portent une, donc son absence reste une lacune du PV (les
// 5 points du 27/03/2024, par exemple). Les chapitres vidéo sans point de PV,
// eux, sont exclus : ils n'ont aucune issue par construction.
const estSansDecision = p => !p.statut && p.type_label !== 'Débat filmé';

// Types qu'une personne DÉPOSE : une motion, une question orale ou une demande
// a forcément un·e auteur·e, et appelle une réponse en séance. Un point
// délibératif, lui, est porté par le Collège et tranché par un vote — n'y
// trouver personne est normal, pas une lacune. D'où cette liste, et pas
// « tout point sans personne » : celui-là serait vrai de 71 % du corpus et ne
// signalerait rien.
const TYPES_A_INTERVENANT = ['Motion', 'Question orale', 'Demande'];
const estIntervenantInconnu = p => TYPES_A_INTERVENANT.includes(p.type_label)
  && !personnesDuPoint(p).length;

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
function pointMatchesFilters(p) {
  return matchesType(p) && matchesFacet(p) && matchesTheme(p) && matchesPerson(p);
}

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
  // « Point délibératif » et non « Point » tout court : le graphe « Activité
  // par année » compte, lui, TOUS les points de la séance sous le libellé
  // « Points » (45 en mars 2024). Le même mot pour deux périmètres — 45 contre
  // 30 — laissait croire à une incohérence entre les deux vues. Le type
  // backend reste « Point », seul son libellé change.
  ['Point', 'Point délibératif'],
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
  // 1re entrée : revenir à toutes les thématiques — même convention que le
  // filtre par intervenant·e, dont ce champ partage le composant.
  return [{ theme: '', n: themes.length, tous: true },
    ...themes.map(t => ({ theme: t, n: counts.get(t) }))];
}

// Une option : la thématique (partie trouvée surlignée) et son nombre de
// points. Le libellé stocké porte des « _ » (voir _thematique_label côté
// backend) ; on les remplace à l'affichage ET dans le texte cherché, sinon
// taper « marché public » ne trouverait pas « marche_public ».
function seanceThemeOptionHtml(t, { id, active, selected, label }) {
  const texte = t.tous ? 'Toutes les thématiques' : label;
  return `<li class="elu-opt${active ? ' elu-opt-active' : ''}" role="option" id="${id}"
      data-key="${escapeHtml(t.theme)}" aria-selected="${selected}">
    <span class="elu-opt-name">${texte}</span>
    <span class="elu-opt-meta"><span class="elu-opt-count">${t.n} ${t.tous ? 'thématiques' : `point${t.n > 1 ? 's' : ''}`}</span></span>
  </li>`;
}
// Personnes atteignables compte tenu des filtres type/thématique actifs
// (demandeur·se OU répondant·e de chaque point), triées, avec leur nombre de
// points — les deux rôles cumulés, puisqu'on cherche « tout ce qui concerne
// cette personne dans cette séance », quel que soit le côté où elle se trouve.
function seancePersonFilterOptions(points) {
  const counts = new Map();
  points.forEach(p => {
    // Dédoublonné PAR POINT : une personne qui y figure des deux côtés (ou
    // deux fois du même) ne le compte qu'une fois — le nombre affiché est
    // bien un nombre de points.
    const vus = new Set();
    personnesDuPoint(p).forEach(({ nom, role }) => {
      if (!nom || vus.has(nom)) return;
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

// Une puce de filtre, générique aux deux rangées (type / facette) — chacune
// avec SA variable active et SON gestionnaire de clic, pour que les deux
// rangées se croisent au lieu de se remplacer (voir l'en-tête du fichier).
// Remplace l'ancien menu déroulant #seanceTypeFilter (trop de clics pour un
// choix aussi fréquent que le rôle).
function _seanceChip(value, label, count, active, handler, aide = null) {
  if (!count && !active) return '';
  const facette = aide ? ' elu-chip-facette' : '';
  const title = aide ? ` title="${escapeHtml(aide)}"` : '';
  return `<button type="button" class="elu-chip${facette}${active ? ' elu-chip-active' : ''}"${title} data-click="${handler}" data-arg="${escapeHtml(value)}">${escapeHtml(label)} (${count})</button>`;
}
function seanceTypeChip(value, label, count, aide = null) {
  return _seanceChip(value, label, count, seanceTypeFilter === value, 'onSeanceTypeChipClick', aide);
}
function seanceFacetChip(value, label, count, aide = null) {
  return _seanceChip(value, label, count, seanceFacetFilter === value, 'onSeanceFacetChipClick', aide);
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
  const chips = [seanceFacetChip('debat_filme', 'Avec débat filmé', points.filter(hasDebateLink).length,
    'Points menant au débat filmé — chapitres vidéo autonomes ET points de PV appariés à leur chapitre. Se recoupe avec les types.')];
  // Statuts (« Approuvé », « Reporté »…) : l'issue d'un point est indépendante
  // de son type, d'où leur place ici plutôt que parmi les puces de type.
  seanceStatutCounts(points).forEach(([st, n]) => {
    chips.push(seanceFacetChip(`statut:${st}`, st, n,
      `Points dont l'issue est « ${st} ». Chacun compte aussi dans son type.`));
  });
  // Puis les deux MANQUES, en fin de rangée : ce que la source ne dit pas, là
  // où elle le dit d'habitude. Ils ne s'affichent que s'il y en a — sur la
  // plupart des séances, cette fin de rangée est vide.
  chips.push(seanceFacetChip('sans_decision', 'Sans issue relevée',
    points.filter(estSansDecision).length,
    "Points dont le procès-verbal ne dit pas ce qu'ils sont devenus — ni décision, "
    + "ni débat, ni prise pour information. C'est l'écart entre le nombre de points de "
    + "la séance et la somme des puces d'issue ci-contre. Les chapitres vidéo sans "
    + "point de PV n'en font pas partie : ils n'ont aucune issue par construction."));
  chips.push(seanceFacetChip('intervenant_inconnu', 'Intervenant·e inconnu·e',
    points.filter(estIntervenantInconnu).length,
    `Motions, questions orales et demandes qui ne nomment personne — ni auteur·e ni `
    + `répondant·e — alors que ces types-là sont déposés par quelqu'un. Les points `
    + `délibératifs en sont exclus : portés par le Collège et tranchés par un vote, `
    + `n'y trouver personne est normal.`));
  // Pas de puce « Autres issues » ici, contrairement au graphe de l'onglet
  // Statistiques : là-bas, quinze séries ne se lisent pas, donc les issues
  // rares sont repliées. Ici chaque statut a déjà SA puce, avec son compte
  // exact — un regroupement par-dessus les aurait fait lire comme « issues
  // non identifiées » alors qu'elles sont nommées à côté (27/05/2026 :
  // « Autres issues (27) » à côté de « Pris acte (3) », « Pris pour
  // information (12) » et « Débat (12) », qui en sont la somme).
  return chips.join('');
}
// Reconstruit les options des filtres (pas les éléments eux-mêmes, pour garder
// leurs écouteurs) à partir des filtres CROISÉS — chacun exclut son propre
// filtre mais applique les autres.
function renderSeanceFilterOptions() {
  if (!currentSeanceDetail) return;
  const all = currentSeanceDetail.points;
  const typeBox = document.getElementById('seanceTypeChips');
  const facetBox = document.getElementById('seanceFacetChips');
  // Types : recalculés sur les points qui passeraient facette/thème/
  // intervenant·e — pas le type lui-même. Choisir un statut (facette) doit
  // ainsi recompter les types de CE statut, et réciproquement.
  if (typeBox) {
    typeBox.innerHTML = seanceTypeChips(all.filter(p => matchesFacet(p) && matchesTheme(p) && matchesPerson(p)));
  }
  if (facetBox) {
    facetBox.innerHTML = seanceFacetChips(all.filter(p => matchesType(p) && matchesTheme(p) && matchesPerson(p)));
  }
  if (seanceThemeCombo) {
    seanceThemeCombo.setItems(
      seanceThemeFilterOptions(all.filter(p => matchesType(p) && matchesFacet(p) && matchesPerson(p))));
    // '' est la clé de l'entrée « toutes » — le filtre, lui, dit 'all'.
    seanceThemeCombo.setSelected(seanceThemeFilter === 'all' ? '' : seanceThemeFilter);
  }
  if (seancePersonCombo) {
    seancePersonCombo.setItems(
      seancePersonFilterOptions(all.filter(p => matchesType(p) && matchesFacet(p) && matchesTheme(p))));
    // '' est la clé de l'entrée « tou·te·s » — le filtre, lui, dit 'all'.
    seancePersonCombo.setSelected(seancePersonFilter === 'all' ? '' : seancePersonFilter);
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
  const filtering = seanceTypeFilter !== 'all' || seanceFacetFilter !== 'all'
    || seanceThemeFilter !== 'all' || seancePersonFilter !== 'all';
  if (count) {
    count.textContent = filtering ? `${filtered.length} / ${currentSeanceDetail.points.length} point(s) affiché(s)` : '';
  }
  const reset = document.getElementById('seanceFilterReset');
  if (reset) reset.hidden = !filtering;
}
// Un changement de filtre recalcule à la fois les options des AUTRES
// filtres (facettes) et la liste de points affichée.
function refreshSeanceFilteredView() { renderSeanceFilterOptions(); renderSeancePoints(); }
function onSeanceThemeSelect(key) { seanceThemeFilter = key || 'all'; refreshSeanceFilteredView(); }
function onSeancePersonSelect(key) {
  seancePersonFilter = key || 'all';
  refreshSeanceFilteredView();
}
// Reclique sur la puce déjà active → "Tous les types" ; sinon sélectionne ce
// type (même geste que les puces de l'onglet Par élu·e). Ne touche QUE
// seanceTypeFilter : une facette (statut…) déjà choisie reste active, et se
// recalcule sur ce nouveau type via renderSeanceFilterOptions.
export function onSeanceTypeChipClick(type) {
  seanceTypeFilter = seanceTypeFilter === type ? 'all' : type;
  refreshSeanceFilteredView();
}
// Même geste, pour la rangée des facettes (statut, débat filmé, manques) —
// variable séparée, pour que type et facette se croisent au lieu de
// s'écraser l'un l'autre.
export function onSeanceFacetChipClick(value) {
  seanceFacetFilter = seanceFacetFilter === value ? 'all' : value;
  refreshSeanceFilteredView();
}
function onSeanceFilterReset() {
  seanceTypeFilter = 'all';
  seanceFacetFilter = 'all';
  seanceThemeFilter = 'all';
  seancePersonFilter = 'all';
  refreshSeanceFilteredView();
}

function renderSeance(d, scroll) {
  currentSeanceDetail = d;
  currentAggregateYear = d.isAggregate ? d.year : null;
  seanceTypeFilter = 'all';
  seanceFacetFilter = 'all';
  seanceThemeFilter = 'all';
  seancePersonFilter = 'all';
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
    <div class="elu-chips" id="seanceTypeChips" aria-label="Filtrer par type de sujet"></div>
    <div class="elu-chips" id="seanceFacetChips" aria-label="Filtrer par particularité (se recoupe avec les types)"></div>
    <div class="elu-combo" id="seanceThemeCombo">
      <svg class="icon elu-combo-icon" aria-hidden="true"><use href="#ico-thematique"/></svg>
      <input type="text" id="seanceThemeFilter" class="elu-select elu-combo-input"
             role="combobox" aria-expanded="false" aria-controls="seanceThemeOptions"
             aria-autocomplete="list" aria-label="Filtrer par thématique"
             placeholder="Filtrer par thématique…" autocomplete="off"
             autocapitalize="off" spellcheck="false" enterkeyhint="search"
             data-form-type="other" data-lpignore="true" data-1p-ignore>
      <button type="button" class="elu-combo-clear" id="seanceThemeClear"
              aria-label="Effacer le filtre" title="Effacer le filtre — revenir à toutes les thématiques" hidden>✕</button>
      <ul class="elu-combo-list" id="seanceThemeOptions" role="listbox" aria-label="Thématiques" hidden></ul>
    </div>
    <p class="sr-only" id="seanceThemeStatus" role="status" aria-live="polite"></p>
    <div class="elu-combo" id="seancePersonCombo">
      <svg class="icon elu-combo-icon" aria-hidden="true"><use href="#ico-search"/></svg>
      <input type="text" id="seancePersonFilter" class="elu-select elu-combo-input"
             role="combobox" aria-expanded="false" aria-controls="seancePersonOptions"
             aria-autocomplete="list" aria-label="Filtrer par intervenant·e"
             placeholder="Filtrer par intervenant·e…" autocomplete="off"
             autocapitalize="off" spellcheck="false" enterkeyhint="search"
             data-form-type="other" data-lpignore="true" data-1p-ignore>
      <button type="button" class="elu-combo-clear" id="seancePersonClear"
              aria-label="Effacer le filtre" title="Effacer le filtre — revenir à tou·te·s les intervenant·e·s" hidden>✕</button>
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
  const resetBtn = document.getElementById('seanceFilterReset');
  const filterToggle = document.getElementById('seanceFilterToggle');
  const filterBar = document.getElementById('seanceFilterBar');
  // Même composant que le filtre par intervenant·e ci-dessous : un menu natif
  // ne tenait pas non plus ici — 113 thématiques par séance en médiane, jusqu'à
  // 252, et sa frappe rapide ne cherche que le début du libellé.
  seanceThemeCombo = createCombobox({
    input: document.getElementById('seanceThemeFilter'),
    list: document.getElementById('seanceThemeOptions'),
    clear: document.getElementById('seanceThemeClear'),
    status: document.getElementById('seanceThemeStatus'),
    idPrefix: 'seance-theme',
    itemKey: x => x.theme,
    itemLabel: x => x.theme.replace(/_/g, ' '),
    renderItem: seanceThemeOptionHtml,
    emptyText: q => `Aucune thématique ne correspond à « ${q} ».`,
    statusText: n => (n ? `${n} thématique${n > 1 ? 's' : ''} — utilisez les flèches puis Entrée`
      : 'Aucun résultat'),
    placeholder: n => (n > 1 ? `Filtrer parmi ${n - 1} thématiques…` : 'Filtrer par thématique…'),
    onSelect: onSeanceThemeSelect,
  });
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
