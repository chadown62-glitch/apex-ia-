import { NavLink } from "react-router-dom";

import { SPECIALTY_CATEGORIES } from "../../specialties/data";
import type { Specialty, SpecialtyCategory } from "../../specialties/types";
import { useSidebar } from "./SidebarContext";
import "./layout.css";

const CATEGORY_ORDER: SpecialtyCategory[] = ["workspace", "intelligence", "cognition", "creative", "data", "system"];

function groupByCategory(specialties: Specialty[]): [SpecialtyCategory, Specialty[]][] {
  return CATEGORY_ORDER.map((category) => [category, specialties.filter((s) => s.category === category)]).filter(
    ([, items]) => items.length > 0,
  ) as [SpecialtyCategory, Specialty[]][];
}

export function Sidebar({ specialties }: { specialties: Specialty[] }) {
  const { collapsed, toggleCollapsed, mobileOpen, setMobileOpen } = useSidebar();
  const groups = groupByCategory(specialties);

  return (
    <>
      {mobileOpen && <div className="sidebar__scrim" onClick={() => setMobileOpen(false)} aria-hidden="true" />}
      <nav
        className={`sidebar ${collapsed ? "sidebar--collapsed" : ""} ${mobileOpen ? "sidebar--mobile-open" : ""}`}
        aria-label="Navigation principale"
      >
        <div className="sidebar__brand">
          <span className="sidebar__brand-mark" aria-hidden="true">
            ◆
          </span>
          {!collapsed && <span className="sidebar__brand-label">APEX</span>}
          <button
            type="button"
            className="sidebar__collapse-toggle"
            onClick={toggleCollapsed}
            aria-label={collapsed ? "Développer la navigation" : "Réduire la navigation"}
            title={collapsed ? "Développer" : "Réduire"}
          >
            {collapsed ? "»" : "«"}
          </button>
        </div>
        <div className="sidebar__scroll">
          {groups.map(([category, items]) => (
            <div className="sidebar__group" key={category}>
              {!collapsed && <div className="sidebar__group-label">{SPECIALTY_CATEGORIES[category]}</div>}
              <ul className="sidebar__list">
                {items.map((specialty) => (
                  <li key={specialty.id}>
                    <NavLink
                      to={specialty.path}
                      className={({ isActive }) => `sidebar__link ${isActive ? "sidebar__link--active" : ""}`}
                      end={specialty.path === "/control"}
                      title={collapsed ? specialty.label : undefined}
                      onClick={() => setMobileOpen(false)}
                    >
                      <span className="sidebar__icon" aria-hidden="true">
                        {specialty.icon}
                      </span>
                      {!collapsed && <span className="sidebar__label">{specialty.label}</span>}
                      {specialty.status === "planned" && <span className="sidebar__planned-dot" title="Planifié" />}
                    </NavLink>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </nav>
    </>
  );
}
