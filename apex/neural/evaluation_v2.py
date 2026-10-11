"""Mesures du Cerveau APEX v2 sur des données JAMAIS vues à l'entraînement.

- `validation_loss` : perte et bits par octet (BPB, indépendants du tokenizer) par groupe du corpus, sur des
  fenêtres consécutives et déterministes des flux de validation (mêmes fenêtres à chaque mesure : deux
  mesures sont comparables). Pour l'instruction : perte sur les seules réponses de l'assistant du jeu de
  validation.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn.functional as F

from apex.brain.model import IGNORE_INDEX, ApexLM
from apex.brain.tokenizer import ByteBPETokenizer


def token_byte_lengths(tokenizer: ByteBPETokenizer) -> torch.Tensor:
    return torch.tensor(
        [len(tokenizer.token_bytes(i)) for i in range(tokenizer.vocab_size)], dtype=torch.float64
    )


@torch.no_grad()
def _sum_nll(
    model: ApexLM, inputs: torch.Tensor, targets: torch.Tensor, device: torch.device, amp: bool
) -> tuple[torch.Tensor, torch.Tensor]:
    with torch.autocast(device_type=device.type, dtype=torch.bfloat16, enabled=amp):
        logits, _ = model(inputs.to(device))
    flat_targets = targets.to(device).reshape(-1)
    nll = F.cross_entropy(
        logits.reshape(-1, logits.shape[-1]).float(),
        flat_targets,
        ignore_index=IGNORE_INDEX,
        reduction="none",
    )
    return nll.double().cpu(), flat_targets.cpu()


def validation_loss(
    model: ApexLM, config: Any, device: torch.device, *, batch_size: int = 8
) -> dict[str, Any]:
    data_dir = Path(config.data_dir)
    if config.stage == "sft":
        from apex.neural.sft import validation_batches

        total, count = 0.0, 0
        for inputs, targets in validation_batches(data_dir, window=config.window, batch_size=batch_size):
            nll, flat = _sum_nll(model, inputs, targets, device, config.mixed_precision)
            mask = flat != IGNORE_INDEX
            total += float(nll[mask].sum())
            count += int(mask.sum())
        return {"sft_validation_loss": round(total / count, 5) if count else None, "tokens": count}
    from apex.neural.shards import iter_eval_windows, open_streams

    tokenizer = ByteBPETokenizer.load(data_dir / "tokenizer.json")
    lengths = token_byte_lengths(tokenizer)
    result: dict[str, Any] = {}
    all_nll, all_bytes, all_tokens = 0.0, 0.0, 0
    for group, stream in sorted(open_streams(data_dir, "validation").items()):
        windows = list(iter_eval_windows(stream, config.window, config.eval_tokens))
        if not windows:
            continue
        nll_sum, byte_sum, tokens = 0.0, 0.0, 0
        for start in range(0, len(windows), batch_size):
            chunk = torch.from_numpy(np.stack(windows[start : start + batch_size]))
            nll, flat = _sum_nll(model, chunk[:, :-1], chunk[:, 1:], device, config.mixed_precision)
            nll_sum += float(nll.sum())
            byte_sum += float(lengths[flat].sum())
            tokens += int(flat.numel())
        result[group] = {
            "loss": round(nll_sum / tokens, 5),
            "bpb": round(nll_sum / math.log(2) / byte_sum, 5) if byte_sum else None,
            "tokens": tokens,
        }
        all_nll += nll_sum
        all_bytes += byte_sum
        all_tokens += tokens
    if all_tokens:
        result["all"] = {
            "loss": round(all_nll / all_tokens, 5),
            "bpb": round(all_nll / math.log(2) / all_bytes, 5),
            "tokens": all_tokens,
        }
    return result
