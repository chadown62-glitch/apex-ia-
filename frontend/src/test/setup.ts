import "@testing-library/jest-dom/vitest";

// jsdom n'implémente pas matchMedia — utilisé par useTheme (préférence
// système) et par toute future media query testée en JS. Défaut réaliste :
// "matches: false" (jamais "préfère le clair" par défaut), un test qui a
// besoin d'un autre résultat le redéfinit explicitement avec vi.stubGlobal.
// jsdom n'implémente pas non plus scrollIntoView (utilisé par ChatPage
// pour faire défiler vers le dernier message) — no-op suffisant pour les tests.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}

if (!window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList;
}
