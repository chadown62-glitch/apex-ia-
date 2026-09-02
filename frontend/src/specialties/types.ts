/**
 * `Specialty` — module fonctionnel de l'interface (Code, Design, Projects,
 * Research, Security, Learning...). À NE PAS confondre avec `Domain`
 * (domaine d'apprentissage/connaissance — Maths, Physique, Cybersécurité —
 * conçu côté backend dans docs/COGNITIVE_ARCHITECTURE.md §3, pas encore
 * implémenté). Une Specialty peut un jour couvrir plusieurs Domain (ex. la
 * spécialité Learning traversera plusieurs domaines de connaissance) ;
 * l'inverse n'a pas de sens — c'est pourquoi ce sont deux concepts
 * distincts, pas une hiérarchie ou un renommage l'un de l'autre.
 *
 * Ce registre est une liste STATIQUE côté client — une décision de
 * navigation, pas une donnée mesurée. Il n'a donc pas de statut
 * real/simulated/unknown (apex.obs.status.DataStatus) : chaque Specialty a
 * un `status` "real" (le workspace existe et affiche de vraies données) ou
 * "planned" (aucun backend, page d'attente honnête — riche ou générique
 * selon `devStage`). Le jour où `apex/api` exposera un endpoint
 * `/specialties`, cette liste sera remplacée par des données serveur — pas
 * avant.
 */

import type { ComponentType, ReactNode } from "react";

import type { CloneType } from "../api/types";

export type SpecialtyStatus = "real" | "planned";

/** Regroupement visuel de navigation (Sidebar) — une décision de
 * présentation côté client, comme `Specialty` elle-même, jamais une donnée
 * mesurée. Fermé aujourd'hui à 6 valeurs (§ demande « UI Foundation V1 »)
 * mais consommé par un `Record` dans data.ts : ajouter une 7e catégorie un
 * jour = une entrée de plus dans `SPECIALTY_CATEGORIES`, aucun changement
 * dans Sidebar.tsx. */
export type SpecialtyCategory = "workspace" | "intelligence" | "cognition" | "creative" | "data" | "system";

/**
 * Où en est réellement l'implémentation, pour être honnête au-delà du seul
 * REAL/PLANNED binaire :
 * - "connected"  : status "real" — page + endpoint API existants.
 * - "ui-scaffold": status "planned" — page dédiée construite (layout, sections,
 *                  formulaire) mais SANS aucune donnée réelle ni action qui
 *                  fonctionne ; tout est visiblement désactivé/PLANNED.
 * - "concept"    : status "planned" — seulement une entrée de registre,
 *                  rendue par la page générique PlannedSpecialtyPage.
 */
export type DevStage = "connected" | "ui-scaffold" | "concept";

/** Ce qu'un workspace de Specialty possédera, à terme (§ demande utilisateur).
 * `capabilities`/`tools`/`compatibleAgentTypes`/`requiredPermissions` sont
 * des DÉCLARATIONS statiques (ce que la spécialité fera un jour), jamais une
 * mesure — ne portent donc pas de DataStatus. Les permissions RÉELLES d'un
 * viewer restent décidées uniquement par PolicyEngine côté backend (voir
 * apex/agents/view.py::available_actions) ; cette liste ne fait
 * qu'annoncer ce qui sera un jour soumis à cette vérification.
 */
export interface SpecialtyWorkspaceMeta {
  capabilities?: string[];
  tools?: string[];
  compatibleAgentTypes?: CloneType[];
  requiredPermissions?: string[];
}

export interface Specialty {
  id: string;
  label: string;
  icon: ReactNode;
  description: string;
  path: string;
  status: SpecialtyStatus;
  devStage: DevStage;
  category: SpecialtyCategory;
  /** Rendu si présent, quel que soit `status` — une spécialité PLANNED peut
   * avoir sa propre page ("ui-scaffold") ; sinon la page générique PLANNED
   * s'en charge (voir pages/PlannedSpecialtyPage.tsx). */
  component?: ComponentType;
  workspace?: SpecialtyWorkspaceMeta;
}
