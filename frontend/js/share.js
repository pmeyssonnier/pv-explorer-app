// ── PARTAGE (primitives génériques) ──
// URL de l'app sans query/fragment (repère de base pour les liens partagés).
export function shareBaseUrl() { return location.href.split('#')[0].split('?')[0]; }

// Copie « texte + lien » dans le presse-papier (repli si navigator.share absent).
export function copyShare(payload, cb) {
  (navigator.clipboard ? navigator.clipboard.writeText(payload).then(cb) : Promise.reject()).catch(() => {
    const t = document.createElement('textarea'); t.value = payload; document.body.appendChild(t);
    t.select(); try { document.execCommand('copy'); } catch (e) {} t.remove(); cb();
  });
}

// Partage via la feuille native (mobile) si dispo, sinon copie du lien. Le
// bouton confirme brièvement (« Partagé ✓ » / « Lien copié ✓ »).
export function doShare(title, text, url, btn) {
  const orig = btn ? btn.textContent : '';
  const flash = (msg) => { if (btn) { btn.textContent = msg; setTimeout(() => { btn.textContent = orig; }, 1800); } };
  if (navigator.share) {
    navigator.share({ title, text, url }).then(() => flash('Partagé ✓')).catch((err) => {
      if (err && err.name === 'AbortError') return;           // annulé par l'utilisateur
      copyShare(`${text}\n${url}`, () => flash('Lien copié ✓'));
    });
  } else {
    copyShare(`${text}\n${url}`, () => flash('Lien copié ✓'));
  }
}
