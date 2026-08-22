// Applique le thème choisi AVANT le premier rendu (évite le flash clair→
// sombre). Fichier externe (plutôt qu'un <script> inline dans <head>) pour
// respecter une CSP script-src sans 'unsafe-inline' : chargé sans defer/async
// juste après le <meta id="themeColorMeta">, il bloque le rendu comme le
// faisait le script inline, avant que la 1re peinture n'ait lieu.
(function () {
  try {
    var t = (JSON.parse(localStorage.getItem('pv_settings') || '{}').theme) || 'auto';
    if (t === 'light' || t === 'dark') document.documentElement.setAttribute('data-theme', t);
    var dark = t === 'dark' || (t === 'auto' && window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
    document.getElementById('themeColorMeta').setAttribute('content', dark ? '#201a15' : '#fffdf9');
  } catch (e) {}
})();
