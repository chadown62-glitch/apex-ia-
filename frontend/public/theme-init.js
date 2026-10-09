/*
 * Apparence d'APEX posée AVANT le premier rendu (aucun flash de thème) — fichier séparé et non un
 * script en ligne, pour que la politique de sécurité du contenu (CSP) de l'interface interdise tout
 * script en ligne. Doit rester synchronisé avec src/theme/appearance.ts (même clé, mêmes valeurs) :
 * cache local `apex-appearance`, sinon ancien choix `apex-theme` ("dark"/"light"), sinon préférence
 * système, sinon APEX Night.
 */
(function () {
  var root = document.documentElement;
  var theme = "night";
  var animations = true;
  var particles = true;
  var reduceMotion = "system";
  try {
    var raw = localStorage.getItem("apex-appearance");
    var legacy = localStorage.getItem("apex-theme");
    if (raw) {
      var parsed = JSON.parse(raw);
      if (parsed.theme === "dune" || parsed.theme === "night") theme = parsed.theme;
      if (typeof parsed.animations === "boolean") animations = parsed.animations;
      if (typeof parsed.particles === "boolean") particles = parsed.particles;
      if (parsed.reduce_motion === "always" || parsed.reduce_motion === "never") reduceMotion = parsed.reduce_motion;
    } else if (legacy === "light" || legacy === "dark") {
      theme = legacy === "light" ? "dune" : "night";
    } else if (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches) {
      theme = "dune";
    }
  } catch (e) {
    /* stockage indisponible ou valeur corrompue : APEX Night par défaut. */
  }
  root.setAttribute("data-theme", theme);
  root.setAttribute("data-animations", animations ? "on" : "off");
  root.setAttribute("data-particles", particles ? "on" : "off");
  root.setAttribute("data-reduce-motion", reduceMotion);
})();
