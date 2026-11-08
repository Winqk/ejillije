#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сборка банков сэмплов для «Воздушных барабанов» из открытых SFZ-библиотек.

Инструмент для разработчика (в игру не входит). Он:
  1. частично клонирует нужные репозитории github.com/sfzinstruments (только метаданные),
  2. разбирает их SFZ-программы (#include, #define, <curve>, наследование заголовков),
  3. для каждой ноты, градации силы удара и варианта (round robin) смешивает все микрофоны
     так, как это делает сэмплер с настройками авторов (amplitude/pan по CC с кривыми, volume,
     фильтры), докачивая только нужные файлы,
  4. обрезает тишину, выравнивает громкость инструмента (сохраняя динамику между градациями)
     и сохраняет OGG Vorbis 44,1 кГц в desktop/samples/<банк>/<инструмент>/v<градация>_r<вариант>.ogg.

Запуск:  python tools/build_samples.py [--only rusty,virtuosity] [--work /tmp/kits]
Нужны:   numpy, soundfile, soxr, git.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys

import numpy as np
import soundfile as sf
import soxr

SR = 44100
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), 'samples')
NOTE = {'c': 0, 'd': 2, 'e': 4, 'f': 5, 'g': 7, 'a': 9, 'b': 11}


def note_num(v):
    v = v.strip().lower()
    if re.fullmatch(r'-?\d+', v):
        return int(v)
    m = re.fullmatch(r'([a-g])(#|b)?(-?\d+)', v)
    if not m:
        raise ValueError(v)
    n = NOTE[m.group(1)] + (1 if m.group(2) == '#' else -1 if m.group(2) == 'b' else 0)
    return n + (int(m.group(3)) + 1) * 12


# ============================================================ SFZ

