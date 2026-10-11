"""APEX-Q5 — quantification 5 bits des poids du Cerveau APEX (format et algorithme propres à APEX).

Format : chaque ligne d'une matrice est découpée en blocs de 32 poids. Par bloc :

    w ≈ minimum + q * échelle        q entier dans [0, 31] (5 bits)

`échelle` et `minimum` en float16 ; les 32 valeurs q tiennent en 20 octets (16 octets pour les 4 bits de
poids faible, 4 octets pour le bit de poids fort). Soit 6 bits par poids au total (5 + 1 pour échelle et
minimum), contre 32 en float32 : fichier 5,3 fois plus petit.

Algorithme : `échelle`/`minimum` initiaux tirés de l'étendue du bloc, puis AJUSTÉS par moindres carrés (on
recalcule q, puis le couple (échelle, minimum) qui minimise l'erreur quadratique pour ces q, plusieurs
fois) : l'erreur est mesurée, jamais supposée (`QuantReport`).

Deux usages à l'inférence (`apex.brain.inference`) :
- **déquantifié au chargement** : le fichier est Q5 (petit), les poids en mémoire sont en pleine précision —
  le plus rapide ;
- **Q5 résident** (`Q5Linear`) : les poids restent compressés en mémoire, chaque couche les reconstruit au
  moment du calcul — mémoire minimale, calcul plus lent (pas de noyau fusionné : mesuré, pas supposé).

Les vecteurs (normes) restent en float32 : quelques milliers de valeurs, aucune raison de les dégrader.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
import torch.nn.functional as F
from torch import nn

BLOCK = 32
BYTES_PER_BLOCK = 20
FORMAT = "apex-q5/1"
_LEVELS = 31


def _blocks(weight: torch.Tensor) -> tuple[torch.Tensor, int]:
    """(blocs [n, 32] en float32, colonnes ajoutées pour compléter la dernière ligne)."""
    cols = weight.shape[1]
    pad = (-cols) % BLOCK
    w = weight.detach().to(torch.float32)
    if pad:
        w = F.pad(w, (0, pad))
    return w.reshape(-1, BLOCK), pad


def quantize_q5(weight: torch.Tensor, *, refine_steps: int = 4) -> dict[str, torch.Tensor]:
    """Matrice 2D -> {"q5": uint8 [blocs, 20], "scale": float16 [blocs], "min": float16 [blocs]}."""
    if weight.dim() != 2:
        raise ValueError("APEX-Q5 quantifie des matrices (2 dimensions)")
    w, _ = _blocks(weight)
    low = w.min(dim=1).values
    high = w.max(dim=1).values
    scale = (high - low) / _LEVELS
    minimum = low.clone()
    for _ in range(refine_steps):
        safe = torch.where(scale > 0, scale, torch.ones_like(scale))
        q = torch.clamp(torch.round((w - minimum[:, None]) / safe[:, None]), 0, _LEVELS)
        # Moindres carrés de w = minimum + scale * q (régression linéaire de w sur q, bloc par bloc).
        q_mean = q.mean(dim=1)
        w_mean = w.mean(dim=1)
        covariance = ((q - q_mean[:, None]) * (w - w_mean[:, None])).sum(dim=1)
        variance = ((q - q_mean[:, None]) ** 2).sum(dim=1)
        fitted = torch.where(variance > 0, covariance / torch.where(variance > 0, variance, 1.0), scale)
        fitted = torch.where(fitted > 0, fitted, scale)
        scale = fitted
        minimum = w_mean - scale * q_mean
    # Les valeurs stockées sont en float16 : q est recalculé avec ces valeurs-là (celles du décodage).
    scale16 = scale.to(torch.float16)
    min16 = minimum.to(torch.float16)
    safe = torch.where(scale16.float() > 0, scale16.float(), torch.ones_like(scale))
    q = torch.clamp(torch.round((w - min16.float()[:, None]) / safe[:, None]), 0, _LEVELS).to(torch.uint8)
    return {"q5": pack(q), "scale": scale16, "min": min16}


def pack(q: torch.Tensor) -> torch.Tensor:
    """[blocs, 32] valeurs 0..31 -> [blocs, 20] octets : 16 octets de quartets bas (valeurs 2i et 2i+1),
    puis 32 bits hauts (petit-boutiste)."""
    low = (q & 0x0F).to(torch.uint8)
    nibbles = low[:, 0::2] | (low[:, 1::2] << 4)
    high = ((q >> 4) & 1).to(torch.int64)
    bits = (high << torch.arange(32, dtype=torch.int64)).sum(dim=1)
    high_bytes = torch.stack([(bits >> (8 * i)) & 0xFF for i in range(4)], dim=1).to(torch.uint8)
    return torch.cat([nibbles, high_bytes], dim=1)


def unpack(packed: torch.Tensor) -> torch.Tensor:
    """Inverse de `pack` : [blocs, 20] -> [blocs, 32] (uint8, 0..31). Opérations vectorisées (CPU ou
    GPU)."""
    nibbles = packed[:, :16]
    low = torch.stack([nibbles & 0x0F, nibbles >> 4], dim=2).reshape(-1, 32)
    high_bytes = packed[:, 16:].to(torch.int32)
    bits = high_bytes[:, 0] | (high_bytes[:, 1] << 8) | (high_bytes[:, 2] << 16) | (high_bytes[:, 3] << 24)
    shifts = torch.arange(32, dtype=torch.int32, device=packed.device)
    high = ((bits[:, None] >> shifts) & 1).to(torch.uint8)
    return low | (high << 4)


def dequantize_q5(
    parts: dict[str, torch.Tensor], shape: tuple[int, int], *, dtype: torch.dtype = torch.float32
) -> torch.Tensor:
    q = unpack(parts["q5"]).to(dtype)
    w = parts["min"].to(dtype)[:, None] + q * parts["scale"].to(dtype)[:, None]
    rows, cols = shape
    padded = cols + (-cols) % BLOCK
    return w.reshape(rows, padded)[:, :cols]


@dataclass(frozen=True)
class QuantReport:
    tensors: int
    parameters_q5: int
    parameters_full: int
    bytes_q5: int
    bytes_float32: int
    max_relative_rmse: float
    mean_relative_rmse: float

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def quantize_state(
    state: dict[str, torch.Tensor],
) -> tuple[dict[str, torch.Tensor], dict[str, Any], QuantReport]:
    """Poids d'un modèle -> (tenseurs à écrire, métadonnées, rapport d'erreur MESURÉ). Les matrices sont
    quantifiées, les vecteurs gardés en float32. Les poids partagés (tête de sortie liée aux embeddings) ne
    sont écrits qu'une fois."""
    tensors: dict[str, torch.Tensor] = {}
    layout: dict[str, Any] = {}
    errors: list[float] = []
    seen: dict[int, str] = {}
    q5_params = full_params = q5_bytes = 0
    for name, tensor in state.items():
        if tensor.data_ptr() in seen:
            layout[name] = {"alias": seen[tensor.data_ptr()]}
            continue
        seen[tensor.data_ptr()] = name
        if tensor.dim() == 2 and tensor.numel() >= BLOCK:
            parts = quantize_q5(tensor)
            restored = dequantize_q5(parts, tuple(tensor.shape))
            reference = tensor.detach().float()
            rmse = float(torch.sqrt(((restored - reference) ** 2).mean()))
            norm = float(torch.sqrt((reference**2).mean())) or 1.0
            errors.append(rmse / norm)
            for suffix, part in parts.items():
                tensors[f"{name}.{suffix}"] = part.contiguous()
            layout[name] = {"format": "q5", "shape": list(tensor.shape)}
            q5_params += tensor.numel()
            q5_bytes += sum(p.numel() * p.element_size() for p in parts.values())
        else:
            tensors[name] = tensor.detach().to(torch.float32).contiguous()
            layout[name] = {"format": "f32", "shape": list(tensor.shape)}
            full_params += tensor.numel()
    report = QuantReport(
        tensors=len(errors),
        parameters_q5=q5_params,
        parameters_full=full_params,
        bytes_q5=q5_bytes + full_params * 4,
        bytes_float32=(q5_params + full_params) * 4,
        max_relative_rmse=round(max(errors, default=0.0), 6),
        mean_relative_rmse=round(sum(errors) / len(errors), 6) if errors else 0.0,
    )
    return tensors, {"format": FORMAT, "block": BLOCK, "layout": layout}, report


