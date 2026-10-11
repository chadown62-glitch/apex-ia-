"""ApexLM — le réseau de neurones d'APEX : transformeur décodeur (pré-norm).

    tokens -> embeddings -> N x [RMSNorm -> attention causale (RoPE) -> +résidu
                                 RMSNorm -> SwiGLU -> +résidu]
           -> RMSNorm -> tête de sortie -> logits

La perte de modélisation du langage est l'entropie croisée entre les logits
à la position t et le token t+1 ; les positions de remplissage sont ignorées.
Toutes les dimensions viennent de `ModelConfig` : le même code sert un modèle
de quelques milliers de paramètres ou de plusieurs milliards."""

from __future__ import annotations

import ctypes
import hashlib
import math

import torch
import torch.nn.functional as F
from torch import nn

from apex.brain.config import ModelConfig

IGNORE_INDEX = -100
_TORCH_DTYPES = {"float32": torch.float32, "bfloat16": torch.bfloat16, "float16": torch.float16}


def torch_dtype(name: str) -> torch.dtype:
    return _TORCH_DTYPES[name]


class RMSNorm(nn.Module):
    def __init__(self, dim: int, eps: float) -> None:
        super().__init__()
        self.eps = eps
        self.weight = nn.Parameter(torch.ones(dim))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        variance = x.float().pow(2).mean(-1, keepdim=True)
        normed: torch.Tensor = (x.float() * torch.rsqrt(variance + self.eps)).type_as(x) * self.weight
        return normed


class RotaryEmbedding(nn.Module):
    """Encodage de position rotatif : aucune table de positions apprise, la
    longueur de contexte n'est qu'une borne de calcul."""

    cos: torch.Tensor
    sin: torch.Tensor

    def __init__(self, head_dim: int, context_length: int, theta: float) -> None:
        super().__init__()
        inv_freq = 1.0 / (theta ** (torch.arange(0, head_dim, 2, dtype=torch.float32) / head_dim))
        positions = torch.arange(context_length, dtype=torch.float32)
        angles = torch.outer(positions, inv_freq)
        self.register_buffer("cos", angles.cos(), persistent=False)
        self.register_buffer("sin", angles.sin(), persistent=False)

    def forward(self, x: torch.Tensor, offset: int = 0) -> torch.Tensor:
        """`offset` : position du premier élément (décodage incrémental : les jetons déjà en cache)."""
        seq_len = x.shape[-2]
        if offset + seq_len > self.cos.shape[0]:
            raise ValueError(f"position {offset + seq_len} au-delà du contexte {self.cos.shape[0]}")
        cos = self.cos[offset : offset + seq_len].to(x.dtype)
        sin = self.sin[offset : offset + seq_len].to(x.dtype)
        x1, x2 = x[..., 0::2], x[..., 1::2]
        rotated = torch.stack((x1 * cos - x2 * sin, x1 * sin + x2 * cos), dim=-1)
        return rotated.flatten(-2)


class KVCache:
    """Cache clé/valeur du décodage incrémental : chaque jeton n'est calculé qu'UNE fois (le décodage naïf
    recalcule toute la fenêtre à chaque jeton). Préalloué pour `max_length` positions ; `length` = positions
    déjà calculées. `trim(n)` revient en arrière (réutilisation d'un préfixe commun entre deux tours)."""

    def __init__(self, config: ModelConfig, batch: int, max_length: int, *,
                 device: torch.device | str = "cpu",
                 dtype: torch.dtype = torch.float32) -> None:
        if not 0 < max_length <= config.context_length:
            raise ValueError(f"cache de {max_length} positions : entre 1 et le contexte "
                             f"{config.context_length}")
        shape = (batch, config.kv_heads, max_length, config.head_dim)
        self.keys = [torch.zeros(shape, device=device, dtype=dtype) for _ in range(config.num_layers)]
        self.values = [torch.zeros(shape, device=device, dtype=dtype) for _ in range(config.num_layers)]
        self.max_length = max_length
        self.length = 0

    def nbytes(self) -> int:
        return sum(t.numel() * t.element_size() for t in (*self.keys, *self.values))

    def trim(self, length: int) -> None:
        if not 0 <= length <= self.length:
            raise ValueError(f"longueur {length} hors de [0, {self.length}]")
        self.length = length


