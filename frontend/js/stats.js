// ── STATISTIQUES — drill-down Année → Mois → Séance, thématiques, évolution
// d'un thème (budget) ──
import { API_URL } from './config.js';
import { escapeHtml, formatDate, fmtInt, fmtMontant, fmtMontantCompact, fmtEUR, TYPE_COUNT_LABEL } from './utils.js';
import { doShare, shareBaseUrl } from './share.js';

// KPI et thématiques se recalculent pour le périmètre courant, à partir d'un
// résumé compact par séance (s.seances_resume). Navigation 100 % côté client :
// un clic = un niveau plus bas, aucun nouvel appel serveur.
let statsLoaded = false;
let seancesResume = [];                       // [{date, points, votes, montant, themes:[[t,n]]}]
let drill = { level: 'year', year: null, month: null };
let drillMetric = 'points';
let latestYear = '';
let selectedSeance = null;   // date d'un PV choisi dans la liste (affine KPI + thèmes)
let selectedTheme = null;    // thème choisi → ne garde dans la liste que les PV concernés
let expandedYears = new Set();  // années dépliées dans la vue « tous les PV » groupée
let activiteTypesParAnnee = [];   // [{annee, "Question orale":n, "Demande":n, ...}] — voir /stats
let activiteTypeOrder = [];       // ordre fixe des séries (voir ACTIVITY_TYPE_ORDER, backend)
let horsPvParDate = {};           // date -> nb de chapitres vidéo sans point de PV
let statutsParDate = {};          // date -> {statut: nb de points} (voir seances.statuts_par_date)
let statutFilter = 'all';         // série isolée dans le graphe des issues
let statsVue = 'activite';        // sous-onglet affiché (voir showStatsVue)
let qeResume = [];                // {date, thematiques} par question écrite (voir /stats qe_resume)
// Type isolé dans le graphe « Activité citoyenne » via un clic sur sa puce de
// légende ('all' = vue empilée normale) — voir onActivityTypeChipClick.
let activiteTypeFilter = 'all';

const MOIS_FR = ['', 'janvier', 'février', 'mars', 'avril', 'mai', 'juin',
  'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre'];

// Séances du périmètre du GRAPHE (toutes / une année / un mois) — pilote la liste.
function scopeSeances() {
  return seancesResume.filter(s => {
    if (drill.year && s.date.slice(0, 4) !== drill.year) return false;
    if (drill.month && s.date.slice(0, 7) !== drill.year + '-' + drill.month) return false;
    return true;
  });
}

// Périmètre EFFECTIF pour KPI + thématiques : un PV précis s'il est sélectionné
// dans la liste, sinon le périmètre du graphe.
function activeScope() {
  return selectedSeance ? seancesResume.filter(s => s.date === selectedSeance) : scopeSeances();
}

// Agrège une liste de séances → KPI + thématiques (canoniques, top 12).
// Chaque thème cumule son nombre de points ET son montant engagé.
// extraThemeCounts (optionnel) : Map<thème, nombre> à fusionner avant le
// top 12 — sert à intégrer les questions écrites (jamais rattachées à une
// séance, donc absentes de `list`), voir qeThemesInScope().
function aggregate(list, extraThemeCounts) {
  const th = {}; let points = 0, votes = 0, montant = 0;
  list.forEach(s => {
    points += s.points; votes += s.votes; montant += s.montant;
    (s.themes || []).forEach(([t, n, m]) => {
      const e = th[t] || (th[t] = { n: 0, m: 0 });
      e.n += n; e.m += (m || 0);
    });
  });
  if (extraThemeCounts) {
    extraThemeCounts.forEach((n, t) => {
      const e = th[t] || (th[t] = { n: 0, m: 0 });
      e.n += n;
    });
  }
  const top = Object.entries(th).sort((a, b) => b[1].n - a[1].n).slice(0, 12)
    .map(([t, e]) => [t, e.n, e.m]);
  return { nb: list.length, points, votes, montant, themes: top };
}

// Libellé lisible du périmètre courant.
function scopeLabel() {
  if (drill.level === 'year') return 'Toutes les années';
  if (drill.month) return MOIS_FR[+drill.month].replace(/^./, c => c.toUpperCase()) + ' ' + drill.year;
  return drill.year;
}

// KPI (séances / points / votes / montant) recalculés pour le périmètre.
function renderKPIs() {
  const box = document.getElementById('statsKPIs');
  if (!box) return;
  const a = aggregate(activeScope());
  const card = (ico, num, lbl, small) =>
    `<div class="stat-card"><svg class="stat-ico" aria-hidden="true"><use href="#${ico}"/></svg>`
    + `<div class="stat-num"${small ? ' style="font-size:22px"' : ''}>${num}</div>`
    + `<div class="stat-label">${lbl}</div></div>`;
  box.innerHTML =
    card('ico-date', fmtInt(a.nb), 'Séances') +
    card('ico-pv', fmtInt(a.points), 'Points traités') +
    card('ico-vote', fmtInt(a.votes), 'Votes disputés') +
    card('ico-montant', fmtMontantCompact(a.montant), 'Montants engagés', true);
  const lbl = document.getElementById('scopeLabel');
  if (lbl) lbl.textContent = selectedSeance ? 'Séance du ' + formatDate(selectedSeance) : scopeLabel();
  const reset = document.getElementById('drillReset');
  if (reset) reset.hidden = (drill.level === 'year' && !selectedSeance);
}

