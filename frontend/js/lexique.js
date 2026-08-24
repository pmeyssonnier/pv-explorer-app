// ── Commande « //lex … » du chat : enrichit le lexique (synonymes/associations
// + glossaire) via l'endpoint admin /admin/lexique — RÉSERVÉE à une session
// admin connectée (le cookie de session part avec credentials:'include' ; sinon
// le serveur répond 401 et on affiche « réservé à l'administrateur »). Voir
// backend/lexique_store.py + routers/admin.py.
import { API_URL } from './config.js';
import { escapeHtml } from './utils.js';

const KINDS = ['theme', 'decision', 'alias', 'nom', 'def', 'retrait', 'report', 'approbation', 'rejet'];
const LIST_KINDS = new Set(['retrait', 'report', 'approbation', 'rejet']);
const SECTION_TITLES = {
  thematiques: 'Thématiques', decisions: 'Décisions',
  extraction: 'Extraction (formules)', glossaire: 'Glossaire',
};

// Reconnaît « /lex … » ou « //lex … » (avec ou sans arguments).
export function isLexCommand(text) {
  return /^\/\/?lex(\s|$)/i.test((text || '').trim());
}

// Parse la commande en {action:'list'} | {action:'add', kind, key, value} | {error}.
export function parseLexCommand(text) {
  const s = (text || '').trim().replace(/^\/\/?lex\s*/i, '');
  if (!s || /^list$/i.test(s)) return { action: 'list' };
  const eq = s.indexOf('=');
  if (eq === -1) return { error: 'Syntaxe : <code>//lex &lt;type&gt; &lt;clé&gt; = &lt;valeur&gt;</code> — ou <code>//lex list</code>.' };
  const left = s.slice(0, eq).trim();
  const value = s.slice(eq + 1).trim();   // conserve apostrophes/ponctuation de la valeur
  const parts = left.split(/\s+/).filter(Boolean);
  const kind = (parts.shift() || '').toLowerCase();
  const key = parts.join(' ').trim();
  if (!KINDS.includes(kind)) return { error: `Type inconnu « ${escapeHtml(kind)} ». Types : ${KINDS.join(', ')}.` };
  if (LIST_KINDS.has(kind)) {
    if (!value) return { error: `Syntaxe : <code>//lex ${kind} = &lt;phrase&gt;</code>.` };
    return { action: 'add', kind, key: '', value };
  }
  if (!key || !value) return { error: `Syntaxe : <code>//lex ${kind} &lt;clé&gt; = &lt;valeur&gt;</code>.` };
  return { action: 'add', kind, key, value };
}

// Exécute la commande et retourne {ok, html} — html sûr (échappé) à insérer.
export async function runLexCommand(text) {
  const cmd = parseLexCommand(text);
  if (cmd.error) return { ok: false, html: cmd.error };
  try {
    if (cmd.action === 'list') {
      const res = await fetch(API_URL + '/admin/lexique', { credentials: 'include' });
      if (res.status === 401) return { ok: false, html: '🔒 Réservé à l’administrateur connecté.' };
      if (!res.ok) return { ok: false, html: 'Erreur ' + res.status + '.' };
      return { ok: true, html: formatLexique(await res.json()) };
    }
    const res = await fetch(API_URL + '/admin/lexique', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: cmd.kind, key: cmd.key, value: cmd.value }),
    });
    if (res.status === 401) return { ok: false, html: '🔒 Réservé à l’administrateur connecté.' };
    if (res.status === 400) {
      const d = await res.json().catch(() => ({}));
      return { ok: false, html: escapeHtml(d.detail || 'Entrée invalide.') };
    }
    if (!res.ok) return { ok: false, html: 'Erreur ' + res.status + '.' };
    const body = await res.json();
    const label = cmd.key || cmd.value;
    const warn = body.committed ? '' : ' <em>(non commité — appliqué à chaud, persistera au prochain redéploiement)</em>';
    return { ok: true, html: `✓ Ajouté au lexique — <strong>${cmd.kind}</strong> : « ${escapeHtml(label)} »${warn}` };
  } catch (e) {
    return { ok: false, html: 'Réseau indisponible : ' + escapeHtml(e.message) };
  }
}

// Rendu lisible du lexique (réponse de //lex list).
function formatLexique(data) {
  const blocks = [];
  const mapBlock = (title, obj) => {
    const entries = Object.entries(obj || {});
    if (!entries.length) return '';
    const rows = entries.map(([k, v]) => `<li><strong>${escapeHtml(k)}</strong> → ${escapeHtml(String(v))}</li>`).join('');
    return `<div class="md-h">${title}</div><ul class="md-ul">${rows}</ul>`;
  };
  blocks.push(mapBlock(SECTION_TITLES.thematiques, data.thematiques));
  blocks.push(mapBlock(SECTION_TITLES.decisions, data.decisions));
  const pers = data.personnes || {};
  blocks.push(mapBlock('Personnes — alias', pers.alias));
  blocks.push(mapBlock('Personnes — noms', pers.noms));
  const extr = data.extraction || {};
  const extrRows = Object.entries(extr).filter(([, v]) => (v || []).length)
    .map(([fam, arr]) => `<li><strong>${escapeHtml(fam)}</strong> : ${arr.map(escapeHtml).join(' · ')}</li>`).join('');
  if (extrRows) blocks.push(`<div class="md-h">${SECTION_TITLES.extraction}</div><ul class="md-ul">${extrRows}</ul>`);
  blocks.push(mapBlock(SECTION_TITLES.glossaire, data.glossaire));
  const body = blocks.filter(Boolean).join('');
  return body || '<em>Lexique vide.</em>';
}
