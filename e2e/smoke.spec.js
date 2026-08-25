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

// Ouvre l'onglet « Par élu·e » et déplie la liste des élu·e·s. L'onglet
// s'ouvre sur son écran d'accueil : aucune fiche n'est chargée tant qu'on n'a
// pas choisi quelqu'un.
async function openEluCombo(page) {
  await page.locator('#tab-elus').click();
  await expect(page.locator('#eluResult .elu-accueil')).toBeVisible();
  // Le placeholder annonce le nombre d'élu·e·s : il ne le fait qu'une fois
  // /elus arrivé. On attend ce signal plutôt que de cliquer dans le vide.
  await expect(page.locator('#eluSearch')).toHaveAttribute('placeholder', /parmi/);
  await page.locator('#eluSearch').click();
  await expect(page.locator('#eluOptions .elu-opt').first()).toBeVisible();
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

const compte = async loc => Number((await loc.textContent()).match(/(\d+)\s+action/)[1]);
const totalAffiche = page => compte(page.locator('#eluResult .elu-summary'));

// Chaque action est rattachée au rôle exercé À SA DATE (mandats déclarés) :
// isoler un rôle doit afficher exactement le nombre d'actions annoncé par sa
// puce, et cocher les deux doit rendre leur somme — les puces sont des
// interrupteurs indépendants, pas un choix unique.
test('les puces de rôle sont des interrupteurs cumulables, et le total suit', async ({ page }) => {
  const errors = trackErrors(page);
  await page.goto('/');
  test.skip(!await ficheMultiRoles(page), 'aucun·e élu·e à plusieurs rôles dans la base');

  const [chip1, chip2] = [0, 1].map(i => page.locator('#eluRoleChips .elu-chip').nth(i));
  const [n1, n2] = [await compte(chip1), await compte(chip2)];
  const totalInitial = await totalAffiche(page);

  await chip1.click();
  await expect(chip1).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#eluResult .elu-item')).toHaveCount(n1);
  expect(await totalAffiche(page)).toBe(n1);

  await chip2.click();   // les DEUX cochés → somme, pas remplacement
  await expect(chip1).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#eluResult .elu-item')).toHaveCount(n1 + n2);

  await chip1.click();   // décoché → il ne reste que le second
  await expect(chip1).toHaveAttribute('aria-pressed', 'false');
  await expect(page.locator('#eluResult .elu-item')).toHaveCount(n2);

  await chip2.click();   // plus rien de coché → tout revient
  expect(await totalAffiche(page)).toBe(totalInitial);
  expect(errors).toEqual([]);
});

test('les filtres année et thématique sont repliés au départ', async ({ page }) => {
  const errors = trackErrors(page);
  await page.goto('/');
  await openEluCombo(page);
  await page.locator('#eluOptions .elu-opt').first().click();
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

// L'onglet n'ouvre plus la fiche d'une personne prise dans l'ordre
// alphabétique : il invite à chercher, et ne charge que ce qu'on lui demande.
test('l\'onglet s\'ouvre sur un écran d\'accueil, sans élu·e présélectionné·e', async ({ page }) => {
  const errors = trackErrors(page);
  await page.goto('/');
  await page.locator('#tab-elus').click();

  await expect(page.locator('#eluResult .elu-accueil')).toBeVisible();
  await expect(page.locator('#eluResult .elu-name')).toHaveCount(0);
  await expect(page.locator('#eluSearch')).toHaveValue('');
  await expect(page.locator('#eluRoleChips')).toBeHidden();
  await expect(page.locator('#eluTypeFilterChips')).toBeHidden();
  await expect(page.locator('#eluMoreFilters')).toBeHidden();

  // Un choix explicite ouvre bien une fiche, et l'accueil s'efface.
  await page.locator('#eluSearch').click();
  await page.locator('#eluOptions .elu-opt').first().click();
  await expect(page.locator('#eluResult .elu-name')).toBeVisible();
  await expect(page.locator('#eluResult .elu-accueil')).toHaveCount(0);
  expect(errors).toEqual([]);
});

// « Débat filmé » n'est pas un type exclusif mais une FACETTE : la plupart des
// débats filmés sont appariés à leur point PV et gardent leur type (question
// orale, demande…) tout en portant le lien « ▶ Voir le débat ». La puce doit
// compter TOUS ces points — pas seulement les chapitres vidéo orphelins.
async function ficheAvecDebatFilme(page) {
  await openEluCombo(page);
  const cles = await page.$$eval('#eluOptions .elu-opt', els => els.map(e => e.dataset.key));
  for (const key of cles.slice(0, 12)) {
    await page.locator('#eluSearch').click();
    await page.locator(`#eluOptions .elu-opt[data-key="${key}"]`).click();
    await expect(page.locator('#eluResult .elu-name')).toBeVisible();
    if (await page.locator('#eluTypeFilterChips .elu-chip', { hasText: 'filmé' }).count()) return true;
  }
  return false;
}

test('la puce « débat filmé » compte tous les points menant au débat', async ({ page }) => {
  const errors = trackErrors(page);
  await page.goto('/');
  test.skip(!await ficheAvecDebatFilme(page), 'aucune fiche avec débat filmé dans les 12 premières');

  const chip = page.locator('#eluTypeFilterChips .elu-chip', { hasText: 'filmé' });
  const annonce = Number((await chip.textContent()).match(/(\d+)/)[1]);
  await chip.click();

  // Chaque action retenue mène bien au débat, et le compte annoncé est tenu.
  await expect(page.locator('#eluResult .elu-item')).toHaveCount(annonce);
  await expect(page.locator('#eluResult .elu-links a:has-text("Voir le débat")')).toHaveCount(annonce);
  expect(errors).toEqual([]);
});

// ── Onglet Séances : filtre par intervenant·e sur un point à PLUSIEURS
// répondant·e·s ──
// Un point répondu à deux (« Audrey Henry et Justine Harzé ») doit se
// retrouver en cherchant l'un OU l'autre nom, tout en continuant d'afficher
// les deux sur sa ligne.

// Ouvre une séance donnée. La sélection d'une ANNÉE charge elle-même une
// séance : on laisse ce chargement retomber avant de choisir la nôtre, sinon
// sa réponse tardive écrase la sélection.
async function ouvrirSeance(page, annee, date, libelle) {
  await page.locator('#tab-seances').click();
  await page.locator('#seanceYear').selectOption(annee);
  await expect(page.locator('#seanceResult .elu-name')).toContainText(annee);
  await expect(page.locator('#seancePointsList .elu-item').first()).toBeVisible();
  await page.locator('#seanceList').selectOption(date);
  await expect(page.locator('#seanceResult .elu-name')).toHaveText(libelle);
}

test('un point à plusieurs répondant·e·s se retrouve par chacun de leurs noms', async ({ page }) => {
  const errors = trackErrors(page);
  await page.goto('/');
  await ouvrirSeance(page, '2010', '2010-09-29', '29/09/2010');
  await page.locator('#seanceFilterToggle').click();

  // Plus aucune « personne » composée dans la liste des intervenant·e·s.
  await page.locator('#seancePersonFilter').click();
  await expect(page.locator('#seancePersonOptions .elu-opt').first()).toBeVisible();
  const options = await page.$$eval('#seancePersonOptions .elu-opt', els => els.map(e => e.textContent));
  expect(options.filter(o => / et /.test(o))).toEqual([]);
  await page.keyboard.press('Escape');

  // Un point de cette séance a plusieurs répondant·e·s : sa ligne les montre tous.
  const ligne = page.locator('#seancePointsList .elu-item', { has: page.locator('.elu-rep') })
    .filter({ hasText: ' et ' }).first();
  const rep = (await ligne.locator('.elu-rep').textContent()).trim();
  // « A, B et C » (voir utils.text.liste_fr) : la virgule sépare autant que
  // le « et » final.
  const noms = rep.replace(/^[^:]+:\s*/, '').split(/,\s*|\s+et\s+/);
  expect(noms.length).toBeGreaterThan(1);

  // Filtré sur le PREMIER nom, puis sur le DERNIER : le point reste trouvable
  // dans les deux cas, et garde l'affichage de tous ses répondant·e·s.
  for (const nom of [noms[0], noms[noms.length - 1]]) {
    await choisirIntervenant(page, nom);
    const retrouve = page.locator('#seancePointsList .elu-item').filter({ hasText: rep });
    await expect(retrouve).toHaveCount(1);
    await expect(retrouve.locator('.elu-rep')).toHaveText(rep);
  }
  expect(errors).toEqual([]);
});

// Choisit un·e intervenant·e dans le combobox du filtre, en tapant son NOM DE
// FAMILLE — ce que le menu natif qu'il remplace ne savait pas chercher.
async function choisirIntervenant(page, nom) {
  const famille = nom.split(/\s+/).pop();
  await page.locator('#seancePersonFilter').click();
  await page.locator('#seancePersonFilter').fill(famille);
  const option = page.locator(`#seancePersonOptions .elu-opt[data-key="${nom}"]`);
  await expect(option).toBeVisible();
  await option.click();
  await expect(page.locator('#seancePersonFilter')).toHaveValue(nom);
}

test('le filtre par intervenant·e cherche sur le nom de famille, et se défait', async ({ page }) => {
  const errors = trackErrors(page);
  await page.goto('/');
  await ouvrirSeance(page, '2010', '2010-09-29', '29/09/2010');
  await page.locator('#seanceFilterToggle').click();

  const tousLesPoints = await page.locator('#seancePointsList .elu-item').count();
  // Un nom pris dans la liste elle-même (aucun nom en dur).
  await page.locator('#seancePersonFilter').click();
  const nom = await page.locator('#seancePersonOptions .elu-opt').nth(1).getAttribute('data-key');
  await page.keyboard.press('Escape');

  await choisirIntervenant(page, nom);
  const filtres = await page.locator('#seancePointsList .elu-item').count();
  expect(filtres).toBeGreaterThan(0);
  expect(filtres).toBeLessThan(tousLesPoints);

  // La 1re entrée de la liste ramène tout le monde.
  await page.locator('#seancePersonFilter').click();
  await page.locator('#seancePersonOptions .elu-opt').first().click();
  await expect(page.locator('#seancePersonFilter')).toHaveValue('');
  await expect(page.locator('#seancePointsList .elu-item')).toHaveCount(tousLesPoints);
  expect(errors).toEqual([]);
});

// Les puces de TYPE partitionnent les points : leur somme doit égaler le
// nombre annoncé en tête. Elle tombait à 659 pour 676 points en 2025 — les
// chapitres vidéo sans point de PV n'avaient aucune puce. Les FACETTES
// (« Avec débat filmé », statuts) se superposent aux types et ne s'y ajoutent
// pas : elles vivent dans une rangée à part.
test('la somme des puces de type égale le nombre de points annoncé', async ({ page }) => {
  const errors = trackErrors(page);
  await page.goto('/');
  await page.locator('#tab-seances').click();
  await page.locator('#seanceYear').selectOption('2025');
  await expect(page.locator('#seanceResult .elu-name')).toContainText('2025');
  await expect(page.locator('#seancePointsList .elu-item').first()).toBeVisible();
  // Vue agrégée de l'année : le plus grand nombre de points, tous types mêlés.
  await page.locator('#seanceList').selectOption('__all__');
  await expect(page.locator('#seanceResult .elu-name')).toContainText('Toutes les séances');
  await page.locator('#seanceFilterToggle').click();

  const annonce = Number((await page.locator('#seanceResult .elu-role').textContent()).match(/(\d+)\s+points/)[1]);
  const compte = async sel => (await page.$$eval(sel, els => els.map(e => +e.textContent.match(/\((\d+)\)/)[1])));
  const types = await compte('#seanceTypeChips .elu-chip');
  expect(types.reduce((a, b) => a + b, 0)).toBe(annonce);

  // Les facettes existent, et aucune ne dépasse le total.
  const facettes = await compte('#seanceFacetChips .elu-chip');
  expect(facettes.length).toBeGreaterThan(0);
  facettes.forEach(n => expect(n).toBeLessThanOrEqual(annonce));

  // Chaque puce de type ramène exactement le nombre de points qu'elle annonce.
  const premiere = page.locator('#seanceTypeChips .elu-chip').last();   // le type le plus rare
  const n = Number((await premiere.textContent()).match(/\((\d+)\)/)[1]);
  await premiere.click();
  await expect(page.locator('#seancePointsList .elu-item')).toHaveCount(n);
  expect(errors).toEqual([]);
});

// ── Onglet Statistiques : KPI par série et tableaux par année ──
// Le tout premier /stats parcourt les 171 séances pour construire la synthèse
// par année (~8 s à froid, davantage quand plusieurs workers l'attaquent en
// même temps). Les 15 s par défaut suffisaient tant qu'un seul test ouvrait
// l'onglet ; à deux, elles ne suffisent plus.
const STATS_FROID = { timeout: 45_000 };

test('un KPI d\'activité par série, dans l\'ordre de la légende', async ({ page }) => {
  const errors = trackErrors(page);
  await page.goto('/');
  await page.locator('#tab-stats').click();
  await expect(page.locator('#activityKPIs .act-kpi').first()).toBeVisible(STATS_FROID);

  const kpis = await page.$$eval('#activityKPIs .act-kpi', els => els.map(e => e.getAttribute('title')));
  const legende = await page.$$eval('#activityLegend .yc-legend-chip', els => els.map(e => e.textContent.trim()));
  expect(kpis).toEqual(legende);
  expect(kpis).toContain('Débat filmé hors PV');
  expect(errors).toEqual([]);
});

test('le graphe des statuts totalise les mêmes points que celui d\'activité, et descend au mois', async ({ page }) => {
  const errors = trackErrors(page);
  await page.goto('/');
  await page.locator('#tab-stats').click();
  await expect(page.locator('#statutLegend .yc-legend-chip').first()).toBeVisible(STATS_FROID);

  // 1. Les quatre issues demandées, plus le reliquat neutre en dernier.
  const legende = await page.$$eval('#statutLegend .yc-legend-chip', els => els.map(e => e.textContent.trim()));
  expect(legende).toEqual(['Approuvé', 'Décidé', 'Reporté ou retiré', 'Autres issues', 'Sans issue relevée']);
  // Chaque regroupement nomme ce qu'il replie, sinon il devient un fourre-tout
  // opaque — « Autres issues » ses statuts, « Reporté ou retiré » ses deux.
  const infobulle = nom => page.locator('#statutLegend .yc-legend-chip', { hasText: nom })
    .first().getAttribute('title');
  expect(await infobulle('Autres issues')).toContain('Pris pour information');
  expect(await infobulle('Reporté ou retiré')).toContain('Reporté');

  // 2. Un point porte au plus une issue, et chacun en porte une ligne.
  const colonnes = await page.$$eval('#statutPlot .yc-col', els => els.map(e => [
    e.querySelector('.yc-yr').textContent.trim(),
    +e.querySelector('.yc-val').textContent.replace(/[^\d]/g, ''),
  ]));
  expect(colonnes.length).toBeGreaterThan(3);

  // 3. Les deux graphes comptent les mêmes points et se lisent l'un sous
  //    l'autre : leurs totaux doivent coïncider année par année. C'est ce qui
  //    manquait — 663 contre 658 en 2024, l'écart étant les points de PV sans
  //    issue relevée, désormais empilés sous « Sans issue relevée ».
  const activite = await page.$$eval('#drillPlot .yc-col', els => Object.fromEntries(els.map(e => [
    e.querySelector('.yc-yr').textContent.trim(),
    +e.querySelector('.yc-val').textContent.replace(/[^\d]/g, ''),
  ])));
  colonnes.forEach(([an, n]) => expect([an, n]).toEqual([an, activite[an]]));

  // 4. Le clic sur une année ouvre les mois, dont le total la reconstitue.
  const [annee, total] = colonnes[colonnes.length - 2];
  await page.locator('#statutPlot .yc-col').nth(colonnes.length - 2).click();
  await expect(page.locator('#statutTitle')).toContainText(annee);
  const mois = await page.$$eval('#statutPlot .yc-col', els => els.map(
    e => +e.querySelector('.yc-val').textContent.replace(/[^\d]/g, '')));
  expect(mois.reduce((a, b) => a + b, 0)).toBe(total);
  expect(errors).toEqual([]);
});

test('le périmètre survit au changement de vue, et un lien rouvre la bonne', async ({ page }) => {
  const errors = trackErrors(page);
  await page.goto('/');
  await page.locator('#tab-stats').click();
  await expect(page.locator('#statsPvCount')).toBeVisible(STATS_FROID);

  // 1. Trois vues, l'activité par défaut.
  await expect(page.locator('.subtab')).toHaveCount(3);
  await expect(page.locator('#statsvue-activite')).toHaveAttribute('aria-selected', 'true');

  // 2. Forer une année dans « Activité »… (l'avant-dernière : l'année en cours
  //    est incomplète, mais celle d'avant a ses douze mois).
  const colonnes = await page.$$eval('#drillPlot .yc-col', els => els.length);
  await page.locator('#drillPlot .yc-col').nth(colonnes - 2).click();
  const annee = (await page.textContent('#scopeLabel')).trim();
  expect(annee).toMatch(/^\d{4}$/);
  const attendu = +(await page.textContent('#statsPvCount')).replace(/[^\d]/g, '');
  expect(attendu).toBeGreaterThan(0);

  // 3. …le retrouver dans « Procès-verbaux » : c'est tout l'intérêt de garder
  //    le périmètre au-dessus des sous-onglets.
  await page.locator('#statsvue-pv').click();
  await expect(page.locator('#statsvue-panel-pv')).toHaveClass(/active/);
  await expect(page.locator('#pvScope')).toHaveText(annee);
  expect(await page.$$eval('#pvList .pv-row', els => els.length)).toBe(attendu);
  // Et il survit au retour.
  await page.locator('#statsvue-activite').click();
  await expect(page.locator('#scopeLabel')).toHaveText(annee);

  // 4. Le lien partagé rouvre la vue, pas seulement l'onglet.
  await page.goto('/?tab=stats&vue=pv');
  await expect(page.locator('#statsvue-pv')).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('#statsvue-panel-pv')).toHaveClass(/active/);
  expect(errors).toEqual([]);
});

test('les KPI d\'activité suivent le périmètre, jusqu\'au mois', async ({ page }) => {
  const errors = trackErrors(page);
  await page.goto('/');
  await page.locator('#tab-stats').click();
  await expect(page.locator('#activityKPIs .act-kpi').first()).toBeVisible(STATS_FROID);

  const sommeKPI = () => page.$$eval('#activityKPIs .act-kpi .act-kpi-num',
    els => els.reduce((a, e) => a + (+e.textContent.replace(/[^\d]/g, '') || 0), 0));
  const barres = () => page.$$eval('#activityPlot .yc-col .yc-val',
    els => els.map(e => +e.textContent.replace(/[^\d]/g, '') || 0));

  // 1. Toutes les années : les KPI totalisent toutes les barres.
  const annees = await barres();
  expect(annees.length).toBeGreaterThan(3);
  expect(await sommeKPI()).toBe(annees.reduce((a, b) => a + b, 0));

  // 2. Une année : ses mois, dont la somme est la même que celle des KPI.
  await page.locator('#activityPlot .yc-col').nth(annees.length - 2).click();
  const mois = await barres();
  expect(mois.length).toBeGreaterThan(1);
  expect(await sommeKPI()).toBe(mois.reduce((a, b) => a + b, 0));

  // 3. Un mois : le graphe montre toujours TOUS les mois, un seul mis en
  //    évidence — les KPI, eux, doivent tomber sur ce seul mois.
  await page.locator('#activityPlot .yc-col').nth(2).click();
  await expect(page.locator('#scopeLabel')).not.toHaveText(/^\d{4}$/);
  expect(await sommeKPI()).toBe(mois[2]);
  expect(errors).toEqual([]);
});

test('le fil d\'Ariane est répété au-dessus des trois graphes', async ({ page }) => {
  const errors = trackErrors(page);
  await page.goto('/');
  await page.locator('#tab-stats').click();
  await expect(page.locator('#statutLegend .yc-legend-chip').first()).toBeVisible(STATS_FROID);

  const fils = () => page.$$eval('.drill-crumb', els => els.map(e => e.textContent.replace(/\s+/g, ' ').trim()));
  await expect(page.locator('.drill-crumb')).toHaveCount(3);

  // Forer depuis le graphe des issues : les trois fils suivent, identiques.
  const n = await page.$$eval('#statutPlot .yc-col', els => els.length);
  await page.locator('#statutPlot .yc-col').nth(n - 2).click();
  await page.locator('#statutPlot .yc-col').nth(2).click();
  const apres = await fils();
  expect(new Set(apres).size).toBe(1);
  expect(apres[0]).toMatch(/^Toutes les années›\d{4}›\w+$/);

  // Et il remonte depuis là, sans avoir à retourner en haut de page.
  await page.locator('#statutCrumb a').first().click();
  await expect(page.locator('#scopeLabel')).toHaveText('Toutes les années');
  expect(errors).toEqual([]);
});

test('chaque puce de statut ramène exactement ses points, mention de vote comprise', async ({ page }) => {
  const errors = trackErrors(page);
  await page.goto('/');
  await page.locator('#tab-seances').click();
  await page.locator('#seanceYear').selectOption('2026');
  await expect(page.locator('#seancePointsList .elu-item').first()).toBeVisible();
  await page.locator('#seanceList').selectOption('2026-05-27');
  await expect(page.locator('#seanceResult .elu-name')).toContainText('27/05/2026');
  await page.locator('#seanceFilterToggle').click();

  // Les statuts sont listés un par un, avec leur compte exact. Le libellé du
  // PV porte souvent la mention du vote (« Pris acte à l'unanimité ») : elle
  // est retirée pour le statut, sans quoi chaque variante ferait sa propre
  // puce. Et pas de regroupement par-dessus : il se lirait « issues non
  // identifiées » alors qu'elles sont nommées juste à côté.
  const facettes = await page.$$eval('#seanceFacetChips .elu-chip', els => els.map(e => e.textContent.trim()));
  expect(facettes).not.toContain(expect.stringContaining('Autres issues'));
  expect(facettes.some(t => /^Pris acte \(\d+\)$/.test(t))).toBe(true);
  expect(facettes.some(t => /^Pris pour information \(\d+\)$/.test(t))).toBe(true);

  const puce = page.locator('#seanceFacetChips .elu-chip', { hasText: /^Pris acte/ }).first();
  const n = Number((await puce.textContent()).match(/\((\d+)\)/)[1]);
  expect(n).toBeGreaterThan(0);
  await puce.click();
  await expect(page.locator('#seancePointsList .elu-item')).toHaveCount(n);
  expect(errors).toEqual([]);
});

// ── Filtre par thématique : un champ de recherche, plus un menu natif ──
// Une fiche d'élu·e en compte jusqu'à 232, une séance 113 en médiane : la
// frappe rapide d'un <select> ne cherche que le début du libellé, exactement
// le problème déjà corrigé pour le choix d'un·e élu·e et d'un·e intervenant·e.
for (const cas of [
  {
    nom: 'Par élu·e',
    ouvrir: async page => {
      await page.locator('#tab-elus').click();
      await page.locator('#eluSearch').fill('verzin');
      await page.locator('#eluOptions .elu-opt').first().click();
      await expect(page.locator('#eluResult .elu-item').first()).toBeVisible();
      await page.locator('#eluMoreFilters summary').click();
    },
    champ: '#eluTheme', options: '#eluThemeOptions', croix: '#eluThemeClear',
    liste: '#eluResult .elu-item',
    unite: /intervention/,
  },
  {
    nom: 'Séances',
    ouvrir: async page => {
      await page.locator('#tab-seances').click();
      await page.locator('#seanceYear').selectOption('2026');
      await expect(page.locator('#seancePointsList .elu-item').first()).toBeVisible();
      await page.locator('#seanceList').selectOption('2026-05-27');
      await expect(page.locator('#seanceResult .elu-name')).toContainText('27/05/2026');
      await page.locator('#seanceFilterToggle').click();
    },
    champ: '#seanceThemeFilter', options: '#seanceThemeOptions', croix: '#seanceThemeClear',
    liste: '#seancePointsList .elu-item',
    unite: /point/,
  },
]) {
  test(`onglet ${cas.nom} : le filtre thématique se cherche et se défait`, async ({ page }) => {
    const errors = trackErrors(page);
    await page.goto('/');
    await cas.ouvrir(page);

    const champ = page.locator(cas.champ);
    // Le placeholder annonce ce qu'on interroge, et la 1re entrée permet de
    // revenir à tout — la croix, elle, n'efface que la recherche.
    expect(await champ.getAttribute('placeholder')).toMatch(/Filtrer parmi \d+ thématiques/);
    const total = await page.locator(cas.liste).count();
    expect(total).toBeGreaterThan(1);

    // La recherche ignore les « _ » du libellé stocké (marche_public).
    await champ.click();
    await champ.fill('marche pub');
    const trouve = page.locator(`${cas.options} .elu-opt`).first();
    await expect(trouve).toBeVisible();
    const n = Number((await trouve.textContent()).match(/(\d+)\s*(?:point|intervention)/)[1]);
    expect((await trouve.textContent())).toMatch(cas.unite);
    await trouve.click();
    await expect(page.locator(cas.liste)).toHaveCount(n);
    expect(n).toBeLessThan(total);

    // Se défaire par la 1re entrée de la liste…
    await champ.click();
    await page.locator(`${cas.options} .elu-opt`).first().click();
    await expect(page.locator(cas.liste)).toHaveCount(total);

    // …ou par la croix, qui DÉFAIT le filtre et ne se contente pas de vider le
    // champ : n'effacer que le texte laissait la liste filtrée derrière un
    // champ vide, sans rien qui l'explique. Au repos, elle est cachée.
    await expect(page.locator(cas.croix)).toBeHidden();
    await champ.click();
    await champ.fill('marche pub');
    await page.locator(`${cas.options} .elu-opt`).first().click();
    await expect(page.locator(cas.liste)).toHaveCount(n);
    await expect(page.locator(cas.croix)).toBeVisible();
    await page.locator(cas.croix).click();
    await expect(page.locator(cas.liste)).toHaveCount(total);
    expect(errors).toEqual([]);
  });
}

test('les puces d\'issue et de manque partitionnent les points de la séance', async ({ page }) => {
  const errors = trackErrors(page);
  await page.goto('/');
  await page.locator('#tab-seances').click();
  await page.locator('#seanceYear').selectOption('2018');
  await expect(page.locator('#seancePointsList .elu-item').first()).toBeVisible();
  await page.locator('#seanceList').selectOption('__all__');
  await expect(page.locator('#seanceResult .elu-name')).toContainText('Toutes les séances');
  await page.locator('#seanceFilterToggle').click();

  const annonce = Number((await page.locator('#seanceResult .elu-role').textContent()).match(/(\d+)\s+points/)[1]);
  const lire = async sel => (await page.$$eval(sel, els => els.map(e => e.textContent.trim())));
  const nb = t => +t.match(/\((\d+)\)$/)[1];

  // Chaque point du PV a une issue OU aucune ; un chapitre vidéo sans PV n'a ni
  // l'une ni l'autre. Les trois ensembles couvrent donc exactement la séance —
  // c'est ce que « Sans issue relevée » rend vérifiable, en nommant le solde qui
  // manquait.
  const types = await lire('#seanceTypeChips .elu-chip');
  const facettes = await lire('#seanceFacetChips .elu-chip');
  const horsPv = types.filter(t => t.startsWith('Débat filmé hors PV')).reduce((a, t) => a + nb(t), 0);
  const sansDecision = facettes.filter(t => t.startsWith('Sans issue relevée')).reduce((a, t) => a + nb(t), 0);
  const issues = facettes
    .filter(t => !/^Avec débat filmé|^Sans issue|^Intervenant/.test(t))
    .reduce((a, t) => a + nb(t), 0);
  expect(sansDecision).toBeGreaterThan(0);
  expect(issues + sansDecision + horsPv).toBe(annonce);

  // « Intervenant·e inconnu·e » ne vise que les types déposés par quelqu'un :
  // un point délibératif sans nom est normal, pas une lacune.
  const inconnu = page.locator('#seanceFacetChips .elu-chip', { hasText: 'Intervenant·e inconnu·e' }).first();
  await expect(inconnu).toBeVisible();
  const n = nb(await inconnu.textContent());
  await inconnu.click();
  await expect(page.locator('#seancePointsList .elu-item')).toHaveCount(n);
  const badges = await page.$$eval('#seancePointsList .elu-badge', els => [...new Set(els.map(e => e.textContent.trim()))]);
  expect(badges).not.toContain('POINT DÉLIBÉRATIF');
  expect(errors).toEqual([]);
});

test('une liste de plusieurs personnes se lit « A, B et C »', async ({ page }) => {
  const errors = trackErrors(page);
  await page.goto('/');
  await page.locator('#tab-seances').click();
  await page.locator('#seanceYear').selectOption('2025');
  await expect(page.locator('#seancePointsList .elu-item').first()).toBeVisible();
  await page.locator('#seanceList').selectOption('2025-09-24');
  await expect(page.locator('#seanceResult .elu-name')).toContainText('24/09/2025');

  // Les noms sont bien séparés côté données depuis longtemps ; c'est
  // l'AFFICHAGE qui les recollait avec « et » entre chacun — « A et B et C et
  // D et E » se lit comme un libellé brut du PV, pas comme une liste.
  const lignes = await page.$$eval('#seancePointsList .elu-demandeur, #seancePointsList .elu-rep',
    els => els.map(e => e.textContent.replace(/\s+/g, ' ').trim()));
  const multiples = lignes.filter(t => / et /.test(t));
  expect(multiples.length).toBeGreaterThan(0);
  multiples.forEach(t => expect(t.match(/ et /g)).toHaveLength(1));
  expect(multiples.some(t => t.includes(', '))).toBe(true);
  expect(errors).toEqual([]);
});
