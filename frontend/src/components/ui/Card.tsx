import type { CSSProperties, HTMLAttributes } from "react";
import "./ui.css";

export function Card({
  interactive,
  className = "",
  ...rest
}: HTMLAttributes<HTMLDivElement> & { interactive?: boolean }) {
  const classes = ["ui-card", interactive ? "ui-card--interactive" : "", className].filter(Boolean).join(" ");
  return <div className={classes} {...rest} />;
}

export function Skeleton({ className = "", style }: { className?: string; style?: CSSProperties }) {
  return <div className={`skeleton ${className}`} style={style} aria-hidden="true" />;
}

export function SkeletonText({ lines = 1 }: { lines?: number }) {
  return (
    <div aria-hidden="true">
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className="skeleton skeleton--text" style={{ width: i === lines - 1 ? "60%" : "100%" }} />
      ))}
    </div>
  );
}
