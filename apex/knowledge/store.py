"""Knowledge store : journal JSONL en ajout seul, chaîné par empreintes.

Chaque ligne porte l'empreinte de la précédente : modifier, supprimer ou
réordonner une entrée casse la chaîne et `verify()` le signale. Un élément
n'est jamais réécrit ; une révision est une nouvelle entrée."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

from apex.knowledge.model import Decision, KnowledgeItem, item_from_dict

GENESIS = "sha256:" + "0" * 64


class StoreCorruptedError(RuntimeError):
    """La chaîne d'empreintes du journal est rompue."""


def _entry_hash(seq: int, prev: str, item: dict[str, object]) -> str:
    payload = json.dumps({"seq": seq, "prev": prev, "item": item}, sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


class KnowledgeStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _entries(self) -> Iterator[dict[str, object]]:
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)

    def verify(self) -> int:
        """Nombre d'entrées si la chaîne est intacte, sinon `StoreCorruptedError`."""
        prev = GENESIS
        count = 0
        for seq, entry in enumerate(self._entries()):
            item = entry["item"]
            if entry.get("seq") != seq or entry.get("prev") != prev or not isinstance(item, dict):
                raise StoreCorruptedError(f"entrée {seq} : séquence ou chaînage incohérent")
            if entry.get("hash") != _entry_hash(seq, prev, item):
                raise StoreCorruptedError(f"entrée {seq} : contenu modifié après écriture")
            prev = str(entry["hash"])
            count += 1
        return count

    def _tail(self) -> tuple[int, str]:
        seq, prev = 0, GENESIS
        for entry in self._entries():
            seq, prev = int(str(entry["seq"])) + 1, str(entry["hash"])
        return seq, prev

    def append(self, item: KnowledgeItem) -> str:
        self.verify()
        seq, prev = self._tail()
        data = item.to_dict()
        digest = _entry_hash(seq, prev, data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {"seq": seq, "prev": prev, "hash": digest, "item": data},
                    sort_keys=True,
                    ensure_ascii=False,
                )
                + "\n"
            )
        return digest

    def items(self, decision: Decision | None = None) -> list[KnowledgeItem]:
        self.verify()
        loaded = [item_from_dict(entry["item"]) for entry in self._entries()]  # type: ignore[arg-type]
        return [i for i in loaded if decision is None or i.decision is decision]
