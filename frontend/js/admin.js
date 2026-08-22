// ── ADMIN (authentification) ──
// Compte unique (voir backend/services/auth.py) : le cadenas dans l'en-tête
// ouvre une modale de connexion (même mécanique que le panneau Options — voir
// settings.js) ; une fois connecté·e, l'onglet Admin apparaît. Pour l'instant
// son contenu se limite à l'état connecté + déconnexion — fondation pour
// l'upload de PV à venir.
import { API_URL } from './config.js';
import { escapeHtml } from './utils.js';

let adminUsername = null;

// Session vérifiée via cookie httpOnly (jamais lu par ce script — juste
// renvoyé automatiquement par le navigateur, `credentials: 'include'`) : on
// interroge /admin/me pour savoir si une session valide existe déjà.
export async function checkAdminSession() {
  try {
    const res = await fetch(API_URL + '/admin/me', { credentials: 'include' });
    adminUsername = res.ok ? (await res.json()).username : null;
  } catch {
    adminUsername = null;
  }
  updateAdminUI();
}

function updateAdminUI() {
  const tabBtn = document.getElementById('tab-admin');
  if (tabBtn) tabBtn.hidden = !adminUsername;
  // Session absente/expirée alors que l'onglet Admin était actif : revient à
  // Question plutôt que de laisser un onglet vide/caché actif.
  const adminPanel = document.getElementById('panel-admin');
  if (!adminUsername && adminPanel && adminPanel.classList.contains('active')) {
    document.getElementById('tab-chat')?.click();
  }
  renderAdminPanel();
}

function renderAdminPanel() {
  const box = document.getElementById('adminPanelBody');
  if (!box) return;
  box.innerHTML = adminUsername
    ? `<p class="yc-note">Connecté·e en tant que <strong>${escapeHtml(adminUsername)}</strong>.</p>
       <button type="button" class="drill-reset" data-click="adminLogout">Se déconnecter</button>`
    : '';
}

export function openAdminLogin() {
  document.getElementById('adminLoginOverlay').classList.add('open');
  document.getElementById('adminLoginError').textContent = '';
  document.getElementById('adminLoginUsername').focus();
}
export function closeAdminLogin() {
  document.getElementById('adminLoginOverlay').classList.remove('open');
}
// Clic sur le fond assombri (pas sur le panneau) → ferme, comme Options.
export function initAdminLoginOverlay() {
  const overlay = document.getElementById('adminLoginOverlay');
  if (overlay) overlay.addEventListener('click', e => { if (e.target === overlay) closeAdminLogin(); });
}

export async function submitAdminLogin(ev) {
  ev.preventDefault();
  const usernameEl = document.getElementById('adminLoginUsername');
  const passwordEl = document.getElementById('adminLoginPassword');
  const errBox = document.getElementById('adminLoginError');
  const btn = document.getElementById('adminLoginSubmit');
  const username = usernameEl.value.trim();
  const password = passwordEl.value;
  if (!username || !password) return;
  errBox.textContent = '';
  btn.disabled = true;
  try {
    const res = await fetch(API_URL + '/admin/login', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    if (!res.ok) {
      errBox.textContent = 'Identifiants invalides.';
      return;
    }
    const data = await res.json();
    adminUsername = data.username;
    passwordEl.value = '';
    closeAdminLogin();
    updateAdminUI();
  } catch {
    errBox.textContent = 'Connexion impossible — réessayez.';
  } finally {
    btn.disabled = false;
  }
}

export async function adminLogout() {
  try {
    await fetch(API_URL + '/admin/logout', { method: 'POST', credentials: 'include' });
  } catch { /* la session cookie expirera de toute façon côté serveur */ }
  adminUsername = null;
  updateAdminUI();
}