class CausalSelfAttention(nn.Module):
    def __init__(self, config: ModelConfig, rope: RotaryEmbedding) -> None:
        super().__init__()
        self.num_heads = config.num_heads
        self.kv_heads = config.kv_heads
        self.head_dim = config.head_dim
        self.kv_dim = self.kv_heads * self.head_dim
        # Une seule projection fusionnée [requêtes | clés | valeurs] : en attention multi-têtes (v1), même
        # forme (3 x hidden) et même nom de paramètre — les poids v1 se chargent tels quels.
        self.qkv = nn.Linear(config.hidden_size, config.hidden_size + 2 * self.kv_dim, bias=False)
        self.out = nn.Linear(config.hidden_size, config.hidden_size, bias=False)
        self.dropout = config.dropout
        self.rope = rope
        if config.qk_norm:
            self.q_norm: RMSNorm | None = RMSNorm(self.head_dim, config.norm_eps)
            self.k_norm: RMSNorm | None = RMSNorm(self.head_dim, config.norm_eps)
        else:
            self.q_norm = self.k_norm = None

    def forward(self, x: torch.Tensor, cache: KVCache | None = None, layer: int = 0) -> torch.Tensor:
        batch, seq_len, hidden = x.shape
        q, k, v = self.qkv(x).split((hidden, self.kv_dim, self.kv_dim), dim=-1)
        q = q.view(batch, seq_len, self.num_heads, self.head_dim).transpose(1, 2)
        k = k.view(batch, seq_len, self.kv_heads, self.head_dim).transpose(1, 2)
        v = v.view(batch, seq_len, self.kv_heads, self.head_dim).transpose(1, 2)
        if self.q_norm is not None and self.k_norm is not None:
            q, k = self.q_norm(q), self.k_norm(k)
        offset = cache.length if cache is not None else 0
        q, k = self.rope(q, offset), self.rope(k, offset)
        mask: torch.Tensor | None = None
        causal = True
        if cache is not None:
            end = offset + seq_len
            if end > cache.max_length:
                raise ValueError(f"cache plein : {end} positions > {cache.max_length}")
            cache.keys[layer][:batch, :, offset:end] = k.to(cache.keys[layer].dtype)
            cache.values[layer][:batch, :, offset:end] = v.to(cache.values[layer].dtype)
            k = cache.keys[layer][:batch, :, :end].to(q.dtype)
            v = cache.values[layer][:batch, :, :end].to(q.dtype)
            if offset:
                causal = False
                if seq_len > 1:  # le jeton i voit le cache et les nouveaux jetons jusqu'à lui-même
                    rows = torch.arange(seq_len, device=x.device)[:, None] + offset
                    mask = torch.arange(end, device=x.device)[None, :] <= rows
        attended = F.scaled_dot_product_attention(
            q, k, v, attn_mask=mask, is_causal=causal and mask is None,
            dropout_p=self.dropout if self.training else 0.0,
            enable_gqa=self.kv_heads != self.num_heads,
        )
        projected: torch.Tensor = self.out(attended.transpose(1, 2).reshape(batch, seq_len, hidden))
        return projected