class Sfz:
    BUILTIN = {0: np.linspace(0, 1, 128), 1: np.linspace(-1, 1, 128), 2: np.linspace(1, 0, 128),
               3: np.linspace(1, -1, 128), 4: np.linspace(0, 1, 128) ** 2, 5: np.sqrt(np.linspace(0, 1, 128)),
               6: np.sqrt(np.linspace(1, 0, 128))}

    def __init__(self, repo, path, cc=None):
        self.repo, self.path = repo, path
        self.root = os.path.dirname(path)
        self.cc = {}
        self.curves = dict(self.BUILTIN)
        self.regions = []
        self.sw_default = None
        text = self._inline(path, 0)
        text = self._defines(text)
        self._parse(text)
        if cc:
            self.cc.update(cc)

    def _read(self, rel):
        rel = os.path.normpath(rel).replace(os.sep, '/')
        r = subprocess.run(['git', '-C', self.repo, 'show', f'HEAD:{rel}'], capture_output=True)
        if r.returncode:
            raise FileNotFoundError(rel)
        return r.stdout.decode('utf-8', 'replace')

    def _inline(self, rel, depth):
        text = self._read(rel)
        text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.S)
        out = []
        for line in text.splitlines():
            line = line.split('//')[0]
            m = re.match(r'\s*#include\s+"([^"]+)"', line)
            if m and depth < 20:
                inc = os.path.join(self.root, m.group(1))  # пути — от корневого .sfz
                out.append(self._inline(inc, depth + 1))
            else:
                out.append(line)
        return '\n'.join(out) + '\n'

    def _defines(self, text):
        defs = {}
        out = []
        sub = lambda t: re.sub(r'\$(\w+)', lambda mm: defs.get(mm.group(1), mm.group(0)), t)  # noqa: E731
        for line in text.splitlines():
            found = list(re.finditer(r'#define\s+\$(\w+)\s+(\S+)', line))
            if found:  # на одной строке может быть несколько #define
                for m in found:
                    defs[m.group(1)] = sub(m.group(2))
                rest = re.sub(r'#define\s+\$\w+\s+\S+', ' ', line)
                if rest.strip():
                    out.append(sub(rest))
                continue
            out.append(sub(line))
        self.defines = defs
        return '\n'.join(out)

    def _parse(self, text):
        toks = list(re.finditer(r'<(\w+)>|([A-Za-z_][\w]*)=', text))
        levels = {'global': {}, 'master': {}, 'group': {}}
        cur = None
        header = None
        default_path = ''
        curve = None

        def close():
            nonlocal curve
            if header == 'region' and cur is not None:
                r = {}
                for lv in ('global', 'master', 'group'):
                    r.update(levels[lv])
                r.update(cur)
                r['_default_path'] = default_path
                self.regions.append(r)
            if header == 'curve' and curve is not None:
                idx = int(curve.get('curve_index', -1))
                pts = sorted((int(k[1:]), float(v)) for k, v in curve.items() if re.fullmatch(r'v\d+', k))
                if idx >= 0 and pts:
                    xs, ys = zip(*pts)
                    self.curves[idx] = np.interp(np.arange(128), xs, ys)
                curve = None

        for i, t in enumerate(toks):
            if t.group(1):
                close()
                header = t.group(1)
                if header == 'global':
                    levels = {'global': {}, 'master': {}, 'group': {}}
                    cur = levels['global']
                elif header == 'master':
                    levels['master'], levels['group'] = {}, {}
                    cur = levels['master']
                elif header == 'group':
                    levels['group'] = {}
                    cur = levels['group']
                elif header == 'region':
                    cur = {}
                elif header == 'curve':
                    curve = {}
                    cur = curve
                else:
                    cur = {}
                continue
            name = t.group(2)
            end = toks[i + 1].start() if i + 1 < len(toks) else len(text)
            val = text[t.end():end].strip()
            if name != 'sample':
                val = val.split()[0] if val.split() else ''
            if header == 'control':
                if name == 'default_path':
                    default_path = val
                elif name.startswith('set_cc') or name.startswith('set_hdcc'):
                    n = int(re.sub(r'\D', '', name))
                    v = float(val)
                    self.cc[n] = v * 127 if name.startswith('set_hdcc') else v
                continue
            if name == 'sw_default':
                self.sw_default = note_num(val)
            if cur is not None:
                cur[name] = val
        close()

    # ---------- выбор регионов
    def _ccval(self, n):
        return self.cc.get(n, 0.0)

    def _curve(self, idx, cc):
        c = self.curves.get(idx, self.BUILTIN[0])
        return float(np.interp(cc, np.arange(128), c))

    def match(self, key, vel, seq, rand=0.5):
        out = []
        for r in self.regions:
            if r.get('trigger', 'attack') not in ('attack', 'first'):
                continue
            if 'key' in r:
                lo = hi = note_num(r['key'])
            else:
                lo, hi = note_num(r.get('lokey', '0')), note_num(r.get('hikey', '127'))
            if not lo <= key <= hi:
                continue
            if not int(r.get('lovel', 1)) <= vel <= int(r.get('hivel', 127)):
                continue
            if int(r.get('seq_position', 1)) != seq:
                continue
            ok = True
            for k, v in r.items():
                m = re.fullmatch(r'(lo|hi)cc(\d+)', k)
                if m:
                    c = self._ccval(int(m.group(2)))
                    if (m.group(1) == 'lo' and c < float(v)) or (m.group(1) == 'hi' and c > float(v)):
                        ok = False
                        break
            if not ok:
                continue
            if 'lorand' in r or 'hirand' in r:
                if not float(r.get('lorand', 0)) <= rand < float(r.get('hirand', 1)) + 1e-9:
                    continue
            if 'sw_last' in r and self.sw_default is not None and note_num(r['sw_last']) != self.sw_default:
                continue
            out.append(r)
        return out

    def seq_len(self, key, vel):
        n = 1
        while self.match(key, vel, n + 1):
            n += 1
        return n

    def layers(self, key):
        """Верхние границы градаций силы удара для ноты (по hivel регионов)."""
        his = set()
        for r in self.regions:
            if 'key' in r:
                lo = hi = note_num(r['key'])
            else:
                lo, hi = note_num(r.get('lokey', '0')), note_num(r.get('hikey', '127'))
            if lo <= key <= hi and r.get('trigger', 'attack') in ('attack', 'first'):
                his.add(int(r.get('hivel', 127)))
        return sorted(his)

    def gain_pan(self, r):
        amp = float(r.get('amplitude', 100)) / 100
        vol = float(r.get('volume', 0))
        pan = float(r.get('pan', 0))
        for k, v in r.items():
            m = re.fullmatch(r'(amplitude|pan|volume|gain)_(?:on)?cc(\d+)', k)
            if not m:
                continue
            kind, n = m.group(1), int(m.group(2))
            cidx = int(r.get(f'{kind}_curvecc{n}', 0))
            c = self._curve(cidx, self._ccval(n))
            if kind == 'amplitude':
                amp *= float(v) / 100 * c
            elif kind == 'pan':
                pan += float(v) * c
            else:
                vol += float(v) * c
        return amp * 10 ** (vol / 20), max(-100.0, min(100.0, pan))

    def sample_path(self, r):
        return os.path.normpath(os.path.join(self.root, r.get('_default_path', ''), r['sample'].replace('\\', '/'))).replace(os.sep, '/')


