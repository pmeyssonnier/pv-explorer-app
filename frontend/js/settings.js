// ── OPTIONS (menu ⚙️) — préférences par navigateur (localStorage) ──
import { appVersion } from './config.js';
import { trimCaches, renderHistory } from './chat.js';

const SETTINGS_KEY = 'pv_settings';
const SETTINGS_DEFAULTS = {
  theme: 'auto', maxSources: 15, topK: 30, scoreMin: 0,
  model: 'claude-sonnet-4-6', order: 'relevance', cacheSize: 15,
};
function loadSettings() {
  try { return Object.assign({}, SETTINGS_DEFAULTS, JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}')); }
  catch (e) { return Object.assign({}, SETTINGS_DEFAULTS); }
}
export let settings = loadSettings();
function saveSettings() { try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings)); } catch (e) {} }

// Icône thème séparée du ⚙️ (en-tête) : un tap change directement l'apparence
// — action fréquente — sans ouvrir le tiroir d'Options, qui ne porte plus
// que les réglages de recherche (plus rarement modifiés).
const THEME_ICONS = {
  light: '<circle cx="12" cy="12" r="4.2"/><path d="M12 3v2.4M12 18.6V21M4.9 4.9l1.7 1.7M17.4 17.4l1.7 1.7M3 12h2.4M18.6 12H21M4.9 19.1l1.7-1.7M17.4 6.6l1.7-1.7"/>',
  dark: '<path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a6.8 6.8 0 0 0 10.5 10.5Z"/>',
  auto: '<circle cx="12" cy="12" r="8.5"/><path d="M12 3.5a8.5 8.5 0 0 0 0 17Z" fill="currentColor" stroke="none"/>',
};
const THEME_LABELS = { light: 'clair', dark: 'sombre', auto: 'auto' };
const THEME_ORDER = ['light', 'dark', 'auto'];

// Couleur de chrome navigateur (barre d'état mobile, aperçu multitâche, fond
// du splash screen à l'installation) — doit suivre le thème résolu comme le
// reste de l'app ; valeurs alignées sur --blanc (styles.css) en clair/sombre.
// Même résolution qu'au chargement (voir le script inline dans <head>, qui
// évite le flash avant que ce module ne soit chargé).
const THEME_COLOR_LIGHT = '#fffdf9';
const THEME_COLOR_DARK = '#201a15';

export function applyTheme() {
  if (settings.theme === 'light' || settings.theme === 'dark')
    document.documentElement.setAttribute('data-theme', settings.theme);
  else
    document.documentElement.removeAttribute('data-theme');   // « auto » → préférence OS
  const icon = document.getElementById('themeIcon');
  if (icon) icon.innerHTML = THEME_ICONS[settings.theme] || THEME_ICONS.auto;
  const btn = document.getElementById('themeBtn');
  if (btn) {
    const label = `Thème : ${THEME_LABELS[settings.theme] || 'auto'}`;
    btn.setAttribute('aria-label', label);
    btn.setAttribute('title', label + ' (appuyer pour changer)');
  }
  const meta = document.getElementById('themeColorMeta');
  if (meta) {
    const isDark = settings.theme === 'dark' ||
      (settings.theme === 'auto' && window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
    meta.setAttribute('content', isDark ? THEME_COLOR_DARK : THEME_COLOR_LIGHT);
  }
}
export function cycleTheme() {
  updateSetting('theme', THEME_ORDER[(THEME_ORDER.indexOf(settings.theme) + 1) % THEME_ORDER.length]);
}
export function openSettings() { renderSettings(); document.getElementById('settingsOverlay').classList.add('open'); }
export function closeSettings() {
  const panel = document.getElementById('settingsPanel');
  if (panel) panel.style.transform = '';   // efface un éventuel reliquat de glissement interrompu
  document.getElementById('settingsOverlay').classList.remove('open');
}
export function updateSetting(key, val) {
  settings[key] = val; saveSettings();
  if (key === 'theme') applyTheme();
  if (key === 'cacheSize') { trimCaches(); renderHistory(); }
  renderSettings();
}
export function resetSettings() {
  settings = Object.assign({}, SETTINGS_DEFAULTS);
  saveSettings(); applyTheme(); trimCaches(); renderHistory(); renderSettings();
}
// Reflète l'état courant dans les contrôles du panneau.
export function renderSettings() {
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };
  const txt = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  set('setMaxSources', settings.maxSources); txt('valMaxSources', settings.maxSources);
  set('setTopK', settings.topK); txt('valTopK', settings.topK);
  set('setScoreMin', settings.scoreMin); txt('valScoreMin', (+settings.scoreMin).toFixed(2));
  set('setOrder', settings.order);
  set('setCacheSize', settings.cacheSize); txt('valCacheSize', settings.cacheSize);
  txt('appVersion', appVersion);
}

// Glisser la poignée du tiroir (mobile) vers le bas pour le fermer — pattern
// natif des bottom sheets. Sans effet sur tablette/PC (poignée masquée par
// CSS, donc jamais de pointerdown dessus).
export function initSettingsDrag() {
  const handle = document.getElementById('settingsHandle');
  const panel = document.getElementById('settingsPanel');
  if (!handle || !panel) return;
  const THRESHOLD = 90;
  let startY = null, dragging = false;

  handle.addEventListener('pointerdown', e => {
    dragging = true; startY = e.clientY;
    panel.classList.add('dragging');
    handle.setPointerCapture(e.pointerId);
  });
  handle.addEventListener('pointermove', e => {
    if (!dragging) return;
    panel.style.transform = `translateY(${Math.max(0, e.clientY - startY)}px)`;
  });
  const release = e => {
    if (!dragging) return;
    dragging = false;
    panel.classList.remove('dragging');
    const dy = Math.max(0, e.clientY - startY);
    panel.style.transform = '';
    if (dy > THRESHOLD) closeSettings();
  };
  handle.addEventListener('pointerup', release);
  handle.addEventListener('pointercancel', release);
}

// ── MODE D'INTERROGATION (Rapide / Réflexion) ──
// Remplace le sélecteur de modèle (retiré des Options) : le mode PILOTE le
// paramètre `model` envoyé au backend. Rapide = modèle léger (Haiku), Réflexion
// = modèle précis (Sonnet). Persisté comme le reste des réglages.
const MODEL_RAPIDE = 'claude-haiku-4-5-20251001';
const MODEL_REFLEXION = 'claude-sonnet-4-6';
export function currentMode() { return settings.model === MODEL_RAPIDE ? 'rapide' : 'reflexion'; }
export function setMode(mode) {
  settings.model = (mode === 'rapide') ? MODEL_RAPIDE : MODEL_REFLEXION;
  saveSettings();
  syncModeUI();
}
export function syncModeUI() {
  const m = currentMode();
  document.querySelectorAll('#modeSeg button').forEach(b =>
    b.classList.toggle('on', b.dataset.mode === m));
}
