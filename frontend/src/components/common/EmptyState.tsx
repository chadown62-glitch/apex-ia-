import "./common.css";

export function EmptyState({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="empty-state">
      <p className="empty-state__title">{title}</p>
      {detail && <p className="empty-state__detail">{detail}</p>}
    </div>
  );
}

export function ErrorState({ detail }: { detail?: string | null }) {
  return (
    <div className="empty-state empty-state--error">
      <p className="empty-state__title">Donnée indisponible</p>
      {detail && <p className="empty-state__detail">{detail}</p>}
    </div>
  );
}

export function LoadingState() {
  return <div className="empty-state empty-state--loading">Chargement…</div>;
}
