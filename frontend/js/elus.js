// ── INTERVENTIONS PAR ÉLU·E (agrégation structurée via /elus, /elu/{key}) ──
// La recherche sémantique du chat est sensible à la formulation et non
// exhaustive ; cette vue liste TOUTES les interventions d'une personne.
import { API_URL } from './config.js';
import {
  escapeHtml, formatDate, TYPE_ACTOR_LABEL, TYPE_COUNT_LABEL, renderThemeTags,
  renderTypeBadge, renderPvPdfLink, renderVideoLink, renderPersonLine, renderReponseDetails,
  hasDebateLink, listeFr, REVEIL_DELAI_MS,
} from './utils.js';
import { doShare, shareBaseUrl } from './share.js';
import { createCombobox } from './combobox.js';

let elusData = null;        // liste complète [{key,nom,role,depose,repond}]
let elusLoaded = false;
let pendingEluKey = null;   // élu·e à présélectionner depuis un lien partagé (?elu=)
let currentEluData = null;  // dernière fiche /elu/{key} chargée (pour reFiltrer sans refetch)
let eluYearFilter = 'all';  // année sélectionnée dans le filtre ("all" = toutes)
// Puces = interrupteurs indépendants et cumulables. Ensemble VIDE = aucun
// filtre (tout est montré) ; sinon on garde les actions correspondant à l'un
// des éléments cochés (OU dans un groupe, ET entre les deux groupes).
let eluTypeSel = new Set();   // types d'action cochés — dépôts uniquement
let eluThemeFilter = 'all'; // thématique sélectionnée ("all" = toutes) — depose ET repond
// Le filtre thématique passe par le même composant que la recherche d'élu·e et
// que celui de l'onglet Séances : un menu natif ne tient pas la centaine de
// thématiques d'une fiche, et sa frappe rapide ne cherche que le début du
// libellé. Créé une fois (l'élément est statique dans index.html).
let eluThemeCombo = null;
let eluRoleSel = new Set();   // rôles cochés ('conseiller', 'echevin', 'bourgmestre')
let eluSelectedKey = null;  // clé actuellement chargée
let eluCombo = null;        // instance du combobox partagé (voir combobox.js)

// Présélection appliquée depuis un lien partagé (?tab=elus&elu=…), voir handleDeepLink.
export function setPendingEluKey(key) { pendingEluKey = key; }

export async function loadElus() {
  if (elusLoaded) return;
  const input = document.getElementById('eluSearch');
  if (!input) return;
  // Écran d'accueil TOUT DE SUITE, sans attendre la réponse de /elus : la
  // construction de l'index côté serveur prend un instant au premier appel
  // (base des PV + chapitrage + questions écrites), et le panneau restait
  // vide pendant ce temps.
  renderEluAccueil();
  // Le champ de recherche reste affiché mais ne peut rien trouver tant que
  // /elus n'a pas répondu : désactivé, avec un message plutôt qu'un silence.
  // Si l'API a dû se réveiller (plan Render gratuit, endormi après une
  // période d'inactivité — voir REVEIL_DELAI_MS), le message évolue pour ne
  // pas laisser croire à une panne pendant jusqu'à une minute.
  input.disabled = true;
  const placeholderInitial = input.placeholder;
  input.placeholder = 'Chargement…';
  const minuteurReveil = setTimeout(() => {
    input.placeholder = 'Réveil du service, patientez…';
  }, REVEIL_DELAI_MS);
  try {
    const res = await fetch(API_URL + '/elus');
    clearTimeout(minuteurReveil);
    if (!res.ok) throw new Error('Erreur ' + res.status);
    elusData = (await res.json()).elus || [];
    elusLoaded = true;
    input.disabled = false;
    input.placeholder = placeholderInitial;
    populateElus();
  } catch (err) {
    clearTimeout(minuteurReveil);
    input.disabled = true;
    input.placeholder = 'Indisponible';
  }
}

// ── Puces de RÔLE de la personne affichée ───────────────────────────────────
// Une même personne change de rôle au fil des législatures (conseiller·ère,
// puis échevin·e, puis bourgmestre…) : chaque action est rattachée au rôle
// qu'elle exerçait À SA DATE, d'après ses mandats déclarés (voir
// backend/services/people/mandats.py, même règle de priorité). Une puce par
// rôle réellement exercé, avec son nombre d'actions ; un clic n'affiche que
// les actions de ce rôle, un reclic les rend toutes.

