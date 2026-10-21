"""Conservative reconciliation of overlapping, revisable text windows."""
import re
import unicodedata


def words(text):
    return [(unicodedata.normalize('NFKC', m.group()).casefold(), m.start(), m.end())
            for m in re.finditer(r"\w+(?:['’]\w+)*", text)]


def trim_overlap(previous, current, max_words=16):
    """Require two matching words; uncertain matches preserve the source text.

    No word timestamps are available. Repeated phrases remain intrinsically
    ambiguous, so return the raw text and alignment metadata to consumers too.
    """
    left, right = words(previous), words(current)
    for count in range(min(len(left), len(right), max_words), 1, -1):
        if [w[0] for w in left[-count:]] == [w[0] for w in right[:count]]:
            end = right[count-1][2]
            return current[end:].lstrip(' ,.;:!?—-\n\t'), count, False
    return current, 0, bool(left and right)


class PreviewText:
    def __init__(self, max_overlap_words=16):
        self.max_overlap_words = max_overlap_words
        self.start = None
        self.prefix = ''
        self.previous = ''

    def update(self, start, text):
        if self.start is not None and start != self.start:
            self.prefix = self.previous
        tail, _, uncertain = trim_overlap(self.prefix, text, self.max_overlap_words) if self.prefix else (text, 0, False)
        self.previous = ' '.join(part for part in (self.prefix, tail) if part)
        self.start = start
        return self.previous, uncertain