// Barres du niveau courant : années, ou mois de l'année sélectionnée.
// (Pas de niveau « séance » : ~1 séance/mois → le mois EST la séance.)
function drillBuckets() {
  if (drill.level === 'year') {
    const m = {};
    seancesResume.forEach(s => { const y = s.date.slice(0, 4); (m[y] = m[y] || []).push(s); });
    return Object.keys(m).sort().map(k => ({ key: k, label: k, list: m[k] }));
  }
  // Niveau mois : tous les mois de l'année (indépendant du mois éventuellement focalisé).
  const m = {};
  seancesResume.filter(s => s.date.slice(0, 4) === drill.year)
    .forEach(s => { const mo = s.date.slice(5, 7); (m[mo] = m[mo] || []).push(s); });
  return Object.keys(m).sort().map(k => ({ key: k, label: MOIS_FR[+k].slice(0, 4) + '.', list: m[k] }));
}
const bucketVal = b => drillMetric === 'pv' ? b.list.length : aggregate(b.list).points;

// Graphe du niveau courant + titre + fil d'Ariane.
function renderDrill() {
  const plot = document.getElementById('drillPlot');
  if (!plot) return;
  const bs = drillBuckets();
  const max = Math.max(...bs.map(bucketVal), 1);
  plot.innerHTML = bs.map(b => {
    const v = bucketVal(b), h = Math.max(4, Math.round(v / max * 130)), a = aggregate(b.list);
    const sel = (drill.level === 'month' && b.key === drill.month);
    const tip = `${b.label} · ${fmtInt(a.points)} points · ${fmtInt(a.nb)} séance(s) · ${fmtMontant(a.montant)}`;
    return `<div class="yc-col yc-clic${sel ? ' yc-sel' : ''}" data-click="drillInto" data-arg="${escapeHtml(b.key)}" title="${escapeHtml(tip)}">`
      + `<span class="yc-val">${drillMetric === 'points' ? fmtInt(v) : v}</span>`
      + `<div class="yc-bar" style="height:${h}px"></div>`
      + `<span class="yc-yr">${b.label}</span></div>`;
  }).join('');

  const t = document.getElementById('drillTitle');
  if (t) t.textContent = drill.level === 'year' ? 'Activité par année' : 'Activité par mois — ' + drill.year;
  const hint = drill.level === 'year'
    ? 'Cliquez une année pour voir ses mois.'
    : 'Cliquez un mois pour affiner les indicateurs et les thématiques à cette séance.';
  const hn = document.getElementById('drillHint'); if (hn) hn.textContent = hint;
  document.querySelectorAll('.yc-toggle button').forEach(btn =>
    btn.classList.toggle('active', btn.dataset.metric === drillMetric));
  renderCrumb();
}

// Fil d'Ariane cliquable pour remonter d'un niveau — répété au-dessus des TROIS
// graphes, qui partagent le même état `drill` : une fois descendu jusqu'au
// graphe des issues, remonter d'un niveau ne doit pas obliger à retourner en
// haut de la page.
const CRUMB_BOXES = ['drillCrumb', 'activityCrumb', 'statutCrumb'];

function renderCrumb() {
  const boxes = CRUMB_BOXES.map(id => document.getElementById(id)).filter(Boolean);
  if (!boxes.length) return;
  let p;
  if (drill.level === 'year') {
    p = [selectedSeance
      ? '<a data-click="drillTo" data-arg="year">Toutes les années</a>'
      : '<span class="crumb-here">Toutes les années</span>'];
  } else {
    p = ['<a data-click="drillTo" data-arg="year">Toutes les années</a>'];
    const yearHere = !drill.month && !selectedSeance;
    p.push(yearHere
      ? `<span class="crumb-here">${drill.year}</span>`
      : `<a data-click="drillTo" data-arg="month" data-arg2="${escapeHtml(drill.year)}">${drill.year}</a>`);
    if (drill.month) {
      const monthHere = !selectedSeance;
      p.push(monthHere
        ? `<span class="crumb-here">${MOIS_FR[+drill.month]}</span>`
        : `<a data-click="clearSeance">${MOIS_FR[+drill.month]}</a>`);
    }
  }
  if (selectedSeance) p.push(`<span class="crumb-here">Séance du ${formatDate(selectedSeance)}</span>`);
  const html = p.join('<span class="crumb-sep">›</span>');
  boxes.forEach(box => { box.innerHTML = html; });
}

// Thématiques des questions écrites dans le périmètre courant (année/mois du
// drill-down) — jamais rattachées à une séance précise, donc absentes de
// activeScope()/seancesResume : agrégées séparément puis fusionnées dans
// renderThemes(). Une question écrite n'appartenant à aucune séance, un PV
// précis sélectionné dans la liste exclut par définition toute question écrite.
function qeThemesInScope() {
  const counts = new Map();
  if (selectedSeance) return counts;
  qeResume
    .filter(q => !drill.year || q.date.slice(0, 4) === drill.year)
    .filter(q => !drill.month || q.date.slice(5, 7) === drill.month)
    .forEach(q => (q.thematiques || []).forEach(t => counts.set(t, (counts.get(t) || 0) + 1)));
  return counts;
}

