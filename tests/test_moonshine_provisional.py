"""Validate frame accounting and equivalence with a float encoder.

Dynamic INT8 scales can depend on the query batch; live transcript comparisons
and the physical integer oracle cover the deployed quantized path separately.
"""
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'software'))
from moonshine.streaming import Encoder


class ProvisionalTests(unittest.TestCase):
    def encoder(self):
        torch.manual_seed(23)
        layers = []
        for _ in range(3):
            attention = SimpleNamespace(head_dim=4, num_key_value_groups=1, scaling=.5,
                **{name: torch.nn.Linear(8, 8) for name in ('q_proj', 'k_proj', 'v_proj', 'o_proj')})
            layers.append(SimpleNamespace(self_attn=attention,
                input_layernorm=torch.nn.LayerNorm(8), post_attention_layernorm=torch.nn.LayerNorm(8),
                mlp=torch.nn.Sequential(torch.nn.Linear(8, 16), torch.nn.SiLU(), torch.nn.Linear(16, 8))))
        return SimpleNamespace(config=SimpleNamespace(hidden_size=8, sliding_windows=[(5, 3)]*3),
                               layers=layers, final_norm=torch.nn.LayerNorm(8))

    @torch.inference_mode()
    def test_tail_recomputation_preserves_commits_and_full_context_final(self):
        model = self.encoder()
        features = torch.randn(1, 43, 8)
        reference, preview = Encoder(model), Encoder(model)
        stable, provisional = [], []
        position = 0
        for count in (1, 2, 0, 7, 1, 11, 3, 18):
            block = features[:, position:position+count]
            position += count
            a = reference.push(block)
            b = preview.push(block, provisional=True)
            torch.testing.assert_close(a, b)
            self.assertEqual(reference.committed, preview.committed)
            for old, new in zip(reference.buffers, preview.buffers):
                torch.testing.assert_close(old, new)
            stable.append(a)
            provisional.append(b)
            self.assertEqual(sum(x.shape[1] for x in provisional)+preview.preview.shape[1], position)
            # A flush of the current prefix independently defines the unfinished tail.
            full = Encoder(model).push(features[:, :position], final=True)
            torch.testing.assert_close(torch.cat(provisional+[preview.preview], dim=1), full)
        stable.append(reference.push(features[:, :0], final=True))
        provisional.append(preview.push(features[:, :0], final=True))
        self.assertEqual(preview.preview.shape[1], 0)
        torch.testing.assert_close(torch.cat(stable, dim=1), torch.cat(provisional, dim=1))
        torch.testing.assert_close(torch.cat(provisional, dim=1), Encoder(model).push(features, final=True))


if __name__ == '__main__':
    unittest.main()
