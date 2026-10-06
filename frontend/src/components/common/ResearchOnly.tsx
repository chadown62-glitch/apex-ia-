import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { getDistribution } from "../../api/endpoints";
import { EmptyState, LoadingState } from "./EmptyState";

/**
 * Enveloppe des pages qui dépendent de l'extension RECHERCHE (brain, learning, population, self-model,
 * weakness…). Dans le Runtime packagé, ces routes n'existent pas : la page le dit, sans lancer une seule
 * requête vouée au 404 — jamais une capacité absente présentée comme disponible.
 */
export function ResearchOnly({ feature, children }: { feature: string; children: ReactNode }) {
  const distribution = useQuery({ queryKey: ["distribution"], queryFn: getDistribution, staleTime: 60_000 });
  if (distribution.isLoading) return <LoadingState />;
  const research = distribution.data?.status === "real" ? distribution.data.data?.research : undefined;
  if (research === false) {
    return (
      <div className="page">
        <EmptyState
          title={`${feature} : absent de cette distribution`}
          detail="Cette fonction relève de l'extension Recherche d'APEX, non incluse dans le Runtime installé."
        />
      </div>
    );
  }
  return <>{children}</>;
}