// Thématiques recalculées pour le périmètre courant.
function renderThemes() {
  const box = document.getElementById('themesBars');
  if (!box) return;
  const rows = aggregate(activeScope(), qeThemesInScope()).themes;
  const sc = document.getElementById('themesScope');
  if (sc) sc.textContent = selectedSeance ? 'séance du ' + formatDate(selectedSeance) : scopeLabel().toLowerCase();
  if (!rows.length) { box.innerHTML = '<p class="yc-note">Aucune donnée pour cette vue.</p>'; return; }
  const max = Math.max(...rows.map(r => r[1]), 1);
  box.innerHTML = rows.map(([nom, n, m]) => `
    <div class="bar-row bar-clic${nom === selectedTheme ? ' bar-sel' : ''}"
         data-click="selectTheme" data-arg="${escapeHtml(nom)}"
         title="Ne montrer que les PV traitant « ${escapeHtml(nom.replace(/_/g, ' '))} »">
      <div class="bar-label">${escapeHtml(nom.replace(/_/g, ' '))}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${n / max * 100}%"></div></div>
      <div class="bar-val">${fmtInt(n)}</div>
      <div class="bar-eur" title="Montant engagé sur les points de ce thème">${m ? fmtMontant(m) : '—'}</div>
    </div>`).join('')
    + '<p class="yc-note">Chiffre = points traités portant la thématique. Un point peut relever de '
    + 'plusieurs thématiques : la somme des barres (points et montants) dépasse donc les totaux, '
    + 'et seules les 12 thématiques principales sont affichées.</p>';
}

// Lignes du graphe « Activité citoyenne » au niveau courant : totaux par
// année précalculés côté serveur (activite_types_par_annee), ou — au niveau
// mois — agrégés côté client depuis seancesResume (types PV) + qeResume
// (questions écrites, comptées par leur propre date), comme les thématiques.
function activityRows() {
  if (drill.level === 'year') {
    return activiteTypesParAnnee.map(row => ({ key: row.annee, label: row.annee, counts: row }));
  }
  const m = {};
  seancesResume.filter(s => s.date.slice(0, 4) === drill.year).forEach(s => {
    const mo = s.date.slice(5, 7);
    const e = m[mo] || (m[mo] = {});
    Object.entries(s.activite || {}).forEach(([t, n]) => { e[t] = (e[t] || 0) + n; });
  });
  qeResume.filter(q => q.date.slice(0, 4) === drill.year).forEach(q => {
    const mo = q.date.slice(5, 7);
    const e = m[mo] || (m[mo] = {});
    e['Question écrite'] = (e['Question écrite'] || 0) + 1;
  });
  // Chapitres vidéo sans point de PV : comptés par DATE, car certaines de ces
  // séances n'ont aucun PV — elles n'ont donc pas de ligne dans seancesResume.
  Object.entries(horsPvParDate).forEach(([d, n]) => {
    if (d.slice(0, 4) !== drill.year) return;
    const mo = d.slice(5, 7);
    const e = m[mo] || (m[mo] = {});
    e['Débat filmé hors PV'] = (e['Débat filmé hors PV'] || 0) + n;
  });
  return Object.keys(m).sort().map(k => ({ key: k, label: MOIS_FR[+k].slice(0, 4) + '.', counts: m[k] }));
}

// KPI par série, au-dessus du graphe : le total de chaque type sur le périmètre
// affiché — le chiffre qu'on vient chercher, avant d'aller le lire barre par
// barre. Chaque pastille porte la couleur de sa série, mais l'identité ne
// repose jamais sur la seule couleur (libellé + nombre l'accompagnent), et la
// couleur suit la série, pas son rang.
const ACTIVITY_KPI_SHORT = {
  'Question orale': 'Q. orales',
  'Demande': 'Demandes',
  'Motion': 'Motions',
  'Question écrite': 'Q. écrites',
  'Débat filmé hors PV': 'Hors PV',
};

function renderActivityKPIs(rows) {
  const box = document.getElementById('activityKPIs');
  if (!box) return;
  // Le graphe montre TOUS les mois de l'année, un seul mis en évidence : sommer
  // ses barres redonnerait l'année entière alors que le périmètre affiché est
  // ce mois-là. On restreint donc au mois foré, comme le font les KPI du haut
  // et les thématiques.
  const scope = drill.month ? rows.filter(row => row.key === drill.month) : rows;
  box.innerHTML = activiteTypeOrder.map((t, si) => {
    const n = scope.reduce((sum, row) => sum + (row.counts[t] || 0), 0);
    return `<div class="act-kpi" title="${escapeHtml(t)}">
      <div class="act-kpi-head"><span class="yc-legend-swatch yc-seg-s${si}"></span>`
      + `<span class="act-kpi-label">${escapeHtml(ACTIVITY_KPI_SHORT[t] || t)}</span></div>
      <div class="act-kpi-num">${fmtInt(n)}</div>
    </div>`;
  }).join('');
}

