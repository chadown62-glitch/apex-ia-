"""Tokenizer et réseau d'APEX : calcul réel, vérifié numériquement."""

from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

try:
    import torch
except ImportError:  # l'extra « neural » n'est pas installé
    torch = None

SKIP = unittest.skipIf(torch is None, "PyTorch absent (extra « neural » non installé)")

CORPUS = [
    "Le transformeur calcule des logits pour chaque position.",
    "La rétropropagation calcule les gradients de chaque paramètre.",
    "def add(a, b):\n    return a + b\n",
    "Les gradients mettent à jour les paramètres du modèle.",
] * 5


@SKIP
class TestTokenizer(unittest.TestCase):
    def setUp(self) -> None:
        from apex.neural.tokenizer import ByteBPETokenizer

        self.cls = ByteBPETokenizer
        self.tok = ByteBPETokenizer.train(CORPUS, vocab_size=400)

    def test_every_text_round_trips(self) -> None:
        for text in ("", "a", "héllo wörld — 数学 🚀", "  \n\tx = 1_000 ;", CORPUS[0], "zzzz qqqq"):
            with self.subTest(text=text):
                self.assertEqual(self.tok.decode(self.tok.encode(text)), text)

    def test_learned_merges_compress_the_training_text(self) -> None:
        untrained = self.cls()
        text = CORPUS[1]
        self.assertLess(len(self.tok.encode(text)), len(untrained.encode(text)))
        self.assertEqual(len(untrained.encode(text)), len(text.encode("utf-8")))

    def test_training_is_deterministic_and_bounded(self) -> None:
        again = self.cls.train(CORPUS, vocab_size=400)
        self.assertEqual(again.merges, self.tok.merges)
        self.assertLessEqual(self.tok.vocab_size, 400)
        self.assertTrue(all(0 <= i < self.tok.vocab_size for i in self.tok.encode(" ".join(CORPUS))))

    def test_save_and_load_give_the_same_tokenizer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tok.json"
            self.tok.save(path)
            loaded = self.cls.load(path)
        self.assertEqual(loaded.fingerprint(), self.tok.fingerprint())
        self.assertEqual(loaded.encode(CORPUS[2]), self.tok.encode(CORPUS[2]))

    def test_special_tokens_are_distinct_and_not_decoded(self) -> None:
        ids = self.tok.encode("abc", add_bos=True, add_eos=True)
        self.assertEqual((ids[0], ids[-1]), (self.tok.bos_id, self.tok.eos_id))
        self.assertEqual(self.tok.decode(ids), "abc")
        self.assertEqual(len({self.tok.bos_id, self.tok.eos_id, self.tok.pad_id}), 3)


