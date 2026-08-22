// ── DÉLÉGATION D'ÉVÉNEMENTS ──
// Remplace les attributs onclick inline (interdits par script-src sans
// 'unsafe-inline' : un attribut onXXX est traité comme du script inline par
// la CSP au même titre qu'une balise <script>). Le HTML — statique dans
// index.html ou généré dynamiquement dans les templates de chat.js/stats.js/
// seances.js — porte à la place :
//   data-click="nomAction"   → actions['nomAction'](arg?, arg2?, el, event)
//   data-arg="valeur"        → 1er argument (avant l'élément et l'event)
//   data-arg2="valeur"       → 2e argument
// Les fonctions existantes qui attendaient l'élément cliqué en 1er paramètre
// (ex. askSuggestion(this)) le reçoivent toujours en 1re position quand il
// n'y a pas de data-arg — les arguments surnuméraires sont simplement ignorés
// par les fonctions qui n'en ont pas besoin.
const actions = {};
export function registerActions(map) { Object.assign(actions, map); }

document.addEventListener('click', (ev) => {
  const el = ev.target.closest('[data-click]');
  if (!el) return;
  const fn = actions[el.dataset.click];
  if (!fn) return;
  const args = [];
  if (el.dataset.arg !== undefined) args.push(el.dataset.arg);
  if (el.dataset.arg2 !== undefined) args.push(el.dataset.arg2);
  fn(...args, el, ev);
});

// Active au clavier (Entrée/Espace) les éléments cliquables non natifs (ex.
// bulle de question, role="button" sur un <div>) en rejouant le même clic —
// pas de logique dupliquée avec le handler ci-dessus.
document.addEventListener('keydown', (ev) => {
  if (ev.key !== 'Enter' && ev.key !== ' ') return;
  const el = ev.target.closest('[data-keyclick]');
  if (!el) return;
  ev.preventDefault();
  el.click();
});
