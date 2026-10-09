import { useEffect } from "react";

import { setAppearance } from "../theme/appearance";
import { useSettings } from "./hooks";

/**
 * Aligne l'apparence sur les paramètres ENREGISTRÉS par le backend (source de vérité) dès qu'ils sont
 * chargés — le cache local n'a servi qu'à éviter un flash au démarrage. Sans réponse du backend, l'apparence
 * en cache reste en place (rien n'est inventé).
 */
export function AppearanceSync() {
  const settings = useSettings();
  const appearance = settings.data?.appearance;

  useEffect(() => {
    if (appearance) setAppearance(appearance);
  }, [appearance]);

  return null;
}