// Libellés courts (MANDAT_LABEL est la forme longue, pour l'en-tête de fiche).
const ROLE_CHIP_LABEL = {
  conseiller: 'Conseiller·ère',
  echevin: 'Échevin·e',
  bourgmestre: 'Bourgmestre',
};

const _inRanges = (year, ranges) => (ranges || [])
  .some(r => r.debut <= year && (r.fin === null || r.fin === undefined || year <= r.fin));

// Rôle exact à une date (« 2019-02-27 » → 'echevin'). Un mandat de Collège
// l'emporte sur le mandat de conseiller·ère simultané — les échevin·e·s
// restent conseiller·ère·s mais agissent ès qualité de Collège. null hors de
// tout mandat connu (personne absente des données déclaratives, action
// antérieure à l'entrée en fonction, trou entre deux mandats).
function roleAt(mandats, date) {
  const year = parseInt(String(date || '').slice(0, 4), 10);
  if (!mandats || !year) return null;
  if (_inRanges(year, mandats.bourgmestre)) return 'bourgmestre';
  if (_inRanges(year, mandats.echevin)) return 'echevin';
  if (_inRanges(year, mandats.conseiller)) return 'conseiller';
  return null;
}

function filterByRole(items, mandats, roles) {
  if (!roles.size) return items;
  return items.filter(it => roles.has(roleAt(mandats, it.date)));
}

// Nombre d'actions (dépôts + réponses) par rôle, sur la période affichée.
function eluRoleCounts(mandats, actions) {
  const counts = { conseiller: 0, echevin: 0, bourgmestre: 0 };
  actions.forEach(it => {
    const r = roleAt(mandats, it.date);
    if (r) counts[r] += 1;
  });
  return counts;
}

function eluRoleChip(role, count) {
  if (!count) return '';
  const on = eluRoleSel.has(role);
  return `<button type="button" class="elu-chip${on ? ' elu-chip-active' : ''}" aria-pressed="${on}"`
    + ` data-click="onEluRoleChipClick" data-arg="${role}">`
    + `<strong>${escapeHtml(ROLE_CHIP_LABEL[role])}</strong> · ${count} action${count > 1 ? 's' : ''}</button>`;
}

function renderEluRoleChips(counts) {
  const box = document.getElementById('eluRoleChips');
  if (!box) return;
  const html = MANDAT_ORDER.map(r => eluRoleChip(r, counts[r])).join('');
  box.innerHTML = html;
  box.hidden = !html;
}

// Interrupteur : coche/décoche ce rôle. Plus aucun coché = tous les rôles.
export function onEluRoleChipClick(role) {
  toggle(eluRoleSel, role);
  if (currentEluData) renderElu(currentEluData);
}

const toggle = (set, v) => (set.has(v) ? set.delete(v) : set.add(v));

// ── SÉLECTEUR D'ÉLU·E ───────────────────────────────────────────────────────
// Combobox partagé (voir combobox.js, aussi utilisé par le filtre
// « intervenant·e » de l'onglet Séances) : recherche libre sur le prénom ET le
// nom, insensible aux accents et à la casse. Ne reste ici que ce qui est
// propre aux élu·e·s — ce qu'on lit sur une option avant de choisir.
const ROLE_LABEL = { college: 'Collège', conseiller: 'Conseiller·ère' };

// Élu·e·s cherchables : tout le monde, trié par nom de famille (la clé
// backend), pas par nombre d'interventions — liste stable et parcourable.
function eluCandidates() {
  return (elusData || []).slice().sort((a, b) => a.key.localeCompare(b.key, 'fr'));
}

// Une option : nom (partie trouvée surlignée) + rôle + volume d'activité —
// de quoi reconnaître la bonne personne SANS ouvrir sa fiche (homonymes,
// élu·e·s d'une seule législature…).
function eluOptionHtml(e, { id, active, selected, label }) {
  const counts = [];
  if (e.depose) counts.push(`${e.depose} déposée${e.depose > 1 ? 's' : ''}`);
  if (e.repond) counts.push(`${e.repond} réponse${e.repond > 1 ? 's' : ''}`);
  return `<li class="elu-opt${active ? ' elu-opt-active' : ''}" role="option" id="${id}"
      data-key="${escapeHtml(e.key)}" aria-selected="${selected}">
    <span class="elu-opt-name">${label}</span>
    <span class="elu-opt-meta">
      <span class="elu-opt-role elu-opt-role-${e.role}">${ROLE_LABEL[e.role] || e.role}</span>
      ${counts.length ? `<span class="elu-opt-count">${counts.join(' · ')}</span>` : ''}
    </span>
  </li>`;
}

