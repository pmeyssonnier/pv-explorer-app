// ── HELPERS GÉNÉRIQUES (formatage, échappement, Markdown minimal) ──
export function escapeHtml(t) {
  return String(t).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

export function formatDate(iso) {
  if (!iso) return '?';
  const parts = iso.split('-');
  if (parts.length === 3) return `${parts[2]}/${parts[1]}/${parts[0]}`;
  return iso;
}

// Entier groupé « # ### ##0 » avec des espaces (déterministe, indépendant de la
// locale du navigateur — évite « 8718 » quand fr-BE n'est pas disponible).
export const fmtInt = n => Math.round(n || 0).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
// Montant « # ### ##0 € ».
export const fmtMontant = n => fmtInt(n) + ' €';
export function fmtEUR(n) { return fmtMontant(n); }   // même format « # ### ##0 € » que les KPI

// Montant compact pour le KPI « Montants engagés » (onglet Statistiques) :
// « 1 125,5 Mio € » au-delà d'1 million, sinon « 817,1 k € » — le montant
// brut (fmtMontant/fmtEUR, « # ### ##0 € ») reste utilisé partout ailleurs
// (liste des PV, sources, évolution par thème).
export function fmtMontantCompact(n) {
  const v = n || 0;
  return Math.abs(v) > 1_000_000
    ? _fmtDecimal(v / 1_000_000) + ' Mio €'
    : _fmtDecimal(v / 1_000) + ' k €';
}
function _fmtDecimal(n) {
  const [intPart, decPart] = n.toFixed(1).split('.');
  return fmtInt(Number(intPart)) + ',' + decPart;
}

// Rendu Markdown minimal et SÛR (échappe le texte d'abord, puis n'insère que nos
// propres balises) : tableaux, titres, gras/italique, code, listes, séparateurs.
export function renderMarkdown(src) {
  const inline = s => escapeHtml(s)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*(?!\s)([^*]+?)\*/g, '$1<em>$2</em>')
    .replace(/`([^`]+?)`/g, '<code>$1</code>');
  const lines = String(src || '').replace(/\r\n/g, '\n').split('\n');
  const out = [];
  let i = 0;
  const isRow = l => /^\s*\|.*\|\s*$/.test(l);
  const cells = l => l.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
  while (i < lines.length) {
    const l = lines[i];
    if (isRow(l) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      const head = cells(l); i += 2; const rows = [];
      while (i < lines.length && isRow(lines[i])) { rows.push(cells(lines[i])); i++; }
      const thead = '<tr>' + head.map(h => `<th>${inline(h)}</th>`).join('') + '</tr>';
      const tbody = rows.map(r => '<tr>' + head.map((_, j) => `<td>${inline(r[j] || '')}</td>`).join('') + '</tr>').join('');
      out.push(`<div class="md-tablewrap"><table class="md-table"><thead>${thead}</thead><tbody>${tbody}</tbody></table></div>`);
      continue;
    }
    const mh = l.match(/^(#{1,6})\s+(.*)$/);
    if (mh) { out.push(`<h4 class="md-h">${inline(mh[2])}</h4>`); i++; continue; }
    if (/^\s*---+\s*$/.test(l)) { out.push('<hr class="md-hr">'); i++; continue; }
    if (/^\s*[-*]\s+/.test(l)) {
      const items = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) { items.push(`<li>${inline(lines[i].replace(/^\s*[-*]\s+/, ''))}</li>`); i++; }
      out.push(`<ul class="md-ul">${items.join('')}</ul>`); continue;
    }
    if (/^\s*$/.test(l)) { i++; continue; }
    const para = [];
    while (i < lines.length && !/^\s*$/.test(lines[i]) && !isRow(lines[i])
           && !/^#{1,6}\s/.test(lines[i]) && !/^\s*[-*]\s+/.test(lines[i]) && !/^\s*---+\s*$/.test(lines[i])) {
      para.push(inline(lines[i])); i++;
    }
    if (para.length) out.push(`<p>${para.join('<br>')}</p>`);
  }
  return out.join('');
}

// ── Libellés partagés « type de point » (badge + qui l'a soulevé) — utilisés
// à la fois par la vue « Par élu·e » et la vue « Séances ». ──
export const TYPE_BADGE = {
  'Question orale': 'b-q',
  'Demande': 'b-d',
  'Motion': 'b-m',
  'Débat filmé': 'b-v',
  'Question écrite': 'b-e',
};

// Terme utilisé pour désigner qui a soulevé le point, selon son type — affiché
// au-dessus du/de la répondant·e pour que chaque ligne se comprenne seule
// (ex. sur une capture d'écran, sans le contexte de la page).
export const TYPE_ACTOR_LABEL = {
  // Un point délibératif (approbation, règlement, convention…) est déposé par
  // le Collège : personne ne l'a « demandé ». Les noms qui y figurent sont
  // celles et ceux qui SONT INTERVENU·E·S au débat (voir attribution.py,
  // _MANUAL_AUTHOR_OVERRIDES) — les appeler « Auteur·e » était faux.
  'Point': 'Intervenant·e·s',
  'Question orale': 'Auteur·e de la question',
  'Demande': 'Demandeur·se',
  'Motion': 'Auteur·e de la motion',
  'Débat filmé': 'Intervenant·e',
  'Question écrite': 'Auteur·e de la question',
};

// Texte pluriel « N question(s) orale(s) » pour un décompte par type — utilisé
// par les puces cliquables de l'onglet Par élu·e (résumé d'activité) et de
// l'onglet Statistiques (légende du graphe « Activité citoyenne »).
export const TYPE_COUNT_LABEL = {
  'Point': n => `${n} point${n > 1 ? 's' : ''} délibératif${n > 1 ? 's' : ''}`,
  'Question orale': n => `${n} question${n > 1 ? 's' : ''} orale${n > 1 ? 's' : ''}`,
  'Demande': n => `${n} demande${n > 1 ? 's' : ''}`,
  'Motion': n => `${n} motion${n > 1 ? 's' : ''}`,
  // « Débat filmé » ne désigne ICI que les chapitres vidéo qu'on n'a pas su
  // apparier à un point de PV (voir elus.js, TYPE_FILTER_ORDER) — une vraie
  // catégorie, pas une facette : d'où « hors PV » dans le texte, pour ne pas
  // se confondre avec la puce-facette « dont N débats filmés » (le lien
  // vidéo tous types confondus, gérée séparément par elus.js).
  'Débat filmé': n => `${n} débat${n > 1 ? 's' : ''} filmé${n > 1 ? 's' : ''} hors PV`,
  // Même libellé, pour la légende du graphe Statistiques (« Activité
  // citoyenne ») dont la série backend porte ce nom en toutes lettres — voir
  // ACTIVITY_TYPE_ORDER.
  'Débat filmé hors PV': n => `${n} débat${n > 1 ? 's' : ''} filmé${n > 1 ? 's' : ''} hors PV`,
  'Question écrite': n => `${n} question${n > 1 ? 's' : ''} écrite${n > 1 ? 's' : ''}`,
};

// Les quatre issues qui TRANCHENT le sort d'un point. Le corpus en compte une
// quinzaine, dont une longue traîne de variantes rares : ces quatre-là sont
// nommées partout (les séries du graphe des issues, les puces de l'onglet
// Séances), le reste est regroupé sous « Autres issues ». Source unique, pour
// que les deux vues ne divergent jamais sur ce qu'est une « autre » issue.
export const STATUT_PRINCIPAUX = ['Approuvé', 'Décidé', 'Reporté', 'Retiré'];
export const STATUT_AUTRES = 'Autres issues';

// Un point donne accès au DÉBAT FILMÉ quand c'est un chapitre vidéo autonome
// (type "video", aucun point PV apparié), ou quand un chapitre vidéo a été
// apparié précisément à ce point (video_precise) — dans ce dernier cas le
// point garde son type (question orale, demande…) et le lien pointe l'instant
// du débat. Un point reporté OU retiré n'a rien été débattu ce jour-là.
// Définition partagée par l'onglet Séances (ligne + puce de filtre) et la vue
// Par élu·e (puce « Débat filmé ») : une seule définition, partout la même.
export function hasDebateLink(it) {
  return (it.type === 'video' && !!it.url)
    || !!(it.video_url && it.video_precise && !it.reporte && !it.retire);
}

// Bloc de tags « thématiques » — même rendu partout où un point est affiché
// (Séances, Par élu·e, sources des réponses) : avant ce correctif, seule la
// vue Séances les montrait. '' si aucune thématique (jamais de bloc vide).
export function renderThemeTags(thematiques) {
  return (thematiques && thematiques.length)
    ? `<div class="elu-tags">${thematiques.map(t => `<span class="elu-tag">${escapeHtml(t)}</span>`).join('')}</div>`
    : '';
}

// Réponse d'une question écrite : repliée par défaut (texte souvent long) —
// partagé entre l'onglet Par élu·e (elus.js) et les sources du chat (chat.js).
// '' si aucune réponse (jamais de bloc vide).
export function renderReponseDetails(reponse) {
  return reponse
    ? `<details class="elu-qe-reponse"><summary>Voir la réponse</summary><p>${escapeHtml(reponse)}</p></details>`
    : '';
}

// ── Fragments partagés entre Séances et Par élu·e (mêmes classes CSS,
// mêmes gabarits, jusqu'ici réécrits à l'identique à 4 endroits :
// seancePointRow/renderSeance dans seances.js, eluDeposeRow/eluRepondRow
// dans elus.js). Ce qui reste spécifique à chaque appelant — QUELS liens
// afficher, sous QUELLE condition, avec QUEL libellé — reste dans
// l'appelant : seule la construction du fragment HTML est partagée. ──
// Libellé affiché d'un type, quand le type brut prête à confusion ailleurs.
// « Point » se dispute le mot avec le décompte « Points » du graphe d'activité,
// qui couvre TOUS les points d'une séance : on précise donc de quel point il
// s'agit — celui que le conseil délibère et tranche.
const TYPE_BADGE_LABEL = { 'Point': 'Point délibératif' };

export function renderTypeBadge(typeLabel) {
  const cls = TYPE_BADGE[typeLabel] || 'b-d';
  const texte = TYPE_BADGE_LABEL[typeLabel] || typeLabel;
  return `<span class="elu-badge ${cls}" title="${escapeHtml(typeLabel)}">${escapeHtml(texte)}</span>`;
}

export function renderPvPdfLink(url, label = 'PV (PDF)', title = 'Ouvrir le PV (PDF) sur 1030.be') {
  return url
    ? `<a class="elu-link" href="${url}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(title)}"><svg class="icon" aria-hidden="true"><use href="#ico-date"/></svg>${escapeHtml(label)}</a>`
    : '';
}

export function renderVideoLink(url, label, title) {
  return url
    ? `<a class="elu-link elu-link-video" href="${url}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(title)}"><svg class="icon" aria-hidden="true"><use href="#ico-video"/></svg>${label}</a>`
    : '';
}

// Ligne « Auteur·e de la question : X » / « Répondant·e : X » — le libellé
// varie selon l'appelant/le rôle, la classe CSS et le gabarit non.
// Énumération à la française : « A », « A et B », « A, B et C ». Pendant de
// utils.text.liste_fr côté backend — les listes qui arrivent déjà ponctuées en
// viennent, celle-ci sert quand le front recompose la liste (cosignataires
// d'une question écrite, où la fiche consultée s'ajoute en tête).
export const listeFr = noms => {
  const l = (noms || []).filter(Boolean);
  return l.length <= 1 ? (l[0] || '') : `${l.slice(0, -1).join(', ')} et ${l[l.length - 1]}`;
};

export function renderPersonLine(cssClass, label, name) {
  return name ? `<div class="${cssClass}">${escapeHtml(label)} : ${escapeHtml(name)}</div>` : '';
}

// ── CHARGEMENT & RÉVEIL DU SERVEUR ──
// Le plan gratuit qui héberge l'API (voir render.yaml) l'endort après une
// période d'inactivité ; la réveiller prend jusqu'à une minute, largement
// plus qu'une requête normale. En dessous de ce délai, une réponse aurait
// déjà eu le temps d'arriver — pas la peine d'inquiéter pour rien. Partagé
// par les onglets Séances et Par élu·e, dont le tout premier appel réseau
// (respectivement /seances et /elus) est le plus exposé : c'est lui qui
// réveille le service si personne ne l'a fait avant.
export const REVEIL_DELAI_MS = 4000;

// Indicateur de chargement dont le message évolue si la réponse tarde. Sans
// lui, une attente de 30 à 50 s sur un simple « Chargement… » se lit comme
// une panne plutôt qu'un démarrage. Retourne une fonction à appeler dès que
// la réponse arrive (succès ou échec) pour annuler le minuteur — sans quoi le
// message de réveil s'afficherait même après une réponse déjà revenue.
export function renderLoadingReveil(box, msg = 'Chargement') {
  box.innerHTML = `<div class="loading"><span>${escapeHtml(msg)}</span><span class="dots"><span></span><span></span><span></span></span></div>`;
  const minuteur = setTimeout(() => {
    box.innerHTML = `<div class="loading"><span>Réveil du service</span><span class="dots"><span></span><span></span><span></span></span></div>
      <p class="loading-hint">Le service gratuit qui héberge les données se réveille après une période d'inactivité — jusqu'à une minute la première fois, puis ce sera rapide.</p>`;
  }, REVEIL_DELAI_MS);
  return () => clearTimeout(minuteur);
}