// ── GRAPHE DES ISSUES (statuts) ─────────────────────────────────────────────
// Même forme, même geste et même enchaînement (année → mois → PV) que le
// graphe d'activité, mais l'autre question : que DEVIENNENT les points ?
//
// Le corpus compte une quinzaine de statuts, dont une longue traîne de
// variantes rares. Empiler quinze séries ne se lit pas : quatre sont nommées
// — celles qui tranchent le sort d'un point — et le reste est REGROUPÉ dans
// « Autres issues », en gris neutre, avec le détail en infobulle. Ce n'est pas
// une identité de plus dans la palette catégorielle : c'est un reliquat, d'où
// une couleur volontairement hors palette (voir --chart-autres).
const STATUT_SERIES = ['Approuvé', 'Décidé', 'Reporté', 'Retiré'];
const STATUT_AUTRES = 'Autres issues';

// Statuts du périmètre courant, repliés sur les 5 séries. Retourne aussi le
// détail de ce qui a été regroupé, pour l'infobulle de la légende.
function statutRows() {
  const parCle = {};
  const detailAutres = {};
  Object.entries(statutsParDate).forEach(([date, counts]) => {
    let cle = null;
    if (drill.level === 'year') cle = date.slice(0, 4);
    else if (date.slice(0, 4) === drill.year) cle = date.slice(5, 7);
    if (!cle) return;
    const e = parCle[cle] || (parCle[cle] = {});
    Object.entries(counts).forEach(([st, n]) => {
      const serie = STATUT_SERIES.includes(st) ? st : STATUT_AUTRES;
      e[serie] = (e[serie] || 0) + n;
      if (serie === STATUT_AUTRES) detailAutres[st] = (detailAutres[st] || 0) + n;
    });
  });
  const rows = Object.keys(parCle).sort().map(k => ({
    key: k,
    label: drill.level === 'year' ? k : MOIS_FR[+k].slice(0, 4) + '.',
    counts: parCle[k],
  }));
  return { rows, detailAutres };
}

export function onStatutChipClick(statut) {
  statutFilter = statutFilter === statut ? 'all' : statut;
  renderStatuts();
}

function renderStatuts() {
  const plot = document.getElementById('statutPlot');
  const legend = document.getElementById('statutLegend');
  if (!plot || !legend) return;
  const series = [...STATUT_SERIES, STATUT_AUTRES];
  const { rows, detailAutres } = statutRows();
  if (!rows.length) {
    plot.innerHTML = '<p class="yc-note">Aucune donnée disponible.</p>';
    legend.innerHTML = '';
    return;
  }
  const isolate = statutFilter !== 'all' && series.includes(statutFilter);
  const totals = rows.map(row => series.reduce((sum, t) => sum + (row.counts[t] || 0), 0));
  const values = isolate ? rows.map(row => row.counts[statutFilter] || 0) : totals;
  const max = Math.max(...values, 1);

  plot.innerHTML = rows.map((row, i) => {
    const value = values[i];
    const stackH = Math.max(4, Math.round(value / max * 130));
    const sel = (drill.level === 'month' && row.key === drill.month);
    let firstNonZero = true;
    const segs = (isolate ? [statutFilter] : series).map(t => {
      const si = series.indexOf(t);
      const n = row.counts[t] || 0;
      if (!n) return '';
      const segH = isolate ? stackH : Math.max(2, Math.round(n / value * stackH));
      const topCls = firstNonZero ? ' yc-seg-top' : '';
      firstNonZero = false;
      const cls = t === STATUT_AUTRES ? 'yc-seg-autres' : `yc-seg-s${si}`;
      return `<div class="yc-seg ${cls}${topCls}" style="height:${segH}px" title="${escapeHtml(t)} : ${fmtInt(n)}"></div>`;
    }).join('');
    return `<div class="yc-col yc-clic${sel ? ' yc-sel' : ''}" data-click="drillInto" data-arg="${escapeHtml(row.key)}" title="${escapeHtml(row.label)} : ${fmtInt(value)} point${value > 1 ? 's' : ''}">
      <span class="yc-val">${fmtInt(value)}</span>
      <div class="yc-stack" style="height:${stackH}px">${segs}</div>
      <span class="yc-yr">${escapeHtml(row.label)}</span>
    </div>`;
  }).join('');

  const detail = Object.entries(detailAutres).sort((a, b) => b[1] - a[1])
    .map(([st, n]) => `${st} : ${fmtInt(n)}`).join(' · ');
  legend.innerHTML = series.map((t, si) => {
    const active = statutFilter === t ? ' yc-legend-chip-active' : '';
    const cls = t === STATUT_AUTRES ? 'yc-seg-autres' : `yc-seg-s${si}`;
    const aide = t === STATUT_AUTRES ? ` title="${escapeHtml(detail || 'Aucune')}"` : '';
    return `<button type="button" class="yc-legend-chip${active}"${aide} data-click="onStatutChipClick" data-arg="${escapeHtml(t)}">`
      + `<span class="yc-legend-swatch ${cls}"></span>${escapeHtml(t)}</button>`;
  }).join('');

  const title = document.getElementById('statutTitle');
  if (title) title.textContent = drill.level === 'year'
    ? 'Issue des points par année' : 'Issue des points par mois — ' + drill.year;
  const hint = document.getElementById('statutHint');
  if (hint) hint.textContent = drill.level === 'year'
    ? 'Cliquez une année pour voir la répartition par mois.'
    : 'Cliquez un mois pour affiner les indicateurs, thématiques et la liste des PV ci-dessous.';
}

