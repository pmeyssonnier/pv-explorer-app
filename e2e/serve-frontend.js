// Sert frontend/ en statique avec les mêmes en-têtes que vercel.json (surtout
// la CSP), pour que le smoke test s'exécute dans les mêmes conditions qu'en
// production (script-src 'self' sans 'unsafe-inline', etc.).
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'frontend');
const PORT = process.env.PORT || 5500;

// CSP lue directement dans vercel.json (pas recopiée à la main) : une CSP
// dupliquée ici divergeait silencieusement de la prod à chaque changement
// de vercel.json (cas vécu : l'ajout de Vercel Web Analytics a mis à jour
// vercel.json sans toucher cette copie, faisant échouer 32 tests en CSP
// bloquant le script en test alors qu'il passait en prod). Seule
// l'origine de l'API change : le backend de test tourne en local.
const VERCEL_CONFIG = JSON.parse(fs.readFileSync(path.join(ROOT, 'vercel.json'), 'utf8'));
const CSP = VERCEL_CONFIG.headers[0].headers
  .find(h => h.key === 'Content-Security-Policy').value
  .replace('https://pv-explorer-api.onrender.com', 'http://localhost:8000');

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
};

http.createServer((req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);
  // Mêmes réécritures que vercel.json : « / » = page d'accueil (index.html),
  // « /app » = l'outil (app.html). La redirection des anciens liens
  // « /?tab=… » vers /app est faite côté page (js/landing.js), donc valable
  // ici comme en prod, sans dépendre des règles `redirects` de Vercel.
  const route = url.pathname === '/' ? '/index.html' : url.pathname === '/app' ? '/app.html' : url.pathname;
  let filePath = path.join(ROOT, decodeURIComponent(route));
  if (!filePath.startsWith(ROOT)) { res.writeHead(403); res.end(); return; }

  fs.readFile(filePath, (err, data) => {
    if (err) { res.writeHead(404); res.end('Not found'); return; }
    res.setHeader('Content-Security-Policy', CSP);
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Content-Type', MIME[path.extname(filePath)] || 'application/octet-stream');
    res.writeHead(200);
    res.end(data);
  });
}).listen(PORT, () => console.log(`frontend statique sur http://localhost:${PORT}`));
