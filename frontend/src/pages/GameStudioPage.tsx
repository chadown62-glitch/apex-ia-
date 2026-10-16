import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  cancelGameStudioMission, gameStudioCapabilities, getGameStudioArtifact,
  getGameStudioMission, listGameStudioMissions, resumeGameStudioMission,
  cancelGameStudioProject, getGameStudioProject, listGameStudioProjects,
  resumeGameStudioProject, submitGameStudioProject, getGameStudioProjectArtifact,
} from "../api/endpoints";
import type { GameStudioArtifact, GameStudioMission, GameStudioRequest } from "../api/types";
import { Panel } from "../components/common/Panel";
import "./pages.css";
import "./game-studio.css";

const initial: GameStudioRequest = {
  creation_type: "vehicule", style: "realiste", quality: "prototype", tool: "auto", compute_backend: "auto", controller_model: "auto",
  description: "Crée une voiture de sport réaliste avec une carrosserie détaillée, des matériaux PBR et quatre rendus.",
  judges_all_tasks: true, automatic_corrections: true, max_time_s: 1800,
  max_rss_mib: 12000, max_attempts: 3, criteria: {}, references: [],
};

function ArtifactImage({ jobId, artifact, project = false }: { jobId: string; artifact: GameStudioArtifact; project?: boolean }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    let reader: FileReader | null = null;
    (project ? getGameStudioProjectArtifact : getGameStudioArtifact)(jobId, artifact.hash).then((blob) => {
      if (!active) return;
      // The existing APEX UI CSP allows data: images, while blob: images are blocked.
      reader = new FileReader();
      reader.onload = () => { if (active && typeof reader?.result === "string") setUrl(reader.result); };
      reader.onerror = () => { if (active) setUrl(null); };
      reader.readAsDataURL(blob);
    }).catch(() => setUrl(null));
    return () => { active = false; if (reader?.readyState === FileReader.LOADING) reader.abort(); };
  }, [jobId, artifact.hash]);
  return url ? <img src={url} alt={`Rendu Blender natif ${artifact.name}`} /> : <span>Chargement du rendu…</span>;
}

function ArtifactDownload({ jobId, artifact, project = false }: { jobId: string; artifact: GameStudioArtifact; project?: boolean }) {
  const [busy, setBusy] = useState(false);
  async function download() {
    setBusy(true);
    try {
      const blob = await (project ? getGameStudioProjectArtifact : getGameStudioArtifact)(jobId, artifact.hash);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = artifact.name.split("/").pop() ?? "artifact";
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } finally { setBusy(false); }
  }
  return <button type="button" onClick={download} disabled={busy}>{artifact.name}</button>;
}

function MissionDetail({ mission, onCancel, onResume, controls = true }: {
  mission: GameStudioMission; onCancel: () => void; onResume: () => void; controls?: boolean;
}) {
  return <section className="game-mission" aria-label="Suivi de mission">
    <div className="game-mission__heading"><h3>{mission.request.description}</h3><strong>{mission.status}</strong></div>
    <p>Mission APEX {mission.mission_id} · {mission.specialist} · {mission.model}</p>
    {mission.budget && <p>Temps cumulé : {Math.ceil(mission.budget.observed_s ?? mission.budget.charged_s)} / {mission.budget.limit_s} s</p>}
    <p>Tâche : {mission.current_task} · début {new Date(mission.created_at).toLocaleString()}</p>
    {mission.error && <p role="alert">{mission.error}</p>}
    {controls && <div className="game-actions">
      {mission.status === "running" || mission.status === "queued" ? <button type="button" onClick={onCancel}>Arrêter</button> : null}
      {["failed", "interrupted", "cancelled"].includes(mission.status) ? <button type="button" onClick={onResume}>Reprendre</button> : null}
    </div>}
    <div className="game-revisions">
      {mission.revisions.map((revision) => {
        const renders = revision.artifacts.filter((item) => item.role === "view");
        return <article key={revision.number}>
          <h4>Version {revision.number} {mission.selected_revision_number === revision.number ? "· retenue" : ""} · juge : {revision.verdict}</h4>
          {revision.defects.length > 0 && <p>Défauts : {revision.defects.map((d) => String(d.id)).join(", ")}</p>}
          {revision.unavailable.length > 0 && <p>Contrôles non qualifiés : {revision.unavailable.length}</p>}
          <div className="game-renders">{renders.map((item) => <ArtifactImage key={item.hash} jobId={mission.id} artifact={item} />)}</div>
          <div className="game-files">{revision.artifacts.filter((item) => ["blender_project", "exchange", "report", "controller"].includes(item.role))
            .map((item) => <ArtifactDownload key={item.hash + item.name} jobId={mission.id} artifact={item} />)}</div>
        </article>;
      })}
    </div>
    {mission.controller_attempts?.length ? <div className="game-files">
      <p>Plans du contrôleur refusés (traces conservées) :</p>
      {mission.controller_attempts.map((item) => <ArtifactDownload key={item.hash + item.name} jobId={mission.id} artifact={item} />)}
    </div> : null}
    <details><summary>Historique et mesures</summary><pre>{JSON.stringify({ events: mission.events, resources: mission.resource_measurements, limitations: mission.limitations }, null, 2)}</pre></details>
  </section>;
}