class SwiGLU(nn.Module):
    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.gate = nn.Linear(config.hidden_size, config.intermediate_size, bias=False)
        self.up = nn.Linear(config.hidden_size, config.intermediate_size, bias=False)
        self.down = nn.Linear(config.intermediate_size, config.hidden_size, bias=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        down: torch.Tensor = self.down(F.silu(self.gate(x)) * self.up(x))
        return down


class TransformerBlock(nn.Module):
    def __init__(self, config: ModelConfig, rope: RotaryEmbedding) -> None:
        super().__init__()
        self.attn_norm = RMSNorm(config.hidden_size, config.norm_eps)
        self.attn = CausalSelfAttention(config, rope)
        self.mlp_norm = RMSNorm(config.hidden_size, config.norm_eps)
        self.mlp = SwiGLU(config)
        self.drop = nn.Dropout(config.dropout)

    def forward(self, x: torch.Tensor, cache: KVCache | None = None, layer: int = 0) -> torch.Tensor:
        x = x + self.drop(self.attn(self.attn_norm(x), cache, layer))
        out: torch.Tensor = x + self.drop(self.mlp(self.mlp_norm(x)))
        return out


class ApexLM(nn.Module):
    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.config = config
        #: Entraînement : recalculer les activations de chaque bloc pendant la rétropropagation au lieu de
        #: les garder (beaucoup moins de mémoire GPU, environ un tiers de calcul en plus). Sans effet en
        #: inférence.
        self.activation_checkpointing = False
        self.embed = nn.Embedding(config.vocab_size, config.hidden_size)
        rope = RotaryEmbedding(config.head_dim, config.context_length, config.rope_theta)
        self.layers = [TransformerBlock(config, rope) for _ in range(config.num_layers)]
        self.blocks = nn.ModuleList(self.layers)
        self.norm = RMSNorm(config.hidden_size, config.norm_eps)
        self.head = nn.Linear(config.hidden_size, config.vocab_size, bias=False)
        if config.tie_embeddings:
            self.head.weight = self.embed.weight
        self.apply(self._init_weights)
        residual_std = 0.02 / math.sqrt(2 * config.num_layers)
        for block in self.layers:
            nn.init.normal_(block.attn.out.weight, std=residual_std)
            nn.init.normal_(block.mlp.down.weight, std=residual_std)

    @staticmethod
    def _init_weights(module: nn.Module) -> None:
        if isinstance(module, nn.Linear | nn.Embedding):
            nn.init.normal_(module.weight, std=0.02)

    def num_parameters(self) -> int:
        return sum(p.numel() for p in {id(p): p for p in self.parameters()}.values())

    def forward(
        self, input_ids: torch.Tensor, targets: torch.Tensor | None = None, *, cache: KVCache | None = None,
        last_only: bool = False,
    ) -> tuple[torch.Tensor, torch.Tensor | None]:
        """`cache` : décodage incrémental (les positions continuent celles du cache, qui est complété).
        `last_only` : logits de la dernière position seulement (génération : évite la tête de sortie, la
        plus grosse matrice d'un petit modèle, sur toute l'invite)."""
        if input_ids.dim() != 2:
            raise ValueError("input_ids doit être de forme (batch, séquence)")
        start = cache.length if cache is not None else 0
        if start + input_ids.shape[1] > self.config.context_length:
            raise ValueError(
                f"séquence de {start + input_ids.shape[1]} tokens > contexte {self.config.context_length}"
            )
        x = self.embed(input_ids)
        recompute = (self.activation_checkpointing and self.training and cache is None
                     and torch.is_grad_enabled())
        for index, block in enumerate(self.layers):
            if recompute:
                from torch.utils.checkpoint import checkpoint

                x = checkpoint(block, x, use_reentrant=False)
            else:
                x = block(x, cache, index)
        if cache is not None:
            cache.length = start + input_ids.shape[1]
        if last_only:
            x = x[:, -1:]
        logits = self.head(self.norm(x))
        loss = None if targets is None else language_modeling_loss(logits, targets)
        return logits, loss

    @torch.no_grad()
    def generate(
        self, input_ids: torch.Tensor, max_new_tokens: int, *, temperature: float = 0.0
    ) -> torch.Tensor:
        """Décodage glouton (temperature=0) ou échantillonné."""
        was_training = self.training
        self.eval()
        out = input_ids
        for _ in range(max_new_tokens):
            window = out[:, -self.config.context_length :]
            logits, _ = self(window)
            next_logits = logits[:, -1, :].float()
            if temperature <= 0:
                next_id = next_logits.argmax(dim=-1, keepdim=True)
            else:
                next_id = torch.multinomial(F.softmax(next_logits / temperature, dim=-1), 1)
            out = torch.cat([out, next_id], dim=1)
        self.train(was_training)
        return out


@torch.no_grad()
def generate_batch(
    model: ApexLM, prompts: list[list[int]], max_new_tokens: int, *, eos_id: int, batch_size: int = 256
) -> list[list[int]]:
    """Décodage GLOUTON de nombreuses invites : regroupées par longueur (aucun remplissage, donc
    exactement les mêmes sorties que `ApexLM.generate` une à une), arrêt dès que toutes les
    séquences d'un lot ont émis `eos_id`. Chaque sortie est tronquée à son premier `eos_id`
    (exclu). Coût : un passage avant par jeton et par lot, au lieu d'un par invite."""
    was_training = model.training
    model.eval()
    by_length: dict[int, list[int]] = {}
    for index, prompt in enumerate(prompts):
        if not prompt:
            raise ValueError("invite vide : le décodage a besoin d'au moins un jeton")
        by_length.setdefault(len(prompt), []).append(index)
    outputs: list[list[int]] = [[] for _ in prompts]
    context = model.config.context_length
    for length, indices in by_length.items():
        for start in range(0, len(indices), batch_size):
            chunk = indices[start : start + batch_size]
            x = torch.tensor([prompts[i] for i in chunk], dtype=torch.long)
            done = torch.zeros(len(chunk), dtype=torch.bool)
            for _ in range(max_new_tokens):
                logits, _ = model(x[:, -context:])
                nxt = logits[:, -1, :].float().argmax(dim=-1)
                x = torch.cat([x, nxt[:, None]], dim=1)
                done |= nxt == eos_id
                if bool(done.all()):
                    break
            for row, i in enumerate(chunk):
                generated = x[row, length:].tolist()
                outputs[i] = generated[: generated.index(eos_id)] if eos_id in generated else generated
    model.train(was_training)
    return outputs


def language_modeling_loss(logits: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
    """Entropie croisée moyenne sur les positions cibles non ignorées."""
    if not bool((targets != IGNORE_INDEX).any()):
        raise ValueError("aucune position cible : la perte n'est pas définie")
    return F.cross_entropy(
        logits.reshape(-1, logits.shape[-1]).float(), targets.reshape(-1), ignore_index=IGNORE_INDEX
    )


def shift_for_language_modeling(
    tokens: torch.Tensor, pad_id: int | None = None
) -> tuple[torch.Tensor, torch.Tensor]:
    """(entrées, cibles) : la cible de la position t est le token t+1."""
    inputs = tokens[:, :-1]
    targets = tokens[:, 1:].clone()
    if pad_id is not None:
        targets[targets == pad_id] = IGNORE_INDEX
    return inputs, targets


def model_fingerprint(model: ApexLM) -> str:
    """SHA-256 des poids réels (octets des tenseurs, dans l'ordre du state_dict) — identité d'une version du
    modèle, indépendante du format de fichier (checkpoint d'entraînement ou poids servis)."""
    digest = hashlib.sha256()
    for name, tensor in model.state_dict().items():
        data = tensor.detach().to("cpu").contiguous()
        digest.update(name.encode())
        digest.update(ctypes.string_at(data.data_ptr(), data.numel() * data.element_size()))
    return "sha256:" + digest.hexdigest()