// Reclique sur la puce déjà active → retour à la vue empilée (toutes les
// séries) ; sinon isole cette série (même geste que eluTypeChip côté Par élu·e).
export function onActivityTypeChipClick(typeLabel) {
  activiteTypeFilter = activiteTypeFilter === typeLabel ? 'all' : typeLabel;
  renderActivityTypes();
}

// Graphe empilé « Activité citoyenne » : 5 séries DISJOINTES (question
// orale/demande/motion/question écrite/débat filmé hors PV) — volontairement
// PAS les points administratifs "point normal"/"urgent" (~95 % du volume
// total, qui écraserait visuellement celles-ci dans le même graphe empilé,
// voir backend/services/statistics.py). Palette catégorielle dédiée (--chart-s0..s4),
// hors de l'accent --bordeaux réservé aux éléments interactifs. Partage le
// drill-down Année → Mois de l'autre graphe (même état `drill`, mêmes
// gestes drillInto/drillTo) : légende + étiquette de total + infobulle par
// segment restent (skill dataviz : relief obligatoire dès qu'une teinte
// catégorielle tombe sous 3:1 de contraste sur le fond clair). La légende est
// cliquable (comme les puces de profil de l'onglet Par élu·e) : isole une
// série — le graphe passe d'empilé à un seul type, mis à l'échelle sur SES
// propres valeurs (pas le total empilé, sinon la barre isolée paraîtrait minuscule).
function renderActivityTypes() {
  const plot = document.getElementById('activityPlot');
  const legend = document.getElementById('activityLegend');
  if (!plot || !legend) return;
  if (!activiteTypesParAnnee.length || !activiteTypeOrder.length) {
    plot.innerHTML = '<p class="yc-note">Aucune donnée disponible.</p>';
    legend.innerHTML = '';
    return;
  }
  const rows = activityRows();
  renderActivityKPIs(rows);
  const isolate = activiteTypeFilter !== 'all' && activiteTypeOrder.includes(activiteTypeFilter);
  const totals = rows.map(row => activiteTypeOrder.reduce((sum, t) => sum + (row.counts[t] || 0), 0));
  const values = isolate ? rows.map(row => row.counts[activiteTypeFilter] || 0) : totals;
  const max = Math.max(...values, 1);

  plot.innerHTML = rows.map((row, i) => {
    const value = values[i];
    const stackH = Math.max(4, Math.round(value / max * 130));
    const sel = (drill.level === 'month' && row.key === drill.month);
    const typesToRender = isolate ? [activiteTypeFilter] : activiteTypeOrder;
    let firstNonZero = true;
    const segs = typesToRender.map(t => {
      const si = activiteTypeOrder.indexOf(t);
      const n = row.counts[t] || 0;
      if (!n) return '';
      const segH = isolate ? stackH : Math.max(2, Math.round(n / value * stackH));
      const topCls = firstNonZero ? ' yc-seg-top' : '';
      firstNonZero = false;
      return `<div class="yc-seg yc-seg-s${si}${topCls}" style="height:${segH}px" title="${escapeHtml(t)} : ${fmtInt(n)}"></div>`;
    }).join('');
    const barTitle = isolate
      ? `${row.label} : ${TYPE_COUNT_LABEL[activiteTypeFilter](value)}`
      : `${row.label} : ${fmtInt(value)} au total`;
    return `<div class="yc-col yc-clic${sel ? ' yc-sel' : ''}" data-click="drillInto" data-arg="${escapeHtml(row.key)}" title="${escapeHtml(barTitle)}">
      <span class="yc-val">${fmtInt(value)}</span>
      <div class="yc-stack" style="height:${stackH}px">${segs}</div>
      <span class="yc-yr">${escapeHtml(row.label)}</span>
    </div>`;
  }).join('');

  legend.innerHTML = activiteTypeOrder.map((t, si) => {
    const active = activiteTypeFilter === t ? ' yc-legend-chip-active' : '';
    return `<button type="button" class="yc-legend-chip${active}" data-click="onActivityTypeChipClick" data-arg="${escapeHtml(t)}">`
      + `<span class="yc-legend-swatch yc-seg-s${si}"></span>${escapeHtml(t)}</button>`;
  }).join('');

  const title = document.getElementById('activityTitle');
  if (title) title.textContent = drill.level === 'year' ? 'Activité citoyenne par année' : 'Activité citoyenne par mois — ' + drill.year;
  const hint = document.getElementById('activityHint');
  if (hint) hint.textContent = drill.level === 'year'
    ? 'Cliquez une année pour voir la répartition par mois.'
    : 'Cliquez un mois pour affiner les indicateurs, thématiques et la liste des PV ci-dessous.';
}

// Séances à lister : celles du périmètre courant ; au niveau « toutes les
// années », on retombe sur la dernière année (défaut demandé) plutôt que 144.
function listSeances() {
  // « Toutes les années » → tous les PV (regroupés par année à l'affichage) ;
  // sinon les PV du périmètre du graphe (année ou mois).
  let base = (drill.level === 'year') ? seancesResume.slice() : scopeSeances();
  if (selectedTheme) base = base.filter(s => (s.themes || []).some(([t]) => t === selectedTheme));
  return base;
}

