import { useState, type FormEvent } from "react";

import { Button } from "../components/ui/Button";
import { Input, Textarea } from "../components/ui/Field";
import type { Project } from "./api";

export interface ProjectDraft {
  name: string;
  description: string;
  instructions: string;
}

/** Formulaire de projet (création et édition) — mêmes bornes que l'API ; l'erreur du backend est affichée telle quelle. */
export function ProjectForm({
  initial,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  initial?: Pick<Project, "name" | "description" | "instructions">;
  submitLabel: string;
  onSubmit: (draft: ProjectDraft) => Promise<void>;
  onCancel?: () => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [instructions, setInstructions] = useState(initial?.instructions ?? "");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) {
      setError("Le nom du projet est obligatoire.");
      return;
    }
    setPending(true);
    setError(null);
    try {
      await onSubmit({ name: name.trim(), description: description.trim(), instructions: instructions.trim() });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(false);
    }
  }

  return (
    <form className="ax-form" onSubmit={submit}>
      <label className="ax-field">
        <span className="ax-field__label">Nom</span>
        <Input value={name} maxLength={200} required autoFocus onChange={(e) => setName(e.target.value)} placeholder="Ex. Rénovation de l'appartement" />
      </label>
      <label className="ax-field">
        <span className="ax-field__label">Description</span>
        <Textarea value={description} maxLength={4000} rows={2} onChange={(e) => setDescription(e.target.value)} placeholder="À quoi sert ce projet (facultatif)" />
      </label>
      <label className="ax-field">
        <span className="ax-field__label">Consignes du projet</span>
        <Textarea
          value={instructions}
          maxLength={8000}
          rows={5}
          onChange={(e) => setInstructions(e.target.value)}
          placeholder="Envoyées au modèle dans chaque conversation du projet. Ex. « Réponds en français, montants en euros TTC. »"
        />
        <span className="ax-field__hint">{instructions.length} / 8000 caractères</span>
      </label>
      {error && (
        <p className="ax-inline-error" role="alert">
          {error}
        </p>
      )}
      <div className="ax-form__actions">
        {onCancel && (
          <Button variant="ghost" onClick={onCancel} disabled={pending}>
            Annuler
          </Button>
        )}
        <Button type="submit" variant="primary" disabled={pending}>
          {pending ? "Enregistrement…" : submitLabel}
        </Button>
      </div>
    </form>
  );
}
