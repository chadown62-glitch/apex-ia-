import type { ReactNode } from "react";

import { StatusBadge } from "../status/StatusBadge";
import "./planned-sections.css";

/**
 * Bandeau d'en-tête pour une page "ui-scaffold" : la mise en page existe,
 * rien derrière ne fonctionne encore. Toujours en haut de ces pages, pour
 * qu'aucun visiteur ne confonde la maquette avec une fonctionnalité livrée.
 */
export function PlannedBanner({ note }: { note: string }) {
  return (
    <div className="planned-banner">
      <StatusBadge status="planned" />
      <p className="planned-banner__text">{note}</p>
    </div>
  );
}

export interface PlannedSection {
  icon: ReactNode;
  title: string;
  desc: string;
}

/**
 * Grille de sections prévues pour un futur module — jamais un chiffre, un
 * graphique vide ou une liste factice : uniquement l'intitulé et l'objet de
 * chaque section, jusqu'à ce qu'un vrai backend existe.
 */
export function PlannedSectionGrid({ sections }: { sections: PlannedSection[] }) {
  return (
    <div className="section-grid">
      {sections.map((s) => (
        <div key={s.title} className="section-card">
          <div className="section-card__top">
            <span className="section-card__icon" aria-hidden="true">
              {s.icon}
            </span>
            <StatusBadge status="planned" />
          </div>
          <h3 className="section-card__title">{s.title}</h3>
          <p className="section-card__desc">{s.desc}</p>
        </div>
      ))}
    </div>
  );
}