export function initEluCombo() {
  eluCombo = createCombobox({
    input: document.getElementById('eluSearch'),
    list: document.getElementById('eluOptions'),
    clear: document.getElementById('eluClear'),
    status: document.getElementById('eluComboStatus'),
    idPrefix: 'elu-opt',
    itemKey: e => e.key,
    itemLabel: e => e.nom,
    renderItem: eluOptionHtml,
    emptyText: q => `Aucun·e élu·e ne correspond à « ${q} ».`,
    statusText: n => (n ? `${n} élu·e${n > 1 ? 's' : ''} — utilisez les flèches puis Entrée`
      : 'Aucun résultat'),
    // Placeholder parlant : le nombre d'élu·e·s cherchables — on sait ce qu'on
    // interroge (« Rechercher parmi 105 élu·e·s… »).
    placeholder: n => (n ? `Rechercher parmi ${n} élu·e·s…` : 'Rechercher un·e élu·e…'),
    onSelect: key => selectElu(key),
  });

  eluThemeCombo = createCombobox({
    input: document.getElementById('eluTheme'),
    list: document.getElementById('eluThemeOptions'),
    clear: document.getElementById('eluThemeClear'),
    status: document.getElementById('eluThemeStatus'),
    idPrefix: 'elu-theme',
    itemKey: t => t.theme,
    // Le libellé stocké porte des « _ » (voir _thematique_label côté backend) :
    // on les remplace À L'AFFICHAGE ET DANS LE TEXTE CHERCHÉ, sinon taper
    // « marché public » ne trouverait pas « marche_public ».
    itemLabel: t => t.theme.replace(/_/g, ' '),
    renderItem: eluThemeOptionHtml,
    emptyText: q => `Aucune thématique ne correspond à « ${q} ».`,
    statusText: n => (n ? `${n} thématique${n > 1 ? 's' : ''} — utilisez les flèches puis Entrée`
      : 'Aucun résultat'),
    placeholder: n => (n > 1 ? `Filtrer parmi ${n - 1} thématiques…` : 'Filtrer par thématique…'),
    onSelect: onEluThemeSelect,
  });
}

// Une option : la thématique (partie trouvée surlignée) et son nombre
// d'interventions sur la fiche courante.
function eluThemeOptionHtml(t, { id, active, selected, label }) {
  return `<li class="elu-opt${active ? ' elu-opt-active' : ''}" role="option" id="${id}"
      data-key="${escapeHtml(t.theme)}" aria-selected="${selected}">
    <span class="elu-opt-name">${t.tous ? 'Toutes les thématiques' : label}</span>
    <span class="elu-opt-meta"><span class="elu-opt-count">${t.n} ${t.tous ? 'thématiques' : `intervention${t.n > 1 ? 's' : ''}`}</span></span>
  </li>`;
}

// (Re)construit la liste des candidat·e·s selon le filtre de rôle courant et
// garantit une sélection valide.
export function populateElus() {
  if (!elusData || !eluCombo) return;
  const prevKey = eluSelectedKey;
  const list = eluCandidates();
  eluCombo.setItems(list);
  // Présélection : uniquement un lien partagé (?elu=), ou la sélection déjà
  // faite. Aucun repli sur la 1re entrée — ouvrir l'onglet sur la fiche d'une
  // personne prise dans l'ordre alphabétique donnerait à lire une activité que
  // personne n'a demandé à voir. Sans sélection : écran d'accueil.
  let nextKey = null;
  if (pendingEluKey && list.some(e => e.key === pendingEluKey)) {
    nextKey = pendingEluKey;
    pendingEluKey = null;
  } else if (list.some(e => e.key === prevKey)) {
    nextKey = prevKey;
  }
  selectElu(nextKey);
}

