import { vi } from "vitest";

import "@testing-library/jest-dom/vitest";

// Jeton API par défaut pour TOUTE la suite (passe « Frontend API
// Integration ») : `fetchEnvelope` (src/api/client.ts) exige désormais un
// jeton configuré avant même de tenter un appel réseau — sans cette valeur
// par défaut, chaque test de page qui mocke `fetch` échouerait avant que
// son mock ne soit jamais atteint. Un test qui veut spécifiquement le cas
// "jeton absent" le redéfinit explicitement (`vi.unstubAllEnvs()`), comme
// `client.test.ts` le fait déjà.
vi.stubEnv("VITE_APEX_API_TOKEN", "test-suite-token");

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
