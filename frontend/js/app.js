// ── POINT D'ENTRÉE — initialisation + câblage des gestionnaires "onclick"
// inline du HTML (qui exigent des fonctions globales, contrairement aux
// modules ES) sur `window`. Chaque fonctionnalité vit dans son propre module ;
// ce fichier n'orchestre que le démarrage et la navigation par onglets. ──
import { fetchVersion } from './config.js';
import {
  applyTheme, cycleTheme, openSettings, closeSettings, updateSetting,
  resetSettings, setMode, syncModeUI, initSettingsDrag,
} from './settings.js';
import {
  onAskInput, askSuggestion, reuseQuestion, clearHistory, removeHistoryItem,
  newSearch, toggleDictation, submitQuestion, copyAnswer, downloadAnswer, shareAnswer,
} from './chat.js';
import {
  loadStats, toggleYear, drillInto, drillTo, selectSeance, clearSeance,
  selectTheme, setMetric, shareStats, trendSuggestion, loadTrend,
} from './stats.js';
import { loadElus, populateElus, onEluInput, shareElu, setPendingEluKey } from './elus.js';
import {
  loadSeances, loadSeance, renderSeanceYearList, shareSeance, shareSeanceDate,
  setPendingSeanceDate,
} from './seances.js';

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

// ── Fonctions référencées depuis des attributs "onclick"/"oninput"/"onchange"
// inline du HTML (statique dans index.html, ou généré dynamiquement dans les
// templates des modules) : doivent exister sur `window`, les modules ES ne
// créent pas de globales automatiquement. ──
Object.assign(window, {
  switchTab,
  cycleTheme, openSettings, closeSettings, updateSetting, resetSettings, setMode,
  onAskInput, askSuggestion, reuseQuestion, clearHistory, removeHistoryItem,
  newSearch, toggleDictation, submitQuestion, copyAnswer, downloadAnswer, shareAnswer,
  toggleYear, drillInto, drillTo, selectSeance, clearSeance, selectTheme, setMetric,
  shareStats, trendSuggestion, loadTrend,
  populateElus, onEluInput, shareElu,
  loadSeance, renderSeanceYearList, shareSeance, shareSeanceDate,
});

// ── INITIALISATION ──
fetchVersion();
applyTheme();   // le <head> a déjà posé le thème ; on confirme après chargement des modules
syncModeUI();
initSettingsDrag();
handleDeepLink();