// Applique une sélection par clé et charge la fiche ; sans clé, l'onglet
// revient à son écran d'accueil.
function selectElu(key) {
  eluSelectedKey = key;
  const entry = key ? (elusData || []).find(e => e.key === key) : null;
  if (eluCombo) eluCombo.setSelected(entry ? key : null);
  if (entry) loadElu(key);
  else renderEluAccueil();
}

// Écran d'accueil : aucune fiche ouverte, donc aucun filtre à proposer — on
// masque toute la barre de filtres, sinon des puces d'une fiche précédente y
// resteraient affichées sans rien à filtrer.
function renderEluAccueil() {
  currentEluData = null;
  ['eluRoleChips', 'eluTypeFilterChips', 'eluMoreFilters'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.hidden = true;
  });
  const box = document.getElementById('eluResult');
  if (!box) return;
  box.innerHTML = `<div class="elu-accueil">
    <svg class="icon elu-accueil-icon" aria-hidden="true"><use href="#ico-search"/></svg>
    <p class="elu-accueil-titre">Cherchez un·e élu·e</p>
    <p>Par nom ou par prénom — trois lettres suffisent.</p>
  </div>`;
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
  // Comptées, ce que le menu natif ne montrait pas : savoir qu'une thématique
  // ne porte qu'une intervention évite d'y aller pour rien.
  const counts = new Map();
  const compte = it => (it.thematiques || []).forEach(t => counts.set(t, (counts.get(t) || 0) + 1));
  deposeForYear.forEach(compte);
  repondForYear.forEach(compte);
  return [...counts.keys()].sort((a, b) => a.localeCompare(b, 'fr'))
    .map(t => ({ theme: t, n: counts.get(t) }));
}

// (Re)remplit le filtre de thématique pour la fiche courante ; conserve la
// thématique sélectionnée si elle existe encore, sinon "Toutes".
function populateEluThemeSelect(themes) {
  const box = document.getElementById('eluThemeCombo');
  if (!box || !eluThemeCombo) return;
  if (!themes.length) { box.hidden = true; eluThemeFilter = 'all'; eluThemeCombo.setItems([]); return; }
  if (!themes.some(t => t.theme === eluThemeFilter)) eluThemeFilter = 'all';
  box.hidden = false;
  // 1re entrée : revenir à toutes les thématiques. La croix du champ n'efface
  // que la recherche — un filtre doit pouvoir se défaire depuis la liste.
  eluThemeCombo.setItems([{ theme: '', n: themes.length, tous: true }, ...themes]);
  eluThemeCombo.setSelected(eluThemeFilter === 'all' ? '' : eluThemeFilter);
}

function onEluThemeSelect(key) {
  eluThemeFilter = key || 'all';
  if (currentEluData) renderElu(currentEluData);
}

function filterByTheme(items, theme) {
  if (theme === 'all') return items;
  return items.filter(it => (it.thematiques || []).includes(theme));
}

// Ordre d'affichage fixe (pas l'ordre d'apparition dans les données) : même
// ordre que TYPE_BADGE côté utils.js, pour une liste toujours dans le même
// sens quel que soit l'élu·e sélectionné·e.
const TYPE_FILTER_ORDER = ['Point', 'Question orale', 'Demande', 'Motion', 'Débat filmé', 'Question écrite'];
const TYPE_DEBAT = 'Débat filmé';

// « Débat filmé » est une FACETTE, pas un type exclusif — même définition que
// l'onglet Séances (voir hasDebateLink). La plupart des débats filmés sont
// appariés à leur point PV : le point garde alors son type (question orale,
// demande…) et porte le lien « ▶ Voir le débat ». Ne compter que le type
// `video` — les chapitres qu'on n'a PAS su apparier — donnait un chiffre à
// contresens du libellé : 2 pour Georges Verzin, là où 14 de ses interventions
// sont filmées. Conséquence assumée : les puces ne forment plus une partition,
// leur somme peut dépasser le total.
const countOfType = (items, typeLabel) => (typeLabel === TYPE_DEBAT
  ? items.filter(hasDebateLink).length
  : items.filter(it => it.type_label === typeLabel).length);

