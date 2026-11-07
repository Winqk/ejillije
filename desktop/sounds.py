# -*- coding: utf-8 -*-
"""Звуки ударной установки: синтез на numpy при запуске (без файлов-сэмплов)."""
from __future__ import annotations

import collections
import math
import random
import threading
import time

import numpy as np
import pygame

RATIOS = (2, 3, 4.16, 5.43, 6.79, 8.21)  # «металлические» соотношения частот, как в TR-808


def _clamp(x, a, b):
    return a if x < a else b if x > b else x


class Synth:
    def __init__(self, sr: int, seed: int | None = None):
        self.sr = sr
        self.rng = np.random.default_rng(seed)
        self.ir = self._make_ir(1.7, 3.2)

    # ---------- помощники
    def t(self, sec):
        return np.arange(int(self.sr * sec), dtype=np.float64) / self.sr

    @staticmethod
    def env(t, attack, decay):  # decay — время спада до −80 дБ
        e = np.exp(-t * 9.21 / decay)
        if attack > 0:
            e *= np.clip(t / attack, 0, 1)
        return e

    @staticmethod
    def env_at(t, start, decay):
        tt = t - start
        return np.where(tt >= 0, np.exp(-np.clip(tt, 0, None) * 9.21 / decay), 0.0)

    def noise(self, n):
        return self.rng.uniform(-1, 1, n)

    def filt(self, x, hp=None, lp=None, peak=None, order=2):
        n = len(x)
        X = np.fft.rfft(x)
        f = np.maximum(np.fft.rfftfreq(n, 1 / self.sr), 1.0)
        H = np.ones_like(f)
        if hp:
            H /= np.sqrt(1 + (hp / f) ** (2 * order))
        if lp:
            H /= np.sqrt(1 + (f / lp) ** (2 * order))
        if peak:
            fc, db, w = peak
            H *= 1 + (10 ** (db / 20) - 1) * np.exp(-0.5 * (np.log2(f / fc) / w) ** 2)
        return np.fft.irfft(X * H, n)

    def sweep(self, t, f0, f1, tau, drift=0.0):
        f = f1 * (1 - drift * t / max(t[-1], 1e-3)) + (f0 - f1) * np.exp(-t / tau)
        return 2 * np.pi * np.cumsum(f) / self.sr

    @staticmethod
    def tri(ph):
        return 2 / np.pi * np.arcsin(np.sin(ph))

    @staticmethod
    def square(t, f):
        return np.sign(np.sin(2 * np.pi * f * t))

    def metal(self, t, fund):
        return sum(self.square(t, fund * r) for r in RATIOS) / len(RATIOS)

    def _make_ir(self, sec, decay):
        n = int(self.sr * sec)
        k = np.arange(n) / n
        fade = np.clip(np.arange(n) / (self.sr * 0.006), 0, 1)
        ir = self.rng.uniform(-1, 1, (n, 2)) * ((1 - k) ** decay * fade)[:, None]
        return ir / np.sqrt((ir ** 2).sum(axis=0))

    def reverb(self, x):
        n = len(x) + len(self.ir) - 1
        N = 1 << (n - 1).bit_length()
        X = np.fft.rfft(x, N)
        return np.stack([np.fft.irfft(X * np.fft.rfft(self.ir[:, c], N), N)[:n] for c in range(2)], 1)

    def finish(self, x, pan, send, peak):
        a = (pan + 1) * math.pi / 4
        dry = np.stack([x * math.cos(a), x * math.sin(a)], 1) * math.sqrt(2)
        out = dry
        if send > 0:
            wet = self.reverb(x) * (send * 0.55)
            out = np.zeros_like(wet)
            out[:len(dry)] += dry
            out += wet
        loud = np.nonzero(np.abs(out).max(axis=1) > 2e-4)[0]
        out = out[:loud[-1] + 1] if len(loud) else out[:1]
        out *= peak / max(np.abs(out).max(), 1e-9)
        return out

    # ---------- инструменты: (моно-сигнал, посыл на реверб)
    def kick(self, f0=165, f1=43, tau=0.028, decay=0.55, drive=2.4, click=0.32, chp=2200, clp=4500, send=0.06, sec=0.7):
        t = self.t(sec)
        body = np.sin(self.sweep(t, f0, f1, tau)) * self.env(t, 0.003, decay)
        body = np.tanh(drive * body) / np.tanh(drive)
        c = self.filt(self.noise(len(t)), hp=chp, lp=clp) * self.env(t, 0.001, 0.025) * click
        return body + c, send

    def snare(self, tone=(205, 168), hp=1100, peak=(4500, 5, 0.6), ndecay=0.26, tdecay=0.14, namt=0.8, tamt=0.75,
              attack=0.001, send=0.25, sec=0.5, drive=0.0):
        t = self.t(sec)
        x = self.tri(self.sweep(t, tone[0], tone[1], 0.04)) * self.env(t, attack, tdecay) * tamt
        x += np.sin(2 * np.pi * tone[0] * 1.66 * t) * self.env(t, attack, tdecay * 0.5) * tamt * 0.37
        x += self.filt(self.noise(len(t)), hp=hp, lp=13000, peak=peak) * self.env(t, attack, ndecay) * namt
        if drive:
            x = np.tanh(drive * x) / np.tanh(drive)
        return x, send

    def clap(self):
        t = self.t(0.55)
        n = self.filt(self.noise(len(t)), hp=900, lp=2600)
        e = sum(self.env_at(t, d, 0.012) for d in (0.0, 0.011, 0.022)) + self.env_at(t, 0.033, 0.28) * 0.9
        return n * e, 0.3

    def snap(self):
        t = self.t(0.2)
        x = self.filt(self.noise(len(t)), hp=2000, lp=5500) * self.env(t, 0.0005, 0.06)
        x += np.sin(2 * np.pi * 1900 * t) * self.env(t, 0.0005, 0.02) * 0.3
        return x, 0.28

    def rim(self):
        t = self.t(0.2)
        x = self.tri(2 * np.pi * 1700 * t) * self.env(t, 0.0003, 0.03)
        x += self.filt(self.noise(len(t)), hp=1800, lp=6000) * self.env(t, 0.0003, 0.02) * 0.6
        x += np.sin(2 * np.pi * 520 * t) * self.env(t, 0.0005, 0.05) * 0.5
        return x, 0.2

    def cowbell(self):
        t = self.t(0.5)
        x = (self.square(t, 540) + self.square(t, 800)) * 0.5
        x = self.filt(x, hp=500, lp=3000, peak=(800, 6, 0.5))
        return x * (self.env(t, 0.001, 0.4) * 0.8 + self.env(t, 0.001, 0.04) * 0.4), 0.2

    def tom(self, f0, decay):
        t = self.t(decay + 0.15)
        o = np.sin(self.sweep(t, f0 * 1.6, f0, 0.02, drift=0.1)) * self.env(t, 0.002, decay) * 0.95
        o2 = self.tri(self.sweep(t, f0 * 2.4, f0 * 1.5, 0.02)) * self.env(t, 0.001, 0.12) * 0.18
        nz = self.filt(self.noise(len(t)), lp=3000) * self.env(t, 0.001, 0.05) * 0.3
        return o + o2 + nz, 0.25

    def tom808(self, f0, decay):
        t = self.t(decay + 0.15)
        o = np.sin(self.sweep(t, f0 * 1.35, f0, 0.05, drift=0.05)) * self.env(t, 0.002, decay)
        return np.tanh(1.2 * o) / np.tanh(1.2), 0.15

    def hat(self, open_, fund=40, hp=7000, cdecay=0.075, odecay=0.75, nz=0.35):
        d = odecay if open_ else cdecay
        t = self.t(d + 0.1)
        m = self.filt(self.metal(t, fund), hp=hp, lp=13000)
        n = self.filt(self.noise(len(t)), hp=hp + 1000) * nz
        return (m + n) * self.env(t, 0.0008, d) * (0.85 if open_ else 1.1), 0.12

    def crash(self, fund=57, hp=3000, lp=12500, decay=2.4, mdecay=1.6, sec=3.0, trash=0.0):
        t = self.t(sec)
        x = self.filt(self.noise(len(t)), hp=hp, lp=lp) * self.env(t, 0.002, decay) * 0.42
        x += self.filt(self.metal(t, fund), hp=hp + 2500) * self.env(t, 0.001, mdecay) * 0.4
        x += self.filt(self.noise(len(t)), hp=3500, lp=7000) * self.env(t, 0.001, 0.06) * 0.55
        if trash:  # «чайна»: перегруженный, «грязный» звук
            x = self.filt(np.tanh(trash * x), hp=1500, peak=(4500, 5, 0.7))
        return x, 0.33

    def ride(self, fund=72, band=(6500, 9800), decay=1.5, wash=0.16, wdecay=0.9, ping=(3100, 4350), pamt=0.05, sec=2.0):
        t = self.t(sec)
        x = self.filt(self.metal(t, fund), hp=band[0], lp=band[1], order=3) * self.env(t, 0.001, decay) * 1.25
        x += self.filt(self.noise(len(t)), hp=9000) * self.env(t, 0.001, wdecay) * wash
        x += sum(np.sin(2 * np.pi * f * t) for f in ping) * self.env(t, 0.001, 0.45) * pamt
        return x, 0.23

    def ride_bell(self):
        t = self.t(1.6)
        x = sum(a * np.sin(2 * np.pi * f * t) * self.env(t, 0.001, d)
                for f, a, d in ((780, 1.0, 1.3), (1170, 0.6, 1.0), (1650, 0.4, 0.7), (2340, 0.3, 0.5)))
        x += self.filt(self.metal(t, 72), hp=7000) * self.env(t, 0.001, 0.4) * 0.3
        return x * 0.5, 0.25

    def click(self, accent):
        t = self.t(0.08)
        return np.sin(2 * np.pi * (1760 if accent else 1175) * t) * self.env(t, 0.001, 0.05)


