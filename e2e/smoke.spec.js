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

// ── Recherche d'élu·e (combobox, voir frontend/js/elus.js) ──
// Ces tests interrogent la vraie base (le backend local sert /elus) mais ne
// codent en dur AUCUN nom : les cas sont dérivés de la liste affichée, pour
// rester valables quand la base évolue.

// Ouvre l'onglet « Par élu·e » et déplie la liste des élu·e·s.
async function openEluCombo(page) {
  await page.locator('#tab-elus').click();
  await expect(page.locator('#eluResult .elu-name')).toBeVisible();
  await page.locator('#eluSearch').click();
  await expect(page.locator('#eluOptions')).toBeVisible();
}
const optionNames = page => page.$$eval('#eluOptions .elu-opt .elu-opt-name', els => els.map(e => e.textContent.trim()));

// Le cas que le <select> natif ne savait PAS traiter : sa frappe rapide ne
// cherche que dans le début du libellé affiché, donc le prénom.
test('recherche d\'un·e élu·e par son nom de famille', async ({ page }) => {
  const errors = trackErrors(page);
  await page.goto('/');
  await openEluCombo(page);
  const noms = await optionNames(page);
  const cible = noms.find(n => n.split(/\s+/).length > 1);
  expect(cible, 'aucun nom en deux mots dans la liste').toBeTruthy();
  const famille = cible.split(/\s+/).pop();

  await page.locator('#eluSearch').fill(famille);
  await expect(page.locator('#eluOptions .elu-opt').first()).toBeVisible();
  expect(await optionNames(page)).toContain(cible);
  expect(errors).toEqual([]);
});

test('recherche insensible aux accents', async ({ page }) => {
  const errors = trackErrors(page);
  await page.goto('/');
  await openEluCombo(page);
  const noms = await optionNames(page);
  // Un nom accentué, cherché sans son accent (« cecile » → « Cécile »).
  const accentue = noms.find(n => n.normalize('NFD') !== n.normalize('NFC'));
  expect(accentue, 'aucun nom accentué dans la liste').toBeTruthy();
  const mot = accentue.split(/\s+/).find(w => w.normalize('NFD') !== w.normalize('NFC'));
  const sansAccent = mot.normalize('NFD').replace(/[\u0300-\u036f]/g, '');

  await page.locator('#eluSearch').fill(sansAccent);
  await expect(page.locator('#eluOptions .elu-opt').first()).toBeVisible();
  expect(await optionNames(page)).toContain(accentue);
  expect(errors).toEqual([]);
});

test('sélection au clavier (flèches + Entrée) puis fermeture', async ({ page }) => {
  const errors = trackErrors(page);
  await page.goto('/');
  await openEluCombo(page);

  await page.keyboard.press('ArrowDown');
  // L'option mise en avant est celle qu'Entrée doit charger (on la lit plutôt
  // que de supposer son rang : la liste s'ouvre sur la sélection courante).
  const attendu = await page.locator('#eluOptions .elu-opt-active .elu-opt-name').textContent();
  await page.keyboard.press('Enter');

  await expect(page.locator('#eluResult .elu-name')).toHaveText(attendu);
  await expect(page.locator('#eluOptions')).toBeHidden();
  await expect(page.locator('#eluSearch')).toHaveAttribute('aria-expanded', 'false');
  await expect(page.locator('#eluSearch')).toHaveValue(attendu);
  expect(errors).toEqual([]);
});

test('aucun résultat : la liste le dit', async ({ page }) => {
  const errors = trackErrors(page);
  await page.goto('/');
  await openEluCombo(page);
  await page.locator('#eluSearch').fill('zzzzq');
  await expect(page.locator('#eluOptions .elu-opt-empty')).toContainText('Aucun·e élu·e');
  expect(errors).toEqual([]);
});

// ── Filtres de la fiche (puces de rôle / de type, filtres repliés) ──

// Sélectionne le/la premier·ère élu·e du Collège ayant exercé PLUSIEURS rôles
// (donc plusieurs puces) — c'est le cas que le filtre par mandat sert.
async function ficheMultiRoles(page) {
  await openEluCombo(page);
  const college = await page.$$eval('#eluOptions .elu-opt', els => els
    .filter(e => e.querySelector('.elu-opt-role-college'))
    .map(e => e.dataset.key));
  for (const key of college.slice(0, 8)) {
    await page.locator('#eluSearch').click();
    await page.locator(`#eluOptions .elu-opt[data-key="${key}"]`).click();
    await expect(page.locator('#eluResult .elu-name')).toBeVisible();
    if (await page.locator('#eluRoleChips .elu-chip').count() >= 2) return true;
  }
  return false;
}

// Chaque action est rattachée au rôle exercé À SA DATE (mandats déclarés) :
// isoler un rôle doit donc afficher exactement le nombre d'actions annoncé
// par sa puce.
test('les puces de rôle filtrent les actions du mandat correspondant', async ({ page }) => {
  const errors = trackErrors(page);
  await page.goto('/');
  test.skip(!await ficheMultiRoles(page), 'aucun·e élu·e à plusieurs rôles dans la base');

  const chip = page.locator('#eluRoleChips .elu-chip').nth(1);
  const annonce = Number((await chip.textContent()).match(/(\d+)\s+action/)[1]);
  await chip.click();

  await expect(chip).toHaveClass(/elu-chip-active/);
  await expect(page.locator('#eluResult .elu-item')).toHaveCount(annonce);

  await chip.click();   // reclic → tous les rôles
  await expect(chip).not.toHaveClass(/elu-chip-active/);
  expect(errors).toEqual([]);
});

test('les filtres année et thématique sont repliés au départ', async ({ page }) => {
  const errors = trackErrors(page);
  await page.goto('/');
  await page.locator('#tab-elus').click();
  await expect(page.locator('#eluResult .elu-name')).toBeVisible();

  const more = page.locator('#eluMoreFilters');
  await expect(more).toBeVisible();
  await expect(page.locator('#eluYear')).toBeHidden();      // replié = non atteignable
  await page.locator('.elu-more-summary').click();
  await expect(page.locator('#eluYear')).toBeVisible();

  // Une année choisie reste lisible même une fois le bloc replié.
  const annee = (await page.$$eval('#eluYear option', els => els.map(e => e.value)))[1];
  await page.locator('#eluYear').selectOption(annee);
  await expect(page.locator('#eluMoreActive')).toHaveText(` · ${annee}`);
  expect(errors).toEqual([]);
});