// Les deux listes de valeurs (année, thématique) vivent dans un <details>
// replié. On le masque s'il n'a rien à offrir, on le déplie d'office quand un
// filtre y est actif, et le résumé rappelle lesquels — un résultat filtré ne
// doit jamais paraître inexpliqué parce que le filtre est replié.
function syncEluMoreFilters() {
  const box = document.getElementById('eluMoreFilters');
  const year = document.getElementById('eluYear');
  // L'enveloppe du combobox, pas le champ : c'est elle qui porte [hidden]
  // (le champ, lui, reste toujours visible à l'intérieur).
  const theme = document.getElementById('eluThemeCombo');
  const actif = document.getElementById('eluMoreActive');
  if (!box || !year || !theme) return;
  box.hidden = year.hidden && theme.hidden;
  const actifs = [];
  if (eluYearFilter !== 'all') actifs.push(eluYearFilter);
  // Affiché comme dans la liste : « marche public », pas « marche_public ».
  if (eluThemeFilter !== 'all') actifs.push(eluThemeFilter.replace(/_/g, ' '));
  if (actif) actif.textContent = actifs.length ? ` · ${actifs.join(' · ')}` : '';
  if (actifs.length) box.open = true;
}

function filterByType(items, types) {
  if (!types.size) return items;
  return items.filter(it => types.has(it.type_label)
    || (types.has(TYPE_DEBAT) && hasDebateLink(it)));
}

// Puce-interrupteur par type présent, dans la barre de filtres
// (#eluTypeFilterChips). '' si aucune action de ce type dans le périmètre
// courant (jamais de puce à 0).
function eluTypeChip(typeLabel, count) {
  if (!count) return '';
  const on = eluTypeSel.has(typeLabel);
  const text = TYPE_COUNT_LABEL[typeLabel](count);
  return `<button type="button" class="elu-chip${on ? ' elu-chip-active' : ''}" aria-pressed="${on}"`
    + ` data-click="onEluChipClick" data-arg="${escapeHtml(typeLabel)}">${escapeHtml(text)}</button>`;
}

