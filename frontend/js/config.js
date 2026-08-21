// ═══════════════════════════════════════════════════════════════
// CONFIGURATION — À MODIFIER APRÈS DÉPLOIEMENT DU BACKEND
// ═══════════════════════════════════════════════════════════════
// Remplace l'URL de production par celle de ton backend Railway/Render.
// En local (fichier ouvert directement), l'app utilise automatiquement localhost.
const API_PROD = "https://pv-explorer-api.onrender.com";  // backend Render
const API_LOCAL = "http://localhost:8000";

// Détection automatique : localhost en dev, URL prod sinon
export const API_URL = (location.hostname === "localhost" || location.hostname === "127.0.0.1" || location.protocol === "file:")
  ? API_LOCAL
  : API_PROD;

// Version applicative : source unique = le backend (GET /health → { version }). La
// constante locale n'est qu'un REPLI affiché si le backend est injoignable (hors-ligne,
// ou réveil du service Render). Garder cette valeur vaguement à jour, sans plus.
export const APP_VERSION = '1.6.0';
export let appVersion = APP_VERSION;

// Récupère la version auprès du backend (source unique). Silencieux en cas
// d'échec : on garde le repli local `APP_VERSION`. Met à jour l'affichage même
// si le panneau Options est déjà ouvert.
export async function fetchVersion() {
  try {
    const r = await fetch(`${API_URL}/health`, { cache: 'no-store' });
    if (!r.ok) return;
    const d = await r.json();
    if (d && d.version) {
      appVersion = d.version;
      const el = document.getElementById('appVersion');
      if (el) el.textContent = appVersion;
    }
  } catch (e) { /* backend injoignable → repli local conservé */ }
}
