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
let eluQuery = '';          // texte tapé dans le champ de recherche ('' = liste complète)
let eluActiveKey = null;    // option mise en avant au clavier (aria-activedescendant)
let eluMatches = [];        // dernier résultat de recherche affiché (ordre de la liste)

// Présélection appliquée depuis un lien partagé (?tab=elus&elu=…), voir handleDeepLink.
export function setPendingEluKey(key) { pendingEluKey = key; }

export async function loadElus() {
  if (elusLoaded) return;
  const input = document.getElementById('eluSearch');
  if (!input) return;
  try {
    const res = await fetch(API_URL + '/elus');
    if (!res.ok) throw new Error('Erreur ' + res.status);
    elusData = (await res.json()).elus || [];
    elusData.forEach(decorateElu);
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

// ── SÉLECTEUR D'ÉLU·E : COMBOBOX AVEC RECHERCHE ─────────────────────────────
// Le <select> natif ne tenait plus à 105 élu·e·s : la frappe rapide du
// navigateur ne matche que le DÉBUT du libellé affiché — donc le PRÉNOM
// (« Georges Verzin ») — alors que la liste est triée par NOM DE FAMILLE (clé
// backend, particules gérées : « Yvan de Beauffort » → clé « beauffort »).
// Taper « verzin » ne trouvait rien, l'ordre paraissait aléatoire, et il
// fallait choisir à l'aveugle (ni rôle ni volume d'activité visibles).
// Ici : recherche libre sur le prénom ET le nom, insensible aux accents et à
// la casse, avec rôle et compteurs affichés avant de choisir.

// Sans accents ni casse : « Amrani » / « amrani » / « Ammi » ↔ « ammi ».
// (NFD décompose « é » en « e » + diacritique combinant, qu'on retire.)
const _deburr = s => String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
// Mots d'un nom : « Mohamed el Arnouki » → ['mohamed','el','arnouki'] ; les
// apostrophes et traits d'union séparent aussi (« Ben-Addi », « d'Hondt »).
const _wordsOf = s => _deburr(s).split(/[^a-z0-9]+/).filter(Boolean);

// Champs de recherche pré-calculés une fois pour toutes (105 entrées × chaque
// frappe, autant ne pas re-normaliser à chaque fois).
function decorateElu(e) {
  e._key = _deburr(e.key);
  e._flat = _deburr(e.nom);
  e._words = [..._wordsOf(e.nom), e._key];
}

// Pertinence d'un mot de la requête (le plus petit gagne) : nom de famille
// d'abord — c'est ce qu'on tape le plus souvent — puis n'importe quel mot du
// nom, puis une correspondance au milieu d'un mot. null = ne matche pas.
const R_KEY = 0, R_WORD = 1, R_INSIDE = 2;
function tokenRank(tok, e) {
  if (e._key.startsWith(tok)) return R_KEY;
  if (e._words.some(w => w.startsWith(tok))) return R_WORD;
  if (e._flat.includes(tok)) return R_INSIDE;
  return null;
}

// Élu·e·s candidat·e·s selon le filtre de rôle, triés par nom de famille (la
// clé backend), pas par nombre d'interventions : liste stable et parcourable.
function eluCandidates() {
  return (elusData || [])
    .filter(e => eluRoleFilter === 'all' || e.role === eluRoleFilter)
    .slice()
    .sort((a, b) => a.key.localeCompare(b.key, 'fr'));
}

// Recherche : TOUS les mots de la requête doivent matcher (« verzin georges »
// comme « georges verzin »), le classement retenant le pire rang de chacun.
function searchElus(query, pool = eluCandidates()) {
  const tokens = _wordsOf(query);
  if (!tokens.length) return pool;
  const scored = [];
  for (const e of pool) {
    let worst = R_KEY;
    let ok = true;
    for (const tok of tokens) {
      const r = tokenRank(tok, e);
      if (r === null) { ok = false; break; }
      if (r > worst) worst = r;
    }
    if (ok) scored.push([worst, e]);
  }
  return scored
    .sort((a, b) => a[0] - b[0] || a[1].key.localeCompare(b[1].key, 'fr'))
    .map(x => x[1]);
}

// Surligne la partie trouvée, mot par mot. Découpe en conservant les
// séparateurs pour réécrire le nom à l'identique ; normalisation NFC d'abord,
// pour que les index de `_deburr` (é→e, ç→c : 1 caractère → 1 caractère)
// restent alignés sur la source qu'on découpe.
function highlightName(nom, tokens) {
  const parts = String(nom).normalize('NFC').split(/([^\p{L}\p{N}]+)/u);
  if (!tokens.length) return parts.map(escapeHtml).join('');
  return parts.map(src => {
    const norm = _deburr(src);
    if (!norm) return escapeHtml(src);
    // Début de mot : on surligne le plus long préfixe trouvé.
    let pref = 0;
    tokens.forEach(t => { if (norm.startsWith(t) && t.length > pref) pref = t.length; });
    if (pref) return `<mark>${escapeHtml(src.slice(0, pref))}</mark>${escapeHtml(src.slice(pref))}`;
    for (const t of tokens) {
      const i = norm.indexOf(t);
      if (i >= 0) {
        return escapeHtml(src.slice(0, i)) + `<mark>${escapeHtml(src.slice(i, i + t.length))}</mark>`
          + escapeHtml(src.slice(i + t.length));
      }
    }
    return escapeHtml(src);
  }).join('');
}

const ROLE_LABEL = { college: 'Collège', conseiller: 'Conseiller·ère' };
// Même rôle, formulé pour une phrase (« … parmi les membres du Collège »).
const ROLE_LABEL_PARMI = { college: 'membres du Collège', conseiller: 'conseiller·ère·s' };

// Une option : nom (partie trouvée surlignée) + rôle + volume d'activité —
// de quoi reconnaître la bonne personne SANS ouvrir sa fiche (homonymes,
// élu·e·s d'une seule législature…).
function eluOptionHtml(e, tokens, i) {
  const counts = [];
  if (e.depose) counts.push(`${e.depose} déposée${e.depose > 1 ? 's' : ''}`);
  if (e.repond) counts.push(`${e.repond} réponse${e.repond > 1 ? 's' : ''}`);
  const cls = 'elu-opt' + (e.key === eluActiveKey ? ' elu-opt-active' : '');
  return `<li class="${cls}" role="option" id="elu-opt-${i}" data-key="${escapeHtml(e.key)}"
      aria-selected="${e.key === eluSelectedKey}">
    <span class="elu-opt-name">${highlightName(e.nom, tokens)}</span>
    <span class="elu-opt-meta">
      <span class="elu-opt-role elu-opt-role-${e.role}">${ROLE_LABEL[e.role] || e.role}</span>
      ${counts.length ? `<span class="elu-opt-count">${counts.join(' · ')}</span>` : ''}
    </span>
  </li>`;
}

// (Re)dessine la liste déroulante pour la requête courante. `resetActive` :
// la frappe repositionne la mise en avant sur le 1er résultat (Entrée le
// choisit directement) ; la navigation au clavier la conserve.
function renderEluOptions(resetActive = false) {
  const list = document.getElementById('eluOptions');
  if (!list || !elusData) return;
  const tokens = _wordsOf(eluQuery);
  eluMatches = searchElus(eluQuery);
  if (resetActive || !eluMatches.some(e => e.key === eluActiveKey)) {
    eluActiveKey = eluMatches.length
      ? (eluMatches.some(e => e.key === eluSelectedKey) && !tokens.length ? eluSelectedKey : eluMatches[0].key)
      : null;
  }
  if (eluMatches.length) {
    list.innerHTML = eluMatches.map((e, i) => eluOptionHtml(e, tokens, i)).join('');
  } else {
    // Aucun résultat : si le filtre de rôle est ce qui masque la personne
    // cherchée, on le dit et on propose de l'élargir — plutôt qu'un cul-de-sac.
    const ailleurs = eluRoleFilter !== 'all' && searchElus(eluQuery, elusData).length;
    list.innerHTML = `<li class="elu-opt-empty" role="presentation">
      Aucun·e élu·e ne correspond à « ${escapeHtml(eluQuery)} »${eluRoleFilter !== 'all' ? ` parmi les ${escapeHtml(ROLE_LABEL_PARMI[eluRoleFilter])}` : ''}.
      ${ailleurs ? `<button type="button" class="elu-opt-allroles" data-allroles="1">Chercher parmi tous les rôles (${ailleurs})</button>` : ''}
    </li>`;
  }
  syncEluActiveDescendant();
  const status = document.getElementById('eluComboStatus');
  if (status) {
    status.textContent = eluMatches.length
      ? `${eluMatches.length} élu·e${eluMatches.length > 1 ? 's' : ''} — utilisez les flèches puis Entrée`
      : 'Aucun résultat';
  }
}

// Reflète l'option mise en avant : attribut ARIA sur le champ + défilement
// pour la garder visible (liste de 105 entrées, 8 visibles à la fois).
function syncEluActiveDescendant() {
  const input = document.getElementById('eluSearch');
  const list = document.getElementById('eluOptions');
  if (!input || !list) return;
  const i = eluMatches.findIndex(e => e.key === eluActiveKey);
  input.setAttribute('aria-activedescendant', i >= 0 ? `elu-opt-${i}` : '');
  const el = i >= 0 ? list.children[i] : null;
  if (el && !list.hidden) scrollOptionIntoView(list, el);
}

// Défilement calculé À LA MAIN dans la liste, jamais scrollIntoView : celui-ci
// remonte la chaîne des ancêtres et fait sauter la PAGE quand la liste est
// près du bas de l'écran (la liste est `position:absolute`, donc l'offsetParent
// de ses options — offsetTop est bien relatif à elle).
function scrollOptionIntoView(list, el) {
  const haut = el.offsetTop;
  const bas = haut + el.offsetHeight;
  if (haut < list.scrollTop) list.scrollTop = haut;
  else if (bas > list.scrollTop + list.clientHeight) list.scrollTop = bas - list.clientHeight;
}

function openEluList() {
  const input = document.getElementById('eluSearch');
  const list = document.getElementById('eluOptions');
  // `elusData` absent = /elus pas encore répondu : rien à dérouler (sinon une
  // boîte vide s'ouvre le temps du chargement).
  if (!input || !list || input.disabled || !elusData) return;
  list.hidden = false;
  input.setAttribute('aria-expanded', 'true');
}

// Referme et remet le nom sélectionné dans le champ : jamais de recherche
// partielle laissée à l'écran alors qu'une autre fiche est affichée.
function closeEluList() {
  const input = document.getElementById('eluSearch');
  const list = document.getElementById('eluOptions');
  if (!input || !list) return;
  list.hidden = true;
  input.setAttribute('aria-expanded', 'false');
  input.setAttribute('aria-activedescendant', '');
  eluQuery = '';
  syncEluInput();
}

// Le champ affiche, au repos, l'élu·e dont la fiche est ouverte.
function syncEluInput() {
  const input = document.getElementById('eluSearch');
  if (!input) return;
  const entry = (elusData || []).find(e => e.key === eluSelectedKey);
  input.value = entry ? entry.nom : '';
  const clear = document.getElementById('eluClear');
  if (clear) clear.hidden = !input.value;
}

// Placeholder parlant : le nombre d'élu·e·s cherchables (variable avec le
// filtre de rôle) — l'utilisateur sait ce qu'il interroge.
function syncEluPlaceholder() {
  const input = document.getElementById('eluSearch');
  if (!input || input.disabled) return;
  const n = eluCandidates().length;
  input.placeholder = n ? `Rechercher parmi ${n} élu·e·s…` : 'Rechercher un·e élu·e…';
}

// (Re)construit la liste des candidat·e·s selon le filtre de rôle courant et
// garantit une sélection valide.
export function populateElus() {
  if (!elusData) return;
  const prevKey = eluSelectedKey;
  const list = eluCandidates();
  syncEluPlaceholder();
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
  selectElu(nextKey);
}

// Applique une sélection par clé et charge la fiche.
function selectElu(key) {
  eluSelectedKey = key;
  const entry = key ? (elusData || []).find(e => e.key === key) : null;
  syncEluInput();
  if (entry) {
    loadElu(key);
  } else {
    document.getElementById('eluResult').innerHTML = '';
    document.getElementById('eluYear').hidden = true;
  }
}

// ── Câblage du combobox (écouteurs directs : éléments statiques, et une CSP
// sans 'unsafe-inline' interdit les attributs onXXX — voir delegate.js). ──
export function initEluCombo() {
  const input = document.getElementById('eluSearch');
  const list = document.getElementById('eluOptions');
  const clear = document.getElementById('eluClear');
  if (!input || !list) return;

  input.addEventListener('input', () => {
    eluQuery = input.value;
    if (clear) clear.hidden = !input.value;
    openEluList();
    renderEluOptions(true);
  });

  // Prise de focus : on montre TOUTE la liste (le champ affiche un nom, ce
  // n'est pas une recherche) et on sélectionne le texte — la 1re frappe le
  // remplace, sans avoir à effacer.
  input.addEventListener('focus', () => {
    eluQuery = '';
    input.select();
    openEluList();
    renderEluOptions(true);
  });

  input.addEventListener('keydown', onEluSearchKeydown);

  // mousedown : on garde le focus dans le champ (sinon le blur ferme la liste
  // avant que le click n'atteigne l'option).
  list.addEventListener('mousedown', ev => ev.preventDefault());
  list.addEventListener('click', ev => {
    if (ev.target.closest('[data-allroles]')) {
      eluRoleFilter = 'all';
      renderEluRoleChips();
      syncEluPlaceholder();
      renderEluOptions(true);
      return;
    }
    const li = ev.target.closest('.elu-opt');
    if (!li) return;
    selectElu(li.dataset.key);
    closeEluList();
    input.blur();
  });

  if (clear) {
    clear.addEventListener('mousedown', ev => ev.preventDefault());
    clear.addEventListener('click', () => {
      input.value = '';
      eluQuery = '';
      clear.hidden = true;
      input.focus();
      openEluList();
      renderEluOptions(true);
    });
  }

  // Clic à l'extérieur / passage à un autre champ : on referme proprement.
  input.addEventListener('blur', () => closeEluList());
}

function onEluSearchKeydown(ev) {
  const list = document.getElementById('eluOptions');
  if (!list) return;
  const open = !list.hidden;
  switch (ev.key) {
    case 'ArrowDown':
    case 'ArrowUp':
      ev.preventDefault();
      if (!open) { openEluList(); renderEluOptions(true); return; }
      moveEluActive(ev.key === 'ArrowDown' ? 1 : -1);
      return;
    case 'Home':
    case 'End':
      if (!open || !eluMatches.length) return;
      ev.preventDefault();
      eluActiveKey = (ev.key === 'Home' ? eluMatches[0] : eluMatches[eluMatches.length - 1]).key;
      renderEluOptions();
      return;
    case 'Enter':
      if (!open || !eluActiveKey) return;
      ev.preventDefault();
      selectElu(eluActiveKey);
      closeEluList();
      return;
    case 'Escape':
      if (!open) return;
      ev.preventDefault();
      closeEluList();
      return;
    default:
  }
}

function moveEluActive(delta) {
  if (!eluMatches.length) return;
  const i = eluMatches.findIndex(e => e.key === eluActiveKey);
  const next = i < 0 ? (delta > 0 ? 0 : eluMatches.length - 1)
    : (i + delta + eluMatches.length) % eluMatches.length;   // boucle haut ↔ bas
  eluActiveKey = eluMatches[next].key;
  renderEluOptions();
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

// Puce CLIQUABLE par type présent — dans la barre de filtres (#eluTypeFilterChips),
// même emplacement/logique que les puces de type de l'onglet Séances. '' si
// aucune intervention de ce type cette année (jamais de puce à 0).
function eluTypeChip(typeLabel, count) {
  if (!count) return '';
  const active = eluTypeFilter === typeLabel ? ' elu-chip-active' : '';
  const text = TYPE_COUNT_LABEL[typeLabel](count);
  return `<button type="button" class="elu-chip${active}" data-click="onEluChipClick" data-arg="${escapeHtml(typeLabel)}">${escapeHtml(text)}</button>`;
}

// Pastille NON cliquable par type présent — sous le nom de l'élu·e, simple
// rappel du profil d'activité (le filtre lui-même vit dans la barre de
// filtres, voir eluTypeChip ci-dessus/#eluTypeFilterChips). Même décompte
// (année en cours), juste un autre rendu : <span>, pas <button>.
function eluTypeCountBadge(typeLabel, count) {
  if (!count) return '';
  const text = TYPE_COUNT_LABEL[typeLabel](count);
  return `<span class="elu-chip-static">${escapeHtml(text)}</span>`;
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
  // Décompte par type pour l'année courante — une seule fois, réutilisé pour
  // le filtre cliquable (barre de filtres) ET le rappel non cliquable
  // (sous le nom) : même chiffre, deux rendus différents.
  const typeCounts = TYPE_FILTER_ORDER.map(t => [t, deposeForYear.filter(it => it.type_label === t).length]);
  const typeFilterChips = document.getElementById('eluTypeFilterChips');
  if (typeFilterChips) {
    const chipsHtml = typeCounts.map(([t, n]) => eluTypeChip(t, n)).join('');
    typeFilterChips.innerHTML = chipsHtml;
    typeFilterChips.hidden = !chipsHtml;
  }
  const badges = typeCounts.map(([t, n]) => eluTypeCountBadge(t, n)).join('');

  let html = `<div class="elu-head">
    <div class="elu-name">${escapeHtml(d.nom)}</div>
    <span class="elu-role elu-role-${d.role}">${roleLabel}</span>
  </div>`;

  if (deposeForYear.length) {
    html += `<div class="elu-summary"><strong>${depose.length}</strong> intervention${depose.length > 1 ? 's' : ''} déposée${depose.length > 1 ? 's' : ''}</div>`;
    if (badges) html += `<div class="elu-chips">${badges}</div>`;
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
