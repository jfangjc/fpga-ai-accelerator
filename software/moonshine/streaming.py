"""Incremental causal frontend and bounded per-layer encoder context.

Decoder caches are reused within an update and rebuilt when encoder memory grows.
Partial transcripts are revisable. Call finish() at each utterance boundary.
"""
import math
import time
import numpy as np
import torch
import torch.nn.functional as F
from .decoding import DecodeJob
from .transcript import PreviewText


class Frontend:
    def __init__(self, embedder, runtime=None):
        self.e = embedder
        self.runtime = runtime
        self.frame = embedder.frame_len
        self.samples = torch.empty((1, 0))
        self.history = [None, None]
        self.counts = [0, 0]

    def convolution(self, x, index, conv):
        count = self.counts[index]
        history = self.history[index]
        if history is None:
            history = torch.zeros((1, x.shape[1], conv.left_pad))
        joined = torch.cat((history, x), dim=-1)
        offset = (-count) % conv.stride[0]
        # Bypass the subclass's automatic left padding; the history supplies it.
        if offset < x.shape[-1]:
            if self.runtime is not None:
                out = self.runtime.convolution(index, joined[..., offset:])
            elif hasattr(conv, 'project_valid'):
                out = conv.project_valid(joined[..., offset:])
            else:
                out = F.conv1d(joined[..., offset:], conv.weight, conv.bias,
                               stride=conv.stride, dilation=conv.dilation, groups=conv.groups)
        else:
            out = x.new_empty((1, conv.out_channels, 0))
        self.counts[index] += x.shape[-1]
        self.history[index] = joined[..., -conv.left_pad:].clone()
        return out

    def push(self, audio, final=False):
        self.samples = torch.cat((self.samples, torch.as_tensor(audio, dtype=torch.float32).reshape(1, -1)), dim=-1)
        if final and self.samples.shape[-1] % self.frame:
            self.samples = F.pad(self.samples, (0, self.frame - self.samples.shape[-1] % self.frame))
        n = self.samples.shape[-1] // self.frame * self.frame
        if n == 0:
            return torch.empty((1, 0, self.e.conv2.out_channels))
        x = self.samples[:, :n].reshape(1, -1, self.frame)
        self.samples = self.samples[:, n:].clone()
        x = F.silu(self.e.linear(self.e.comp(self.e.cmvn(x)))).transpose(1, 2)
        x = F.silu(self.convolution(x, 0, self.e.conv1))
        if x.shape[-1] == 0:
            return x.new_empty((1, 0, self.e.conv2.out_channels))
        return self.convolution(x, 1, self.e.conv2).transpose(1, 2)


class Encoder:
    def __init__(self, encoder, runtime=None):
        self.encoder = encoder
        self.runtime = runtime
        self.buffers = [torch.empty((1, 0, encoder.config.hidden_size)) for _ in encoder.layers]
        self.committed = [0] * len(encoder.layers)

    def push(self, features, final=False):
        x = features
        for i, layer in enumerate(self.encoder.layers):
            left, right = self.encoder.config.sliding_windows[i]
            # Match HF's strict inequalities: 16 includes self, 4 includes 3 future frames.
            lookahead = max(0, right - 1)
            b = torch.cat((self.buffers[i], x), dim=1)
            start = self.committed[i]
            stop = b.shape[1] if final else max(start, b.shape[1] - lookahead)
            if stop > start:
                q = torch.arange(start, stop)[:, None]
                k = torch.arange(b.shape[1])[None, :]
                distance = q - k
                allowed = ((distance >= 0) & (distance < left)) | ((distance < 0) & (-distance < right))
                # Only compute queries/FFNs for newly committed frames. Historical
                # layer inputs remain available as keys/values for left context.
                if self.runtime is not None:
                    x, mlp_input = self.runtime.attention(i, b, start, stop, allowed[None, None])
                else:
                    attention = layer.self_attn
                    normed = layer.input_layernorm(b)
                    def heads(t):
                        return t.reshape(1, t.shape[1], -1, attention.head_dim).transpose(1, 2)
                    queries = heads(attention.q_proj(normed[:, start:stop]))
                    keys = heads(attention.k_proj(normed))
                    values = heads(attention.v_proj(normed))
                    groups = attention.num_key_value_groups
                    if groups != 1:
                        keys = keys.repeat_interleave(groups, dim=1)
                        values = values.repeat_interleave(groups, dim=1)
                    result = F.scaled_dot_product_attention(queries, keys, values,
                        attn_mask=allowed[None, None], dropout_p=0, scale=attention.scaling)
                    result = result.transpose(1, 2).contiguous().reshape(1, stop-start, -1)
                    x = b[:, start:stop] + attention.o_proj(result)
                    mlp_input = layer.post_attention_layernorm(x)
                x = x + layer.mlp(mlp_input)
            else:
                x = b[:, :0]
            discard = max(0, stop - max(0, left - 1))
            self.buffers[i] = b[:, discard:].clone()
            self.committed[i] = stop - discard
        return self.encoder.final_norm(x) if x.shape[1] else x


