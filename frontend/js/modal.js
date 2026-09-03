// ── MODALES (Options, connexion administrateur) — mécanique d'accessibilité
// partagée. Les deux panneaux restent dans le DOM à opacité 0 quand ils sont
// fermés (pour que le tiroir mobile puisse s'animer, voir styles.css) : sans
// `inert`, leurs contrôles invisibles restaient tabulables et lus — on pouvait
// taper un mot de passe sans le voir. Ici, une seule règle pour les deux :
//   • fermé  → `inert` (ni tabulable, ni lu) ;
//   • ouvert → focus posé dedans, Échap ferme, Tab boucle à l'intérieur,
//              et le focus revient sur le bouton qui a ouvert.
// Sans aucune dépendance, pour être importé par settings.js ET admin.js sans
// cycle (settings.js importe chat.js, qui importe lexique.js…).
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
const openers = new Map();   // id de la modale → élément qui l'a ouverte

export function openOverlay(id, { initialFocus } = {}) {
  const overlay = document.getElementById(id);
  if (!overlay) return;
  openers.set(id, document.activeElement);
  overlay.inert = false;
  overlay.classList.add('open');
  const target = (initialFocus && overlay.querySelector(initialFocus)) || overlay.querySelector(FOCUSABLE);
  if (target) target.focus({ preventScroll: true });
}

export function closeOverlay(id) {
  const overlay = document.getElementById(id);
  if (!overlay || !overlay.classList.contains('open')) return;
  overlay.classList.remove('open');
  overlay.inert = true;
  const back = openers.get(id);
  openers.delete(id);
  if (back && typeof back.focus === 'function' && document.contains(back)) back.focus({ preventScroll: true });
}

// Échap ferme la modale ouverte ; Tab et Maj+Tab bouclent dans ses contrôles.
document.addEventListener('keydown', (ev) => {
  const overlay = document.querySelector('.settings-overlay.open');
  if (!overlay) return;
  if (ev.key === 'Escape') { ev.preventDefault(); closeOverlay(overlay.id); return; }
  if (ev.key !== 'Tab') return;
  const items = [...overlay.querySelectorAll(FOCUSABLE)].filter(el => el.offsetParent !== null);
  if (!items.length) return;
  const first = items[0], last = items[items.length - 1];
  if (!overlay.contains(document.activeElement)) { ev.preventDefault(); first.focus(); }
  else if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
  else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
});
