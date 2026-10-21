"""Cooperative greedy decoding. Each advance performs at most one model call."""
import time


class DecodeJob:
    def __init__(self, prepare, predict, decode, bos, eos, limit, draft=()):
        self.prepare, self.predict, self.decode = prepare, predict, decode
        self.bos, self.eos, self.limit, self.draft = bos, eos, limit, list(draft)
        self.stats = {'prefill_tokens': 0, 'accepted_draft_tokens': 0, 'autoregressive_tokens': 0}
        self.processing_seconds = 0.
        self.result = None
        self.iterator = self._run()

    def _run(self):
        decoder = self.prepare()
        yield
        tokens, cache, ended = [self.bos], None, False
        if len(self.draft) > 1:
            candidate = self.draft[:self.limit+1]
            predictions, cache = self.predict(decoder, [candidate[:-1]], None)
            self.stats['prefill_tokens'] += len(predictions)
            for i, token in enumerate(predictions):
                tokens.append(token)
                if token == self.eos:
                    ended = True
                    break
                if token != candidate[i+1]:
                    break
                self.stats['accepted_draft_tokens'] += 1
            remove = cache.get_seq_length()-(len(tokens)-1)
            if remove > 0:
                cache.crop(-remove)
            yield
        for _ in range(0 if ended else self.limit-(len(tokens)-1)):
            predictions, cache = self.predict(decoder, [[tokens[-1]]], cache)
            token = predictions[-1]
            tokens.append(token)
            self.stats['autoregressive_tokens'] += 1
            if token == self.eos:
                ended = True
                break
            yield
        self.result = {'text': self.decode(tokens), 'tokens': tokens, 'token_limit_reached': not ended}

    def advance(self):
        if self.result is None:
            start = time.perf_counter()
            try:
                next(self.iterator)
            except StopIteration:
                pass
            finally:
                self.processing_seconds += time.perf_counter()-start
        return self.result

    def run(self):
        while self.advance() is None:
            pass
        return self.result
