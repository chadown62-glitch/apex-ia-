/**
 * Projet — consignes (envoyées au modèle dans chaque conversation du projet), pièces jointes TEXTE réellement
 * lues par APEX, conversations du projet, modification et suppression confirmée (avec ce qu'elle emporte).
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type ChangeEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { ApiError } from "../api/client";
import { Button } from "../components/ui/Button";
import { Modal } from "../components/ui/Modal";
import { assistantApi, errorMessage, fileToBase64 } from "./api";
import { keys, useConversations, useSettings } from "./hooks";
import { IconEdit, IconFile, IconPaperclip, IconPlus, IconTrash } from "./Icons";
import { ProjectForm } from "./ProjectForm";

/** Mêmes extensions que le backend (apex/assistant/attachments.py, TEXT_EXTENSIONS) — le backend reste juge. */
const ACCEPT =
  ".txt,.md,.markdown,.rst,.csv,.tsv,.json,.jsonl,.yaml,.yml,.toml,.ini,.cfg,.xml,.html,.htm,.css,.scss,.js,.mjs,.ts,.tsx,.jsx,.py,.ipynb,.sql,.sh,.ps1,.bat,.c,.h,.cpp,.hpp,.cs,.java,.kt,.go,.rs,.rb,.php,.swift,.r,.lua,.log";

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} o`;
  return `${(bytes / 1024).toFixed(1)} Ko`;
}

const date = (iso: string) => new Date(iso).toLocaleString("fr-FR", { dateStyle: "medium", timeStyle: "short" });

export function ProjectView() {
  const { projectId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const project = useQuery({
    queryKey: keys.project(projectId),
    queryFn: () => assistantApi.project(projectId),
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 1,
  });
  const attachments = useQuery({ queryKey: keys.attachments(projectId), queryFn: () => assistantApi.attachments(projectId) });
  const conversations = useConversations();
  const settings = useSettings();
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [uploadReport, setUploadReport] = useState<{ ok: string[]; refused: string[] } | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const ownConversations = (conversations.data ?? []).filter((c) => c.project_id === projectId);

  if (project.isError) {
    const notFound = project.error instanceof ApiError && project.error.status === 404;
    return (
      <div className="ax-page ax-empty">
        <h1>{notFound ? "Projet introuvable" : "Projet indisponible"}</h1>
        <p className="ax-muted">{notFound ? "Il a peut-être été supprimé." : errorMessage(project.error)}</p>
        <div className="ax-empty__actions">
          {!notFound && <Button onClick={() => project.refetch()}>Réessayer</Button>}
          <Button variant="primary" onClick={() => navigate("/")}>
            Accueil
          </Button>
        </div>
      </div>
    );
  }
  if (!project.data) {
    return (
      <div className="ax-page">
        <div className="ax-skeleton-thread" aria-label="Chargement du projet">
          <span />
          <span />
        </div>
      </div>
    );
  }
  const p = project.data;

  async function upload(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (files.length === 0) return;
    setUploading(true);
    const report = { ok: [] as string[], refused: [] as string[] };
    for (const file of files) {
      try {
        await assistantApi.addAttachment(projectId, file.name, await fileToBase64(file));
        report.ok.push(file.name);
      } catch (e) {
        report.refused.push(`${file.name} : ${errorMessage(e)}`);
      }
    }
    setUploadReport(report);
    setUploading(false);
    await queryClient.invalidateQueries({ queryKey: keys.attachments(projectId) });
  }

  async function removeAttachment(id: string) {
    try {
      await assistantApi.deleteAttachment(id);
    } catch (e) {
      setUploadReport({ ok: [], refused: [errorMessage(e)] });
    }
    await queryClient.invalidateQueries({ queryKey: keys.attachments(projectId) });
  }

  async function remove() {
    setDeleteError(null);
    try {
      await assistantApi.deleteProject(projectId);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.projects }),
        queryClient.invalidateQueries({ queryKey: keys.conversations }),
      ]);
      setConfirmDelete(false);
      navigate("/", { replace: true });
    } catch (e) {
      setDeleteError(errorMessage(e));
    }
  }

  const attachmentsOff = settings.data && !settings.data.privacy.attachments_enabled;

  return (
    <div className="ax-page ax-project-view">
      <header className="ax-page__head">
        <div>
          <p className="ax-eyebrow">Projet</p>
          <h1>{p.name}</h1>
          {p.description && <p className="ax-page__lead">{p.description}</p>}
          <p className="ax-muted">
            Créé le {date(p.created_at)}
            {p.updated_at ? ` · modifié le ${date(p.updated_at)}` : ""}
          </p>
        </div>
        <div className="ax-page__actions">
          <Button variant="primary" onClick={() => navigate(`/?project=${encodeURIComponent(p.id)}`)}>
            <IconPlus size={15} /> Nouvelle conversation
          </Button>
          <Button onClick={() => setEditing(true)}>
            <IconEdit size={15} /> Modifier
          </Button>
          <Button variant="danger" onClick={() => setConfirmDelete(true)}>
            <IconTrash size={15} /> Supprimer
          </Button>
        </div>
      </header>

      <div className="ax-project-grid">
        <section className="ax-card glass" aria-labelledby="ax-instructions">
          <h2 id="ax-instructions">Consignes</h2>
          {p.instructions ? (
            <p className="ax-prewrap">{p.instructions}</p>
          ) : (
            <p className="ax-muted">
              Aucune consigne. Les consignes sont envoyées au modèle dans chaque conversation de ce projet (ton, langue, contexte,
              contraintes).
            </p>
          )}
          <Button size="sm" onClick={() => setEditing(true)}>
            {p.instructions ? "Modifier les consignes" : "Ajouter des consignes"}
          </Button>
        </section>

        <section className="ax-card glass" aria-labelledby="ax-attachments">
          <div className="ax-card__head">
            <h2 id="ax-attachments">Pièces jointes</h2>
            <Button size="sm" onClick={() => fileInput.current?.click()} disabled={uploading}>
              <IconPaperclip size={15} /> {uploading ? "Envoi…" : "Joindre"}
            </Button>
            <input ref={fileInput} type="file" multiple accept={ACCEPT} hidden onChange={(e) => void upload(e)} />
          </div>
          <p className="ax-muted">
            Fichiers texte uniquement (code, notes, CSV, JSON…), 512 Ko au plus, 30 par projet. Leur contenu est joint aux
            conversations du projet et déposé dans l'espace de travail (pieces-jointes). Images, PDF et documents bureautiques sont
            refusés : APEX ne sait pas les lire.
          </p>
          {attachmentsOff && (
            <p className="ax-banner ax-banner--warning">
              Désactivées dans Paramètres → Confidentialité : les pièces jointes ne sont pas envoyées au modèle.
            </p>
          )}
          {uploadReport && (
            <div className="ax-upload-report" role="status">
              {uploadReport.ok.length > 0 && <p className="ax-ok">Ajouté : {uploadReport.ok.join(", ")}</p>}
              {uploadReport.refused.map((r) => (
                <p key={r} className="ax-inline-error">
                  Refusé — {r}
                </p>
              ))}
            </div>
          )}
          {attachments.isLoading ? (
            <p className="ax-muted">Chargement…</p>
          ) : attachments.isError ? (
            <p className="ax-inline-error">{errorMessage(attachments.error)}</p>
          ) : attachments.data && attachments.data.length > 0 ? (
            <ul className="ax-attachments">
              {attachments.data.map((a) => (
                <li key={a.id}>
                  <IconFile size={15} />
                  <span className="ax-attachments__name">{a.filename}</span>
                  <span className="ax-attachments__meta" title={`SHA-256 ${a.sha256}`}>
                    {formatSize(a.size_bytes)} · {a.sha256.slice(0, 10)}…
                  </span>
                  <button type="button" className="ax-icon-btn ax-icon-btn--sm" aria-label={`Retirer ${a.filename}`} onClick={() => void removeAttachment(a.id)}>
                    <IconTrash size={14} />
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="ax-muted">Aucune pièce jointe.</p>
          )}
        </section>

        <section className="ax-card glass ax-card--wide" aria-labelledby="ax-project-conversations">
          <h2 id="ax-project-conversations">Conversations ({ownConversations.length})</h2>
          {conversations.isLoading ? (
            <p className="ax-muted">Chargement…</p>
          ) : ownConversations.length === 0 ? (
            <p className="ax-muted">Aucune conversation dans ce projet pour l'instant.</p>
          ) : (
            <ul className="ax-conv-list">
              {ownConversations.map((c) => (
                <li key={c.id}>
                  <Link to={`/c/${c.id}`}>
                    <span className={`ax-mode-tag ax-mode-tag--${c.mode}`}>{c.mode}</span>
                    <span className="ax-conv-list__title">{c.title}</span>
                    <time dateTime={c.updated_at}>{date(c.updated_at)}</time>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <Modal open={editing} onClose={() => setEditing(false)} title="Modifier le projet">
        <ProjectForm
          initial={p}
          submitLabel="Enregistrer"
          onCancel={() => setEditing(false)}
          onSubmit={async (draft) => {
            const updated = await assistantApi.updateProject(p.id, draft);
            queryClient.setQueryData(keys.project(p.id), updated);
            await queryClient.invalidateQueries({ queryKey: keys.projects });
            setEditing(false);
          }}
        />
      </Modal>

      <Modal open={confirmDelete} onClose={() => setConfirmDelete(false)} title="Supprimer le projet">
        <p>
          Le projet « {p.name} » sera supprimé définitivement avec ses {ownConversations.length} conversation
          {ownConversations.length > 1 ? "s" : ""}, leurs messages, ses {attachments.data?.length ?? 0} pièce
          {(attachments.data?.length ?? 0) > 1 ? "s" : ""} jointe{(attachments.data?.length ?? 0) > 1 ? "s" : ""} et son espace de travail.
        </p>
        {deleteError && (
          <p className="ax-inline-error" role="alert">
            {deleteError}
          </p>
        )}
        <div className="ax-form__actions">
          <Button variant="ghost" onClick={() => setConfirmDelete(false)}>
            Annuler
          </Button>
          <Button variant="danger" onClick={() => void remove()}>
            Supprimer le projet
          </Button>
        </div>
      </Modal>
    </div>
  );
}
