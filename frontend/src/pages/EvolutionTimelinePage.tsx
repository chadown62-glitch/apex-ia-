import { useAuditLog } from "../api/hooks";
import { AuditTimeline } from "../components/events/EventTimeline";
import { ErrorState, LoadingState } from "../components/common/EmptyState";
import { Panel } from "../components/common/Panel";
import "./pages.css";

export function EvolutionTimelinePage() {
  const audit = useAuditLog(200);

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Evolution Timeline</h1>
          <p className="page__subtitle">audit_log (EXISTS, INSERT-only) — chaque décision de gouvernance, jamais modifiable.</p>
        </div>
      </div>

      <Panel dense>
        {audit.isLoading ? (
          <LoadingState />
        ) : audit.data?.status === "real" && audit.data.data ? (
          <AuditTimeline entries={audit.data.data} />
        ) : (
          <ErrorState detail={audit.data?.detail} />
        )}
      </Panel>
    </div>
  );
}