def save_q5(model: nn.Module, path: Path) -> QuantReport:
    from safetensors.torch import save_file

    tensors, metadata, report = quantize_state(model.state_dict())
    save_file(tensors, str(path), metadata={"apex": json.dumps(metadata)})
    return report


def read_layout(path: Path) -> dict[str, Any]:
    from safetensors import safe_open

    with safe_open(str(path), framework="pt") as handle:
        raw = (handle.metadata() or {}).get("apex")
    if raw is None:
        raise ValueError(f"{path} : pas un fichier APEX-Q5 (métadonnées absentes)")
    layout = json.loads(raw)
    if layout.get("format") != FORMAT:
        raise ValueError(f"{path} : format {layout.get('format')} inconnu (attendu {FORMAT})")
    return layout


def load_q5_state(path: Path, *, dtype: torch.dtype = torch.float32) -> dict[str, torch.Tensor]:
    """Fichier APEX-Q5 -> poids pleine précision (`dtype`) prêts pour `load_state_dict`."""
    from safetensors.torch import load_file

    layout = read_layout(path)["layout"]
    raw = load_file(str(path))
    state: dict[str, torch.Tensor] = {}
    for name, entry in layout.items():
        if "alias" in entry:
            continue
        if entry["format"] == "q5":
            parts = {suffix: raw[f"{name}.{suffix}"] for suffix in ("q5", "scale", "min")}
            state[name] = dequantize_q5(parts, tuple(entry["shape"]), dtype=dtype)
        else:
            state[name] = raw[name].to(dtype)
    for name, entry in layout.items():
        if "alias" in entry:
            state[name] = state[entry["alias"]]
    return state


