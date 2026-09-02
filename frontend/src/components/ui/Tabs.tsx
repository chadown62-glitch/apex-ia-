import { useId, useState, type KeyboardEvent, type ReactNode } from "react";
import "./ui.css";

export interface TabItem {
  id: string;
  label: string;
  content: ReactNode;
}

/** Onglets réels — navigation clavier (flèches gauche/droite, Home/End),
 * association aria-controls/aria-labelledby correcte, aucune bibliothèque
 * externe. Contrôlable (`value`/`onChange`) ou non (état interne). */
export function Tabs({
  items,
  value,
  onChange,
  defaultValue,
}: {
  items: TabItem[];
  value?: string;
  onChange?: (id: string) => void;
  defaultValue?: string;
}) {
  const baseId = useId();
  const [internal, setInternal] = useState(defaultValue ?? items[0]?.id);
  const active = value ?? internal;

  function select(id: string) {
    if (onChange) onChange(id);
    else setInternal(id);
  }

  function onKeyDown(e: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    e.preventDefault();
    let nextIndex = index;
    if (e.key === "ArrowRight") nextIndex = (index + 1) % items.length;
    if (e.key === "ArrowLeft") nextIndex = (index - 1 + items.length) % items.length;
    if (e.key === "Home") nextIndex = 0;
    if (e.key === "End") nextIndex = items.length - 1;
    const next = items[nextIndex];
    select(next.id);
    const el = document.getElementById(`${baseId}-tab-${next.id}`);
    el?.focus();
  }

  const activeItem = items.find((i) => i.id === active) ?? items[0];

  return (
    <div>
      <div className="tabs__list" role="tablist">
        {items.map((item, index) => (
          <button
            key={item.id}
            id={`${baseId}-tab-${item.id}`}
            type="button"
            role="tab"
            className="tabs__tab"
            aria-selected={item.id === active}
            aria-controls={`${baseId}-panel-${item.id}`}
            tabIndex={item.id === active ? 0 : -1}
            onClick={() => select(item.id)}
            onKeyDown={(e) => onKeyDown(e, index)}
          >
            {item.label}
          </button>
        ))}
      </div>
      {activeItem && (
        <div
          className="tabs__panel"
          role="tabpanel"
          id={`${baseId}-panel-${activeItem.id}`}
          aria-labelledby={`${baseId}-tab-${activeItem.id}`}
          key={activeItem.id}
        >
          {activeItem.content}
        </div>
      )}
    </div>
  );
}