// Interrupteur : coche/décoche ce type. Plus aucun coché = tous les types.
export function onEluChipClick(typeLabel) {
  toggle(eluTypeSel, typeLabel);
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
  // co_auteurs est une LISTE de noms. Tolère la chaîne recollée que renvoyait
  // l'ancien backend : le frontend (Vercel) et l'API (Render) se déploient
  // séparément, et un spread sur une chaîne l'écrirait lettre par lettre.
  const co = Array.isArray(it.co_auteurs) ? it.co_auteurs
    : (it.co_auteurs ? [it.co_auteurs] : []);
  const auteurs = listeFr([nom, ...co]);
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
  // Badge = type du POINT auquel on répond (voir registry.py) : montre à
  // quel filtre de puce cette réponse correspond, comme pour un dépôt.
  const badge = renderTypeBadge(it.type_label);
  const sp = it.sp ? `<span class="elu-sp">SP ${it.sp}</span>` : '';
  const link = renderPvPdfLink(it.url);
  const demandeur = renderPersonLine('elu-demandeur', 'Demandé par', it.demandeur);
  const rep = renderPersonLine('elu-rep', 'Répondant·e', nom);
  const tags = renderThemeTags(it.thematiques);
  return `<div class="elu-item">
    <div class="elu-date">${formatDate(it.date)}</div>
    <div class="elu-body">
      ${badge}${sp}
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
  // Une sélection devenue vide de sens en changeant d'élu·e/d'année (sa puce
  // n'est alors plus affichée) laisserait un filtre actif sans interrupteur
  // pour le défaire : on la nettoie. Une réponse en séance porte le type du
  // POINT auquel elle répond (voir registry.py) : comptée au même titre
  // qu'un dépôt de ce type.
  eluTypeSel.forEach(t => {
    if (!countOfType(deposeForYear, t) && !countOfType(repondForYear, t)) eluTypeSel.delete(t);
  });

  // Comptages CROISÉS : chaque groupe de puces compte dans le périmètre défini
  // par l'AUTRE groupe (et non par lui-même), sinon cocher une puce ferait
  // tomber à 0 toutes les autres de sa rangée et le filtre deviendrait un
  // cul-de-sac. Chaque puce annonce donc ce qu'elle ajoute réellement.
  const roleCounts = eluRoleCounts(d.mandats,
    filterByType([...deposeForYear, ...repondForYear], eluTypeSel));
  renderEluRoleChips(roleCounts);
  eluRoleSel.forEach(r => { if (!roleCounts[r]) eluRoleSel.delete(r); });

  const deposeForRole = filterByRole(deposeForYear, d.mandats, eluRoleSel);
  const repondForRole = filterByRole(repondForYear, d.mandats, eluRoleSel);
  const typeFilterChips = document.getElementById('eluTypeFilterChips');
  if (typeFilterChips) {
    const chipsHtml = TYPE_FILTER_ORDER
      .map(t => eluTypeChip(t, countOfType(deposeForRole, t) + countOfType(repondForRole, t))).join('');
    typeFilterChips.innerHTML = chipsHtml;
    typeFilterChips.hidden = !chipsHtml;
  }

  // Thématiques disponibles pour la période affichée — indépendantes du type et
  // de la thématique déjà choisis, sinon le menu se viderait au filtrage.
  populateEluThemeSelect(eluThemes(deposeForRole, repondForRole));
  syncEluMoreFilters();
  const depose = filterByType(filterByTheme(deposeForRole, eluThemeFilter), eluTypeSel);
  const repond = filterByType(filterByTheme(repondForRole, eluThemeFilter), eluTypeSel);
  const roleLabel = formatMandats(d.mandats) || (d.role === 'college'
    ? 'Collège (échevin·e / bourgmestre)'
    : 'Conseiller·ère');

  let html = `<div class="elu-head">
    <div class="elu-name">${escapeHtml(d.nom)}</div>
    <span class="elu-role elu-role-${d.role}">${roleLabel}</span>
  </div>`;

  // Total RECALCULÉ à chaque bascule d'interrupteur, avec sa décomposition —
  // c'est le chiffre que l'on vient chercher, il doit suivre les puces.
  const total = depose.length + repond.length;
  if (total) {
    const detail = [];
    if (depose.length) detail.push(`${depose.length} déposée${depose.length > 1 ? 's' : ''}`);
    if (repond.length) detail.push(`${repond.length} réponse${repond.length > 1 ? 's' : ''} en séance`);
    html += `<div class="elu-summary"><strong>${total}</strong> action${total > 1 ? 's' : ''}`
      + (detail.length > 1 ? ` <span class="elu-summary-detail">${detail.join(' · ')}</span>` : '')
      + '</div>';
  }
  if (depose.length) {
    html += `<div class="elu-list">${groupByYear(depose, it => eluDeposeRow(it, d.nom))}</div>`;
  }

  if (repond.length) {
    // Repliées quand elles accompagnent les dépôts (activité secondaire d'un·e
    // conseiller·ère) ; dépliées dès qu'elles sont ce qu'on est venu voir.
    html += `<details class="elu-repond"${depose.length ? '' : ' open'}>
      <summary><strong>${repond.length}</strong> réponse${repond.length > 1 ? 's' : ''} en séance <span class="elu-repond-hint">(activité de Collège)</span></summary>
      <div class="elu-list">${groupByYear(repond, it => eluRepondRow(it, d.nom))}</div>
    </details>`;
  }

  if (!depose.length && !repond.length) {
    const filters = [];
    const listeFr = xs => xs.length > 1 ? `${xs.slice(0, -1).join(', ')} ou ${xs[xs.length - 1]}` : xs[0];
    if (eluRoleSel.size) filters.push(`en tant que ${listeFr([...eluRoleSel].map(r => ROLE_CHIP_LABEL[r]))}`);
    if (eluTypeSel.size) filters.push(`de type ${listeFr([...eluTypeSel].map(t => `« ${t} »`))}`);
    if (eluThemeFilter !== 'all') filters.push(`sur la thématique « ${eluThemeFilter} »`);
    if (eluYearFilter !== 'all') filters.push(`en ${eluYearFilter}`);
    html += filters.length
      ? `<div class="trend-empty">Aucune intervention ${filters.join(' ')}.</div>`
      : `<div class="trend-empty">Aucune intervention identifiée.</div>`;
  }

  html += `<p class="elu-note">Agrégation déterministe depuis les procès-verbaux (2012–2026) et le chapitrage des séances filmées. Attribution : question orale → 1er intervenant ; demande → auteur du titre ou 1er intervenant ; motion → auteur nommé dans le titre. Liste indicative, non exhaustive des prises de parole en débat.</p>`;

  box.innerHTML = html;
}