// HTML d'une ligne PV (titre cliquable pour affiner + lien PDF si disponible).
function pvRowHtml(s) {
  const sel = s.date === selectedSeance;
  const label = `Séance du ${formatDate(s.date)}`;
  const dateBtn = `<button type="button" class="pv-pick" data-click="selectSeance" data-arg="${escapeHtml(s.date)}"
      title="Afficher les indicateurs et thématiques de cette séance">${label}</button>`;
  const icons = (s.url
      ? `<a class="pv-pdf" href="${s.url}" target="_blank" rel="noopener noreferrer"
           title="Ouvrir le PV (PDF) sur 1030.be"><svg class="icon" aria-hidden="true"><use href="#ico-pv"/></svg></a>`
      : '')
    + (s.video_url
      ? `<a class="pv-video" href="${s.video_url}" target="_blank" rel="noopener noreferrer"
           title="Voir la vidéo de la séance"><svg class="icon" aria-hidden="true"><use href="#ico-video"/></svg></a>`
      : '');
  // Colonnes fixes : date | icônes | méta → tout aligné à gauche, sans flottement.
  // Points/votes (ligne 1) et montant (ligne 2) toujours sur deux lignes
  // distinctes (voir .pv-meta en colonne, styles.css) — un flux texte unique
  // ne renvoyait le montant à la ligne que lorsque le texte dépassait la
  // largeur disponible, donnant un alignement incohérent d'une ligne à l'autre.
  return `<div class="pv-row${sel ? ' pv-sel' : ''}">`
    + `<span class="pv-date">${dateBtn}</span>`
    + `<span class="pv-icons">${icons}</span>`
    + `<span class="pv-meta">`
    + `<span class="pv-meta-top">${fmtInt(s.points)} points · ${fmtInt(s.votes)} votes</span>`
    + `<span class="pv-montant">${fmtMontant(s.montant)}</span>`
    + `</span></div>`;
}

// Liste des PV du périmètre. « Toutes les années » → groupée par année (repliable) ;
// une année/un mois → liste chronologique simple.
function renderPvList() {
  const box = document.getElementById('pvList');
  if (!box) return;
  const cap = document.getElementById('pvScope');
  if (cap) {
    const base = drill.level === 'year' ? 'Toutes les années' : scopeLabel();
    cap.innerHTML = selectedTheme
      ? `${base} · thème « ${escapeHtml(selectedTheme.replace(/_/g, ' '))} » `
        + `<button type="button" class="pv-clear" data-click="selectTheme" data-arg="${escapeHtml(selectedTheme)}">✕</button>`
      : base;
  }
  const all = listSeances();
  // Pastille du sous-onglet : combien de PV le périmètre courant contient, sans
  // avoir à l'ouvrir. Lue de la même source que la liste, jamais du DOM — les
  // années repliées ne rendent aucune ligne, un comptage du DOM mentirait.
  const pastille = document.getElementById('statsPvCount');
  if (pastille) {
    pastille.textContent = fmtInt(all.length);
    pastille.hidden = false;
  }
  if (!all.length) { box.innerHTML = '<p class="yc-note">Aucune séance.</p>'; return; }

  if (drill.level === 'year') {
    // Regroupement par année, plus récente en premier, chaque groupe repliable.
    const byYear = {};
    all.forEach(s => { const y = s.date.slice(0, 4); (byYear[y] = byYear[y] || []).push(s); });
    const years = Object.keys(byYear).sort().reverse();
    box.innerHTML = years.map(y => {
      const items = byYear[y].slice().sort((a, b) => a.date < b.date ? 1 : -1);
      const open = expandedYears.has(y);
      return `<div class="pv-group">
        <button type="button" class="pv-group-head" data-click="toggleYear" data-arg="${escapeHtml(y)}">
          <span class="pv-caret">${open ? '▾' : '▸'}</span>
          <span class="pv-group-year">${y}</span>
          <span class="pv-group-count">${items.length} séance${items.length > 1 ? 's' : ''}</span>
        </button>
        ${open ? `<div class="pv-group-body">${items.map(pvRowHtml).join('')}</div>` : ''}
      </div>`;
    }).join('');
  } else {
    const list = all.slice().sort((a, b) => a.date < b.date ? 1 : -1);
    box.innerHTML = list.map(pvRowHtml).join('')
      + (list.length > 1 ? '<p class="yc-note">Cliquez une séance pour n\'afficher que ses indicateurs et thématiques.</p>' : '');
  }
}

// Plier/déplier une année dans la vue « tous les PV ».
export function toggleYear(y) {
  if (expandedYears.has(y)) expandedYears.delete(y); else expandedYears.add(y);
  renderPvList();
}

function refreshStats() {
  renderKPIs(); renderDrill(); renderActivityTypes(); renderStatuts();
  renderThemes(); renderPvList();
}

