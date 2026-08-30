import type { ReactNode } from "react";
import "./common.css";

export function Panel({
  title,
  action,
  children,
  dense,
}: {
  title?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  dense?: boolean;
}) {
  return (
    <section className={`panel ${dense ? "panel--dense" : ""}`}>
      {title && (
        <header className="panel__header">
          <h2 className="panel__title">{title}</h2>
          {action}
        </header>
      )}
      <div className="panel__body">{children}</div>
    </section>
  );
}
