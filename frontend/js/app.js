// ── POINT D'ENTRÉE — initialisation + câblage des gestionnaires d'événements
// (délégation "data-click" pour le contenu généré dynamiquement, écouteurs
// directs pour les éléments statiques). Chaque fonctionnalité vit dans son
// propre module ; ce fichier n'orchestre que le démarrage et la navigation
// par onglets. ──
import { fetchVersion } from './config.js';
import { registerActions } from './delegate.js';
import {
  applyTheme, cycleTheme, openSettings, closeSettings, updateSetting,
  resetSettings, setMode, syncModeUI, initSettingsDrag, initSettingsOverlay,
} from './settings.js';
import {
  onAskInput, askSuggestion, reuseQuestion, clearHistory, removeHistoryItem,
  newSearch, toggleDictation, submitQuestion, retryQuestion, copyAnswer, downloadAnswer, shareAnswer,
} from './chat.js';
import {
  loadStats, toggleYear, drillInto, drillTo, selectSeance, clearSeance,
  selectTheme, setMetric, shareStats, trendSuggestion, loadTrend, onActivityTypeChipClick,
  onStatutChipClick, onDrillTypeChipClick, showStatsVue, setPendingStatsVue,
} from './stats.js';
import {
  loadElus, initEluCombo, onEluYearChange, retryElu,
  onEluChipClick, onEluRoleChipClick, onEluFacetChipClick, shareElu, setPendingEluKey,
} from './elus.js';
import {
  loadSeances, renderSeanceYearList, onSeanceListChange, shareSeance,
  jumpToSeance, setPendingSeanceDate, onSeanceTypeChipClick, onSeanceFacetChipClick,
} from './seances.js';
import {
  checkAdminSession, openAdminLogin, closeAdminLogin, initAdminLoginOverlay,
  submitAdminLogin, adminLogout, cancelAdminExtract, confirmAdminPublish,
  cancelQeExtract, confirmQePublish, switchAdminSubTab,
  onMandatEditClick, onMandatNewClick, cancelMandatEdit, onMandatRoleChipClick,
  onMandatLegislatureChipClick, onMandatSortClick,
  onMandatDeleteClick, cancelMandatDelete, confirmMandatDelete,
} from './admin.js';

// ── ONGLETS ──
function switchTab(tab) {
  document.querySelectorAll('.tab').forEach(t => { t.classList.remove('active'); t.setAttribute('aria-selected', 'false'); });
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  const btn = document.getElementById('tab-' + tab);
  btn.classList.add('active');
  btn.setAttribute('aria-selected', 'true');
  document.getElementById('panel-' + tab).classList.add('active');
  document.getElementById('askBar').style.display = (tab === 'chat') ? 'block' : 'none';
  if (tab === 'stats') loadStats();
  if (tab === 'elus') loadElus();
  if (tab === 'seances') loadSeances();
}

// Liens partagés : ?tab=stats ouvre l'onglet Statistiques ; ?q=… ré-ouvre la
// question et la relance automatiquement (au chargement).
function handleDeepLink() {
  const params = new URLSearchParams(location.search);
  if (params.get('tab') === 'stats') {
    setPendingStatsVue(params.get('vue'));   // ?vue=pv / theme — voir showStatsVue
    switchTab('stats');
  }
  if (params.get('tab') === 'elus') {
    setPendingEluKey(params.get('elu') || null);   // appliqué quand la liste est chargée
    switchTab('elus');
  }
  if (params.get('tab') === 'seances') {
    setPendingSeanceDate(params.get('seance') || null);  // appliqué quand la liste est chargée
    switchTab('seances');
  }
  const q = params.get('q');
  if (q) {
    const input = document.getElementById('askInput');
    if (input) { input.value = q; submitQuestion(); }
  }
}

// ── Fonctions référencées depuis les attributs "data-click" du HTML
// (statique dans index.html, ou généré dynamiquement dans les templates des
// modules) : voir delegate.js — remplace les anciens attributs onclick
// inline, incompatibles avec une CSP script-src sans 'unsafe-inline'. ──
registerActions({
  switchTab,
  cycleTheme, openSettings, closeSettings, resetSettings, setMode,
  askSuggestion, reuseQuestion, clearHistory, removeHistoryItem,
  newSearch, toggleDictation, submitQuestion, retryQuestion, copyAnswer, downloadAnswer, shareAnswer,
  // Boutons « Réessayer » des blocs d'erreur (chat, fiche d'élu·e, stats, séances).
  retryElu, retryStats: loadStats, retrySeances: loadSeances,
  toggleYear, drillInto, drillTo, selectSeance, clearSeance, selectTheme, setMetric,
  shareStats, trendSuggestion, loadTrend, onActivityTypeChipClick, onStatutChipClick,
  showStatsVue, onDrillTypeChipClick,
  shareElu, onEluChipClick, onEluRoleChipClick, onEluFacetChipClick,
  shareSeance, jumpToSeance, onSeanceTypeChipClick, onSeanceFacetChipClick,
  openAdminLogin, closeAdminLogin, adminLogout, cancelAdminExtract, confirmAdminPublish,
  cancelQeExtract, confirmQePublish, switchAdminSubTab,
  onMandatEditClick, onMandatNewClick, cancelMandatEdit, onMandatRoleChipClick,
  onMandatLegislatureChipClick, onMandatSortClick,
  onMandatDeleteClick, cancelMandatDelete, confirmMandatDelete,
});

// ── Écouteurs directs pour les éléments statiques qui portaient un
// oninput/onchange/onkeydown inline (pas de délégation nécessaire : éléments
// uniques présents dès le chargement de la page). ──
function bind(id, event, handler) {
  const el = document.getElementById(id);
  if (el) el.addEventListener(event, handler);
}
function initStaticListeners() {
  bind('askInput', 'input', onAskInput);
  bind('askInput', 'keydown', e => { if (e.key === 'Enter') submitQuestion(); });
  bind('trendInput', 'keydown', e => { if (e.key === 'Enter') loadTrend(); });
  bind('eluYear', 'change', onEluYearChange);
  bind('seanceYear', 'change', renderSeanceYearList);
  bind('seanceList', 'change', e => onSeanceListChange(e.target));
  bind('adminLoginForm', 'submit', submitAdminLogin);
  bind('setMaxSources', 'input', e => updateSetting('maxSources', +e.target.value));
  bind('setTopK', 'input', e => updateSetting('topK', +e.target.value));
  bind('setScoreMin', 'input', e => updateSetting('scoreMin', +e.target.value));
  bind('setOrder', 'change', e => updateSetting('order', e.target.value));
  bind('setCacheSize', 'input', e => updateSetting('cacheSize', +e.target.value));
}

// ── INITIALISATION ──
fetchVersion();
applyTheme();   // le <head> a déjà posé le thème ; on confirme après chargement des modules
syncModeUI();
initSettingsDrag();
initSettingsOverlay();
initAdminLoginOverlay();
initStaticListeners();
initEluCombo();
handleDeepLink();
checkAdminSession();
