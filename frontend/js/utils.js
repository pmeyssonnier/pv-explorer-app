// ── HELPERS GÉNÉRIQUES (formatage, échappement, Markdown minimal) ──
export function escapeHtml(t) {
  return String(t).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

export function formatDate(iso) {
  if (!iso) return '?';
  const parts = iso.split('-');
  if (parts.length === 3) return `${parts[2]}/${parts[1]}/${parts[0]}`;
  return iso;
}

// Entier groupé « # ### ##0 » avec des espaces (déterministe, indépendant de la
// locale du navigateur — évite « 8718 » quand fr-BE n'est pas disponible).
export const fmtInt = n => Math.round(n || 0).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
// Montant « # ### ##0 € ».
export const fmtMontant = n => fmtInt(n) + ' €';
export function fmtEUR(n) { return fmtMontant(n); }   // même format « # ### ##0 € » que les KPI

// Rendu Markdown minimal et SÛR (échappe le texte d'abord, puis n'insère que nos
// propres balises) : tableaux, titres, gras/italique, code, listes, séparateurs.
export function renderMarkdown(src) {
  const inline = s => escapeHtml(s)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*(?!\s)([^*]+?)\*/g, '$1<em>$2</em>')
    .replace(/`([^`]+?)`/g, '<code>$1</code>');
  const lines = String(src || '').replace(/\r\n/g, '\n').split('\n');
  const out = [];
  let i = 0;
  const isRow = l => /^\s*\|.*\|\s*$/.test(l);
  const cells = l => l.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
  while (i < lines.length) {
    const l = lines[i];
    if (isRow(l) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      const head = cells(l); i += 2; const rows = [];
      while (i < lines.length && isRow(lines[i])) { rows.push(cells(lines[i])); i++; }
      const thead = '<tr>' + head.map(h => `<th>${inline(h)}</th>`).join('') + '</tr>';
      const tbody = rows.map(r => '<tr>' + head.map((_, j) => `<td>${inline(r[j] || '')}</td>`).join('') + '</tr>').join('');
      out.push(`<div class="md-tablewrap"><table class="md-table"><thead>${thead}</thead><tbody>${tbody}</tbody></table></div>`);
      continue;
    }
    const mh = l.match(/^(#{1,6})\s+(.*)$/);
    if (mh) { out.push(`<h4 class="md-h">${inline(mh[2])}</h4>`); i++; continue; }
    if (/^\s*---+\s*$/.test(l)) { out.push('<hr class="md-hr">'); i++; continue; }
    if (/^\s*[-*]\s+/.test(l)) {
      const items = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) { items.push(`<li>${inline(lines[i].replace(/^\s*[-*]\s+/, ''))}</li>`); i++; }
      out.push(`<ul class="md-ul">${items.join('')}</ul>`); continue;
    }
    if (/^\s*$/.test(l)) { i++; continue; }
    const para = [];
    while (i < lines.length && !/^\s*$/.test(lines[i]) && !isRow(lines[i])
           && !/^#{1,6}\s/.test(lines[i]) && !/^\s*[-*]\s+/.test(lines[i]) && !/^\s*---+\s*$/.test(lines[i])) {
      para.push(inline(lines[i])); i++;
    }
    if (para.length) out.push(`<p>${para.join('<br>')}</p>`);
  }
  return out.join('');
}

// ── Libellés partagés « type de point » (badge + qui l'a soulevé) — utilisés
// à la fois par la vue « Par élu·e » et la vue « Séances ». ──
export const TYPE_BADGE = {
  'Question orale': 'b-q',
  'Demande': 'b-d',
  'Motion': 'b-m',
  'Débat filmé': 'b-v',
};

// Terme utilisé pour désigner qui a soulevé le point, selon son type — affiché
// au-dessus du/de la répondant·e pour que chaque ligne se comprenne seule
// (ex. sur une capture d'écran, sans le contexte de la page).
export const TYPE_ACTOR_LABEL = {
  'Question orale': 'Auteur·e de la question',
  'Demande': 'Demandeur·se',
  'Motion': 'Auteur·e de la motion',
  'Débat filmé': 'Intervenant·e',
};
