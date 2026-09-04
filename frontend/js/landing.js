// Page d'accueil (« / ») — anciens liens partagés. Avant, l'outil vivait à la
// racine : les liens « /?tab=stats&vue=pv », « /?tab=elus&elu=… »,
// « /?q=… » circulent encore (partages, favoris). La racine est désormais la
// page de présentation et l'outil vit sur /app : on renvoie ces liens tels
// quels vers /app, query et fragment compris. Fait côté page, donc valable en
// local (e2e/serve-frontend.js) comme en prod, en plus des règles
// `redirects` de vercel.json qui font la même chose plus tôt, côté serveur.
// Chargé sans defer dans <head> pour partir avant la première peinture.
(function () {
  try {
    var p = new URLSearchParams(location.search);
    if (p.has('tab') || p.has('q') || p.has('elu') || p.has('seance')) {
      location.replace('/app' + location.search + location.hash);
    }
  } catch (e) {}
})();