# ---------- каталог инструментов
# type — как рисовать; cat — группа (цвет в скинах «пэды» и «неон»)
INSTRUMENTS = {
    'kick':        dict(name='Бочка', type='kick', cat='kick', pan=0.0, peak=0.62, fn=lambda s: s.kick()),
    'kick_jazz':   dict(name='Бочка джаз', type='kick', cat='kick', pan=0.0, peak=0.55,
                        fn=lambda s: s.kick(120, 60, 0.05, 0.7, 1.3, 0.15, send=0.16, sec=0.9)),
    'kick_tight':  dict(name='Бочка метал', type='kick', cat='kick', pan=0.0, peak=0.62,
                        fn=lambda s: s.kick(210, 52, 0.014, 0.32, 3.0, 0.75, 3000, 9000, 0.04, 0.5)),
    'kick808':     dict(name='Бочка 808', type='kick', cat='kick', pan=0.0, peak=0.7,
                        fn=lambda s: s.kick(110, 46, 0.045, 1.3, 1.6, 0.08, send=0.03, sec=1.5)),
    'kick909':     dict(name='Бочка 909', type='kick', cat='kick', pan=0.0, peak=0.65,
                        fn=lambda s: s.kick(240, 50, 0.018, 0.5, 3.2, 0.45, 1500, 6000, 0.05, 0.7)),
    'snare':       dict(name='Малый', type='snare', cat='snare', pan=-0.1, peak=0.55, fn=lambda s: s.snare()),
    'snare_brush': dict(name='Малый щётки', type='snare', cat='snare', pan=-0.1, peak=0.45,
                        fn=lambda s: s.snare(tamt=0.25, hp=1800, peak=(5000, 3, 0.8), ndecay=0.42, attack=0.012,
                                             namt=0.7, send=0.3, sec=0.7)),
    'snare_tight': dict(name='Малый метал', type='snare', cat='snare', pan=-0.1, peak=0.58,
                        fn=lambda s: s.snare((245, 200), 2000, (6000, 6, 0.5), 0.2, 0.1, 0.95, send=0.18)),
    'snare909':    dict(name='Малый 909', type='snare', cat='snare', pan=-0.1, peak=0.55,
                        fn=lambda s: s.snare((190, 160), 1500, (7000, 4, 0.7), 0.24, 0.1, send=0.2, drive=1.8)),
    'clap':        dict(name='Хлопок', type='perc', cat='snare', pan=0.1, peak=0.55, fn=lambda s: s.clap()),
    'snap':        dict(name='Щелчок', type='perc', cat='perc', pan=0.15, peak=0.45, fn=lambda s: s.snap()),
    'rim':         dict(name='Римшот', type='perc', cat='perc', pan=-0.1, peak=0.45, fn=lambda s: s.rim()),
    'cowbell':     dict(name='Ковбелл', type='perc', cat='perc', pan=0.3, peak=0.4, fn=lambda s: s.cowbell()),
    'tom_hi':      dict(name='Том высокий', type='drum', cat='tom', pan=-0.15, peak=0.5, fn=lambda s: s.tom(215, 0.5)),
    'tom_mid':     dict(name='Том средний', type='drum', cat='tom', pan=0.05, peak=0.5, fn=lambda s: s.tom(160, 0.62)),
    'tom_lo':      dict(name='Том низкий', type='drum', cat='tom', pan=0.2, peak=0.5, fn=lambda s: s.tom(128, 0.72)),
    'floor':       dict(name='Напольный', type='floor', cat='tom', pan=0.4, peak=0.52, fn=lambda s: s.tom(102, 0.9)),
    'tom808':      dict(name='Том 808', type='drum', cat='tom', pan=-0.1, peak=0.5, fn=lambda s: s.tom808(165, 0.6)),
    'tom808_lo':   dict(name='Том 808 низкий', type='floor', cat='tom', pan=0.3, peak=0.52,
                        fn=lambda s: s.tom808(110, 0.8)),
    'hihat':       dict(name='Хай-хэт', type='hihat', cat='hat', pan=-0.45, peak=0.30, fn=lambda s: s.hat(False)),
    'hihat_open':  dict(name='Хай-хэт открытый', type='hihat', cat='hat', pan=-0.45, peak=0.32, fn=lambda s: s.hat(True)),
    'hat808':      dict(name='Хай-хэт 808', type='hihat', cat='hat', pan=-0.4, peak=0.28,
                        fn=lambda s: s.hat(False, 47, 8000, 0.05, 0.45, 0.15)),
    'hat808_open': dict(name='Хай-хэт 808 открытый', type='hihat', cat='hat', pan=-0.4, peak=0.3,
                        fn=lambda s: s.hat(True, 47, 8000, 0.05, 0.45, 0.15)),
    'crash':       dict(name='Крэш', type='cymbal', cat='cymbal', pan=-0.5, peak=0.42, fn=lambda s: s.crash()),
    'crash_dark':  dict(name='Крэш тёмный', type='cymbal', cat='cymbal', pan=-0.4, peak=0.42,
                        fn=lambda s: s.crash(47, 2200, 9000, 2.8, 1.9, 3.3)),
    'splash':      dict(name='Сплэш', type='cymbal', cat='cymbal', pan=-0.2, peak=0.38,
                        fn=lambda s: s.crash(66, 4200, 13500, 0.95, 0.7, 1.2)),
    'china':       dict(name='Чайна', type='cymbal', cat='cymbal', pan=0.5, peak=0.42,
                        fn=lambda s: s.crash(64, 1800, 9500, 1.7, 1.3, 2.2, trash=2.2)),
    'ride':        dict(name='Райд', type='cymbal', cat='cymbal', pan=0.5, peak=0.34, fn=lambda s: s.ride()),
    'ride_jazz':   dict(name='Райд джаз', type='cymbal', cat='cymbal', pan=0.45, peak=0.36,
                        fn=lambda s: s.ride(64, (5500, 9000), 2.4, 0.32, 2.0, (2700, 3900), 0.035, 3.0)),
    'ride_bell':   dict(name='Купол райда', type='cymbal', cat='cymbal', pan=0.5, peak=0.4, fn=lambda s: s.ride_bell()),
}
OPEN_HATS = {'hihat': 'hihat_open', 'hat808': 'hat808_open'}
HATS = {'hihat', 'hihat_open', 'hat808', 'hat808_open'}
PICKER_GROUPS = [
    ('Бочки', ['kick', 'kick_jazz', 'kick_tight', 'kick808', 'kick909']),
    ('Малые и хлопки', ['snare', 'snare_brush', 'snare_tight', 'snare909', 'clap']),
    ('Томы', ['tom_hi', 'tom_mid', 'tom_lo', 'floor', 'tom808', 'tom808_lo']),
    ('Хай-хэты и тарелки', ['hihat', 'hihat_open', 'hat808', 'hat808_open', 'crash', 'crash_dark', 'splash',
                            'china', 'ride', 'ride_jazz', 'ride_bell']),
    ('Перкуссия', ['rim', 'snap', 'cowbell']),
]
SELECTABLE = [k for _, ks in PICKER_GROUPS for k in ks]


