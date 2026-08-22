// Smoke test frontend (Chromium headless) — ne teste pas les données, juste
// que l'app se charge et réagit sans erreur JS/console/CSP. Complète les
// tests backend (pytest) : plusieurs bugs de ce projet n'étaient visibles
// qu'au rendu réel dans un navigateur (ex. `[hidden]` neutralisé par une
// règle CSS de même spécificité — voir PR #107/#110).
import { test, expect } from '@playwright/test';

const PUBLIC_TABS = ['chat', 'stats', 'elus', 'seances'];

// Attache les écouteurs d'erreurs AVANT toute navigation, sinon on rate les
// erreurs survenant pendant le chargement initial.
//
// Chrome logge lui-même en "error" tout chargement de ressource en échec
// (ex. "Failed to load resource: ... 401") même quand le code applicatif
// gère proprement la réponse — c'est le cas attendu de checkAdminSession()
// qui interroge /admin/me pour savoir si une session existe déjà (401 pour
// tout visiteur anonyme). On ignore ces lignes de diagnostic réseau du
// navigateur ; on garde tout le reste (exceptions JS non interceptées,
// violations CSP, vraies erreurs applicatives).
const BENIGN_RESOURCE_ERROR = /^Failed to load resource:/;
function trackErrors(page) {
  const errors = [];
  page.on('console', msg => {
    if (msg.type() === 'error' && !BENIGN_RESOURCE_ERROR.test(msg.text())) errors.push(msg.text());
  });
  page.on('pageerror', err => errors.push(String(err)));
  return errors;
}

test('la page charge sans erreur console', async ({ page }) => {
  const errors = trackErrors(page);
  await page.goto('/');
  await expect(page.locator('#tab-chat')).toBeVisible();
  await expect(page.locator('#panel-chat')).toHaveClass(/active/);
  expect(errors).toEqual([]);
});

test('changement de thème', async ({ page }) => {
  const errors = trackErrors(page);
  await page.goto('/');
  const html = page.locator('html');
  const before = await html.getAttribute('data-theme');
  await page.locator('#themeBtn').click();
  await expect
    .poll(() => html.getAttribute('data-theme'))
    .not.toBe(before);
  expect(errors).toEqual([]);
});

for (const tab of PUBLIC_TABS) {
  test(`onglet public « ${tab} » s'ouvre sans erreur`, async ({ page }) => {
    const errors = trackErrors(page);
    await page.goto('/');
    await page.locator(`#tab-${tab}`).click();
    await expect(page.locator(`#panel-${tab}`)).toHaveClass(/active/);
    await expect(page.locator(`#tab-${tab}`)).toHaveClass(/active/);
    expect(errors).toEqual([]);
  });
}

test('la connexion admin ouvre le formulaire de login', async ({ page }) => {
  const errors = trackErrors(page);
  await page.goto('/');
  await page.locator('#adminLoginBtn').click();
  await expect(page.locator('#adminLoginOverlay')).toHaveClass(/open/);
  await page.locator('#adminLoginOverlay .settings-close').click();
  await expect(page.locator('#adminLoginOverlay')).not.toHaveClass(/open/);
  expect(errors).toEqual([]);
});

test('le panneau Options ouvre et ferme', async ({ page }) => {
  const errors = trackErrors(page);
  await page.goto('/');
  await page.locator('[data-click="openSettings"]').click();
  await expect(page.locator('#settingsOverlay')).toHaveClass(/open/);
  await page.locator('#settingsOverlay .settings-close').click();
  await expect(page.locator('#settingsOverlay')).not.toHaveClass(/open/);
  expect(errors).toEqual([]);
});

test('pas de débordement horizontal en 390px de large', async ({ page }) => {
  const errors = trackErrors(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  for (const tab of PUBLIC_TABS) {
    await page.locator(`#tab-${tab}`).click();
    const { scrollWidth, clientWidth } = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }));
    expect(scrollWidth, `débordement horizontal sur l'onglet ${tab}`).toBeLessThanOrEqual(clientWidth);
  }
  expect(errors).toEqual([]);
});
