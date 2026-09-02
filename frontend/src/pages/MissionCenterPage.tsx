import { useMemo, useState } from "react";

import {
  useAgents,
  useCancelMission,
  useConfiguredAgents,
  useCreateMission,
  useEvents,
  useLaunchMission,
  useMission,
  useMissionExecutions,
  useMissions,
  useRejectMission,
  useRunMissionViaAgent,
  useValidateMission,
} from "../api/hooks";
import type { MissionPriority } from "../api/types";
import { ErrorState, LoadingState } from "../components/common/EmptyState";
import { Panel } from "../components/common/Panel";
import { PlannedBanner } from "../components/common/PlannedSections";
import { EventTimeline } from "../components/events/EventTimeline";
import { SPECIALTY_DEFINITIONS } from "../specialties/data";
import type { MissionState } from "../api/types";
import "./pages.css";
import "./mission-center.css";

const PRIORITIES: MissionPriority[] = ["low", "normal", "high", "critical"];

const STATE_LABEL: Record<string, string> = {
  draft: "Brouillon", queued: "En file", running: "En cours", review: "En revue",
  completed: "Terminée", failed: "Échouée", cancelled: "Annulée",
};

/** Vocabulaire d'état d'une Mission (apex.missions.states.MissionState) —
 * distinct de DataStatus (real/simulated/unknown) : répond à "où en est la
 * mission", pas "la donnée est-elle réelle". Ne jamais réutiliser
 * StatusBadge ici, ce serait fusionner deux vocabulaires différents. */
function MissionStatePill({ state }: { state: MissionState }) {
  return <span className={`mission-state mission-state--${state}`}>{STATE_LABEL[state] ?? state}</span>;
}

/**
 * Mission Engine (apex.missions.service) est réellement câblé : créer,
 * lister, consulter et annuler une mission le sont bout en bout via
 * PostgreSQL. Depuis la passe « Agents V1 », une mission portant un
 * `agent_id` peut réellement atteindre REVIEW via « Lancer via Agent »
 * (AgentRegistryService.run_execution → Model Router → provider) ; Valider/
 * rejeter restent désactivés tant que la mission sélectionnée n'est pas
 * réellement REVIEW, jamais activés par supposition.
 */