@SKIP
class TestModel(unittest.TestCase):
    def config(self, **overrides):
        from apex.neural.config import ModelConfig

        values = {
            "vocab_size": 64,
            "hidden_size": 32,
            "num_layers": 2,
            "num_heads": 4,
            "intermediate_size": 64,
            "context_length": 16,
        }
        values.update(overrides)
        return ModelConfig(**values)

    def model(self, seed: int = 0, **overrides):
        from apex.neural.model import ApexLM

        torch.manual_seed(seed)
        return ApexLM(self.config(**overrides))

    def tokens(self, batch: int = 2, length: int = 12, seed: int = 1):
        generator = torch.Generator().manual_seed(seed)
        return torch.randint(0, 64, (batch, length), generator=generator)

    def test_logits_have_the_expected_shape_and_are_finite(self) -> None:
        logits, loss = self.model()(self.tokens())
        self.assertEqual(tuple(logits.shape), (2, 12, 64))
        self.assertIsNone(loss)
        self.assertTrue(torch.isfinite(logits).all())

    def test_loss_equals_a_manual_cross_entropy(self) -> None:
        from apex.neural.model import shift_for_language_modeling

        model = self.model()
        inputs, targets = shift_for_language_modeling(self.tokens())
        logits, loss = model(inputs, targets)
        log_probs = torch.log_softmax(logits.float(), dim=-1)
        manual = -log_probs.gather(-1, targets.unsqueeze(-1)).mean()
        self.assertAlmostEqual(loss.item(), manual.item(), places=5)

    def test_initial_loss_is_close_to_uniform(self) -> None:
        from apex.neural.model import shift_for_language_modeling

        inputs, targets = shift_for_language_modeling(self.tokens(batch=8))
        _, loss = self.model()(inputs, targets)
        self.assertLess(abs(loss.item() - math.log(64)), 0.3)

    def test_padding_positions_do_not_change_the_loss(self) -> None:
        from apex.neural.model import IGNORE_INDEX

        model = self.model()
        inputs = self.tokens(batch=1, length=6)
        targets = self.tokens(batch=1, length=6, seed=2)
        _, loss = model(inputs, targets)
        padded_inputs = torch.cat([inputs, torch.zeros(1, 4, dtype=torch.long)], dim=1)
        padded_targets = torch.cat([targets, torch.full((1, 4), IGNORE_INDEX)], dim=1)
        _, padded_loss = model(padded_inputs, padded_targets)
        self.assertAlmostEqual(loss.item(), padded_loss.item(), places=5)

    def test_a_loss_without_any_target_is_refused(self) -> None:
        from apex.neural.model import IGNORE_INDEX

        with self.assertRaises(ValueError):
            self.model()(self.tokens(), torch.full((2, 12), IGNORE_INDEX))

    def test_attention_is_causal_and_earlier_tokens_propagate(self) -> None:
        model = self.model().eval()
        tokens = self.tokens(batch=1)
        future = tokens.clone()
        future[0, 8] = (future[0, 8] + 1) % 64
        past = tokens.clone()
        past[0, 2] = (past[0, 2] + 1) % 64
        base, _ = model(tokens)
        changed_future, _ = model(future)
        changed_past, _ = model(past)
        self.assertTrue(torch.allclose(base[0, :8], changed_future[0, :8], atol=1e-6))
        self.assertFalse(torch.allclose(base[0, 8:], changed_future[0, 8:]))
        self.assertFalse(torch.allclose(base[0, 3:], changed_past[0, 3:]))

    def test_same_seed_same_network_same_logits(self) -> None:
        a, _ = self.model(seed=3)(self.tokens())
        b, _ = self.model(seed=3)(self.tokens())
        c, _ = self.model(seed=4)(self.tokens())
        self.assertTrue(torch.equal(a, b))
        self.assertFalse(torch.equal(a, c))

    def test_every_parameter_receives_a_finite_non_zero_gradient(self) -> None:
        from apex.neural.model import shift_for_language_modeling

        model = self.model()
        _, loss = model(*shift_for_language_modeling(self.tokens()))
        loss.backward()
        for name, param in model.named_parameters():
            with self.subTest(parameter=name):
                self.assertIsNotNone(param.grad)
                self.assertTrue(torch.isfinite(param.grad).all())
                self.assertGreater(param.grad.abs().sum().item(), 0.0)

    def test_embedding_rows_of_absent_tokens_get_no_gradient(self) -> None:
        model = self.model(tie_embeddings=False)
        inputs = torch.tensor([[1, 2, 3, 4]])
        _, loss = model(inputs, torch.tensor([[2, 3, 4, 5]]))
        loss.backward()
        grad = model.embed.weight.grad
        self.assertGreater(grad[1:5].abs().sum().item(), 0.0)
        self.assertEqual(grad[6:].abs().sum().item(), 0.0)

    def test_short_and_full_context_sequences_work_and_longer_is_refused(self) -> None:
        model = self.model()
        for length in (1, 16):
            logits, _ = model(self.tokens(length=length))
            self.assertTrue(torch.isfinite(logits).all())
        with self.assertRaises(ValueError):
            model(self.tokens(length=17))

    def test_invalid_configurations_are_refused(self) -> None:
        for overrides in ({"hidden_size": 30}, {"num_heads": 0}, {"dtype": "int8"}, {"dropout": 1.0}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.config(**overrides)

    def test_the_size_comes_only_from_the_configuration(self) -> None:
        small = self.model()
        larger = self.model(hidden_size=64, num_layers=4, intermediate_size=128)
        self.assertGreater(larger.num_parameters(), 3 * small.num_parameters())


if __name__ == "__main__":
    unittest.main()


@SKIP
class TestBatchedGeneration(unittest.TestCase):
    """`generate_batch` : mêmes sorties que le décodage une invite à la fois, en moins de passes."""

    def setUp(self) -> None:
        from apex.neural.config import ModelConfig
        from apex.neural.model import ApexLM

        torch.manual_seed(0)
        self.model = ApexLM(
            ModelConfig(
                vocab_size=64,
                hidden_size=32,
                num_layers=2,
                num_heads=4,
                intermediate_size=64,
                context_length=48,
            )
        )
        gen = torch.Generator().manual_seed(1)
        self.prompts = [
            torch.randint(3, 64, (int(n),), generator=gen).tolist()
            for n in torch.randint(1, 12, (40,), generator=gen)
        ]

    def sequential(self, prompt: list[int], max_new: int, eos_id: int) -> list[int]:
        out = self.model.generate(torch.tensor([prompt]), max_new_tokens=max_new)[0, len(prompt) :].tolist()
        return out[: out.index(eos_id)] if eos_id in out else out

    def test_outputs_equal_one_prompt_at_a_time_greedy_decoding(self) -> None:
        from apex.neural.model import generate_batch

        for eos_id in (2, 17):  # 17 : jeton fréquent, pour exercer la troncature et l'arrêt anticipé
            with self.subTest(eos_id=eos_id):
                batched = generate_batch(self.model, self.prompts, 20, eos_id=eos_id, batch_size=7)
                self.assertEqual(batched, [self.sequential(p, 20, eos_id) for p in self.prompts])

    def test_generation_past_the_context_window_matches_too(self) -> None:
        from apex.neural.model import generate_batch

        long = [self.prompts[0] * 6][0][:40]
        self.assertEqual(
            generate_batch(self.model, [long], 20, eos_id=2), [self.sequential(long, 20, eos_id=2)]
        )

    def test_empty_prompts_are_refused(self) -> None:
        from apex.neural.model import generate_batch

        with self.assertRaises(ValueError):
            generate_batch(self.model, [[5], []], 4, eos_id=2)
