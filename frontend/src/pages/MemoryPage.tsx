import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { ApiError } from "../api/client";
import { addMemoryDocument, searchMemory } from "../api/endpoints";
import type { MemorySearchHit } from "../api/types";
import { EmptyState, ErrorState } from "../components/common/EmptyState";
import { Panel } from "../components/common/Panel";
import { Button } from "../components/ui/Button";
import { Checkbox, Input, Textarea } from "../components/ui/Field";
import "./pages.css";

const CATEGORIES: Array<{ id: string; label: string }> = [
  { id: "knowledge", label: "Documents" },
  { id: "execution", label: "Exécutions" },
  { id: "conversation", label: "Conversations" },
  { id: "project", label: "Projets" },
];

function errorText(error: unknown): string {
  return error instanceof ApiError ? error.message : String(error);
}

/**
 * Mémoire système d'APEX — UI -> `POST /memory/documents` (découpe + indexation, catégorie « knowledge ») et
 * `GET /memory/search` (BM25 lexical, provenance et score) : exactement ce que le Chat et le TaskLoop
 * reçoivent quand ils consultent la mémoire. Aucun embedding : la recherche est lexicale, et c'est dit.
 */
export function MemoryPage() {
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [query, setQuery] = useState("");
  const [categories, setCategories] = useState<string[]>(CATEGORIES.map((c) => c.id));

  const add = useMutation({ mutationFn: () => addMemoryDocument({ title, content }) });
  const search = useMutation({ mutationFn: () => searchMemory(query, categories) });
  const hits: MemorySearchHit[] = search.data?.status === "real" ? search.data.data ?? [] : [];

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Mémoire</h1>
          <p className="page__subtitle">
            Mémoire système réelle : documents, exécutions, conversations et projets indexés, recherche par
            pertinence lexicale (BM25) avec provenance — sans embeddings.
          </p>
        </div>
      </div>

      <Panel title="Ajouter un document">
        <Input aria-label="Titre du document" placeholder="Titre" value={title} onChange={(e) => setTitle(e.target.value)} />
        <Textarea aria-label="Contenu du document" rows={6} value={content} onChange={(e) => setContent(e.target.value)} />
        <Button variant="primary" onClick={() => add.mutate()} disabled={!title.trim() || !content.trim() || add.isPending}>
          Indexer le document
        </Button>
        {add.isError && <ErrorState detail={errorText(add.error)} />}
        {add.data?.status === "real" && add.data.data && (
          <p>Document indexé en {add.data.data.chunks} morceau(x) — source « document:{add.data.data.document_id} ».</p>
        )}
      </Panel>

      <Panel title="Recherche par pertinence">
        <Input aria-label="Requête" placeholder="ex. palindrome, Startup Gate…" value={query} onChange={(e) => setQuery(e.target.value)} />
        <div className="form-row">
          {CATEGORIES.map((c) => (
            <Checkbox
              key={c.id}
              label={c.label}
              checked={categories.includes(c.id)}
              onChange={(e) =>
                setCategories((prev) => (e.target.checked ? [...prev, c.id] : prev.filter((x) => x !== c.id)))
              }
            />
          ))}
        </div>
        <Button onClick={() => search.mutate()} disabled={!query.trim() || categories.length === 0 || search.isPending}>
          Rechercher
        </Button>
        {search.isError && <ErrorState detail={errorText(search.error)} />}
        {search.data && search.data.status !== "real" && <ErrorState detail={search.data.detail} />}
        {search.isSuccess && hits.length === 0 && <EmptyState title="Aucun souvenir pertinent" detail="aucun terme commun avec la requête" />}
        <ol aria-label="Résultats">
          {hits.map((h) => (
            <li key={h.id}>
              <span className="mono">[{h.category ?? "?"} · {h.source}]</span> score {h.score.toFixed(3)}
              <pre className="mono">{h.snippet}</pre>
            </li>
          ))}
        </ol>
      </Panel>
    </div>
  );
}
