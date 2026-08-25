// Config du smoke test frontend : démarre le backend FastAPI (données locales
// uniquement — /stats /elus /seances /health ne nécessitent aucune clé API)
// et le frontend statique (avec la CSP de production), puis lance Chromium.
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  timeout: 30_000,
  // Le tout premier appel à /elus ou /seances construit l'index côté serveur
  // (base des PV + chapitrage vidéo + questions écrites). Sur un runner CI
  // froid, avec plusieurs workers qui l'attaquent en même temps, cela dépasse
  // les 5 s par défaut — d'où des échecs qui ne reproduisaient jamais en local,
  // où le serveur était déjà chaud.
  expect: { timeout: 15_000 },
  fullyParallel: true,
  reporter: 'list',
  use: {
    baseURL: 'http://localhost:5500',
    trace: 'retain-on-failure',
    // Permet de pointer vers un Chromium déjà installé sur la machine (utile
    // en environnement restreint où le téléchargement du binaire Playwright
    // est bloqué) plutôt que d'en télécharger un. Non défini par défaut : la
    // CI télécharge normalement via `playwright install`.
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_PATH
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH }
      : {},
  },
  webServer: [
    {
      command: 'node serve-frontend.js',
      url: 'http://localhost:5500',
      timeout: 10_000,
      reuseExistingServer: !process.env.CI,
    },
    {
      command: 'python3 -m uvicorn app:app --host 0.0.0.0 --port 8000',
      cwd: '../backend',
      // Sonde /elus et NON /health : /health répond avant que le moindre index
      // soit construit, si bien que le premier test à ouvrir l'onglet Par élu·e
      // payait la construction complète (base des PV + chapitrage vidéo +
      // questions écrites) sur son propre budget de 30 s, en concurrence avec
      // les autres workers. Une action Playwright — un `click` — n'est pas
      // couverte par `expect.timeout` : elle hérite du temps restant du test,
      // d'où un échec par timeout sans rapport avec ce qui était testé.
      // En sondant l'endpoint le plus lourd, la construction a lieu AVANT le
      // premier test, une seule fois, hors de tout budget de test.
      url: 'http://localhost:8000/elus',
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
    },
  ],
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
});