export function MissionCenterPage() {
  const agents = useAgents();
  const configuredAgents = useConfiguredAgents();
  const missions = useMissions();
  const createMission = useCreateMission();
  const cancelMission = useCancelMission();
  const validateMission = useValidateMission();
  const rejectMission = useRejectMission();
  const launchMission = useLaunchMission();
  const runViaAgent = useRunMissionViaAgent();

  const [specialtyId, setSpecialtyId] = useState("research");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [objective, setObjective] = useState("");
  const [agentId, setAgentId] = useState("");
  const [configuredAgentId, setConfiguredAgentId] = useState("");
  const [priority, setPriority] = useState<MissionPriority>("normal");
  const [constraints, setConstraints] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);
  const [selectedMissionId, setSelectedMissionId] = useState<string | null>(null);

  const configuredAgentList = configuredAgents.data?.status === "real" ? configuredAgents.data.data ?? [] : [];

  const specialty = SPECIALTY_DEFINITIONS.find((s) => s.id === specialtyId) ?? SPECIALTY_DEFINITIONS[0];
  const compatibleTypes = specialty.workspace?.compatibleAgentTypes ?? [];
  const compatibleAgents = useMemo(() => {
    const data = agents.data?.data ?? [];
    if (compatibleTypes.length === 0) return [];
    return data.filter((a) => compatibleTypes.includes(a.type));
  }, [agents.data, compatibleTypes]);

  const selected = useMission(selectedMissionId ?? undefined);
  const selectedMission = selected.data?.status === "real" ? selected.data.data : null;
  const missionExecutions = useMissionExecutions(selectedMissionId ?? undefined);
  const executionList = missionExecutions.data?.status === "real" ? missionExecutions.data.data ?? [] : [];
  const latestExecution = executionList[0] ?? null;
  const events = useEvents(200);
  const history = selectedMissionId
    ? (events.data?.data?.filter((e) => e.context.mission_id === selectedMissionId) ?? [])
    : [];

  async function handleCreate() {
    setCreateError(null);
    const envelope = await createMission.mutateAsync({
      title, description, specialty: specialtyId, objective, priority,
      constraints: constraints || null, assigned_agent: agentId || null,
      agent_id: configuredAgentId || null,
    });
    if (envelope.status === "real" && envelope.data) {
      setSelectedMissionId(envelope.data.id);
      setTitle("");
      setDescription("");
      setObjective("");
      setAgentId("");
      setConfiguredAgentId("");
      setConstraints("");
    } else {
      setCreateError(envelope.detail ?? "Création refusée par le backend.");
    }
  }

  const canValidateOrReject = selectedMission?.state === "review";

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Mission Center</h1>
          <p className="page__subtitle">Mission Engine — création/liste/consultation/annulation réelles (PostgreSQL).</p>
        </div>
      </div>

      <PlannedBanner note="Validation/rejet exigent l'état REVIEW — atteignable en assignant « Mon Agent » à la mission puis « Lancer via Agent » (Agents V1, apex/agent_registry/service.py) ; sinon la mission reste en file, ces boutons restent désactivés tant qu'elle n'est pas réellement REVIEW." />

      <div className="two-col">
        <Panel title="Nouvelle mission">
          <div className="mission-form">
            <label className="mission-form__field">
              <span>Titre</span>
              <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Titre de la mission" />
            </label>

            <label className="mission-form__field">
              <span>Description</span>
              <textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
            </label>

            <label className="mission-form__field">
              <span>Spécialité</span>
              <select
                value={specialtyId}
                onChange={(e) => {
                  setSpecialtyId(e.target.value);
                  setAgentId("");
                }}
              >
                {SPECIALTY_DEFINITIONS.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.icon} {s.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="mission-form__field">
              <span>Objectif</span>
              <textarea rows={3} value={objective} onChange={(e) => setObjective(e.target.value)} placeholder="Ce que la mission doit accomplir…" />
            </label>

            <label className="mission-form__field">
              <span>Agent compatible ({compatibleAgents.length} disponible{compatibleAgents.length > 1 ? "s" : ""}, optionnel)</span>
              <select value={agentId} onChange={(e) => setAgentId(e.target.value)} disabled={compatibleAgents.length === 0}>
                <option value="">— aucun —</option>
                {compatibleAgents.map((a) => (
                  <option key={a.clone_id} value={a.clone_id}>
                    {a.type} · {a.clone_id.slice(0, 8)} · {a.state}
                  </option>
                ))}
              </select>
            </label>

            <label className="mission-form__field">
              <span>Mon Agent ({configuredAgentList.length} disponible{configuredAgentList.length > 1 ? "s" : ""}, exécutable via le Model Router, optionnel)</span>
              <select
                value={configuredAgentId}
                onChange={(e) => setConfiguredAgentId(e.target.value)}
                disabled={configuredAgentList.length === 0}
              >
                <option value="">— aucun —</option>
                {configuredAgentList.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name} · {a.role}
                  </option>
                ))}
              </select>
            </label>

            <div className="mission-form__row">
              <label className="mission-form__field">
                <span>Priorité</span>
                <select value={priority} onChange={(e) => setPriority(e.target.value as MissionPriority)}>
                  {PRIORITIES.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
              </label>
              <label className="mission-form__field">
                <span>Contraintes</span>
                <input value={constraints} onChange={(e) => setConstraints(e.target.value)} placeholder="ex. pas d'accès réseau" />
              </label>
            </div>

            <label className="mission-form__field mission-form__field--checkbox">
              <input type="checkbox" checked disabled />
              <span>Validation humaine requise avant COMPLETED (non désactivable — ADR-0011)</span>
            </label>

            <button
              className="action-button action-button--live"
              disabled={!title.trim() || !objective.trim() || createMission.isPending}
              onClick={handleCreate}
            >
              {createMission.isPending ? "Création…" : "Créer la mission"}
            </button>
            {createError && <ErrorState detail={createError} />}
          </div>
        </Panel>

        <Panel title={selectedMission ? "Mission sélectionnée" : "Statut"}>
          {!selectedMissionId ? (
            <p style={{ margin: 0, fontSize: 13, color: "var(--text-muted)" }}>
              Aucune mission sélectionnée — créez-en une ou choisissez-en une dans la liste ci-dessous.
            </p>
          ) : selected.isLoading ? (
            <LoadingState />
          ) : !selectedMission ? (
            <ErrorState detail={selected.data?.detail} />
          ) : (
            <div className="mission-detail">
              <div className="mission-detail__row">
                <strong>{selectedMission.title}</strong>
                <MissionStatePill state={selectedMission.state} />
              </div>
              <div className="kv-table-wrap">
                <table className="kv-table">
                  <tbody>
                    <tr><th>ID</th><td className="mono">{selectedMission.id}</td></tr>
                    <tr><th>État</th><td>{STATE_LABEL[selectedMission.state] ?? selectedMission.state}</td></tr>
                    <tr><th>Spécialité</th><td>{selectedMission.specialty}</td></tr>
                    <tr><th>Agent assigné (Clone)</th><td className="mono">{selectedMission.assigned_agent ?? "— aucun —"}</td></tr>
                    <tr><th>Mon Agent assigné</th><td className="mono">{selectedMission.agent_id ?? "— aucun —"}</td></tr>
                    <tr><th>Priorité</th><td>{selectedMission.priority}</td></tr>
                    <tr><th>Décision humaine</th><td>{selectedMission.validation_status ?? "— aucune —"}</td></tr>
                    {latestExecution && (
                      <>
                        <tr><th>Modèle utilisé</th><td className="mono">{latestExecution.model_id ?? "— inconnu —"}</td></tr>
                        <tr><th>Provider utilisé</th><td className="mono">{latestExecution.provider_id ?? "— inconnu —"}</td></tr>
                      </>
                    )}
                    {selectedMission.result && <tr><th>Résultat</th><td>{selectedMission.result}</td></tr>}
                    {selectedMission.error && <tr><th>Erreur</th><td>{selectedMission.error}</td></tr>}
                  </tbody>
                </table>
              </div>

              <div className="mission-detail__actions">
                <button
                  className="action-button action-button--live"
                  disabled={selectedMission.state !== "queued" || launchMission.isPending}
                  title={selectedMission.state === "queued" ? undefined : "Mission pas en file — rien à ordonnancer"}
                  onClick={() => launchMission.mutate(selectedMission.id)}
                >
                  {launchMission.isPending ? "Lancement…" : "Lancer"}
                </button>
                <button
                  className="action-button action-button--live"
                  disabled={!selectedMission.agent_id || selectedMission.state !== "queued" || runViaAgent.isPending}
                  title={
                    !selectedMission.agent_id
                      ? "Aucun Agent assigné à cette mission"
                      : selectedMission.state === "queued"
                        ? undefined
                        : "Mission pas en file — rien à exécuter"
                  }
                  onClick={() => runViaAgent.mutate({ missionId: selectedMission.id })}
                >
                  {runViaAgent.isPending ? "Exécution…" : "Lancer via Agent"}
                </button>
                <button
                  className="action-button action-button--live"
                  disabled={!selectedMission || ["completed", "failed", "cancelled"].includes(selectedMission.state) || cancelMission.isPending}
                  onClick={() => cancelMission.mutate(selectedMission.id)}
                >
                  Annuler
                </button>
                <button
                  className="action-button"
                  disabled={!canValidateOrReject || validateMission.isPending}
                  title={canValidateOrReject ? undefined : "Mission pas en REVIEW — validation indisponible"}
                  onClick={() => canValidateOrReject && validateMission.mutate(selectedMission.id)}
                >
                  Valider
                </button>
                <button
                  className="action-button"
                  disabled={!canValidateOrReject || rejectMission.isPending}
                  title={canValidateOrReject ? undefined : "Mission pas en REVIEW — rejet indisponible"}
                  onClick={() => canValidateOrReject && rejectMission.mutate({ missionId: selectedMission.id })}
                >
                  Rejeter
                </button>
              </div>
              {launchMission.data && launchMission.data.status !== "real" && (
                <ErrorState detail={launchMission.data.detail} />
              )}
              {runViaAgent.data && runViaAgent.data.status !== "real" && (
                <ErrorState detail={runViaAgent.data.detail} />
              )}
            </div>
          )}
        </Panel>
      </div>

      {selectedMissionId && (
        <Panel title="Historique de la mission" dense>
          {events.isLoading ? <LoadingState /> : <EventTimeline events={history} />}
        </Panel>
      )}

      <Panel title="Missions récentes" dense>
        {missions.isLoading ? (
          <LoadingState />
        ) : missions.data?.status === "real" && missions.data.data && missions.data.data.length > 0 ? (
          <div className="kv-table-wrap">
            <table className="kv-table">
              <thead>
                <tr>
                  <th>Titre</th>
                  <th>Spécialité</th>
                  <th>État</th>
                  <th>Créée</th>
                </tr>
              </thead>
              <tbody>
                {missions.data.data.map((m) => (
                  <tr
                    key={m.id}
                    className="mission-row"
                    onClick={() => setSelectedMissionId(m.id)}
                    style={{ cursor: "pointer", background: m.id === selectedMissionId ? "var(--bg-hover)" : undefined }}
                  >
                    <td>{m.title}</td>
                    <td>{m.specialty}</td>
                    <td><MissionStatePill state={m.state} /></td>
                    <td className="mono">{new Date(m.created_at).toLocaleString("fr-FR")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <ErrorState detail={missions.data?.detail ?? "Aucune mission créée pour l'instant."} />
        )}
      </Panel>
    </div>
  );
}
