"""Architecture du modèle APEX (`ApexLM`) — définie dans `apex.brain.model` : le Cerveau APEX est servi par
l'application (paquet Runtime `apex.brain`), entraîné ici (recherche). Une seule définition, réexportée."""

from apex.brain.model import (
    IGNORE_INDEX,
    ApexLM,
    CausalSelfAttention,
    RMSNorm,
    RotaryEmbedding,
    SwiGLU,
    TransformerBlock,
    generate_batch,
    language_modeling_loss,
    model_fingerprint,
    shift_for_language_modeling,
    torch_dtype,
)

__all__ = [
    "IGNORE_INDEX", "ApexLM", "CausalSelfAttention", "RMSNorm", "RotaryEmbedding", "SwiGLU",
    "TransformerBlock", "generate_batch", "language_modeling_loss", "model_fingerprint",
    "shift_for_language_modeling", "torch_dtype",
]