# ============================================================ звук

_cache = {}


def load(repo, rel):
    key = (repo, rel)
    if key not in _cache:
        x, sr = sf.read(os.path.join(repo, rel), dtype='float32', always_2d=True)
        if sr != SR:
            x = soxr.resample(x, sr, SR)
        _cache[key] = x
    return _cache[key]


def fft_filter(x, kind, fc, poles=2):
    n = len(x)
    X = np.fft.rfft(x, axis=0)
    f = np.maximum(np.fft.rfftfreq(n, 1 / SR), 1.0)[:, None]
    order = 1 if poles == 1 else 2
    H = 1 / np.sqrt(1 + (fc / f) ** (2 * order)) if kind == 'hp' else 1 / np.sqrt(1 + (f / fc) ** (2 * order))
    return np.fft.irfft(X * H, n, axis=0)


def render(repo, pairs):
    parts = []
    for sfz, r in pairs:
        x = load(repo, sfz.sample_path(r)).copy()
        off = int(float(r.get('offset', 0)) * SR / 48000) if 'offset' in r else 0
        if off:
            x = x[off:]
        if 'end' in r and float(r['end']) > 0:
            x = x[:int(float(r['end']) * SR / 48000)]
        for fk, ck in (('fil_type', 'cutoff'), ('fil1_type', 'cutoff1'), ('fil2_type', 'cutoff2')):
            t = r.get(fk, '')
            if ck in r and (t.startswith('hpf') or t.startswith('lpf')):
                x = fft_filter(x, t[:2], float(r[ck]), 1 if '1p' in t else 2)
        g, pan = sfz.gain_pan(r)
        a = (pan / 100 + 1) * math.pi / 4
        if x.shape[1] == 1:
            st = np.concatenate([x * math.cos(a), x * math.sin(a)], axis=1) * math.sqrt(2)
        else:
            st = x[:, :2].copy()
            if pan < 0:
                st[:, 1] *= 1 + pan / 100
            elif pan > 0:
                st[:, 0] *= 1 - pan / 100
        parts.append(st * g)
    if not parts:
        return None
    n = max(len(p) for p in parts)
    out = np.zeros((n, 2), np.float32)
    for p in parts:
        out[:len(p)] += p
    return out


def finish(x, max_sec, floor_db=-62):
    peak = np.abs(x).max()
    if peak <= 0:
        return x
    loud = np.nonzero(np.abs(x).max(axis=1) > peak * 10 ** (floor_db / 20))[0]
    x = x[:loud[-1] + 1]
    if len(x) > max_sec * SR:
        x = x[:int(max_sec * SR)]
    fade = min(len(x), int(0.25 * SR) if len(x) > SR else int(0.02 * SR))
    x[-fade:] *= np.linspace(1, 0, fade)[:, None] ** 2
    return x


# ============================================================ банки

