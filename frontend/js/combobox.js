// ── COMBOBOX AVEC RECHERCHE (composant partagé) ─────────────────────────────
// Un menu déroulant natif ne tient pas au-delà de quelques dizaines d'entrées :
// sa frappe rapide ne cherche que le DÉBUT du libellé affiché — donc le PRÉNOM
// d'une personne — alors que ces listes se pensent par NOM DE FAMILLE. Taper
// « verzin » n'y trouvait rien.
//
// Ce composant le remplace partout où l'on choisit dans une longue liste :
// recherche libre sur tous les mots du libellé, insensible aux accents et à la
// casse, navigation au clavier complète, et rendu d'option libre (l'appelant
// décide ce qu'on lit avant de choisir : rôle, décompte…).
//
// Quatre usages : le choix d'un·e élu·e (105 personnes), le filtre
// « intervenant·e » d'une séance (jusqu'à 115), et les deux filtres par
// thématique — celui d'une séance (113 en médiane, jusqu'à 252) et celui d'une
// fiche d'élu·e (jusqu'à 232). Les trois FILTRES portent une entrée « tou·te·s
// / toutes » de clé vide en tête, ce qui change ce que fait leur croix : voir
// le gestionnaire de `clear`.
import { escapeHtml } from './utils.js';

// Sans accents ni casse : « Amrani » / « amrani » ; « Köse » / « kose ».
// (NFD décompose « é » en « e » + diacritique combinant, qu'on retire.)
export const deburr = s => String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
// Mots d'un libellé : « Mohamed el Arnouki » → ['mohamed','el','arnouki'] ; les
// apostrophes et traits d'union séparent aussi (« Ben-Addi », « d'Hondt »).
export const wordsOf = s => deburr(s).split(/[^a-z0-9]+/).filter(Boolean);

