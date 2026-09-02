import { useId, useState, type ReactNode } from "react";
import "./ui.css";

/** Info-bulle déclenchée au survol ET au focus clavier (jamais au survol
 * seul — un utilisateur clavier doit pouvoir la voir aussi). */
export function Tooltip({ label, children }: { label: string; children: ReactNode }) {
  const [visible, setVisible] = useState(false);
  const id = useId();

  return (
    <span
      className="tooltip-wrap"
      onMouseEnter={() => setVisible(true)}
      onMouseLeave={() => setVisible(false)}
      onFocus={() => setVisible(true)}
      onBlur={() => setVisible(false)}
    >
      <span aria-describedby={visible ? id : undefined}>{children}</span>
      <span role="tooltip" id={id} className={`tooltip-bubble ${visible ? "tooltip-bubble--visible" : ""}`}>
        {label}
      </span>
    </span>
  );
}