class Q5Linear(nn.Module):
    """Couche linéaire dont les poids RESTENT en APEX-Q5 en mémoire ; reconstruits à chaque appel."""

    q5: torch.Tensor
    scale: torch.Tensor
    minimum: torch.Tensor

    def __init__(self, parts: dict[str, torch.Tensor], shape: tuple[int, int]) -> None:
        super().__init__()
        self.register_buffer("q5", parts["q5"])
        self.register_buffer("scale", parts["scale"])
        self.register_buffer("minimum", parts["min"])
        self.shape = shape

    def weight_now(self, dtype: torch.dtype) -> torch.Tensor:
        return dequantize_q5(
            {"q5": self.q5, "scale": self.scale, "min": self.minimum}, self.shape, dtype=dtype
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return F.linear(x, self.weight_now(x.dtype))


def make_q5_resident(model: nn.Module, path: Path) -> int:
    """Remplace les couches linéaires d'un modèle par des `Q5Linear` lues dans `path` (poids compressés en
    mémoire). Les embeddings restent en pleine précision (lecture par index, pas un produit matriciel).
    Rend le nombre de couches remplacées."""
    from safetensors.torch import load_file

    layout = read_layout(path)["layout"]
    raw = load_file(str(path))
    replaced = 0
    for module_name, module in list(model.named_modules()):
        for child_name, child in list(module.named_children()):
            full = f"{module_name}.{child_name}" if module_name else child_name
            entry = layout.get(f"{full}.weight")
            if not isinstance(child, nn.Linear) or entry is None or entry.get("format") != "q5":
                continue
            if child.weight is getattr(getattr(model, "embed", None), "weight", None):
                continue  # tête liée aux embeddings : partagée, laissée en pleine précision
            parts = {suffix: raw[f"{full}.weight.{suffix}"] for suffix in ("q5", "scale", "min")}
            setattr(module, child_name, Q5Linear(parts, tuple(entry["shape"])).to(child.weight.device))
            replaced += 1
    return replaced