class Audio:
    """Микшер pygame + фоновый синтез: сначала звуки текущего пресета, потом остальные."""

    def __init__(self, buffer: int, volume: float, log=print, variants: int = 2):
        self.ok = False
        self.volume = volume
        self.log = log
        self.variants = variants
        self.lock = threading.Lock()
        self.open_ch = None
        self.sounds: dict[str, list] = {}
        self.open_set = set()
        self.queue = collections.deque()
        self.qcond = threading.Condition()
        try:
            pygame.mixer.init(44100, -16, 2, int(buffer), allowedchanges=0)
            pygame.mixer.set_num_channels(48)
            self.sr = pygame.mixer.get_init()[0]
            synth = Synth(self.sr, 1)
            self.clicks = {}
            for acc in (True, False):
                x = synth.click(acc) * (0.45 if acc else 0.28)
                self.clicks[acc] = self._make(np.stack([x, x], 1))
            self.ok = True
            self.queue.extend(INSTRUMENTS)
            threading.Thread(target=self._worker, daemon=True).start()
        except Exception as e:  # нет звуковой карты и т.п.
            log('Звук недоступен: ' + repr(e))

    @staticmethod
    def _make(arr):
        return pygame.sndarray.make_sound(np.ascontiguousarray((arr * 32767).astype(np.int16)))

    def prioritize(self, keys):
        """Звуки нового пресета синтезируются в первую очередь."""
        with self.qcond:
            for k in reversed(list(keys) + [OPEN_HATS[k] for k in keys if k in OPEN_HATS]):
                if k in INSTRUMENTS and k not in self.sounds:
                    self.queue.appendleft(k)
            self.qcond.notify()

    def ready(self, keys):
        return all(k in self.sounds for k in keys)

    def _worker(self):
        synth = Synth(self.sr)
        t0 = time.perf_counter()
        while True:
            with self.qcond:
                while not self.queue:
                    self.qcond.wait()
                key = self.queue.popleft()
            if key in self.sounds:
                continue
            info = INSTRUMENTS[key]
            try:
                snds = []
                for _ in range(self.variants):  # несколько вариантов — без эффекта «пулемёта»
                    x, send = info['fn'](synth)
                    snds.append(self._make(synth.finish(x, info['pan'], send, info['peak'])))
                with self.lock:
                    self.sounds[key] = snds
                    if key in ('hihat_open', 'hat808_open'):
                        self.open_set.update(snds)
            except Exception as e:
                self.log(f'Не удалось синтезировать {key}: {e!r}')
                self.sounds[key] = []
            if len(self.sounds) == len(INSTRUMENTS) and t0:
                self.log(f'Звуки готовы: {len(self.sounds)} инструментов за {time.perf_counter() - t0:.1f} с')
                t0 = 0.0

    def play(self, inst: str, vel: float, open_: bool = False):
        if not self.ok:
            return
        key = OPEN_HATS.get(inst, inst) if open_ else inst
        with self.lock:
            snds = self.sounds.get(key)
            if not snds:
                if key not in self.sounds:
                    self.prioritize([key])
                return
            if key in HATS and self.open_ch is not None:  # любой удар по хай-хэту глушит открытый
                if self.open_ch.get_busy() and self.open_ch.get_sound() in self.open_set:
                    self.open_ch.fadeout(40)
                self.open_ch = None
            ch = pygame.mixer.find_channel(True)
            vol = _clamp(vel, 0.05, 1.0) * self.volume ** 1.5
            ch.set_volume(vol)
            ch.play(random.choice(snds))
            ch.set_volume(vol)
            if key in ('hihat_open', 'hat808_open'):
                self.open_ch = ch

    def click(self, accent: bool):
        if not self.ok:
            return
        with self.lock:
            ch = pygame.mixer.find_channel(True)
            ch.set_volume(self.volume ** 1.5)
            ch.play(self.clicks[accent])