// Navigation graphe : Année → mois ; mois → focalise ce mois. Toute navigation
// dans le graphe annule la sélection d'un PV précis.
export function drillInto(key) {
  selectedSeance = null; selectedTheme = null;
  if (drill.level === 'year') drill = { level: 'month', year: key, month: null };
  else drill = { level: 'month', year: drill.year, month: key };
  refreshStats();
}
// Remonter via le fil d'Ariane ou le bouton « Toutes les années ».
export function drillTo(level, year) {
  selectedSeance = null; selectedTheme = null;
  if (level === 'year') drill = { level: 'year', year: null, month: null };
  else if (level === 'month') drill = { level: 'month', year: year || drill.year, month: null };
  refreshStats();
}
// Sélection d'un PV dans la liste : affine KPI + thématiques à cette séance
// (re-cliquer désélectionne). clearSeance() revient au périmètre du graphe.
export function selectSeance(date) {
  selectedSeance = (selectedSeance === date) ? null : date;
  refreshStats();
}
export function clearSeance() { selectedSeance = null; refreshStats(); }
// Clic sur un thème : ne garde dans la liste que les PV concernés (toggle).
// Change le filtre → annule la sélection d'un PV précis.
export function selectTheme(nom) {
  selectedTheme = (selectedTheme === nom) ? null : nom;
  selectedSeance = null;
  refreshStats();
}
export function setMetric(m) { drillMetric = m; renderDrill(); }

export async function loadStats() {
  if (statsLoaded) return;
  const container = document.getElementById('statsContent');
  try {
    const res = await fetch(API_URL + '/stats');
    if (!res.ok) {
      if (res.status === 429) throw new Error("Trop de requêtes. Patientez un instant.");
      throw new Error('Erreur ' + res.status);
    }
    const s = await res.json();
    seancesResume = s.seances_resume || [];
    activiteTypesParAnnee = s.activite_types_par_annee || [];
    activiteTypeOrder = s.activite_type_order || [];
    horsPvParDate = s.hors_pv_par_date || {};
    statutsParDate = s.statuts_par_date || {};
    qeResume = s.qe_resume || [];
    latestYear = seancesResume.reduce((mx, x) => x.date.slice(0, 4) > mx ? x.date.slice(0, 4) : mx, '');
    expandedYears = new Set([latestYear]);   // année la plus récente dépliée par défaut
    drill = { level: 'year', year: null, month: null };
    drillMetric = 'points';
    activiteTypeFilter = 'all';

    document.getElementById('statsScope').innerHTML = `
      <div class="scope-line">
        <div class="scope-title">Vue&nbsp;: <b id="scopeLabel">Toutes les années</b></div>
        <button class="drill-reset" id="drillReset" hidden data-click="drillTo" data-arg="year">↩ Toutes les années</button>
      </div>`;

    container.innerHTML = `
      <div class="stats-grid" id="statsKPIs"></div>
      <div class="stat-section">
        <div class="yc-head">
          <h3><svg class="icon" aria-hidden="true"><use href="#ico-stats"/></svg><span id="drillTitle">Activité par année</span></h3>
          <div class="yc-toggle">
            <button data-metric="pv" data-click="setMetric" data-arg="pv">PV</button>
            <button data-metric="points" class="active" data-click="setMetric" data-arg="points">Points</button>
          </div>
        </div>
        <div class="drill-crumb" id="drillCrumb"></div>
        <div class="yc-scroll"><div class="yc-plot" id="drillPlot"></div></div>
        <p class="yc-note" id="drillHint"></p>
      </div>
      <div class="stat-section" id="activitySection">
        <div class="yc-head">
          <h3><svg class="icon" aria-hidden="true"><use href="#ico-intervenant"/></svg><span id="activityTitle">Activité citoyenne par année</span></h3>
        </div>
        <div class="drill-crumb" id="activityCrumb"></div>
        <div class="act-kpis" id="activityKPIs"></div>
        <div class="yc-scroll"><div class="yc-plot" id="activityPlot"></div></div>
        <div class="yc-legend" id="activityLegend"></div>
        <p class="yc-note" id="activityHint"></p>
        <p class="yc-note">Questions orales, demandes, motions, questions écrites et débats filmés
          hors PV — les points administratifs/collectifs (approbations, conventions…) ne sont pas
          comptés ici, ils domineraient sans rien dire de l'activité citoyenne.</p>
      </div>
      <div class="stat-section" id="statutSection">
        <div class="yc-head">
          <h3><svg class="icon" aria-hidden="true"><use href="#ico-decision"/></svg><span id="statutTitle">Issue des points par année</span></h3>
        </div>
        <div class="drill-crumb" id="statutCrumb"></div>
        <div class="yc-scroll"><div class="yc-plot" id="statutPlot"></div></div>
        <div class="yc-legend" id="statutLegend"></div>
        <p class="yc-note" id="statutHint"></p>
        <p class="yc-note">Ce que devient chaque point : approuvé, décidé, reporté, retiré — le reste
          (prises d'acte, prises pour information, débats sans vote…) est regroupé sous « Autres
          issues », dont le détail s'affiche au survol de la légende. Les points sans décision
          (chapitres vidéo sans PV) n'y figurent pas.</p>
      </div>
      <div class="stat-section" id="themesSection">
        <div class="yc-head">
          <h3><svg class="icon" aria-hidden="true"><use href="#ico-thematique"/></svg>Thématiques — <span id="themesScope">toutes les années</span></h3>
        </div>
        <div id="themesBars"></div>
      </div>`;

    // La liste des PV est une destination à part entière, pas le bas d'une
    // longue page : elle a son propre sous-onglet, et suit le périmètre choisi
    // dans « Activité ».
    document.getElementById('statsPvContent').innerHTML = `
      <div class="stat-section" id="pvSection">
        <div class="yc-head">
          <h3><svg class="icon" aria-hidden="true"><use href="#ico-pv"/></svg>Procès-verbaux — <span id="pvScope"></span></h3>
        </div>
        <div id="pvList" class="pv-list"></div>
      </div>`;
    refreshStats();
    statsLoaded = true;
  } catch (err) {
    container.innerHTML = `<div class="error-box">Impossible de charger les statistiques. ${escapeHtml(err.message)}</div>`;
  }
}