async function encodeReferences(files: FileList | null, provenance: string): Promise<GameStudioRequest["references"]> {
  if (!files?.length) return [];
  if (!provenance.trim()) throw new Error("Indiquez la provenance et les droits des références.");
  if (files.length > 4) throw new Error("Quatre références maximum.");
  return Promise.all(Array.from(files).map(async (file) => {
    if (!["image/png", "image/jpeg", "text/plain"].includes(file.type) || file.size > 4_000_000)
      throw new Error(`Format ou taille de référence refusé : ${file.name}`);
    const encoded = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
      reader.onerror = () => reject(new Error("Lecture de référence impossible"));
      reader.readAsDataURL(file);
    });
    return { content_type: file.type as "image/png" | "image/jpeg" | "text/plain", data_base64: encoded,
      provenance: `${provenance.trim()} — ${file.name}` };
  }));
}

export function GameStudioPage() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<GameStudioRequest>(initial);
  const [files, setFiles] = useState<FileList | null>(null);
  const [provenance, setProvenance] = useState("");
  const [criteriaText, setCriteriaText] = useState("{}");
  const [selected, setSelected] = useState<string | null>(null);
  const [selectedProject, setSelectedProject] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const capabilities = useQuery({ queryKey: ["game-studio-capabilities"], queryFn: gameStudioCapabilities });
  const list = useQuery({ queryKey: ["game-studio-missions"], queryFn: listGameStudioMissions, refetchInterval: 5000 });
  const detail = useQuery({ queryKey: ["game-studio-mission", selected], queryFn: () => getGameStudioMission(selected!),
    enabled: Boolean(selected), refetchInterval: 3000 });
  const projects = useQuery({ queryKey: ["game-studio-projects"], queryFn: listGameStudioProjects, refetchInterval: 5000 });
  const projectDetail = useQuery({ queryKey: ["game-studio-project", selectedProject], queryFn: () => getGameStudioProject(selectedProject!),
    enabled: Boolean(selectedProject), refetchInterval: 3000 });
  const cancel = useMutation({ mutationFn: cancelGameStudioMission, onSuccess: () => queryClient.invalidateQueries({ queryKey: ["game-studio-mission"] }) });
  const resume = useMutation({ mutationFn: resumeGameStudioMission, onSuccess: () => queryClient.invalidateQueries({ queryKey: ["game-studio-mission"] }) });
  const projectSubmit = useMutation({ mutationFn: submitGameStudioProject, onSuccess: (result) => {
    if (result.data) { setSelectedProject(result.data.id); queryClient.invalidateQueries({ queryKey: ["game-studio-projects"] }); }
    else setError(result.detail ?? "Projet indisponible");
  }, onError: (failure) => setError(String(failure)) });
  const projectCancel = useMutation({ mutationFn: cancelGameStudioProject, onSuccess: () => queryClient.invalidateQueries({ queryKey: ["game-studio-project"] }) });
  const projectResume = useMutation({ mutationFn: resumeGameStudioProject, onSuccess: () => queryClient.invalidateQueries({ queryKey: ["game-studio-project"] }) });
  const mission = detail.data?.data;
  const project = projectDetail.data?.data;

  async function send(event: React.FormEvent) {
    event.preventDefault(); setError(null);
    try {
      const criteria = JSON.parse(criteriaText) as GameStudioRequest["criteria"];
      if (!criteria || Array.isArray(criteria) || typeof criteria !== "object") throw new Error("Critères JSON invalides");
      const references = await encodeReferences(files, provenance);
      projectSubmit.mutate({ ...form, criteria, references });
    } catch (failure) { setError(String(failure)); }
  }

  return <main className="game-studio-page">
    <header><h1>Game Studio</h1><p>APEX Game Graphics — spécialiste composé : Qwen local, Blender et juges techniques. Aucun nouveau modèle entraîné.</p></header>
    <Panel title="Capacités observées">
      {capabilities.data?.data ? <div className="game-capabilities">
        {Object.entries(capabilities.data.data).filter(([, value]) => typeof value === "object" && value !== null && "status" in value)
          .map(([name, value]) => <p key={name}><strong>{name}</strong> : {String((value as { status: string }).status)}</p>)}
      </div> : <p>{capabilities.data?.detail ?? "Diagnostic des dépendances en cours…"}</p>}
      <details><summary>Spécialiste, modèles et dépendances</summary><pre>{JSON.stringify(capabilities.data?.data, null, 2)}</pre></details>
    </Panel>
    <Panel title="Nouvelle mission graphique">
      <form onSubmit={send} className="game-form">
        <label>Type de création<select value={form.creation_type} onChange={(e) => {
          const creation_type = e.target.value as GameStudioRequest["creation_type"];
          setForm({ ...form, creation_type, description: creation_type === "environnement" && form.description === initial.description
            ? "Crée un petit garage réaliste avec voiture sportive, bâtiment, mobilier de décor et quatre rendus par asset."
            : form.description });
        }}>
          <option value="vehicule">Véhicule</option><option value="batiment">Bâtiment</option><option value="objet_3d">Objet 3D</option>
          <option value="environnement">Environnement : plan autonome</option><option value="monde_ouvert">Monde ouvert (plan et limites)</option><option value="autre">Autre (plan et limites)</option>
        </select></label>
        {form.creation_type === "autre" && <label>Type libre<input value={form.creation_other ?? ""} onChange={(e) => setForm({ ...form, creation_other: e.target.value })} /></label>}
        <label>Style<select value={form.style} onChange={(e) => setForm({ ...form, style: e.target.value as GameStudioRequest["style"] })}>
          <option value="realiste">Réaliste</option><option value="photorealiste">Photoréaliste</option><option value="stylise">Stylisé</option>
          <option value="cartoon_anime">Cartoon / anime</option><option value="low_poly">Low-poly</option><option value="autre">Autre</option>
        </select></label>
        {form.style === "autre" && <label>Style libre<input value={form.style_other ?? ""} onChange={(e) => setForm({ ...form, style_other: e.target.value })} /></label>}
        <label>Niveau<select value={form.quality} onChange={(e) => setForm({ ...form, quality: e.target.value as GameStudioRequest["quality"] })}>
          <option value="prototype">Prototype</option><option value="jouable">Jouable</option><option value="soigne">Soigné</option><option value="photorealiste">Photoréaliste</option>
        </select></label>
        <label>Outil<select value={form.tool} onChange={(e) => setForm({ ...form, tool: e.target.value as GameStudioRequest["tool"] })}>
          <option value="auto">Automatique</option><option value="blender">Blender</option><option value="unreal">Unreal Engine 5</option><option value="hybride">Hybride</option>
        </select></label>
        <label>Calcul du modèle<select value={form.compute_backend} onChange={(e) => setForm({ ...form, compute_backend: e.target.value as GameStudioRequest["compute_backend"] })}>
          <option value="auto">CUDA prioritaire</option><option value="cuda">CUDA obligatoire</option><option value="cpu">CPU choisi explicitement</option>
        </select></label>
        <label>Modèle local<select value={form.controller_model} onChange={(e) => setForm({ ...form, controller_model: e.target.value as GameStudioRequest["controller_model"] })}>
          <option value="auto">Sélection APEX</option><option value="pinned_7b">Qwen 7B épinglé</option><option value="apex_native" disabled>Modèle natif APEX — planification non qualifiée</option>
        </select></label>
        <label className="game-form__wide">Description<textarea value={form.description} rows={4} onChange={(e) => setForm({ ...form, description: e.target.value })} required minLength={20} /></label>
        <label className="game-form__wide">Références (PNG, JPEG, texte ; 4 maximum)<input type="file" multiple accept="image/png,image/jpeg,text/plain" onChange={(e) => setFiles(e.target.files)} /></label>
        <label className="game-form__wide">Provenance et droits des références<input value={provenance} onChange={(e) => setProvenance(e.target.value)} placeholder="Fichiers créés par moi, usage autorisé…" /></label>
        <label className="game-check"><input type="checkbox" checked={form.judges_all_tasks} onChange={(e) => setForm({ ...form, judges_all_tasks: e.target.checked })} /> Activer les juges sur toutes les tâches</label>
        <label className="game-check"><input type="checkbox" checked={form.automatic_corrections} onChange={(e) => setForm({ ...form, automatic_corrections: e.target.checked })} /> Autoriser les corrections automatiques</label>
        <label className="game-check"><input type="checkbox" checked={Boolean(form.plan_only)} onChange={(e) => setForm({ ...form, plan_only: e.target.checked })} /> Évaluer le plan sans lancer Blender</label>
        <label>Temps maximum (s)<input type="number" min={120} max={3600} value={form.max_time_s} onChange={(e) => setForm({ ...form, max_time_s: Number(e.target.value) })} /></label>
        <label>RAM échantillonnée max (Mio)<input type="number" min={512} max={16384} value={form.max_rss_mib} onChange={(e) => setForm({ ...form, max_rss_mib: Number(e.target.value) })} /></label>
        <label>Versions max par asset<input type="number" min={1} max={3} value={form.max_attempts} onChange={(e) => setForm({ ...form, max_attempts: Number(e.target.value) })} /></label>
        <label className="game-form__wide">Critères de qualité personnalisés (JSON)<textarea rows={2} value={criteriaText} onChange={(e) => setCriteriaText(e.target.value)} /></label>
        <p className="game-form__wide">Les projets construisent un plan borné avec le modèle local. Les familles exécutables sont véhicule, bâtiment et fauteuil ; les autres demandes restent signalées comme bloquées. Les devantures spécialisées non qualifiées sont bloquées. Les références sont conservées avec provenance, mais leur contenu n'est pas encore exploité par le planificateur. UE5 et CUDA gardent leur statut mesuré.</p>
        {error && <p role="alert" className="game-form__wide">{error}</p>}
        <button type="submit" disabled={projectSubmit.isPending}>{projectSubmit.isPending ? "Création…" : "Créer la mission APEX"}</button>
      </form>
    </Panel>
    <Panel title="Projets multi-assets">
      {projects.data?.data?.length ? <ul>{projects.data.data.map((item) => <li key={item.id}><button type="button" onClick={() => setSelectedProject(item.id)}>
        {item.recipe} · {item.status} · {new Date(item.created_at).toLocaleString()}
      </button></li>)}</ul> : <p>{projects.data?.detail ?? "Aucun projet enregistré."}</p>}
      {project && <section className="game-mission" aria-label="Suivi du projet">
        <h3>{project.request.description}</h3><p>Mission APEX {project.mission_id} · {project.status} · livraison {project.delivery_status ?? "en cours"} · {project.elapsed_s != null ? `temps actif cumulé : ${project.elapsed_s} s` : "en cours"}</p>
        {project.plan && <article><h4>Plan généré et validé</h4><p>{project.plan.steps.length} tâches · budget estimé pour admission {project.plan.estimated_minimum_s} s · tâches proposées exécutables : {project.plan.fully_feasible ? "oui" : "non"}. La fidélité artistique reste non certifiée.</p>
          {project.plan.coverage && <details open><summary>Couverture des exigences · {project.plan.coverage.verdict}</summary><p>Contrôle indépendant des quantités et contextes reconnus ; les propriétés non analysées restent non vérifiées.</p><pre>{JSON.stringify(project.plan.coverage, null, 2)}</pre></details>}
          {project.plan.unmet_requirements.length > 0 && <p>Demandes non satisfaites : {project.plan.unmet_requirements.join(" ; ")}</p>}
        </article>}
        {project.budget && <p>Budget cumulé : {Math.ceil(project.budget.observed_s ?? project.budget.charged_s)} / {project.budget.limit_s} s. Réservation conservatrice de 10 s maximum après interruption.</p>}
        {project.planning_artifact && <ArtifactDownload jobId={project.id} artifact={project.planning_artifact} project />}
        <div className="game-actions">
          {["running", "queued"].includes(project.status) && <button type="button" onClick={() => projectCancel.mutate(project.id)}>Arrêter le projet</button>}
          {(["failed", "interrupted", "cancelled"].includes(project.status) && (!project.budget || project.budget.charged_s < project.budget.limit_s)) &&
            <button type="button" onClick={() => projectResume.mutate(project.id)}>Reprendre le projet</button>}
        </div>
        {project.steps.map((step) => <article key={step.key}>
          <h4>{step.key} · {step.kind ?? step.creation_type} · {step.specialist} · {step.status}</h4>
          <p>{step.description}</p><p>Dépendances : {step.depends_on.length ? step.depends_on.join(", ") : "aucune"}</p>
          {step.reason && <p>Limite : {step.reason}</p>}
          {step.handoff && <details><summary>Précontrôle Game Engineer et correction · {step.handoff.status}</summary>
            <pre>{JSON.stringify({ before: step.handoff.before, after: step.handoff.after }, null, 2)}</pre>
            <div className="game-files">{step.handoff.artifacts?.map((item) =>
              <ArtifactDownload key={item.hash + item.name} jobId={project.id} artifact={item} project />)}</div></details>}
          {step.mission && <MissionDetail mission={step.mission} controls={false} onCancel={() => cancel.mutate(step.mission!.id)} onResume={() => resume.mutate(step.mission!.id)} />}
        </article>)}
        {project.assembly && <article>
          <h4>Scène assemblée · version {project.assembly.version ?? 1} · {project.assembly.verdict}</h4>
          <p>Fichier Blender rouvert dans un second processus. Meshes par asset : {JSON.stringify(project.assembly.source_mesh_counts)}. Qualité visuelle certifiée : non.</p>
          {project.assembly.engineer_handoff && <p>Game Engineer : {project.assembly.engineer_handoff.status} · {project.assembly.engineer_handoff.reason}.</p>}
          <div className="game-renders">{project.assembly.artifacts.filter((item) => item.role === "view").map((item) =>
            <ArtifactImage key={item.hash} jobId={project.id} artifact={item} project />)}</div>
          <div className="game-files">{project.assembly.artifacts.filter((item) => item.role !== "view").map((item) =>
            <ArtifactDownload key={item.hash + item.name} jobId={project.id} artifact={item} project />)}</div>
        </article>}
        {project.assembly_history?.map((prior, index) => <article key={index}>
          <h4>Scène antérieure · version {prior.version ?? index + 1}</h4>
          <div className="game-renders">{prior.artifacts.filter((item) => item.role === "view").map((item) =>
            <ArtifactImage key={item.hash} jobId={project.id} artifact={item} project />)}</div>
          <div className="game-files">{prior.artifacts.filter((item) => item.role !== "view").map((item) =>
            <ArtifactDownload key={item.hash + item.name} jobId={project.id} artifact={item} project />)}</div>
        </article>)}
        <details><summary>Historique et limites</summary><pre>{JSON.stringify({ events: project.events, limitations: project.limitations }, null, 2)}</pre></details>
      </section>}
    </Panel>
    <Panel title="Missions Game Studio">
      {list.data?.data?.length ? <ul>{list.data.data.map((item) => <li key={item.id}><button type="button" onClick={() => setSelected(item.id)}>
        {item.request.creation_type} · {item.status} · {new Date(item.created_at).toLocaleString()}
      </button></li>)}</ul> : <p>{list.data?.detail ?? "Aucune mission enregistrée."}</p>}
      {mission && <MissionDetail mission={mission} onCancel={() => cancel.mutate(mission.id)} onResume={() => resume.mutate(mission.id)} />}
    </Panel>
  </main>;
}
