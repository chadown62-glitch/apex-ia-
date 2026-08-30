import { NavLink } from "react-router-dom";

import type { Specialty } from "../../specialties/types";
import "./layout.css";

export function Sidebar({ specialties }: { specialties: Specialty[] }) {
  return (
    <nav className="sidebar" aria-label="Navigation principale">
      <div className="sidebar__brand">APEX</div>
      <ul className="sidebar__list">
        {specialties.map((specialty) => (
          <li key={specialty.id}>
            <NavLink
              to={specialty.path}
              className={({ isActive }) => `sidebar__link ${isActive ? "sidebar__link--active" : ""}`}
              end={specialty.path === "/"}
            >
              <span className="sidebar__icon" aria-hidden="true">
                {specialty.icon}
              </span>
              <span className="sidebar__label">{specialty.label}</span>
              {specialty.status === "planned" && <span className="sidebar__planned-dot" title="Planifié" />}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
