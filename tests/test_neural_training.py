"""Moteur d'entraînement et checkpoints : gradients réels, mises à jour réelles,
reprise exacte."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import torch
except ImportError:
    torch = None

SKIP = unittest.skipIf(torch is None, "PyTorch absent (extra « neural » non installé)")

TEXT = "le chat mange. le chien dort. le chat dort. le chien mange. " * 40


def _setup(steps: int = 60, **training):
    from apex.neural.config import ModelConfig, OptimizerSpec, SchedulerSpec, TrainingConfig
    from apex.neural.model import ApexLM
    from apex.neural.tokenizer import ByteBPETokenizer
    from apex.neural.training import TokenBatcher, TrainingEngine

    tok = ByteBPETokenizer.train([TEXT], vocab_size=300)
    tokens = tok.encode(TEXT)
    torch.manual_seed(0)
    model = ApexLM(
        ModelConfig(
            vocab_size=tok.vocab_size,
            hidden_size=32,
            num_layers=2,
            num_heads=4,
            intermediate_size=64,
            context_length=32,
        )
    )
    options = {
        "max_steps": steps,
        "batch_size": 8,
        "seed": 0,
        "optimizer": OptimizerSpec(lr=3e-3),
        "scheduler": SchedulerSpec(name="constant"),
    }
    options.update(training)
    engine = TrainingEngine(model, TrainingConfig(**options))
    batcher = TokenBatcher(tokens, window=32, batch_size=options["batch_size"], seed=1)
    return engine, batcher, tok, tokens


def _params(model):
    return {k: v.detach().clone() for k, v in model.state_dict().items()}


def _changed(before, model) -> bool:
    return any(not torch.equal(before[k], v) for k, v in model.state_dict().items())


def _train_and_measure(steps: int = 60, **training):
    engine, batcher, _, _ = _setup(steps, **training)
    before = _params(engine.model)
    results = engine.fit(batcher)
    first = sum(r.loss for r in results[:5]) / 5
    last = sum(r.loss for r in results[-5:]) / 5
    return _changed(before, engine.model), first, last


@SKIP
class TestBackpropagation(unittest.TestCase):
    def test_training_updates_parameters_and_lowers_the_loss(self) -> None:
        changed, first, last = _train_and_measure()
        self.assertTrue(changed)
        self.assertLess(last, first * 0.7)

    def test_the_check_detects_training_without_a_real_update(self) -> None:
        with patch.object(torch.optim.AdamW, "step", lambda self, closure=None: None):
            changed, first, last = _train_and_measure(steps=20)
        self.assertFalse(changed)
        self.assertGreater(last, first * 0.9)

    def test_an_optimizer_step_without_backward_is_refused(self) -> None:
        engine, batcher, _, _ = _setup()
        engine.forward(batcher.next_batch())
        with self.assertRaises(RuntimeError):
            engine.optimizer_step()

    def test_sgd_also_trains(self) -> None:
        from apex.neural.config import OptimizerSpec

        changed, first, last = _train_and_measure(
            optimizer=OptimizerSpec(name="sgd", lr=0.3, weight_decay=0.0)
        )
        self.assertTrue(changed)
        self.assertLess(last, first)

    def test_gradient_accumulation_equals_one_large_batch(self) -> None:
        engine, batcher, _, _ = _setup(grad_accumulation_steps=2)
        batch = batcher.next_batch()
        engine.backward(engine.forward(batch[:4]))
        engine.backward(engine.forward(batch[4:]))
        accumulated = {n: p.grad.clone() for n, p in engine.model.named_parameters()}
        engine.zero_grad()
        engine.forward(batch).backward()
        for name, param in engine.model.named_parameters():
            with self.subTest(parameter=name):
                self.assertTrue(torch.allclose(accumulated[name], param.grad, atol=1e-6))

    def test_gradients_are_clipped_to_the_configured_norm(self) -> None:
        engine, batcher, _, _ = _setup(grad_clip_norm=1e-3)
        engine.backward(engine.forward(batcher.next_batch()))
        norm_before = engine.optimizer_step()
        grads = [p.grad for p in engine.model.parameters() if p.grad is not None]
        norm_after = torch.linalg.vector_norm(
            torch.stack([torch.linalg.vector_norm(g) for g in grads])
        ).item()
        self.assertGreater(norm_before, 1e-3)
        self.assertAlmostEqual(norm_after, 1e-3, places=5)

    def test_non_finite_values_stop_training(self) -> None:
        engine, batcher, _, _ = _setup()
        with torch.no_grad():
            next(engine.model.parameters()).fill_(float("nan"))
        with self.assertRaises(FloatingPointError):
            engine.train_step(batcher)

    def test_bfloat16_mixed_precision_trains(self) -> None:
        changed, first, last = _train_and_measure(steps=40, mixed_precision=True)
        self.assertTrue(changed)
        self.assertLess(last, first)

    def test_norms_and_vectors_are_not_weight_decayed(self) -> None:
        engine, _, _, _ = _setup()
        decayed, plain = engine.optimizer.param_groups
        self.assertTrue(all(p.dim() >= 2 for p in decayed["params"]))
        self.assertTrue(all(p.dim() < 2 for p in plain["params"]))
        self.assertEqual(plain["weight_decay"], 0.0)


@SKIP
class TestScheduler(unittest.TestCase):
    def test_warmup_then_cosine_down_to_the_floor(self) -> None:
        from apex.neural.config import SchedulerSpec
        from apex.neural.training import lr_multiplier

        spec = SchedulerSpec(name="cosine", warmup_steps=10, min_lr_ratio=0.1)
        self.assertAlmostEqual(lr_multiplier(0, spec, 110), 0.1)
        self.assertAlmostEqual(lr_multiplier(9, spec, 110), 1.0)
        self.assertAlmostEqual(lr_multiplier(10, spec, 110), 1.0)
        self.assertAlmostEqual(lr_multiplier(60, spec, 110), 0.55)
        self.assertAlmostEqual(lr_multiplier(110, spec, 110), 0.1)


@SKIP
class TestCheckpoints(unittest.TestCase):
    def _save(self, engine, batcher, tok, tokens, path):
        from apex.neural.checkpoint import save_checkpoint

        return save_checkpoint(
            path,
            engine,
            tokenizer=tok,
            batcher=batcher,
            dataset_fingerprint="sha256:dataset-a",
            dataset_provenance={"sources": ["test"]},
            dataset_tokens=len(tokens),
        )

    def test_stop_reload_continue_equals_an_uninterrupted_run(self) -> None:
        from apex.neural.checkpoint import load_checkpoint
        from apex.neural.training import TokenBatcher

        continuous, batcher_a, _, _ = _setup()
        continuous_losses = [r.loss for r in continuous.fit(batcher_a, 20)]

        first, batcher_b, tok, tokens = _setup()
        losses = [r.loss for r in first.fit(batcher_b, 10)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ckpt.pt"
            self._save(first, batcher_b, tok, tokens, path)
            fresh_batcher = TokenBatcher(tokens, window=32, batch_size=8, seed=999)
            resumed, loaded_tok, info = load_checkpoint(path, batcher=fresh_batcher)
        losses += [r.loss for r in resumed.fit(fresh_batcher, 10)]

        self.assertEqual(losses, continuous_losses)
        for name, value in continuous.model.state_dict().items():
            with self.subTest(parameter=name):
                self.assertTrue(torch.equal(value, resumed.model.state_dict()[name]))
        self.assertEqual(resumed.step, 20)
        self.assertEqual(loaded_tok.fingerprint(), tok.fingerprint())
        self.assertEqual(info.step, 10)
        self.assertEqual(info.dataset_fingerprint, "sha256:dataset-a")
        self.assertEqual(info.dataset_provenance, {"sources": ["test"]})
        self.assertGreater(info.epoch, 0)
        self.assertIn("torch", info.software)
        self.assertEqual(info.model_config, first.model.config)

    def test_the_resume_check_detects_a_lost_sampler_state(self) -> None:
        from apex.neural.checkpoint import load_checkpoint
        from apex.neural.training import TokenBatcher

        continuous, batcher_a, _, _ = _setup()
        continuous.fit(batcher_a, 20)
        first, batcher_b, tok, tokens = _setup()
        first.fit(batcher_b, 10)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ckpt.pt"
            self._save(first, batcher_b, tok, tokens, path)
            resumed, _, _ = load_checkpoint(path)
        resumed.fit(TokenBatcher(tokens, window=32, batch_size=8, seed=1), 10)
        same = all(
            torch.equal(v, resumed.model.state_dict()[k]) for k, v in continuous.model.state_dict().items()
        )
        self.assertFalse(same)

    def test_an_unknown_checkpoint_format_is_refused(self) -> None:
        from apex.neural.checkpoint import load_checkpoint

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.pt"
            torch.save({"format_version": 999}, path)
            with self.assertRaises(ValueError):
                load_checkpoint(path)


if __name__ == "__main__":
    unittest.main()
