"""Live caption view for demos: finished phrases scroll, the live phrase redraws in place.

    python3 tools/caption_live.py BOARD [--port 8765] [--output transcript.txt]
    python3 tools/caption_live.py --replay session.jsonl[.gz] [--speed 4]

Each final phrase is printed once, on its own line, in the order spoken. The
phrases still in progress sit below them and are rewritten after every event:
the stable words in white, the still-changing tail in amber, and a phrase
being finalised in grey until its final text replaces it. When stdout is not
a terminal, only the final phrases are printed.

The client reconnects when the board restarts (for example after the button
starts a new session) and marks the new session. Standard library only.
"""
import argparse
import gzip
import json
import os
import shutil
import socket
import sys
import time
from datetime import datetime

RESET, DIM, AMBER, GREY, CLEAR = '\x1b[0m', '\x1b[2m', '\x1b[38;5;214m', '\x1b[38;5;245m', '\x1b[J'


def words(s):
    return s.split()


class View:
    def __init__(self, out, color, live, times, transcript):
        self.out, self.color, self.live, self.times = out, color, live, times
        self.transcript = transcript
        self.rows = 0          # terminal rows currently used by the live area
        self.printed = 0       # phrases printed permanently in this session
        self.history = []      # their final texts, to recognise a reconnect
        self.finals = {}       # phrase -> final text, as soon as it is known
        self.status = ''

    def paint(self, text, style):
        return f'{style}{text}{RESET}' if self.color and style and text else text

    def render(self, line):
        """One terminal line from (word, style) pairs; one colour code per run."""
        out, run, style = [], [], None
        for w, s in line + [('', 'end')]:
            if s != style and run:
                out.append(self.paint(' '.join(run), style)); run = []
            style = s; run.append(w)
        return ' '.join(out)

    def wrap(self, styled_words, width):
        """Greedy word wrap of (word, style) pairs into rendered terminal lines."""
        lines, line, n = [], [], 0
        for w, style in styled_words:
            while len(w) > width:  # an unbroken run longer than the screen
                if line:
                    lines.append(self.render(line)); line, n = [], 0
                lines.append(self.paint(w[:width], style)); w = w[width:]
            if line and n+1+len(w) > width:
                lines.append(self.render(line)); line, n = [], 0
            line.append((w, style)); n += len(w)+(n > 0)
        if line:
            lines.append(self.render(line))
        return lines or ['']

    def erase(self):
        if self.live and self.rows:
            self.out.write('\r' + (f'\x1b[{self.rows-1}A' if self.rows > 1 else '') + CLEAR)
            self.rows = 0

    def final(self, text):
        text = ' '.join(words(text))
        self.history.append(text)
        if not text:
            return  # an onset that held no speech
        stamp = datetime.now().strftime('%H:%M:%S ') if self.times else ''
        width = shutil.get_terminal_size().columns-1 if self.live else 10**9
        indent = len(stamp)
        lines = self.wrap([(w, '') for w in words(text)], max(20, width-indent))
        self.out.write(self.paint(stamp, DIM) + ('\n'+' '*indent).join(lines) + '\n')
        if self.transcript:
            self.transcript.write(stamp+text+'\n'); self.transcript.flush()

    def note(self, text):
        self.erase()
        self.out.write(self.paint(text, DIM)+'\n')

    def draw(self, segments):
        if not self.live:
            return
        width = shutil.get_terminal_size().columns-1
        height = shutil.get_terminal_size().lines
        lines = []
        for s in segments:
            if s['phrase'] < self.printed:
                continue
            if s['phrase'] in self.finals:  # final, waiting for an earlier phrase
                styled = [(w, '') for w in words(self.finals[s['phrase']])]
            elif s['state'] == 'Finalising':
                styled = [(w, GREY) for w in words(s['text'])]
            else:  # Speaking: stable words, then the tentative tail
                stable, full = words(s['text']), words(s.get('tentative') or s['text'])
                k = len(stable) if full[:len(stable)] == stable else 0
                styled = [(w, '') for w in full[:k]] + [(w, AMBER) for w in full[k:]]
            if styled:
                lines += self.wrap(styled, width)
        if not lines and self.status:
            lines = [self.paint(self.status, DIM)]
        lines = lines[-max(1, height-2):]  # never taller than the screen
        self.erase()
        if lines:
            self.out.write('\n'.join(lines))
        self.rows = len(lines)

    def event(self, e):
        seg = e.get('segments', [])
        done = e.get('prefix_before_phrase', 0)
        prefix = words(e.get('committed_prefix', ''))
        mine = words(' '.join(self.history))
        if done < self.printed or prefix[:len(mine)] != mine:
            # The board restarted: phrase numbers and the caption begin again.
            self.note(f'--- new session {datetime.now():%H:%M:%S} ---')
            self.printed, self.history, self.finals = 0, [], {}
            mine = []
        if e.get('state') == 'Committed' and not e.get('event_only'):
            self.finals[e['phrase']] = e.get('final', '')
        for s in seg:
            if s['state'] == 'Committed':
                self.finals[s['phrase']] = s['text']
        state = e.get('state')
        self.status = {'Listening': 'listening…', 'Speaking': 'hearing speech…',
                       'Committed': 'listening…'}.get(state, self.status)
        if done > self.printed:
            self.erase()
            if not self.printed and e.get('snapshot') and len(prefix) > len(mine):
                # Joined mid-session: show the earlier caption as one block.
                self.note('(earlier in this session)')
                self.final(' '.join(prefix[len(mine):]))
                self.history = [' '.join(prefix)]
                self.printed = done
            while self.printed < done:
                p = self.printed
                text = self.finals.pop(p, None)
                if text is None:  # not seen: take it from the committed prefix
                    mine = words(' '.join(self.history))
                    text = ' '.join(prefix[len(mine):]) if p == done-1 else ''
                self.final(text)
                self.printed += 1
        self.draw(seg)
        self.out.flush()


