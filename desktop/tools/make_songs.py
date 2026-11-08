#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Встроенные песни для ритм-игры: сочиняются и синтезируются целиком этим скриптом.

Для каждой песни создаётся папка desktop/songs/<id>/:
  backing.ogg — музыка без барабанов (бас, гитары/клавиши/пэды),
  drums.ogg   — подсказка: партия барабанов, сыгранная звуками жанра,
  chart.json  — точные ноты для трёх уровней сложности.
Музыка и ноты созданы для игры, сторонних прав на них нет.

Запуск: python tools/make_songs.py   (нужны numpy, soundfile)
"""
from __future__ import annotations

import json
import math
import os
import random
import sys

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')
from sounds import Synth, INSTRUMENTS  # noqa: E402

SR = 44100
OUT = os.path.join(ROOT, 'songs')
LANES = ['kick', 'snare', 'hihat', 'tom1', 'tom2', 'floor', 'crash', 'ride']
NOTE_N = {'C': 0, 'C#': 1, 'Db': 1, 'D': 2, 'D#': 3, 'Eb': 3, 'E': 4, 'F': 5, 'F#': 6, 'Gb': 6, 'G': 7,
          'G#': 8, 'Ab': 8, 'A': 9, 'A#': 10, 'Bb': 10, 'B': 11}


def hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def chord_notes(name, octave=3):
    """'Em', 'C', 'G7', 'Am7', 'E5' → MIDI-ноты аккорда (корень в заданной октаве)."""
    root = name[:2] if len(name) > 1 and name[1] in '#b' else name[:1]
    q = name[len(root):]
    r = 12 * (octave + 1) + NOTE_N[root]
    if q == '5':
        iv = [0, 7, 12]
    elif q.startswith('m7'):
        iv = [0, 3, 7, 10]
    elif q.startswith('maj7'):
        iv = [0, 4, 7, 11]
    elif q.startswith('m'):
        iv = [0, 3, 7]
    elif q == '7':
        iv = [0, 4, 7, 10]
    else:
        iv = [0, 4, 7]
    return [r + i for i in iv]


# ============================================================ синтез инструментов подложки

def adsr(n, a, d, s, r, sr=SR):
    t = np.arange(n) / sr
    env = np.ones(n)
    env = np.where(t < a, t / max(a, 1e-4), env)
    dd = (t - a) / max(d, 1e-4)
    env = np.where((t >= a) & (t < a + d), 1 - (1 - s) * dd, env)
    env = np.where(t >= a + d, s, env)
    rel = int(r * sr)
    if rel and n > rel:
        env[-rel:] *= np.linspace(1, 0, rel)
    return env


def additive(f, n, kind='saw', cutoff=4000.0, detune=0.0, phase=0.0):
    t = np.arange(n) / SR
    out = np.zeros(n)
    k = 1
    while k * f < min(16000, SR / 2 - 500):
        if kind == 'square' and k % 2 == 0:
            k += 1
            continue
        amp = 1 / k
        amp /= math.sqrt(1 + (k * f / cutoff) ** 4)  # мягкий фильтр по гармоникам
        if amp < 1e-3:
            break
        out += amp * np.sin(2 * np.pi * k * f * (1 + detune) * t + phase * k)
        k += 1
    return out


def fft_eq(x, hp=None, lp=None, peak=None):
    n = len(x)
    X = np.fft.rfft(x)
    f = np.maximum(np.fft.rfftfreq(n, 1 / SR), 1.0)
    H = np.ones_like(f)
    if hp:
        H /= np.sqrt(1 + (hp / f) ** 4)
    if lp:
        H /= np.sqrt(1 + (f / lp) ** 4)
    if peak:
        fc, db, w = peak
        H *= 1 + (10 ** (db / 20) - 1) * np.exp(-0.5 * (np.log2(f / fc) / w) ** 2)
    return np.fft.irfft(X * H, n)


class Track:
    def __init__(self, length):
        self.buf = np.zeros(int(length * SR) + SR * 3)

    def add(self, t, x, gain=1.0):
        i = int(t * SR)
        j = min(len(self.buf), i + len(x))
        if j > i:
            self.buf[i:j] += x[:j - i] * gain


def bass_note(f, dur, style):
    n = int((dur + 0.08) * SR)
    if style == 'sub':  # 808-бас: синус с лёгким перегрузом
        t = np.arange(n) / SR
        x = np.sin(2 * np.pi * f * t) + 0.25 * np.sin(4 * np.pi * f * t)
        x = np.tanh(1.5 * x)
        return x * adsr(n, 0.005, 0.2, 0.8, 0.08)
    cut = {'rock': 900, 'funk': 1600, 'electro': 700, 'metal': 1100}.get(style, 1000)
    x = additive(f, n, 'saw', cut) + 0.6 * np.sin(2 * np.pi * f * np.arange(n) / SR)
    if style == 'funk':
        env = adsr(n, 0.003, 0.12, 0.45, 0.05)
    elif style == 'electro':
        env = adsr(n, 0.004, 0.15, 0.6, 0.06)
    else:
        env = adsr(n, 0.004, 0.25, 0.7, 0.06)
    return x * env


def guitar_chord(notes, dur, mute=False):
    n = int((dur + (0.05 if mute else 0.25)) * SR)
    x = sum(additive(hz(m), n, 'saw', 2600, detune=d) for m in notes for d in (-0.002, 0.002))
    x = np.tanh(5.0 * x / max(1, len(notes)))
    env = adsr(n, 0.004, 0.06 if mute else 0.4, 0.15 if mute else 0.75, 0.05 if mute else 0.2)
    return x * env


def epiano(notes, dur):
    n = int((dur + 0.6) * SR)
    t = np.arange(n) / SR
    x = np.zeros(n)
    for m in notes:
        f = hz(m)
        x += (np.sin(2 * np.pi * f * t) + 0.35 * np.sin(4 * np.pi * f * t) * np.exp(-t * 6)
              + 0.12 * np.sin(2 * np.pi * f * 14 * t) * np.exp(-t * 30))
    return x * adsr(n, 0.004, 0.8, 0.35, 0.3) / max(1, len(notes))


def pad_chord(notes, dur):
    n = int((dur + 0.5) * SR)
    x = sum(additive(hz(m), n, 'saw', 1800, detune=d) for m in notes for d in (-0.004, 0.0, 0.004))
    return x * adsr(n, 0.35, 0.4, 0.8, 0.45) / (3 * max(1, len(notes)))


def pluck(f, dur):
    n = int((dur + 0.15) * SR)
    x = additive(f, n, 'square', 3500)
    return x * adsr(n, 0.002, 0.12, 0.25, 0.08)


# ============================================================ барабаны: партии

def pattern(name):
    """16 шагов на такт: {дорожка: [(шаг, сила)]}."""
    a, m = 1.0, 0.7
    if name == 'rock':
        return {'kick': [(0, a), (8, a), (10, m)], 'snare': [(4, a), (12, a)],
                'hihat': [(s, a if s % 4 == 0 else m) for s in range(0, 16, 2)]}
    if name == 'rock_chorus':
        return {'kick': [(0, a), (6, m), (8, a), (10, m)], 'snare': [(4, a), (12, a)],
                'ride': [(s, a if s % 4 == 0 else m) for s in range(0, 16, 2)]}
    if name == 'funk':
        return {'kick': [(0, a), (3, m), (7, m), (10, a)], 'snare': [(4, a), (12, a)],
                'hihat': [(s, a if s % 2 == 0 else 0.45) for s in range(16)]}
    if name == 'hiphop':
        return {'kick': [(0, a), (7, m), (10, a)], 'snare': [(4, a), (12, a)],
                'hihat': [(s, a if s % 4 == 0 else m) for s in range(0, 16, 2)]}
    if name == 'electro':
        return {'kick': [(s, a) for s in (0, 4, 8, 12)], 'snare': [(4, a), (12, a)],
                'hihat': [(s, a if s % 4 == 2 else 0.5) for s in range(16)]}
    if name == 'metal':
        return {'kick': [(s, a if s % 4 == 0 else m) for s in range(16)], 'snare': [(4, a), (12, a)],
                'ride': [(s, a) for s in (0, 4, 8, 12)]}
    if name == 'half':
        return {'kick': [(0, a)], 'snare': [(8, a)], 'hihat': [(s, m) for s in (0, 4, 8, 12)]}
    raise KeyError(name)


def fill(kind):
    """Сбивка на вторую половину такта."""
    if kind == 'toms':
        return {'snare': [(8, 0.8), (9, 0.7)], 'tom1': [(10, 0.9), (11, 0.8)], 'tom2': [(12, 0.9), (13, 0.8)],
                'floor': [(14, 1.0), (15, 0.9)], 'kick': [(0, 1.0)]}
    if kind == 'snare':
        return {'snare': [(s, 0.6 + 0.4 * (s - 8) / 7) for s in range(8, 16)], 'kick': [(0, 1.0)]}
    return {'kick': [(0, 1.0)], 'snare': [(4, 1.0), (12, 1.0)]}


# ============================================================ песни

SONGS = [
    dict(id='first_beat', title='Первый бит', genre='Рок', preset='rock', bpm=104, bass='rock', comp='guitar',
         sections=[  # (тактов, аккорды по тактам, партия, сбивка в конце, крэш в начале)
             (4, ['E5', 'E5', 'C5', 'D5'], 'half', 'snare', True),
             (8, ['E5', 'C5', 'G5', 'D5'], 'rock', 'toms', True),
             (8, ['A5', 'C5', 'G5', 'D5'], 'rock_chorus', 'toms', True),
             (8, ['E5', 'C5', 'G5', 'D5'], 'rock', 'toms', True),
             (8, ['A5', 'C5', 'G5', 'D5'], 'rock_chorus', 'snare', True),
             (2, ['E5', 'E5'], 'half', None, True)]),
    dict(id='funk_motor', title='Фанк-мотор', genre='Джаз-фанк', preset='jazz', bpm=100, bass='funk', comp='epiano',
         sections=[(4, ['Em7', 'Em7', 'Am7', 'Am7'], 'half', 'snare', True),
                   (8, ['Em7', 'Em7', 'Am7', 'B7'], 'funk', 'toms', True),
                   (8, ['Cmaj7', 'Bm7', 'Am7', 'B7'], 'funk', 'snare', True),
                   (8, ['Em7', 'Em7', 'Am7', 'B7'], 'funk', 'toms', True),
                   (2, ['Em7', 'Em7'], 'half', None, True)]),
    dict(id='night_block', title='Ночной квартал', genre='Хип-хоп', preset='hiphop', bpm=88, bass='sub', comp='epiano',
         sections=[(4, ['Am7', 'Am7', 'Dm7', 'Dm7'], 'half', None, True),
                   (8, ['Am7', 'Fmaj7', 'Dm7', 'E7'], 'hiphop', 'snare', True),
                   (8, ['Am7', 'Fmaj7', 'Dm7', 'E7'], 'hiphop', 'snare', True),
                   (8, ['Am7', 'Fmaj7', 'Dm7', 'E7'], 'hiphop', None, True),
                   (2, ['Am7', 'Am7'], 'half', None, True)]),
    dict(id='neon_city', title='Неоновый город', genre='Электроника', preset='electro', bpm=124, bass='electro', comp='pad',
         sections=[(4, ['Am', 'F', 'C', 'G'], 'half', 'snare', True),
                   (8, ['Am', 'F', 'C', 'G'], 'electro', 'snare', True),
                   (8, ['F', 'G', 'Am', 'Am'], 'electro', 'toms', True),
                   (8, ['Am', 'F', 'C', 'G'], 'electro', 'snare', True),
                   (8, ['F', 'G', 'Am', 'Am'], 'electro', 'snare', True),
                   (2, ['Am', 'Am'], 'half', None, True)]),
    dict(id='iron_storm', title='Железный шторм', genre='Метал', preset='metal', bpm=140, bass='metal', comp='chug',
         sections=[(4, ['E5', 'E5', 'F5', 'E5'], 'half', 'toms', True),
                   (8, ['E5', 'E5', 'G5', 'F#5'], 'metal', 'toms', True),
                   (8, ['C5', 'D5', 'E5', 'E5'], 'rock_chorus', 'toms', True),
                   (8, ['E5', 'E5', 'G5', 'F#5'], 'metal', 'toms', True),
                   (8, ['C5', 'D5', 'E5', 'E5'], 'rock_chorus', 'snare', True),
                   (2, ['E5', 'E5'], 'half', None, True)]),
]


def compose_drums(song):
    """Полная партия (тяжёлый уровень): список (время, дорожка, сила)."""
    spb = 60 / song['bpm']
    step = spb / 4
    notes = []
    bar = 0
    for nbars, chords, pat, fl, crash in song['sections']:
        for b in range(nbars):
            last = b == nbars - 1
            p = pattern(pat)
            if last and fl:  # вторая половина последнего такта — сбивка
                f = fill(fl)
                p = {k: [(s, v) for s, v in vs if s < 8] for k, vs in p.items()}
                for k, vs in f.items():
                    p.setdefault(k, [])
                    p[k] += [(s, v) for s, v in vs if s >= 8 or k == 'kick']
            if b == 0 and crash:
                p = {k: [(s, v) for s, v in vs if not (s == 0 and k in ('hihat', 'ride'))] for k, vs in p.items()}
                p.setdefault('crash', []).append((0, 1.0))
                if (0, 1.0) not in p.get('kick', []):
                    p.setdefault('kick', []).append((0, 1.0))
            for lane, vs in p.items():
                for s, v in vs:
                    notes.append((round((bar * 16 + s) * step, 4), lane, v))
            bar += 1
    # завершающий удар
    notes.append((round(bar * 16 * step, 4), 'crash', 1.0))
    notes.append((round(bar * 16 * step, 4), 'kick', 1.0))
    seen = set()
    out = []
    for t, l, v in sorted(notes):
        if (t, l) not in seen:
            seen.add((t, l))
            out.append((t, l, v))
    return out, bar


def simplify(notes, bpm, level):
    """Уровни сложности из полной партии."""
    step = 60 / bpm / 4
    out = []
    for t, lane, v in notes:
        s = int(round(t / step)) % 16
        if level == 'easy':
            if lane not in ('kick', 'snare', 'crash'):
                continue
            if s % 4 != 0:  # только четверти
                continue
            if lane == 'crash' and s != 0:
                continue
        elif level == 'medium':
            if s % 2 == 1:  # без шестнадцатых
                continue
            if lane in ('hihat', 'ride') and s % 4 != 0:  # тарелки — только на четверти
                continue
            if lane in ('tom1', 'tom2') :
                lane = 'tom1' if lane == 'tom1' else 'floor'
        out.append([t, lane])
    # убрать одновременные удары по одной дорожке и слишком частые на лёгком
    res = []
    last = {}
    for t, lane in out:
        if level == 'easy' and t - last.get(lane, -9) < 0.28:
            continue
        last[lane] = t
        res.append([t, lane])
    return res


def render_backing(song, length):
    spb = 60 / song['bpm']
    bar_len = spb * 4
    tr_b, tr_c, tr_l = Track(length), Track(length), Track(length)
    rnd = random.Random(song['id'])
    t0 = 0.0
    for nbars, chords, pat, fl, crash in song['sections']:
        for b in range(nbars):
            ch = chords[b % len(chords)]
            notes = chord_notes(ch, 3)
            root = notes[0] - 12
            t = t0 + b * bar_len
            st = song['bass']
            if pat == 'half':
                tr_b.add(t, bass_note(hz(root), bar_len * 0.95, st), 0.9)
            elif st == 'rock':
                for i in range(8):
                    tr_b.add(t + i * spb / 2, bass_note(hz(root), spb / 2 * 0.9, st), 0.8)
            elif st == 'metal':
                for i in range(16):
                    tr_b.add(t + i * spb / 4, bass_note(hz(root), spb / 4 * 0.85, st), 0.75)
            elif st == 'funk':
                for i, (s, o) in enumerate(((0, 0), (3, 12), (6, 0), (8, 7), (10, 12), (11, 10), (14, 0))):
                    tr_b.add(t + s * spb / 4, bass_note(hz(root + o), spb / 4 * 1.6, st), 0.85)
            elif st == 'electro':
                for i in range(8):
                    tr_b.add(t + (i + 0.5) * spb / 2, bass_note(hz(root), spb / 2 * 0.7, st), 0.85)
            else:  # sub
                for s in (0, 10):
                    tr_b.add(t + s * spb / 4, bass_note(hz(root), spb * 1.4, 'sub'), 0.9)
            comp = song['comp']
            if comp == 'guitar':
                if pat == 'half':
                    tr_c.add(t, guitar_chord(notes[:3], bar_len * 0.95), 0.55)
                else:
                    for i in range(8):
                        tr_c.add(t + i * spb / 2, guitar_chord(notes[:3], spb / 2, mute=(i % 4 != 0)), 0.5)
            elif comp == 'chug':
                if pat == 'half':
                    tr_c.add(t, guitar_chord(notes[:3], bar_len * 0.95), 0.55)
                else:
                    for i in range(16):
                        accent = i % 4 == 0 or i in (6, 10)
                        tr_c.add(t + i * spb / 4, guitar_chord(notes[:2] if not accent else notes[:3], spb / 4,
                                                              mute=not accent), 0.5 if accent else 0.4)
            elif comp == 'epiano':
                hits = [0, 6, 10] if pat != 'half' else [0]
                for s in hits:
                    tr_c.add(t + s * spb / 4, epiano([n + 12 for n in notes], spb * 1.2), 0.5)
            elif comp == 'pad':
                tr_c.add(t, pad_chord([n + 12 for n in notes], bar_len), 0.6)
                if pat != 'half':  # арпеджио
                    arp = [n + 24 for n in notes] + [notes[1] + 36]
                    for i in range(16):
                        tr_l.add(t + i * spb / 4, pluck(hz(arp[(i * 3 + rnd.randint(0, 1)) % len(arp)]), spb / 4), 0.22)
        t0 += nbars * bar_len
    b = fft_eq(tr_b.buf, hp=35, lp=4000)
    c = tr_c.buf
    if song['comp'] in ('guitar', 'chug'):
        c = fft_eq(c, hp=90, lp=4800, peak=(1600, 3, 0.8))
    l = fft_eq(tr_l.buf, hp=300, lp=9000)
    synth = Synth(SR, 7)
    mix_l = b * 0.9 + c * 0.75 + l * 0.6
    ir = synth.ir
    n = len(mix_l)
    N = 1 << (n + len(ir) - 2).bit_length()
    send = c * 0.75 + l * 0.6
    X = np.fft.rfft(send, N)
    wet = np.stack([np.fft.irfft(X * np.fft.rfft(ir[:, k], N), N)[:n] for k in range(2)], 1) * 0.22
    st = np.stack([mix_l, mix_l], 1) + wet
    st = st[:int((length + 2.5) * SR)]
    rms = float(np.sqrt((st ** 2).mean()))
    st *= 10 ** (-17 / 20) / max(rms, 1e-9)
    return np.tanh(st * 1.2) / 1.2


# ---------- подсказка: барабаны звуками жанра

BANK_FOR = {'rock': ('rusty', {'kick': 'kick', 'snare': 'snare', 'hihat': 'hihat', 'tom1': 'tom_hi', 'tom2': 'tom_mid',
                                'floor': 'floor', 'crash': 'crash', 'ride': 'ride'}),
            'jazz': ('virtuosity', {'kick': 'kick', 'snare': 'snare', 'hihat': 'hihat', 'tom1': 'tom_hi', 'tom2': 'tom_mid',
                                     'floor': 'floor', 'crash': 'crash', 'ride': 'ride'}),
            'metal': ('drs', {'kick': 'kick_tight', 'snare': 'snare_tight', 'hihat': 'hihat', 'tom1': 'tom_hi',
                              'tom2': 'tom_mid', 'floor': 'floor', 'crash': 'crash', 'ride': 'ride'}),
            'hiphop': (None, {'kick': 'kick808', 'snare': 'snare', 'hihat': 'hat808', 'tom1': 'tom808', 'tom2': 'tom808',
                              'floor': 'tom808_lo', 'crash': 'crash', 'ride': 'ride'}),
            'electro': (None, {'kick': 'kick909', 'snare': 'clap', 'hihat': 'hat808', 'tom1': 'tom808', 'tom2': 'tom808',
                               'floor': 'tom808_lo', 'crash': 'crash', 'ride': 'ride'})}


def drum_sounds(preset):
    bank, lanes = BANK_FOR[preset]
    synth = Synth(SR, 3)
    out = {}
    for lane, inst in lanes.items():
        d = os.path.join(ROOT, 'samples', bank, inst) if bank else None
        if d and os.path.isdir(d):
            layers = {}
            for fn in sorted(os.listdir(d)):
                if fn.endswith('.ogg'):
                    L = int(fn[1:].split('_')[0])
                    x, _ = sf.read(os.path.join(d, fn), always_2d=True)
                    layers.setdefault(L, []).append(x)
            out[lane] = [layers[k] for k in sorted(layers)]
        else:
            info = INSTRUMENTS[inst]
            vs = []
            for _ in range(3):
                x, send = info['fn'](synth)
                vs.append(synth.finish(x, info['pan'], send, info['peak']))
            out[lane] = [vs]
    return out


def render_drums(notes, sounds, length):
    buf = np.zeros((int((length + 3) * SR), 2))
    rnd = random.Random(1)
    for t, lane, v in notes:
        layers = sounds[lane]
        li = min(len(layers) - 1, int(round(v * (len(layers) - 1))))
        x = rnd.choice(layers[li])
        i = int(t * SR)
        j = min(len(buf), i + len(x))
        buf[i:j] += x[:j - i] * (0.6 + 0.4 * v)
    peak = np.abs(buf).max()
    return buf / max(peak, 1e-9) * 0.9


def write_ogg(path, x):
    """OGG Vorbis по частям: libsndfile падает, если записать длинный трек одним вызовом."""
    x = np.ascontiguousarray(np.clip(x, -1, 1).astype(np.float32))
    with sf.SoundFile(path, 'w', SR, x.shape[1], format='OGG', subtype='VORBIS', compression_level=0.5) as f:
        for i in range(0, len(x), 16384):
            f.write(x[i:i + 16384])


def main(charts_only=False):
    os.makedirs(OUT, exist_ok=True)
    index = []
    for song in SONGS:
        notes, bars = compose_drums(song)
        length = bars * 4 * 60 / song['bpm'] + 1.5
        d = os.path.join(OUT, song['id'])
        os.makedirs(d, exist_ok=True)
        print(f'{song["title"]}: {bars} тактов, {length:.0f} с, нот {len(notes)}', flush=True)
        if charts_only:  # только переписать ноты, звук не трогать
            with open(os.path.join(d, 'chart.json'), encoding='utf-8') as f:
                n_back = round(json.load(f)['length'] * SR)
        else:
            back = render_backing(song, length)
            write_ogg(os.path.join(d, 'backing.ogg'), back)
            drums = render_drums(notes, drum_sounds(song['preset']), length)[:len(back)]
            write_ogg(os.path.join(d, 'drums.ogg'), drums)
            n_back = len(back)
        chart = {'id': song['id'], 'title': song['title'], 'artist': 'Air Drums', 'genre': song['genre'],
                 'preset': song['preset'], 'bpm': song['bpm'], 'length': round(n_back / SR, 2),
                 'audio': 'backing.ogg', 'drums': 'drums.ogg',
                 'charts': {'easy': simplify(notes, song['bpm'], 'easy'),
                            'medium': simplify(notes, song['bpm'], 'medium'),
                            'hard': [[t, l] for t, l, _ in notes]}}
        with open(os.path.join(d, 'chart.json'), 'w', encoding='utf-8') as f:
            json.dump(chart, f, ensure_ascii=False)
        index.append(song['id'])
        print('   уровни:', {k: len(v) for k, v in chart['charts'].items()}, flush=True)
    with open(os.path.join(OUT, 'index.json'), 'w', encoding='utf-8') as f:
        json.dump(index, f)


if __name__ == '__main__':
    main(charts_only='--charts-only' in sys.argv)