// ── SOUS-ONGLETS ────────────────────────────────────────────────────────────
// Trois vues qui répondent à trois questions différentes : ce que le conseil
// traite (activité), quels PV existent, et combien un thème a coûté. Le
// périmètre (drill) leur est COMMUN et vit au-dessus d'elles — changer de vue
// ne le remet jamais à zéro.
const STATS_VUES = ['activite', 'pv', 'theme'];

export function showStatsVue(vue) {
  if (!STATS_VUES.includes(vue)) vue = 'activite';
  statsVue = vue;
  STATS_VUES.forEach(v => {
    document.getElementById('statsvue-panel-' + v).classList.toggle('active', v === vue);
    document.getElementById('statsvue-' + v).setAttribute('aria-selected', String(v === vue));
  });
}

// Vue demandée par un lien partagé (?tab=stats&vue=pv), appliquée à
// l'ouverture de l'onglet — le panneau existe déjà en statique, donc sans
// attendre /stats.
export function setPendingStatsVue(vue) {
  if (vue) showStatsVue(vue);
}

export function shareStats(btn) {
  doShare('PV Explorer — Statistiques du Conseil communal de Schaerbeek',
          'Statistiques des décisions du Conseil communal de Schaerbeek',
          `${shareBaseUrl()}?tab=stats${statsVue === 'activite' ? '' : '&vue=' + statsVue}`, btn);
}

// ── ÉVOLUTION D'UN THÈME (agrégation exhaustive via /trend) ──
export function trendSuggestion(el) {
  document.getElementById('trendInput').value = el.textContent;
  loadTrend();
}
export async function loadTrend() {
  const theme = document.getElementById('trendInput').value.trim();
  const box = document.getElementById('trendResult');
  if (!theme) { box.innerHTML = ''; return; }
  box.innerHTML = '<div class="loading"><span>Calcul en cours</span><span class="dots"><span></span><span></span><span></span></span></div>';
  try {
    const res = await fetch(API_URL + '/trend?theme=' + encodeURIComponent(theme));
    if (!res.ok) {
      if (res.status === 429) throw new Error("Trop de requêtes. Patientez un instant.");
      const e = await res.json().catch(() => ({}));
      throw new Error(e.detail || e.error || ('Erreur ' + res.status));
    }
    const d = await res.json();
    if (!d.annees || !d.annees.length) {
      box.innerHTML = `<div class="trend-empty">Aucun point trouvé pour « ${escapeHtml(theme)} ». Essayez un autre mot-clé.</div>`;
      return;
    }
    const max = Math.max(...d.annees.map(a => a.total_eur), 1);
    const bars = d.annees.map(a => `
      <div class="bar-row">
        <div class="bar-label">${escapeHtml(a.annee)}</div>
        <div class="bar-track"><div class="bar-fill" style="width:${a.total_eur / max * 100}%"></div></div>
        <div class="bar-val">${fmtEUR(a.total_eur)}</div>
      </div>`).join('');
    const items = (d.top_items || []).map(it => {
      const dateHtml = it.url
        ? `<a class="pv-pdf-link" href="${it.url}" target="_blank" rel="noopener noreferrer" title="Ouvrir le PV (PDF) sur 1030.be"><svg class="icon" aria-hidden="true"><use href="#ico-date"/></svg>${formatDate(it.date)}</a>`
        : `<svg class="icon" aria-hidden="true"><use href="#ico-date"/></svg>${formatDate(it.date)}`;
      return `
      <div class="source-item">
        <div class="source-ref">${dateHtml}<br>SP ${it.sp}</div>
        <div class="source-titre">${escapeHtml(it.titre)}</div>
        <div class="source-decision">${fmtEUR(it.montant_eur)}</div>
      </div>`;
    }).join('');
    box.innerHTML = `
      <div class="trend-summary"><strong>${fmtEUR(d.total_eur)}</strong> cumulés · ${d.points_total} points liés à « ${escapeHtml(d.theme)} »</div>
      <div class="stat-section"><h3>Montants par année</h3>${bars}</div>
      <div class="stat-section"><h3>Plus grosses dépenses</h3>${items}</div>
      <p class="trend-note">${escapeHtml(d.note || '')}</p>`;
  } catch (err) {
    box.innerHTML = `<div class="error-box">Impossible de calculer l'évolution. ${escapeHtml(err.message)}</div>`;
  }
}