class DecoderUpdate:
    """Pinned decoder dataflow with one adapter projection per memory update."""
    def __init__(self, decoder, memory):
        self.decoder = decoder
        positions = decoder.pos_emb(torch.arange(memory.shape[1]))
        self.memory = decoder.proj(memory + positions)

    def __call__(self, input_ids, cache=None):
        from transformers.cache_utils import DynamicCache, EncoderDecoderCache
        from transformers.masking_utils import create_causal_mask
        decoder = self.decoder
        hidden = decoder.embed_tokens(input_ids)
        if cache is None:
            cache = EncoderDecoderCache(DynamicCache(config=decoder.config), DynamicCache(config=decoder.config))
        positions = (torch.arange(hidden.shape[1]) + cache.get_seq_length())[None]
        mask = create_causal_mask(config=decoder.config, inputs_embeds=hidden,
            attention_mask=None, past_key_values=cache, position_ids=positions)
        rotary = decoder.rotary_emb(hidden, position_ids=positions)
        for layer in decoder.layers:
            hidden = layer(hidden, mask, self.memory, encoder_attention_mask=None,
                position_ids=positions, past_key_values=cache, use_cache=True, position_embeddings=rotary)
        return decoder.norm(hidden), cache


class Stream:
    def __init__(self, model, processor, max_seconds=30, max_tokens=256, reuse_draft=True, decoder_runtime=None, encoder_runtime=None,
                 preview_seconds=None, preview_overlap_seconds=1, defer_final=False):
        if max_seconds <= 0 or max_tokens < 1:
            raise ValueError('Positive utterance and token limits required')
        self.model, self.processor = model, processor
        self.frontend = Frontend(model.model.encoder.embedder, encoder_runtime)
        self.encoder = Encoder(model.model.encoder, encoder_runtime)
        # Strict right windows include self. Each downstream layer adds its
        # future context at the encoder hop, after both causal convolutions.
        hop = self.frontend.frame * self.frontend.e.conv1.stride[0] * self.frontend.e.conv2.stride[0]
        self.lookahead_samples = hop * sum(max(0, right-1)
            for _, right in model.model.encoder.config.sliding_windows)
        if preview_seconds is not None and not 0 <= preview_overlap_seconds < preview_seconds <= max_seconds:
            raise ValueError('Preview overlap must be smaller than the preview context and fit the phrase')
        if defer_final and preview_seconds is None:
            raise ValueError('Deferred refinement requires preview windows')
        self.preview_frames = max(1, int(preview_seconds*16000/hop)) if preview_seconds else None
        self.preview_step = max(1, int((preview_seconds-preview_overlap_seconds)*16000/hop)) if preview_seconds else None
        self.preview_start = 0
        self.preview_text = PreviewText(max(2, math.ceil(preview_overlap_seconds*6)))
        self.defer_final, self.final_job = defer_final, None
        self.last_preview = {'text': '', 'tokens': []}
        self.memory = []
        self.samples = 0
        self.max_samples = int(max_seconds * 16000)
        self.max_tokens = max_tokens
        self.closed = False
        self.profile = {'frontend_seconds': 0., 'encoder_seconds': 0., 'decoder_seconds': 0.}
        self.reuse_draft = reuse_draft
        self.decoder_runtime = decoder_runtime
        self.draft = []
        self.decode_stats = {'prefill_tokens': 0, 'accepted_draft_tokens': 0, 'autoregressive_tokens': 0}

    @torch.inference_mode()
    def push(self, audio, final=False, decode=True):
        audio = np.asarray(audio, dtype=np.float32)
        if self.closed:
            raise RuntimeError('Utterance already finished; create a new Stream')
        if audio.ndim != 1 or not np.isfinite(audio).all():
            raise ValueError('Expected finite mono 16 kHz samples')
        if self.samples + len(audio) > self.max_samples:
            raise ValueError('Utterance limit exceeded; finish and start a new Stream')
        self.samples += len(audio)
        start_time = time.perf_counter()
        features = self.frontend.push(audio, final)
        frontend_end = time.perf_counter()
        encoded = self.encoder.push(features, final)
        encoder_end = time.perf_counter()
        self.profile['frontend_seconds'] += frontend_end - start_time
        self.profile['encoder_seconds'] += encoder_end - frontend_end
        self.closed = final
        if encoded.shape[1]:
            self.memory.append(encoded)
        if not self.memory or not decode:
            return {'text': '', 'tokens': [], 'final': final, 'frames': sum(x.shape[1] for x in self.memory)}
        memory = torch.cat(self.memory, dim=1)
        if memory.shape[1] > self.model.config.max_position_embeddings:
            raise ValueError('Encoder position limit exceeded')
        frames = memory.shape[1]
        if final and self.defer_final:
            self.final_job = self.decode_job(memory, self.max_tokens, ())
            return {**self.last_preview, 'final': False, 'frames': frames, 'refinement_pending': True}
        if self.preview_frames and not final:
            while frames-self.preview_start > self.preview_frames:
                self.preview_start += self.preview_step
            memory = memory[:, self.preview_start:]
        # Partial token limits follow the selected window's duration, not the
        # entire phrase. Full-context final decoding retains the full budget.
        limit = self.max_tokens if final else min(self.max_tokens, max(1, math.ceil(
            (memory.shape[1]*self.frontend.frame*self.frontend.e.conv1.stride[0]*self.frontend.e.conv2.stride[0]
             if self.preview_frames else self.samples)/16000*6.5)))
        draft = self.draft if self.reuse_draft and not (final and self.preview_start) else ()
        job = self.decode_job(memory, limit, draft)
        event = job.run()
        self.profile['decoder_seconds'] += job.processing_seconds
        for key, value in job.stats.items():
            self.decode_stats[key] += value
        self.draft = event['tokens']
        if self.preview_frames and not final:
            raw = event['text']
            event['text'], uncertain = self.preview_text.update(self.preview_start, raw)
            event.update(window_text=raw, window_start_frame=self.preview_start,
                         window_frames=memory.shape[1], overlap_uncertain=uncertain)
        self.last_preview = dict(event)
        return {**event, 'final': final, 'frames': frames}

    def decode_job(self, memory, limit, draft=()):
        def prepare():
            with torch.inference_mode():
                return (self.decoder_runtime.update(memory) if self.decoder_runtime is not None
                        else DecoderUpdate(self.model.model.decoder, memory))
        def predict(decoder, ids, cache):
            ids = torch.tensor(ids)
            if self.decoder_runtime is not None:
                return decoder.predict(ids, cache)
            with torch.inference_mode():
                hidden, cache = decoder(ids, cache)
                return self.model.proj_out(hidden).argmax(dim=-1)[0].tolist(), cache
        return DecodeJob(prepare, predict, lambda tokens: self.processor.decode(tokens, skip_special_tokens=True),
                         self.model.config.decoder_start_token_id, self.model.config.eos_token_id, limit, draft)

    def finish(self):
        return self.push(np.empty(0, dtype=np.float32), final=True)