// Surligne la partie trouvée, mot par mot. Découpe en conservant les
// séparateurs pour réécrire le libellé à l'identique ; normalisation NFC
// d'abord, pour que les index de `deburr` (é→e, ç→c : 1 caractère → 1
// caractère) restent alignés sur la source qu'on découpe.
export function highlight(label, tokens) {
  const parts = String(label).normalize('NFC').split(/([^\p{L}\p{N}]+)/u);
  if (!tokens.length) return parts.map(escapeHtml).join('');
  return parts.map(src => {
    const norm = deburr(src);
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

// Pertinence d'un mot de la requête (le plus petit gagne) : la CLÉ d'abord
// (pour une personne, son nom de famille — c'est ce qu'on tape le plus
// souvent), puis n'importe quel mot du libellé, puis une correspondance au
// milieu d'un mot. null = ne matche pas.
const R_KEY = 0, R_WORD = 1, R_INSIDE = 2;
function tokenRank(tok, entry) {
  if (entry._key.startsWith(tok)) return R_KEY;
  if (entry._words.some(w => w.startsWith(tok))) return R_WORD;
  if (entry._flat.includes(tok)) return R_INSIDE;
  return null;
}

/**
 * Câble un combobox sur un trio (champ, liste, croix d'effacement).
 *
 * L'appelant fournit ses données via `setItems`, dit comment lire une entrée
 * (`itemKey`/`itemLabel`) et comment la dessiner (`renderItem`). Le composant
 * garde pour lui la recherche, le clavier, l'ARIA et le cycle ouvrir/fermer.
 */
export function createCombobox({
  input, list, clear, status, idPrefix,
  itemKey, itemLabel,
  renderItem,
  emptyText = q => `Aucun résultat pour « ${q} »`,
  statusText = n => (n ? `${n} résultat${n > 1 ? 's' : ''} — utilisez les flèches puis Entrée` : 'Aucun résultat'),
  placeholder = null,
  onSelect = () => {},
}) {
  if (!input || !list) return null;

  let items = [];
  let matches = [];
  let query = '';
  let activeKey = null;
  let selectedKey = null;

  // Champs de recherche pré-calculés une fois par jeu de données (N entrées ×
  // chaque frappe, autant ne pas re-normaliser à chaque fois).
  const decorate = it => {
    const key = String(itemKey(it) ?? '');
    const label = String(itemLabel(it) ?? '');
    return { it, key, label, _key: deburr(key), _flat: deburr(label), _words: [...wordsOf(label), deburr(key)] };
  };

  // Recherche : TOUS les mots de la requête doivent matcher (« verzin georges »
  // comme « georges verzin »), le classement retenant le pire rang de chacun.
  function search() {
    const tokens = wordsOf(query);
    if (!tokens.length) return items;
    const scored = [];
    for (const e of items) {
      let worst = R_KEY;
      let ok = true;
      for (const tok of tokens) {
        const r = tokenRank(tok, e);
        if (r === null) { ok = false; break; }
        if (r > worst) worst = r;
      }
      if (ok) scored.push([worst, e]);
    }
    return scored.sort((a, b) => a[0] - b[0]).map(x => x[1]);
  }

  // Défilement calculé À LA MAIN dans la liste, jamais scrollIntoView : celui-ci
  // remonte la chaîne des ancêtres et fait sauter la PAGE quand la liste est
  // près du bas de l'écran (la liste est `position:absolute`, donc
  // l'offsetParent de ses options — offsetTop est bien relatif à elle).
  function scrollOptionIntoView(el) {
    const haut = el.offsetTop;
    const bas = haut + el.offsetHeight;
    if (haut < list.scrollTop) list.scrollTop = haut;
    else if (bas > list.scrollTop + list.clientHeight) list.scrollTop = bas - list.clientHeight;
  }

  // Reflète l'option mise en avant : attribut ARIA sur le champ + défilement
  // pour la garder visible (la liste en montre 8 à la fois).
  function syncActiveDescendant() {
    const i = matches.findIndex(e => e.key === activeKey);
    input.setAttribute('aria-activedescendant', i >= 0 ? `${idPrefix}-${i}` : '');
    const el = i >= 0 ? list.children[i] : null;
    if (el && !list.hidden) scrollOptionIntoView(el);
  }

  // (Re)dessine la liste pour la requête courante. `resetActive` : la frappe
  // repositionne la mise en avant sur le 1er résultat (Entrée le choisit
  // directement) ; la navigation au clavier la conserve.
  function render(resetActive = false) {
    const tokens = wordsOf(query);
    matches = search();
    if (resetActive || !matches.some(e => e.key === activeKey)) {
      activeKey = matches.length
        ? (matches.some(e => e.key === selectedKey) && !tokens.length ? selectedKey : matches[0].key)
        : null;
    }
    list.innerHTML = matches.length
      ? matches.map((e, i) => renderItem(e.it, {
        tokens,
        id: `${idPrefix}-${i}`,
        active: e.key === activeKey,
        selected: e.key === selectedKey,
        label: highlight(e.label, tokens),
      })).join('')
      : `<li class="elu-opt-empty" role="presentation">${escapeHtml(emptyText(query))}</li>`;
    syncActiveDescendant();
    if (status) status.textContent = statusText(matches.length);
  }

  function open() {
    // Aucune donnée = rien à dérouler (sinon une boîte vide s'ouvre le temps
    // du chargement).
    if (input.disabled || !items.length) return;
    list.hidden = false;
    input.setAttribute('aria-expanded', 'true');
  }

  // Referme et remet le libellé sélectionné dans le champ : jamais de recherche
  // partielle laissée à l'écran alors qu'autre chose est affiché.
  function close() {
    list.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-activedescendant', '');
    query = '';
    syncInput();
  }

  // Le champ affiche, au repos, le libellé de la sélection courante.
  function syncInput() {
    const entry = items.find(e => e.key === selectedKey);
    input.value = entry ? entry.label : '';
    if (clear) clear.hidden = !input.value;
  }

  function select(key) {
    selectedKey = key;
    const entry = items.find(e => e.key === key);
    syncInput();
    onSelect(key, entry ? entry.it : null);
  }

  function moveActive(delta) {
    if (!matches.length) return;
    const i = matches.findIndex(e => e.key === activeKey);
    const next = i < 0 ? (delta > 0 ? 0 : matches.length - 1)
      : (i + delta + matches.length) % matches.length;   // boucle haut ↔ bas
    activeKey = matches[next].key;
    render();
  }

  function onKeydown(ev) {
    const isOpen = !list.hidden;
    switch (ev.key) {
      case 'ArrowDown':
      case 'ArrowUp':
        ev.preventDefault();
        if (!isOpen) { open(); render(true); return; }
        moveActive(ev.key === 'ArrowDown' ? 1 : -1);
        return;
      case 'Home':
      case 'End':
        if (!isOpen || !matches.length) return;
        ev.preventDefault();
        activeKey = (ev.key === 'Home' ? matches[0] : matches[matches.length - 1]).key;
        render();
        return;
      case 'Enter':
        if (!isOpen || activeKey === null) return;
        ev.preventDefault();
        select(activeKey);
        close();
        return;
      case 'Escape':
        if (!isOpen) return;
        ev.preventDefault();
        close();
        return;
      default:
    }
  }

  // Écouteurs directs : éléments statiques, et une CSP sans 'unsafe-inline'
  // interdit les attributs onXXX (voir delegate.js).
  input.addEventListener('input', () => {
    query = input.value;
    if (clear) clear.hidden = !input.value;
    open();
    render(true);
  });

  // Prise de focus : on montre TOUTE la liste (le champ affiche un libellé, ce
  // n'est pas une recherche) et on sélectionne le texte — la 1re frappe le
  // remplace, sans avoir à effacer.
  input.addEventListener('focus', () => {
    query = '';
    input.select();
    open();
    render(true);
  });

  // Clic sur un champ DÉJÀ focalisé : `focus` ne se redéclenche pas (cas
  // typique après une sélection à Entrée, qui referme la liste sans rendre le
  // focus) — sans cela, recliquer sur le champ ne rouvrait rien.
  input.addEventListener('click', () => {
    if (!list.hidden) return;
    query = '';
    input.select();
    open();
    render(true);
  });

  input.addEventListener('keydown', onKeydown);
  input.addEventListener('blur', () => close());

  // mousedown : on garde le focus dans le champ (sinon le blur ferme la liste
  // avant que le click n'atteigne l'option).
  list.addEventListener('mousedown', ev => ev.preventDefault());
  list.addEventListener('click', ev => {
    const li = ev.target.closest('[data-key]');
    if (!li) return;
    select(li.dataset.key);
    close();
    input.blur();
  });

  if (clear) {
    clear.addEventListener('mousedown', ev => ev.preventDefault());
    clear.addEventListener('click', () => {
      // Les listes de FILTRAGE portent une entrée « tou·te·s / toutes » en
      // tête, de clé vide : la croix la sélectionne, autrement dit elle DÉFAIT
      // le filtre. N'effacer que le texte laissait la liste filtrée derrière
      // un champ vide — le geste promettait plus qu'il ne faisait.
      // Les listes de SÉLECTION (le choix d'un·e élu·e) n'ont pas cette entrée :
      // là, la croix se contente de vider la recherche, sans désélectionner.
      const defait = items.some(e => e.key === '');
      if (defait && selectedKey !== '') select('');
      input.value = '';
      query = '';
      clear.hidden = true;
      input.focus();
      open();
      render(true);
    });
  }

  return {
    /** Remplace le jeu de données (et rafraîchit champ + placeholder). */
    setItems(next) {
      items = (next || []).map(decorate);
      if (placeholder && !input.disabled) input.placeholder = placeholder(items.length);
      syncInput();
    },
    /** Sélection courante, SANS déclencher onSelect (pilotage externe). */
    setSelected(key) {
      selectedKey = key;
      syncInput();
    },
    selected: () => selectedKey,
    close,
  };
}
