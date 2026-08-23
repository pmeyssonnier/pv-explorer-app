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
  newSearch, toggleDictation, submitQuestion, copyAnswer, downloadAnswer, shareAnswer,
} from './chat.js';
import {
  loadStats, toggleYear, drillInto, drillTo, selectSeance, clearSeance,
  selectTheme, setMetric, shareStats, trendSuggestion, loadTrend,
} from './stats.js';
import {
  loadElus, populateElus, onEluSelectChange, onEluYearChange, shareElu, setPendingEluKey,
} from './elus.js';
import {
  loadSeances, renderSeanceYearList, onSeanceListChange, shareSeance,
  jumpToSeance, setPendingSeanceDate,
} from './seances.js';
import {
  checkAdminSession, openAdminLogin, closeAdminLogin, initAdminLoginOverlay,
  submitAdminLogin, adminLogout, cancelAdminExtract, confirmAdminPublish,
  cancelQeExtract, confirmQePublish,
} from './admin.js';

// ── ONGLETS ──
function switchTab(tab) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.getElementById('tab-' + tab).classList.add('active');
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
  if (params.get('tab') === 'stats') switchTab('stats');
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
  newSearch, toggleDictation, submitQuestion, copyAnswer, downloadAnswer, shareAnswer,
  toggleYear, drillInto, drillTo, selectSeance, clearSeance, selectTheme, setMetric,
  shareStats, trendSuggestion, loadTrend,
  shareElu,
  shareSeance, jumpToSeance,
  openAdminLogin, closeAdminLogin, adminLogout, cancelAdminExtract, confirmAdminPublish,
  cancelQeExtract, confirmQePublish,
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
  bind('eluRole', 'change', populateElus);
  bind('eluSelect', 'change', e => onEluSelectChange(e.target));
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
handleDeepLink();
checkAdminSession();