# инструмент: (sfz-программа или None — основная, нота, переопределения CC, макс. длина, целевой пик)
BANKS = {
    'rusty': dict(
        name='Big Rusty Drums', author='Karoryfer Samples', license='CC0-1.0',
        url='https://github.com/sfzinstruments/karoryfer.big-rusty-drums',
        repo='karoryfer.big-rusty-drums', program='Programs/01-full.sfz',
        insts={
            'kick': (None, 36, {}, 2.0, 0.92), 'snare': (None, 38, {}, 2.0, 0.85),
            'rim': (None, 37, {}, 1.0, 0.6), 'tom_hi': (None, 47, {}, 2.5, 0.8),
            'tom_mid': (None, 45, {}, 2.5, 0.8), 'tom_lo': (None, 43, {}, 3.0, 0.82),
            'floor': (None, 41, {}, 3.0, 0.85), 'hihat': (None, 42, {}, 1.2, 0.55),
            'hihat_open': (None, 46, {4: 127}, 3.0, 0.55), 'crash': (None, 49, {}, 4.5, 0.62),
            'china': (None, 57, {}, 4.0, 0.62),
            'ride': (None, 51, {}, 4.5, 0.55), 'ride_bell': (None, 53, {}, 4.0, 0.58),
        }),
    'virtuosity': dict(
        name='Virtuosity Drums', author='Versilian Studios, Karoryfer Samples', license='CC0-1.0',
        url='https://github.com/sfzinstruments/virtuosity_drums',
        repo='virtuosity_drums', program='Programs/02-full-kit.sfz',
        insts={
            'kick_jazz': (None, 36, {}, 2.0, 0.88), 'kick': (None, 36, {}, 2.0, 0.88),
            'snare': (None, 38, {}, 2.0, 0.82), 'rim': (None, 88, {}, 1.0, 0.6),
            'tom_hi': (None, 48, {}, 2.5, 0.78, 2.0), 'tom_mid': (None, 48, {}, 2.5, 0.78),
            'floor': (None, 41, {}, 3.0, 0.82), 'tom_lo': (None, 41, {}, 3.0, 0.82, 2.5),
            'hihat': (None, 42, {4: 127}, 1.2, 0.55), 'hihat_open': (None, 42, {4: 0}, 3.0, 0.55),
            'crash': (None, 49, {}, 4.5, 0.6), 'ride': (None, 51, {}, 4.5, 0.55),
            'ride_jazz': (None, 59, {}, 5.0, 0.55), 'ride_bell': (None, 53, {}, 4.0, 0.58),
            'splash': (None, 55, {}, 3.0, 0.58), 'cowbell': (None, 56, {}, 1.5, 0.5),
        }),
    'drs': dict(
        name='DRSKit (DrumGizmo)', author='Lars Muldjord, Bent Bisballe Nyeng; SFZ: kinwie', license='CC-BY-4.0',
        url='https://github.com/sfzinstruments/DrumGizmo.DRSKit',
        repo='DrumGizmo.DRSKit', program='DrumGizmo/DRSKit/Multi-F/*.sfz',
        insts={
            'kick_tight': (None, 36, {}, 1.5, 0.92), 'kick': (None, 36, {}, 1.5, 0.92),
            'snare_tight': (None, 38, {}, 1.6, 0.86), 'snare': (None, 38, {}, 1.6, 0.86),
            'rim': (None, 37, {}, 1.0, 0.6), 'tom_hi': (None, 47, {}, 2.2, 0.8), 'tom_mid': (None, 43, {}, 2.4, 0.8),
            'tom_lo': (None, 43, {}, 2.6, 0.8, -2.0), 'floor': (None, 41, {}, 2.8, 0.84),
            'hihat': (None, 42, {}, 1.0, 0.52), 'hihat_open': (None, 58, {}, 3.0, 0.52),
            'crash': (None, 49, {}, 4.5, 0.6), 'crash_dark': (None, 57, {}, 4.5, 0.6),
            'ride': (None, 51, {}, 4.5, 0.52), 'ride_bell': (None, 53, {}, 4.0, 0.56),
        },
        aliases={'china': 'rusty'}),
    'frankensnare': dict(
        name='Frankensnare', author='Karoryfer Samples', license='CC0-1.0',
        url='https://github.com/sfzinstruments/karoryfer.frankensnare',
        repo='karoryfer.frankensnare', program=None,
        insts={'snare_brush': ('Programs/05-14x5maplebrush.sfz', 38, {}, 2.0, 0.75)}),
}


def build_hits(sfz, key, allh, his, rr):
    """Удары для каждой выбранной градации. Если у библиотеки нет вариантов (round robin) —
    вариантами становятся соседние, чуть более тихие градации: так повторы не звучат одинаково."""
    hits = []
    for li, hv in enumerate(his):
        nseq = min(rr, sfz.seq_len(key, hv))
        if nseq > 1:
            variants = [(hv, s) for s in range(1, nseq + 1)]
        else:
            i = allh.index(hv)
            variants = [(v, 1) for v in allh[max(0, i - rr + 1):i + 1]][::-1]
        for k, (v, s) in enumerate(variants, 1):
            regs = [r for r in sfz.match(key, v, s, rand=(k - 0.5) / len(variants)) if usable(sfz, r)]
            if regs:
                hits.append((li, k, regs))
    return hits