def events_from_socket(host, port, view, wait):
    first = True
    while True:
        try:
            with socket.create_connection((host, port), timeout=5) as c:
                c.settimeout(None)
                if not first:
                    view.note('(reconnected)')
                first = False
                for line in c.makefile('r', encoding='utf-8'):
                    yield json.loads(line)
            view.note('(connection closed; waiting for the board)')
        except OSError as err:
            if first and not wait:
                raise SystemExit(f'cannot connect to {host}:{port}: {err}')
            view.status = f'waiting for {host}:{port}…'
            view.draw([])
            view.out.flush()
        time.sleep(1)


def events_from_file(path, speed):
    opener = gzip.open if path.endswith('.gz') else open
    start = None
    with opener(path, 'rt', encoding='utf-8') as f:
        for line in f:
            e = json.loads(line)
            if speed and 'time' in e:
                start = start if start is not None else time.monotonic()-e['time']/speed
                delay = start+e['time']/speed-time.monotonic()
                if delay > 0:
                    time.sleep(delay)
            yield e


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('host', nargs='?', help='board address, e.g. 192.168.1.111')
    p.add_argument('--port', type=int, default=8765)
    p.add_argument('--replay', metavar='JSONL', help='play a saved session.jsonl(.gz) instead of connecting')
    p.add_argument('--speed', type=float, default=1, help='replay speed; 0 = as fast as possible')
    p.add_argument('--output', help='also append the final phrases to this file')
    p.add_argument('--times', action='store_true', help='prefix each final phrase with the PC time')
    p.add_argument('--no-color', action='store_true')
    p.add_argument('--no-wait', action='store_true', help='exit if the board is not reachable at start')
    args = p.parse_args()
    if not args.host and not args.replay:
        p.error('give the board address or --replay FILE')
    tty = sys.stdout.isatty()
    if os.name == 'nt':
        os.system('')  # enable ANSI escape handling in the Windows console
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    transcript = open(args.output, 'a', encoding='utf-8') if args.output else None
    view = View(sys.stdout, tty and not args.no_color and 'NO_COLOR' not in os.environ, tty, args.times, transcript)
    source = (events_from_file(args.replay, args.speed) if args.replay
              else events_from_socket(args.host, args.port, view, not args.no_wait))
    try:
        for e in source:
            view.event(e)
        view.erase()
        sys.stdout.flush()
    except KeyboardInterrupt:
        view.erase()
        sys.stdout.flush()
    except BrokenPipeError:  # e.g. piped into head
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    finally:
        if transcript:
            transcript.close()


if __name__ == '__main__':
    main()