def usable(sfz, r):
    return 'sample' in r and not r['sample'].startswith('*') and sfz.gain_pan(r)[0] > 1e-4


DYN_RANGE = 12.0
LOUD = {'kick': -15.0, 'snare': -17.0, 'rim': -20.0, 'tom': -17.0, 'hat': -23.0, 'cymbal': -22.0, 'ride': -24.0, 'perc': -21.0}


def loud_target(inst):
    for prefix, cat in (('kick', 'kick'), ('snare', 'snare'), ('rim', 'rim'), ('tom', 'tom'), ('floor', 'tom'),
                        ('hihat', 'hat'), ('ride', 'ride'), ('cowbell', 'perc')):
        if inst.startswith(prefix):
            return LOUD[cat]
    return LOUD['cymbal']


def rms_db(x, sec=0.15):
    seg = x[:int(sec * SR)]
    return 20 * math.log10(float(np.sqrt((seg ** 2).mean())) + 1e-9)


def pick_layers(his, n):
    """Равномерно выбрать n градаций по силе удара (всегда с самой громкой)."""
    if len(his) <= n:
        return his
    targets = [127 * (i + 1) / n for i in range(n)]
    out = []
    for t in targets:
        h = min(his, key=lambda v: abs(v - t))
        if h not in out:
            out.append(h)
    return sorted(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', default=os.path.join(os.path.expanduser('~'), '.cache', 'airdrums-kits'))
    ap.add_argument('--only', default='')
    ap.add_argument('--layers', type=int, default=4)
    ap.add_argument('--rr', type=int, default=3)
    ap.add_argument('--quality', type=float, default=0.55, help='качество Vorbis 0..1')
    args = ap.parse_args()
    os.makedirs(args.work, exist_ok=True)
    only = set(filter(None, args.only.split(',')))
    credits = {}
    for bank, spec in BANKS.items():
        if only and bank not in only:
            continue
        repo = os.path.join(args.work, spec['repo'])
        if not os.path.isdir(repo):
            subprocess.run(['git', 'clone', '-q', '--filter=blob:none', '--no-checkout', '--depth', '1',
                            f'https://github.com/sfzinstruments/{spec["repo"]}', repo], check=True)
        progs = {}
        plan = {}
        for inst, (prog, key, cc, max_sec, peak, *semi) in spec['insts'].items():
            prog = prog or spec['program']
            if '*' in prog:  # по программе на каждый микрофон — складываем их регионы
                d, pat = prog.rsplit('/', 1)
                files = [f for f in subprocess.run(['git', '-C', repo, 'ls-tree', '--name-only', 'HEAD', d + '/'],
                                                   capture_output=True, text=True).stdout.splitlines()
                         if re.fullmatch(pat.replace('.', r'\.').replace('*', '.*'), f.rsplit('/', 1)[-1])]
                if prog not in progs:
                    progs[prog] = [Sfz(repo, f) for f in files]
                mics = progs[prog]
                allh = mics[0].layers(key)
                his = pick_layers(allh, args.layers)
                hits = {}
                for m in mics:
                    for li, k, regs in build_hits(m, key, allh, his, args.rr):
                        hits.setdefault((li, k), []).extend((m, r) for r in regs)
                plan[inst] = (None, [(li, k, v) for (li, k), v in sorted(hits.items())], cc, max_sec, peak)
                print(f'{bank}/{inst}: нота {key}, градации {his}, ударов {len(hits)}, микрофонов {len(mics)}', flush=True)
                continue
            if prog not in progs:
                progs[prog] = Sfz(repo, prog)
            sfz = progs[prog]
            if key is None:  # программа на одну ноту — берём ноту из регионов
                keys = sorted({note_num(r.get('key', r.get('lokey', '0'))) for r in sfz.regions if 'sample' in r})
                key = keys[len(keys) // 2] if keys else 38
            if cc:  # переопределения CC (например, положение педали хай-хэта) — только для этого инструмента
                sfz = Sfz(repo, prog, cc)
            allh = sfz.layers(key)
            his = pick_layers(allh, args.layers)
            hits = build_hits(sfz, key, allh, his, args.rr)
            plan[inst] = (sfz, hits, cc, max_sec, peak)
            print(f'{bank}/{inst}: нота {key}, градации {his}, ударов {len(hits)}, '
                  f'микрофонов на удар {len(hits[0][2]) if hits else 0}', flush=True)
        # докачать только нужные файлы
        for inst, (sfz, hits, *rest) in list(plan.items()):
            if sfz is not None:  # единый вид: список пар (программа, регион)
                plan[inst] = (None, [(li, k, [(sfz, r) for r in regs]) for li, k, regs in hits], *rest)
        need = sorted({m.sample_path(r) for _, hits, *_ in plan.values() for _, _, prs in hits for m, r in prs})
        missing = [p for p in need if not os.path.isfile(os.path.join(repo, p))]
        if missing:
            lst = os.path.join(args.work, f'{bank}.paths')
            with open(lst, 'w', encoding='utf-8') as f:
                f.write('\n'.join(missing) + '\n')
            print(f'{bank}: скачиваю {len(missing)} файлов…', flush=True)
            subprocess.run(['git', '-C', repo, 'checkout', 'HEAD', f'--pathspec-from-file={lst}'], check=True)
        for inst, (_, hits, cc, max_sec, peak) in plan.items():
            rendered = [(li, s, render(repo, prs)) for li, s, prs in hits]
            st = spec['insts'][inst][5] if len(spec['insts'][inst]) > 5 else 0
            if st:  # перестройка: дубль тома выше или ниже по высоте
                rendered = [(li, s, soxr.resample(x, SR, SR * 2 ** (-st / 12)).astype(np.float32)) for li, s, x in rendered]
            rendered = [(li, s, finish(x, max_sec)) for li, s, x in rendered if x is not None]
            if not rendered:
                print(f'  ! {bank}/{inst}: нет звука', flush=True)
                continue
            # выравнивание по громкости атаки: у самой сильной градации — целевая громкость группы,
            # каждая следующая вниз тише на равный шаг (всего DYN_RANGE дБ), тембр градаций сохраняется
            nl = max(li for li, _, _ in rendered) + 1
            target = loud_target(inst)
            gains = {}
            for li in range(nl):
                lv = [rms_db(x) for l2, _, x in rendered if l2 == li]
                if lv:
                    want = target - DYN_RANGE * (nl - 1 - li) / max(1, nl - 1)
                    gains[li] = 10 ** ((want - float(np.mean(lv))) / 20)
            top = max(np.abs(x * gains[li]).max() for li, _, x in rendered)
            lim = 0.97 / top if top > 0.97 else 1.0
            d = os.path.join(OUT, bank, inst)
            os.makedirs(d, exist_ok=True)
            for fn in os.listdir(d):
                os.remove(os.path.join(d, fn))
            for li, s, x in rendered:
                y = np.clip(x * gains[li] * lim, -1, 1)
                sf.write(os.path.join(d, f'v{li + 1}_r{s}.ogg'), y, SR, format='OGG', subtype='VORBIS',
                         compression_level=1 - args.quality)
            _cache.clear()
        for inst, src in spec.get('aliases', {}).items():
            sdir, ddir = os.path.join(OUT, src, inst), os.path.join(OUT, bank, inst)
            if os.path.isdir(sdir):
                os.makedirs(ddir, exist_ok=True)
                for fn in os.listdir(sdir):
                    with open(os.path.join(sdir, fn), 'rb') as fi, open(os.path.join(ddir, fn), 'wb') as fo:
                        fo.write(fi.read())
        credits[bank] = {k: spec[k] for k in ('name', 'author', 'license', 'url')}
        credits[bank]['instruments'] = sorted(plan)
    path = os.path.join(OUT, 'credits.json')
    old = json.load(open(path, encoding='utf-8')) if os.path.isfile(path) else {}
    old.update(credits)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(old, f, ensure_ascii=False, indent=1)
    print('готово:', OUT)


if __name__ == '__main__':
    sys.exit(main())
