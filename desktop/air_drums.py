#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Воздушные барабаны — десктопная версия.

Играете по воздуху карандашами или палочками, камера ноутбука и нейросеть
MediaPipe находят руки, а программа превращает удары в звук полной ударной
установки. Всё работает локально: без браузера и без отправки видео.

Запуск:  python air_drums.py           (на Windows — «Start Air Drums.bat»)
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import os
import random
import sys
import threading
import time
import traceback
import urllib.request

APP_NAME = 'Воздушные барабаны'
BASE_DIR = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
MODEL_NAME = 'hand_landmarker.task'
MODEL_URL = ('https://storage.googleapis.com/mediapipe-models/hand_landmarker/'
             'hand_landmarker/float16/1/hand_landmarker.task')


def data_dir() -> str:
    root = os.environ.get('APPDATA') if os.name == 'nt' else os.path.join(os.path.expanduser('~'), '.config')
    d = os.path.join(root or os.path.expanduser('~'), 'AirDrums')
    os.makedirs(d, exist_ok=True)
    return d


LOG_PATH = os.path.join(data_dir(), 'air_drums.log')
_log_lock = threading.Lock()


def log(msg: str) -> None:
    line = time.strftime('%Y-%m-%d %H:%M:%S ') + msg
    with _log_lock:
        try:
            with open(LOG_PATH, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except OSError:
            pass
    if sys.stdout:
        try:
            print(line, flush=True)
        except Exception:
            pass


# Под pythonw.exe нет консоли: перенаправляем вывод библиотек в лог, чтобы ничего не падало.
if sys.stdout is None or sys.stderr is None:
    _sink = open(LOG_PATH, 'a', encoding='utf-8', buffering=1)
    sys.stdout = sys.stdout or _sink
    sys.stderr = sys.stderr or _sink

import numpy as np  # noqa: E402
import cv2  # noqa: E402

os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')
import pygame  # noqa: E402


def clamp(x, a, b):
    return a if x < a else b if x > b else x


# ============================================================ настройки

DEFAULTS = {
    'mode': 'hands',        # hands | color
    'sens': 6,              # 1..10
    'volume': 80,           # 0..100
    'bpm': 100,
    'colors': [],
    'pads': {},
    'camera': 0,
    'cam_width': 1280,
    'cam_height': 720,
    'cam_fps': 60,
    'audio_buffer': 256,    # меньше — меньше задержка; если звук трещит — поставьте 512
    'fullscreen': False,
    'shortcut_done': False,
}
SETTINGS_PATH = os.path.join(data_dir(), 'settings.json')


def load_settings() -> dict:
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH, encoding='utf-8') as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            s.update({k: v for k, v in saved.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    if s['mode'] not in ('hands', 'color'):
        s['mode'] = 'hands'
    s['sens'] = int(clamp(int(s['sens']), 1, 10))
    s['volume'] = int(clamp(int(s['volume']), 0, 100))
    s['bpm'] = int(clamp(int(s['bpm']), 40, 240))
    s['colors'] = [c for c in s['colors'] if isinstance(c, dict) and 'h' in c][:2]
    return s


def save_settings(s: dict) -> None:
    try:
        tmp = SETTINGS_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(s, f, ensure_ascii=False, indent=1)
        os.replace(tmp, SETTINGS_PATH)
    except OSError as e:
        log(f'Не удалось сохранить настройки: {e}')


# ============================================================ звук: синтез установки

class Synth:
    """Синтезирует звуки установки один раз при запуске (numpy, без файлов-сэмплов)."""
    RATIOS = (2, 3, 4.16, 5.43, 6.79, 8.21)

    def __init__(self, sr: int, seed: int = 0):
        self.sr = sr
        self.rng = np.random.default_rng(seed)
        self.ir = self._make_ir(1.7, 3.2)

    # --- помощники
    def t(self, sec):
        return np.arange(int(self.sr * sec), dtype=np.float64) / self.sr

    @staticmethod
    def env(t, attack, decay):  # decay — время спада до −80 дБ, как в веб-версии
        e = np.exp(-t * 9.21 / decay)
        if attack > 0:
            e *= np.clip(t / attack, 0, 1)
        return e

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

    def metal(self, t, fund):
        return sum(np.sign(np.sin(2 * np.pi * fund * r * t)) for r in self.RATIOS) / len(self.RATIOS)

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

    # --- инструменты: возвращают (моно-сигнал, посыл на реверб)
    def kick(self):
        t = self.t(0.7)
        body = np.sin(self.sweep(t, 165, 43, 0.028)) * self.env(t, 0.003, 0.55)
        body = np.tanh(2.4 * body) / np.tanh(2.4)
        click = self.filt(self.noise(len(t)), hp=2200, lp=4500) * self.env(t, 0.001, 0.025) * 0.32
        return body + click, 0.06

    def snare(self):
        t = self.t(0.5)
        tone = self.tri(self.sweep(t, 205, 168, 0.04)) * self.env(t, 0.001, 0.14) * 0.75
        tone2 = np.sin(2 * np.pi * 340 * t) * self.env(t, 0.001, 0.07) * 0.28
        nz = self.filt(self.noise(len(t)), hp=1100, lp=13000, peak=(4500, 5, 0.6)) * self.env(t, 0.001, 0.26) * 0.8
        return tone + tone2 + nz, 0.25

    def hat(self, open_):
        d = 0.75 if open_ else 0.075
        t = self.t(d + 0.1)
        m = self.filt(self.metal(t, 40), hp=7000, lp=13000, order=2)
        nz = self.filt(self.noise(len(t)), hp=8000) * 0.35
        return (m + nz) * self.env(t, 0.0008, d) * (0.85 if open_ else 1.1), 0.12

    def tom(self, f0, decay):
        t = self.t(decay + 0.15)
        o = np.sin(self.sweep(t, f0 * 1.6, f0, 0.02, drift=0.1)) * self.env(t, 0.002, decay) * 0.95
        o2 = self.tri(self.sweep(t, f0 * 2.4, f0 * 1.5, 0.02)) * self.env(t, 0.001, 0.12) * 0.18
        nz = self.filt(self.noise(len(t)), lp=3000) * self.env(t, 0.001, 0.05) * 0.3
        return o + o2 + nz, 0.25

    def crash(self):
        t = self.t(3.0)
        nz = self.filt(self.noise(len(t)), hp=3000, lp=12500) * self.env(t, 0.002, 2.4) * 0.42
        m = self.filt(self.metal(t, 57), hp=5500) * self.env(t, 0.001, 1.6) * 0.4
        tr = self.filt(self.noise(len(t)), hp=3500, lp=7000) * self.env(t, 0.001, 0.06) * 0.55
        return nz + m + tr, 0.33

    def ride(self):
        t = self.t(2.0)
        m = self.filt(self.metal(t, 72), hp=6500, lp=9800, order=3) * self.env(t, 0.001, 1.5) * 1.25
        nz = self.filt(self.noise(len(t)), hp=9000) * self.env(t, 0.001, 0.9) * 0.16
        ping = (np.sin(2 * np.pi * 3100 * t) + np.sin(2 * np.pi * 4350 * t)) * self.env(t, 0.001, 0.45) * 0.05
        return m + nz + ping, 0.23

    def click(self, accent):
        t = self.t(0.08)
        return np.sin(2 * np.pi * (1760 if accent else 1175) * t) * self.env(t, 0.001, 0.05)

    # --- сборка
    PAN = {'kick': 0, 'snare': -0.1, 'hihat': -0.45, 'hihat_open': -0.45, 'tom1': -0.15, 'tom2': 0.15,
           'floor': 0.4, 'crash': -0.5, 'ride': 0.5}
    PEAK = {'kick': 0.62, 'snare': 0.55, 'hihat': 0.30, 'hihat_open': 0.32, 'tom1': 0.5, 'tom2': 0.5,
            'floor': 0.52, 'crash': 0.42, 'ride': 0.34}

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

    def render(self, variants=3) -> dict:
        makers = {
            'kick': self.kick, 'snare': self.snare,
            'hihat': lambda: self.hat(False), 'hihat_open': lambda: self.hat(True),
            'tom1': lambda: self.tom(215, 0.5), 'tom2': lambda: self.tom(160, 0.62),
            'floor': lambda: self.tom(102, 0.9), 'crash': self.crash, 'ride': self.ride,
        }
        out = {}
        for k, fn in makers.items():
            out[k] = []
            for _ in range(variants):  # несколько вариантов — без эффекта «пулемёта»
                x, send = fn()
                out[k].append(self.finish(x, self.PAN[k], send, self.PEAK[k]))
        for acc in (True, False):
            x = self.click(acc) * (0.45 if acc else 0.28)
            out['click_hi' if acc else 'click'] = [np.stack([x, x], 1)]
        return out


class Audio:
    def __init__(self, buffer: int, volume: float):
        self.ok = False
        self.volume = volume
        self.lock = threading.Lock()
        self.open_ch = None
        self.sounds = {}
        try:
            pygame.mixer.init(44100, -16, 2, int(buffer), allowedchanges=0)
            pygame.mixer.set_num_channels(48)
            sr = pygame.mixer.get_init()[0]
            t0 = time.perf_counter()
            raw = Synth(sr).render()
            for k, vs in raw.items():
                self.sounds[k] = [pygame.sndarray.make_sound(np.ascontiguousarray((v * 32767).astype(np.int16)))
                                  for v in vs]
            self.open_set = set(self.sounds['hihat_open'])
            self.ok = True
            log(f'Звук: {sr} Гц, буфер {buffer}, синтез {1000 * (time.perf_counter() - t0):.0f} мс')
        except Exception as e:  # нет звуковой карты и т.п.
            log('Звук недоступен: ' + repr(e))

    def play(self, pid: str, vel: float, open_: bool = False):
        if not self.ok:
            return
        with self.lock:
            key = 'hihat_open' if (pid == 'hihat' and open_) else pid
            if pid == 'hihat' and self.open_ch is not None:  # любой удар по хай-хэту глушит открытый
                if self.open_ch.get_busy() and self.open_ch.get_sound() in self.open_set:
                    self.open_ch.fadeout(40)
                self.open_ch = None
            snd = random.choice(self.sounds[key])
            ch = pygame.mixer.find_channel(True)
            vol = clamp(vel, 0.05, 1.0) * self.volume ** 1.5
            ch.set_volume(vol)
            ch.play(snd)
            ch.set_volume(vol)
            if key == 'hihat_open':
                self.open_ch = ch

    def click(self, accent: bool):
        if not self.ok:
            return
        with self.lock:
            ch = pygame.mixer.find_channel(True)
            ch.set_volume(self.volume ** 1.5)
            ch.play(self.sounds['click_hi' if accent else 'click'][0])


# ============================================================ расстановка

PADS_DEFAULT = [
    {'id': 'crash', 'name': 'Крэш', 'type': 'cymbal', 'x': 0.15, 'y': 0.28, 'r': 0.115, 'key': '1'},
    {'id': 'hihat', 'name': 'Хай-хэт', 'type': 'hihat', 'x': 0.16, 'y': 0.60, 'r': 0.095, 'key': '2'},
    {'id': 'snare', 'name': 'Малый', 'type': 'drum', 'x': 0.36, 'y': 0.74, 'r': 0.10, 'key': '3'},
    {'id': 'tom1', 'name': 'Том 1', 'type': 'drum', 'x': 0.39, 'y': 0.42, 'r': 0.082, 'key': '4'},
    {'id': 'tom2', 'name': 'Том 2', 'type': 'drum', 'x': 0.61, 'y': 0.42, 'r': 0.082, 'key': '5'},
    {'id': 'floor', 'name': 'Напольный', 'type': 'drum', 'x': 0.65, 'y': 0.75, 'r': 0.108, 'key': '6'},
    {'id': 'ride', 'name': 'Райд', 'type': 'cymbal', 'x': 0.85, 'y': 0.34, 'r': 0.125, 'key': '7'},
    {'id': 'kick', 'name': 'Бочка', 'type': 'kick', 'x': 0.5, 'y': 0.94, 'r': 0.075, 'key': 'Пробел'},
]
DRUM_DEPTH = {'floor': 0.6, 'snare': 0.3}


def load_pads(saved: dict) -> list:
    pads = []
    for d in PADS_DEFAULT:
        p = dict(d)
        s = (saved or {}).get(d['id'])
        if isinstance(s, dict) and all(isinstance(s.get(k), (int, float)) for k in ('x', 'y', 'r')):
            p.update(x=float(s['x']), y=float(s['y']), r=float(s['r']))
        p['hitT'] = -1e9
        p['hitRgb'] = (255, 255, 255)
        pads.append(p)
    return pads


def pad_distance(p, x, y, aspect):
    rx = p['r'] * aspect
    ry = rx if p['type'] == 'kick' else rx * 0.85
    return math.hypot((x - p['x']) * aspect / rx, (y - p['y']) / ry)


def nearest_pad(pads, x, y, aspect, sticks=True):
    """Ближайший барабан: целиться не нужно. Бочку палочками — только прямым попаданием."""
    best, bd = None, 1e9
    for p in pads:
        d = pad_distance(p, x, y, aspect)
        if sticks and p['id'] == 'kick' and d > 1.0:
            continue
        if d < bd:
            best, bd = p, d
    return best, bd


# ============================================================ детектор ударов

MODE_CFG = {'hands': {'vOn': 4.0, 'minTravel': 0.22}, 'color': {'vOn': 6.0, 'minTravel': 0.3}}
COLOR_SCALE = 0.12   # «условный размер» кончика, чтобы пороги совпадали по смыслу с режимом рук
STICK_RGB = [(56, 225, 255), (255, 95, 162)]


def make_cfg(mode, sens):
    f = 1.9 - 0.15 * sens          # чувствительность 6 → 1.0
    m = MODE_CFG[mode]
    return {'vOn': m['vOn'] * f, 'minTravel': m['minTravel'] * max(0.6, f),
            'release': 0.8, 'rearm': 0.35, 'refractory': 0.075}


class Stick:
    """Скорости считаются в «размерах ладони в секунду», поэтому пороги не зависят от расстояния до камеры."""

    def __init__(self, i):
        self.i = i
        self.rgb = STICK_RGB[i]
        self.last_hit = -1e9
        self.reset()

    def reset(self):
        self.active = False
        self.striking = False
        self.armed = True
        self.trail = collections.deque(maxlen=14)
        self.vy = 0.0
        self.last_seen = -1e9
        self.scale = 0.12

    def _start(self, x, y, t, scale):
        self.active, self.striking, self.armed = True, False, True
        self.x, self.y, self.t, self.last_seen, self.vy, self.scale = x, y, t, t, 0.0, scale
        self.trail.clear()
        self.trail.append((x, y))

    def update(self, px, py, t, scale, aspect, c):
        dt = max(0.008, t - self.t) if self.active else 0.033
        # скачок быстрее 9 высот кадра в секунду — это сбой трекинга, а не удар
        if not self.active or t - self.last_seen > 0.25 or math.hypot((px - self.x) * aspect, py - self.y) / dt > 9.0:
            self._start(px, py, t, scale)
            return None
        nx, ny = self.x + (px - self.x) * 0.7, self.y + (py - self.y) * 0.7
        self.scale += (clamp(scale, 0.04, 0.4) - self.scale) * 0.2
        prev_y = self.y
        self.vy = (ny - self.y) / dt
        self.x, self.y, self.t, self.last_seen = nx, ny, t, t
        self.trail.append((nx, ny))
        v = self.vy / self.scale
        if not self.striking:
            if not self.armed and v < c['vOn'] * c['rearm']:
                self.armed = True
            if self.armed and v > c['vOn'] and t - self.last_hit > c['refractory']:
                self.striking, self.peak, self.start_y, self.strike_t = True, v, prev_y, t
            return None
        self.peak = max(self.peak, v)
        if v < self.peak * c['release']:          # рука начала тормозить — это удар
            self.striking = False
            if (self.y - self.start_y) / self.scale >= c['minTravel']:
                self.last_hit, self.armed = t, False
                # точка приземления: рука ещё чуть-чуть доедет вниз
                y = min(1.0, self.y + min(max(0.0, self.vy) * 0.033, self.scale * 0.6))
                return {'x': self.x, 'y': y, 'peak': self.peak}
        elif t - self.strike_t > 0.45:
            self.striking = False
        return None

    def miss(self, t, c):
        """Рука пропала из кадра. На быстром ударе нейросеть часто теряет её на кадр-другой:
        ждём до 0,1 с, и только если рука не вернулась — засчитываем удар по прогнозу."""
        if not self.active:
            return None
        hit = None
        if self.striking and t - self.last_seen >= 0.1:
            self.striking = False
            y = min(1.0, self.y + min(max(0.0, self.vy) * 0.06, self.scale * 1.5))
            if self.peak > c['vOn'] * 1.25 and (y - self.start_y) / self.scale >= c['minTravel']:
                self.last_hit, self.armed = t, False
                hit = {'x': self.x, 'y': y, 'peak': self.peak}
        if t - self.last_seen > 0.3:
            self.active = False
        return hit


def assign(points, sticks, aspect):
    out = [None, None]
    if not points:
        return out
    if any(p.get('key') is not None for p in points):
        for p in points:
            if p.get('key') in (0, 1):
                out[p['key']] = p
        return out

    def d(s, p):
        return math.hypot((s.x - p['x']) * aspect, s.y - p['y']) if s.active else None

    if len(points) == 1:
        p = points[0]
        d0, d1 = d(sticks[0], p), d(sticks[1], p)
        if d0 is None and d1 is None:
            i = 0 if p['x'] < 0.5 else 1
        elif d0 is None:
            i = 1 if d1 < 0.25 else 0
        elif d1 is None:
            i = 0 if d0 < 0.25 else 1
        else:
            i = 0 if d0 <= d1 else 1
        out[i] = p
        return out
    p, q = points[:2]

    def c(s, pt):
        v = d(s, pt)
        return 0.3 if v is None else v

    if not sticks[0].active and not sticks[1].active:
        swap = p['x'] > q['x']
    else:
        swap = c(sticks[0], q) + c(sticks[1], p) < c(sticks[0], p) + c(sticks[1], q)
    out[0], out[1] = (q, p) if swap else (p, q)
    return out


# ============================================================ трекеры

class HandTracker:
    def __init__(self, model_bytes: bytes):
        import mediapipe as mp
        from mediapipe.tasks.python import vision
        from mediapipe.tasks.python.core.base_options import BaseOptions
        self.mp = mp
        opts = vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_buffer=model_bytes),
            running_mode=vision.RunningMode.VIDEO, num_hands=2,
            min_hand_detection_confidence=0.5, min_hand_presence_confidence=0.5, min_tracking_confidence=0.5)
        self.lm = vision.HandLandmarker.create_from_options(opts)
        self.connections = [(c.start, c.end) for c in vision.HandLandmarksConnections.HAND_CONNECTIONS]
        self.last_ts = -1

    def process(self, frame, t):
        h, w = frame.shape[:2]
        small = frame if w <= 640 else cv2.resize(frame, (640, round(640 * h / w)), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        ts = max(int(t * 1000), self.last_ts + 1)
        self.last_ts = ts
        res = self.lm.detect_for_video(self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb), ts)
        aspect = w / h
        hands, pts = [], []
        for hl in res.hand_landmarks:
            P = [(lm.x, lm.y) for lm in hl]
            hands.append(P)
            # точка палочки — центр костяшек: там палочка зажата в кулаке
            x = sum(P[i][0] for i in (5, 9, 13, 17)) / 4
            y = sum(P[i][1] for i in (5, 9, 13, 17)) / 4
            size = math.hypot((P[0][0] - P[9][0]) * aspect, P[0][1] - P[9][1])
            pts.append({'x': x, 'y': y, 'scale': size})
        return pts, hands

    def close(self):
        try:
            self.lm.close()
        except Exception:
            pass


class ColorTracker:
    W = 192

    def __init__(self):
        self.small = None
        self.mask = None

    def process(self, frame, colors):
        h, w = frame.shape[:2]
        sh = max(60, round(self.W * h / w))
        small = cv2.resize(frame, (self.W, sh), interpolation=cv2.INTER_AREA)
        self.small = small
        mask = np.zeros((sh, self.W), np.uint8)
        if colors:
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV_FULL).astype(np.float32)
            hue, s, v = hsv[..., 0] * (360 / 256), hsv[..., 1] / 255, hsv[..., 2] / 255
            valid = (v >= 40 / 255) & (s * v * 255 >= 22)
            for k, c in enumerate(colors):
                dh = np.abs(hue - c['h'])
                dh = np.minimum(dh, 360 - dh)
                m = valid & (dh <= c['tol']) & (s >= c['sMin']) & (v >= c['vMin']) & (mask == 0)
                mask[m] = k + 1
        self.mask = mask
        blobs = []
        for k in range(len(colors)):
            n, _, stats, cent = cv2.connectedComponentsWithStats((mask == k + 1).astype(np.uint8), connectivity=4)
            for j in range(1, n):
                a = int(stats[j, cv2.CC_STAT_AREA])
                if a >= 3:
                    blobs.append({'k': k, 'area': a, 'x': (cent[j][0] + 0.5) / self.W, 'y': (cent[j][1] + 0.5) / sh})
        blobs.sort(key=lambda b: -b['area'])
        if len(colors) == 2:  # у каждой палочки свой цвет
            out = []
            for k in range(2):
                b = next((b for b in blobs if b['k'] == k), None)
                if b:
                    out.append({'x': b['x'], 'y': b['y'], 'key': k, 'scale': COLOR_SCALE})
            return out
        if not blobs:
            return []
        min_a = max(3, blobs[0]['area'] * 0.25)
        return [{'x': b['x'], 'y': b['y'], 'scale': COLOR_SCALE} for b in blobs if b['area'] >= min_a][:2]

    def calibrate(self, u, v):
        if self.small is None:
            return None
        sh, sw = self.small.shape[:2]
        cx, cy = int(u * sw), int(v * sh)
        best, bs = None, -1.0
        for y in range(cy - 3, cy + 4):
            for x in range(cx - 3, cx + 4):
                if 0 <= x < sw and 0 <= y < sh:
                    b, g, r = (int(c) for c in self.small[y, x])
                    mx, mn = max(r, g, b), min(r, g, b)
                    s, val = ((mx - mn) / mx if mx else 0.0), mx / 255
                    if s * val > bs:
                        hh = 0.0
                        if mx != mn:
                            d = mx - mn
                            hh = ((g - b) / d) if mx == r else ((b - r) / d + 2) if mx == g else ((r - g) / d + 4)
                            hh = (hh * 60) % 360
                        bs, best = s * val, {'h': hh, 's': s, 'v': val, 'rgb': [r, g, b]}
        if not best or best['s'] < 0.3 or best['v'] < 0.2:
            return None
        best.update(tol=16, sMin=max(0.3, best['s'] * 0.55), vMin=max(0.2, best['v'] * 0.45))
        return best


# ============================================================ движок: кадр → удары

class Engine:
    def __init__(self, settings: dict, pads: list, player=None):
        self.s = settings
        self.pads = pads
        self.player = player
        self.hands = None
        self.color = ColorTracker()
        self.sticks = [Stick(0), Stick(1)]
        self.aspect = 16 / 9
        self.events = collections.deque(maxlen=128)
        self.hits_log = []
        self.hit_count = 0
        self.shift = False
        self.view_hands = []
        self.fps = 0.0
        self._frames = 0
        self._fps_t = time.perf_counter()
        self._reset = False
        self.cfg = make_cfg(settings['mode'], settings['sens'])
        self.det_ms = 0.0

    def refresh_cfg(self):
        self.cfg = make_cfg(self.s['mode'], self.s['sens'])

    def request_reset(self):
        self._reset = True

    def process(self, frame, t):
        if self._reset:
            self._reset = False
            for s in self.sticks:
                s.reset()
        h, w = frame.shape[:2]
        self.aspect = w / h
        t0 = time.perf_counter()
        hands = []
        if self.s['mode'] == 'hands':
            pts, hands = self.hands.process(frame, t) if self.hands else ([], [])
        else:
            pts = self.color.process(frame, self.s['colors'])
        self.det_ms = self.det_ms * 0.9 + (time.perf_counter() - t0) * 100
        self.view_hands = hands
        asg = assign(pts, self.sticks, self.aspect)
        c = self.cfg
        for i, s in enumerate(self.sticks):
            p = asg[i]
            hit = s.update(p['x'], p['y'], t, p.get('scale', COLOR_SCALE), self.aspect, c) if p else s.miss(t, c)
            if hit:
                self.on_stick_hit(hit, s, t)
        self._frames += 1
        now = time.perf_counter()
        if now - self._fps_t >= 0.5:
            self.fps = self._frames / (now - self._fps_t)
            self._frames, self._fps_t = 0, now

    def on_stick_hit(self, h, s, t):
        c = self.cfg
        vn = clamp((h['peak'] - c['vOn']) / (c['vOn'] * 2.2), 0, 1)
        vel = 0.35 + 0.65 * vn ** 0.8
        p, d = nearest_pad(self.pads, h['x'], h['y'], self.aspect)
        if p and d <= 4.0:
            self.trigger(p, vel, s.i, h['x'], h['y'], t)
        else:
            self.events.append(('miss', h['x'], h['y'], time.perf_counter()))

    def trigger(self, p, vel, stick=None, x=None, y=None, t=None):
        open_ = p['id'] == 'hihat' and self.shift
        if self.player:
            self.player(p['id'], vel, open_)
        now = time.perf_counter()
        p['hitT'] = now
        p['hitRgb'] = STICK_RGB[stick] if stick is not None else (255, 255, 255)
        self.hit_count += 1
        self.hits_log.append({'id': p['id'], 'vel': round(vel, 2), 'stick': stick,
                              't': round(t if t is not None else now, 3)})
        if len(self.hits_log) > 2000:
            del self.hits_log[:1000]
        if x is not None:
            self.events.append(('hit', x, y, now, p['hitRgb'], vel))


# ============================================================ камера

class Camera(threading.Thread):
    def __init__(self, source, width, height, fps):
        super().__init__(daemon=True)
        self.source, self.req = source, (width, height, fps)
        self.cond = threading.Condition()
        self.frame, self.t, self.seq = None, 0.0, 0
        self.error = None
        self.alive = True
        self.info = ''
        self.is_file = isinstance(source, str) and not source.isdigit()

    def _open(self):
        if self.is_file:
            return cv2.VideoCapture(self.source)
        idx = int(self.source)
        backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY] if os.name == 'nt' else [cv2.CAP_ANY]
        for be in backends:  # DirectShow на Windows открывает камеру за доли секунды
            cap = cv2.VideoCapture(idx, be)
            if cap.isOpened():
                w, h, fps = self.req
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
                cap.set(cv2.CAP_PROP_FPS, fps)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                return cap
            cap.release()
        return None

    def run(self):
        cap = None
        try:
            cap = self._open()
            if cap is None or not cap.isOpened():
                self.error = ('Камера не найдена или занята другой программой (Zoom, Teams, браузер). '
                              'Закройте их и запустите игру снова.')
                return
            fps = cap.get(cv2.CAP_PROP_FPS) or 30
            self.info = f'{int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}×{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}'
            log(f'Камера: {self.info} @ {fps:.0f} к/с')
            period = 1 / (fps if 1 < fps < 240 else 30)
            next_t = time.perf_counter()
            fails = 0
            while self.alive:
                ok, frame = cap.read()
                if not ok or frame is None:
                    if self.is_file:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                    fails += 1
                    if fails > 100:
                        self.error = 'Камера перестала отдавать кадры. Переподключите её и запустите игру снова.'
                        break
                    time.sleep(0.01)
                    continue
                fails = 0
                if self.is_file:  # видеофайл (для проверки) проигрываем в реальном темпе
                    next_t += period
                    time.sleep(max(0.0, next_t - time.perf_counter()))
                frame = cv2.flip(frame, 1)  # как в зеркале
                with self.cond:
                    self.frame, self.t = frame, time.perf_counter()
                    self.seq += 1
                    self.cond.notify_all()
        except Exception as e:
            self.error = f'Ошибка камеры: {e}'
            log(traceback.format_exc())
        finally:
            if cap is not None:
                cap.release()

    def wait(self, last_seq, timeout):
        with self.cond:
            if self.seq == last_seq:
                self.cond.wait(timeout)
            return self.seq, self.frame, self.t

    def latest(self):
        with self.cond:
            return self.seq, self.frame


# ============================================================ графика

def lerp(a, b, k):
    return tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(len(a)))


def grad(stops, k):
    k = clamp(k, 0, 1) * (len(stops) - 1)
    i = min(int(k), len(stops) - 2)
    return lerp(stops[i], stops[i + 1], k - i)


def ellipse_rect(cx, cy, rx, ry):
    return pygame.Rect(round(cx - rx), round(cy - ry), max(1, round(2 * rx)), max(1, round(2 * ry)))


def radial_ellipse(surf, cx, cy, rx, ry, inner, outer, n=26, shift=(-0.25, -0.35)):
    for i in range(n, 0, -1):
        f = i / n
        ox, oy = shift[0] * rx * (1 - f), shift[1] * ry * (1 - f)
        pygame.draw.ellipse(surf, lerp(inner, outer, f), ellipse_rect(cx + ox, cy + oy, rx * f, ry * f))


class PadArt:
    """Рисует барабаны один раз (с 2× сглаживанием) и кэширует картинки."""
    SS = 2

    def __init__(self):
        self.cache = {}

    def get(self, p, W):
        rx = max(8, int(p['r'] * W))
        key = (p['id'], rx)
        art = self.cache.get(key)
        if art is None:
            if len(self.cache) > 64:
                self.cache.clear()
            art = self.cache[key] = self._build(p, rx)
        return art

    def _build(self, p, rx_px):
        ss = self.SS
        rx = rx_px * ss
        typ = p['type']
        if typ == 'drum':
            ry, depth = int(rx * 0.42), int(rx * DRUM_DEPTH.get(p['id'], 0.42))
        elif typ == 'kick':
            ry, depth = rx, 0
        else:
            ry, depth = int(rx * 0.36), int(rx * 0.4) if typ == 'hihat' else 0
        pad = int(rx * 0.3) + 8
        W, H = 2 * rx + 2 * pad, 2 * ry + depth + 2 * pad
        cx, cy = W // 2, pad + ry
        s = pygame.Surface((W, H), pygame.SRCALPHA)
        head = pygame.Surface((W, H), pygame.SRCALPHA)  # форма «пластика» для вспышки
        if typ == 'drum':
            chrome = p['id'] == 'snare'
            stops = ([(59, 66, 80), (174, 182, 196), (242, 245, 250), (174, 182, 196), (59, 66, 80)] if chrome else
                     [(43, 10, 18), (141, 29, 51), (212, 72, 95), (141, 29, 51), (43, 10, 18)])
            for x in range(-rx, rx + 1):
                yy = ry * math.sqrt(max(0.0, 1 - (x / rx) ** 2))
                pygame.draw.line(s, grad(stops, (x + rx) / (2 * rx)), (cx + x, cy), (cx + x, cy + yy + depth))
            for a in (-0.72, -0.25, 0.25, 0.72):
                lx, ly = cx + rx * a, cy + ry * math.sqrt(1 - a * a)
                pygame.draw.line(s, (225, 230, 240), (lx, ly + depth * 0.15), (lx, ly + depth * 0.82), max(2, rx // 40))
            radial_ellipse(s, cx, cy, rx, ry, (255, 255, 255), (207, 202, 189))
            # мишень в центре пластика — сюда и бьём
            pygame.draw.ellipse(s, (214, 208, 194), ellipse_rect(cx, cy, rx * 0.4, ry * 0.4))
            pygame.draw.ellipse(s, (176, 170, 156), ellipse_rect(cx, cy, rx * 0.4, ry * 0.4), max(2, rx // 45))
            pygame.draw.ellipse(s, (150, 144, 130), ellipse_rect(cx, cy, rx * 0.09, ry * 0.09))
            pygame.draw.ellipse(s, (233, 237, 243), ellipse_rect(cx, cy, rx, ry), max(3, int(rx * 0.06)))
            self._spot(head, cx, cy, rx, ry)
        elif typ == 'kick':
            radial_ellipse(s, cx, cy, rx, rx, (141, 29, 51), (43, 10, 18), shift=(0, 0))
            radial_ellipse(s, cx, cy, rx * 0.86, rx * 0.86, (42, 47, 59), (13, 15, 20))
            pygame.draw.circle(s, (233, 237, 243), (cx, cy), int(rx * 0.88), max(3, int(rx * 0.05)))
            pygame.draw.circle(s, (120, 126, 140), (cx, cy), int(rx * 0.3), max(2, rx // 40))
            self._spot(head, cx, cy, rx * 0.86, rx * 0.86)
        else:
            if typ == 'hihat':
                self._cymbal(s, cx, cy + ry * 0.45 * 2, rx, ry, dark=True)
            self._cymbal(s, cx, cy, rx, ry, dark=False)
            self._spot(head, cx, cy, rx * 0.8, ry * 0.8)
        size = (W // ss, H // ss)
        base = pygame.transform.smoothscale(s, size)
        head = pygame.transform.smoothscale(head, size)
        return {'base': base, 'head': head, 'anchor': (cx // ss, cy // ss), 'rx': rx_px, 'ry': ry // ss,
                'depth': depth // ss, 'glow': {}, 'tint': {}}

    @staticmethod
    def _spot(surf, cx, cy, rx, ry, n=24):
        """Вспышка удара: ярче всего в центре и плавно гаснет к краю."""
        for i in range(n, 0, -1):
            f = i / n
            pygame.draw.ellipse(surf, (255, 255, 255, int(255 * (1 - f) ** 0.7)), ellipse_rect(cx, cy, rx * f, ry * f))

    @staticmethod
    def _cymbal(s, cx, cy, rx, ry, dark):
        inner, mid, outer = ((217, 195, 138), (179, 139, 53), (94, 64, 12)) if dark else \
            ((255, 244, 200), (233, 189, 87), (138, 95, 18))
        n = 30
        for i in range(n, 0, -1):
            f = i / n
            col = lerp(mid, outer, (f - 0.45) / 0.55) if f > 0.45 else lerp(inner, mid, f / 0.45)
            pygame.draw.ellipse(s, col, ellipse_rect(cx - 0.15 * rx * (1 - f), cy - 0.4 * ry * (1 - f), rx * f, ry * f))
        rings = pygame.Surface(s.get_size(), pygame.SRCALPHA)
        for k in (0.32, 0.55, 0.78):
            pygame.draw.ellipse(rings, (80, 50, 8, 90), ellipse_rect(cx, cy, rx * k, ry * k), max(2, rx // 90))
        pygame.draw.arc(rings, (255, 240, 200, 140), ellipse_rect(cx, cy, rx, ry), math.pi * 0.4, math.pi * 0.95,
                        max(2, rx // 70))
        s.blit(rings, (0, 0))
        radial_ellipse(s, cx, cy - ry * 0.05, rx * 0.16, ry * 0.22, (255, 248, 220), (201, 150, 46), n=10, shift=(0, -0.3))

    def glow(self, art, rgb):
        g = art['glow'].get(rgb)
        if g is None:
            base = art['base']
            pad = max(6, art['rx'] // 3)
            w, h = base.get_width() + 2 * pad, base.get_height() + 2 * pad
            g = pygame.Surface((w, h), pygame.SRCALPHA)
            scaled = pygame.transform.smoothscale(base, (int(base.get_width() * 1.12), int(base.get_height() * 1.25)))
            scaled.fill((255, 255, 255, 0), special_flags=pygame.BLEND_RGBA_MAX)
            g.blit(scaled, scaled.get_rect(center=(w // 2, pad + base.get_height() // 2)))
            try:
                g = pygame.transform.gaussian_blur(g, max(2, art['rx'] // 7))
            except Exception:  # старый pygame — без размытия
                pass
            g.fill(rgb + (255,), special_flags=pygame.BLEND_RGBA_MULT)
            art['glow'][rgb] = g
        return g

    def tint(self, art, rgb):
        t = art['tint'].get(rgb)
        if t is None:
            t = art['head'].copy()
            t.fill(rgb + (255,), special_flags=pygame.BLEND_RGBA_MULT)
            art['tint'][rgb] = t
        return t


HELP_LINES = [
    ('Как играть', True),
    ('Бейте по воздуху сверху вниз — звучит ближайший барабан, целиться не нужно.', False),
    ('Чем быстрее взмах, тем громче. Короткого резкого движения кистью достаточно.', False),
    ('', False),
    ('Клавиши', True),
    ('Пробел — бочка     Shift — открытый хай-хэт     1–7 — барабаны', False),
    ('Tab — режим: руки (ИИ) / цветные кончики', False),
    ('+ / − — чувствительность     ↑ / ↓ — громкость', False),
    ('M — метроном     ← / → — темп', False),
    ('E — расставить барабаны мышью (колёсико — размер), R — по умолчанию', False),
    ('F11 — во весь экран     H — эта подсказка     Esc — выход', False),
]


class App:
    def __init__(self, args):
        self.args = args
        self.s = load_settings()
        self.pads = load_pads(self.s['pads'])
        self._set_dpi_awareness()
        pygame.display.init()
        pygame.font.init()
        self.audio = Audio(self.s['audio_buffer'], self.s['volume'] / 100) if not args.no_audio else None
        self.engine = Engine(self.s, self.pads, self.audio.play if self.audio else None)
        self.metro_on = False
        self.metro_flash = 0.0
        self.alive = True
        self.status, self.status_err, self.status_until = '', False, 0.0
        self.show_help = True
        self.help_until = time.perf_counter() + 12
        self.editing = False
        self.drag = None
        self.art = PadArt()
        self.splashes = []
        self.frame_seq = -1
        self.frame_surf = None
        self._frame_buf = None
        self.model_state = 'idle'

        sizes = pygame.display.get_desktop_sizes() if hasattr(pygame.display, 'get_desktop_sizes') else [(1600, 900)]
        dw, dh = sizes[0] if sizes else (1600, 900)
        self.win_size = (max(800, int(dw * 0.85)), max(500, int(dh * 0.82)))
        pygame.display.set_caption(APP_NAME)
        try:
            pygame.display.set_icon(make_icon_surface(64))
        except Exception:
            pass
        self.fullscreen = bool(self.s['fullscreen'])
        self._set_mode()

        cam_src = args.camera if args.camera is not None else str(self.s['camera'])
        self.camera = Camera(cam_src, self.s['cam_width'], self.s['cam_height'], self.s['cam_fps'])
        self.camera.start()
        self.set_status('Включаю камеру…')
        threading.Thread(target=self._engine_loop, daemon=True).start()
        threading.Thread(target=self._metro_loop, daemon=True).start()
        if self.s['mode'] == 'hands':
            self.load_model()

    # ---------- служебное
    @staticmethod
    def _set_dpi_awareness():
        if os.name == 'nt':
            try:
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                try:
                    import ctypes
                    ctypes.windll.user32.SetProcessDPIAware()
                except Exception:
                    pass

    def _set_mode(self):
        if self.fullscreen:
            self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        else:
            self.screen = pygame.display.set_mode(self.win_size, pygame.RESIZABLE)
        self._fonts()

    def _fonts(self):
        H = self.screen.get_height()
        names = 'segoeui,arial,helvetica,dejavusans,liberationsans'
        self.f_label = pygame.font.SysFont(names, max(13, H // 48), bold=True)
        self.f_hud = pygame.font.SysFont(names, max(13, H // 54))
        self.f_status = pygame.font.SysFont(names, max(15, H // 42), bold=True)
        self.f_help = pygame.font.SysFont(names, max(14, H // 46))
        self.f_help_b = pygame.font.SysFont(names, max(16, H // 38), bold=True)
        self.label_cache = {}

    def set_status(self, text, secs=0.0, err=False):
        self.status, self.status_err = text, err
        self.status_until = time.perf_counter() + secs if secs else 0.0

    def save(self):
        self.s['pads'] = {p['id']: {'x': round(p['x'], 4), 'y': round(p['y'], 4), 'r': round(p['r'], 4)}
                          for p in self.pads}
        save_settings(self.s)

    def load_model(self):
        if self.model_state in ('loading', 'ready'):
            return
        self.model_state = 'loading'

        def work():
            try:
                path = None
                for d in (BASE_DIR, data_dir()):
                    if os.path.isfile(os.path.join(d, MODEL_NAME)):
                        path = os.path.join(d, MODEL_NAME)
                        break
                if path is None:
                    self.set_status('Скачиваю нейросеть для рук (≈8 МБ, один раз)…')
                    path = os.path.join(data_dir(), MODEL_NAME)
                    tmp = path + '.part'
                    urllib.request.urlretrieve(MODEL_URL, tmp)
                    os.replace(tmp, path)
                self.set_status('Загружаю нейросеть…')
                with open(path, 'rb') as f:
                    model = f.read()
                self.engine.hands = HandTracker(model)
                self.model_state = 'ready'
                self.set_status('Нейросеть готова — играйте!', 2.5)
                log('Нейросеть загружена')
            except Exception as e:
                self.model_state = 'error'
                log(traceback.format_exc())
                self.set_status('Не удалось загрузить нейросеть: проверьте интернет при первом запуске '
                                'или нажмите Tab для режима «цветные кончики».', 0, True)
                log(repr(e))

        threading.Thread(target=work, daemon=True).start()

    def _engine_loop(self):
        last = 0
        while self.alive:
            seq, frame, t = self.camera.wait(last, 0.1)
            if seq == last or frame is None:
                continue
            last = seq
            try:
                self.engine.process(frame, t)
            except Exception:
                log(traceback.format_exc())
                time.sleep(0.05)

    def _metro_loop(self):
        nxt, beat = 0.0, 0
        while self.alive:
            if not self.metro_on or not self.audio:
                nxt, beat = 0.0, 0
                time.sleep(0.01)
                continue
            now = time.perf_counter()
            if nxt == 0.0:
                nxt = now
            if now >= nxt:
                self.audio.click(beat % 4 == 0)
                self.metro_flash = now
                beat += 1
                nxt += 60 / self.s['bpm']
                if nxt < now:
                    nxt = now + 60 / self.s['bpm']
            time.sleep(min(0.002, max(0.0, nxt - time.perf_counter())))

    # ---------- геометрия сцены
    def stage(self):
        sw, sh = self.screen.get_size()
        aspect = self.engine.aspect
        w, h = sw, sw / aspect
        if h > sh:
            h, w = sh, sh * aspect
        return pygame.Rect(int((sw - w) / 2), int((sh - h) / 2), int(w), int(h))

    def to_norm(self, pos):
        st = self.stage()
        return (pos[0] - st.x) / st.w, (pos[1] - st.y) / st.h

    # ---------- ввод
    def handle(self, ev):
        if ev.type == pygame.QUIT:
            self.alive = False
        elif ev.type == pygame.VIDEORESIZE and not self.fullscreen:
            self.win_size = (max(640, ev.w), max(400, ev.h))
            self._fonts()
        elif ev.type == pygame.KEYDOWN:
            self.on_key(ev)
        elif ev.type == pygame.MOUSEBUTTONDOWN:
            self.on_mouse_down(ev)
        elif ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
            if self.drag:
                self.drag = None
                self.save()
        elif ev.type == pygame.MOUSEMOTION and self.drag:
            u, v = self.to_norm(ev.pos)
            p, dx, dy = self.drag
            p['x'], p['y'] = clamp(u - dx, 0.03, 0.97), clamp(v - dy, 0.05, 0.98)
        elif ev.type == pygame.MOUSEWHEEL and self.editing:
            u, v = self.to_norm(pygame.mouse.get_pos())
            p, d = nearest_pad(self.pads, u, v, self.engine.aspect, sticks=False)
            if p and d < 1.3:
                p['r'] = clamp(p['r'] * (1.06 if ev.y > 0 else 0.94), 0.04, 0.22)
                self.save()

    KEY_PADS = {pygame.K_1: 'crash', pygame.K_2: 'hihat', pygame.K_3: 'snare', pygame.K_4: 'tom1',
                pygame.K_5: 'tom2', pygame.K_6: 'floor', pygame.K_7: 'ride', pygame.K_SPACE: 'kick', pygame.K_b: 'kick',
                pygame.K_KP1: 'crash', pygame.K_KP2: 'hihat', pygame.K_KP3: 'snare', pygame.K_KP4: 'tom1',
                pygame.K_KP5: 'tom2', pygame.K_KP6: 'floor', pygame.K_KP7: 'ride', pygame.K_KP0: 'kick'}

    def on_key(self, ev):
        k = ev.key
        self.engine.shift = bool(pygame.key.get_mods() & pygame.KMOD_SHIFT)
        if k in self.KEY_PADS:
            p = next(pp for pp in self.pads if pp['id'] == self.KEY_PADS[k])
            self.engine.trigger(p, 0.9)
            return
        if k == pygame.K_ESCAPE:
            if self.editing:
                self.editing = False
                self.save()
            elif self.show_help:
                self.show_help = False
            else:
                self.alive = False
        elif k in (pygame.K_h, pygame.K_F1):
            self.show_help = not self.show_help
            self.help_until = 0
        elif k in (pygame.K_F11, pygame.K_f):
            self.fullscreen = not self.fullscreen
            self.s['fullscreen'] = self.fullscreen
            self._set_mode()
            self.save()
        elif k == pygame.K_TAB:
            self.set_mode('color' if self.s['mode'] == 'hands' else 'hands')
        elif k in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
            self.change_sens(+1)
        elif k in (pygame.K_MINUS, pygame.K_KP_MINUS):
            self.change_sens(-1)
        elif k in (pygame.K_UP, pygame.K_DOWN):
            self.s['volume'] = int(clamp(self.s['volume'] + (5 if k == pygame.K_UP else -5), 0, 100))
            if self.audio:
                self.audio.volume = self.s['volume'] / 100
            self.set_status(f'Громкость: {self.s["volume"]}%', 1.2)
            self.save()
        elif k == pygame.K_m:
            self.metro_on = not self.metro_on
            self.set_status(f'Метроном {"включён" if self.metro_on else "выключен"}: {self.s["bpm"]} BPM', 1.5)
        elif k in (pygame.K_LEFT, pygame.K_RIGHT):
            self.s['bpm'] = int(clamp(self.s['bpm'] + (5 if k == pygame.K_RIGHT else -5), 40, 240))
            self.set_status(f'Темп: {self.s["bpm"]} BPM', 1.2)
            self.save()
        elif k == pygame.K_e:
            self.editing = not self.editing
            if not self.editing:
                self.save()
        elif k == pygame.K_r and self.editing:
            self.s['pads'] = {}
            fresh = load_pads({})
            for p, f in zip(self.pads, fresh):
                p.update(x=f['x'], y=f['y'], r=f['r'])
            self.save()

    def change_sens(self, d):
        self.s['sens'] = int(clamp(self.s['sens'] + d, 1, 10))
        self.engine.refresh_cfg()
        self.set_status(f'Чувствительность: {self.s["sens"]} из 10', 1.2)
        self.save()

    def set_mode(self, m):
        self.s['mode'] = m
        self.engine.refresh_cfg()
        self.engine.request_reset()
        if m == 'hands':
            self.load_model()
            self.set_status('Режим: руки (ИИ)', 1.5)
        else:
            self.set_status('Режим: цветные кончики. Кликните по яркому кончику палочки на видео.'
                            if not self.s['colors'] else 'Режим: цветные кончики', 3)
        self.save()

    def on_mouse_down(self, ev):
        if self.show_help:
            self.show_help = False
        u, v = self.to_norm(ev.pos)
        if self.editing and ev.button == 1:
            p, d = nearest_pad(self.pads, u, v, self.engine.aspect, sticks=False)
            if p and d < 1.2:
                self.drag = (p, u - p['x'], v - p['y'])
            return
        if self.s['mode'] == 'color':
            if ev.button == 3:
                self.s['colors'] = []
                self.engine.request_reset()
                self.set_status('Цвета сброшены. Кликните по кончику палочки.', 2.5)
                self.save()
            elif ev.button == 1 and 0 <= u <= 1 and 0 <= v <= 1:
                col = self.engine.color.calibrate(u, v)
                if not col:
                    self.set_status('Слишком блёклый цвет. Нужен яркий кончик: изолента, колпачок, маркер.', 3.5, True)
                    return
                self.s['colors'] = [col] if len(self.s['colors']) >= 2 else self.s['colors'] + [col]
                self.engine.request_reset()
                self.set_status('Цвет запомнен. Палочка другого цвета? Кликните и по ней.' if len(self.s['colors']) == 1
                                else 'Оба цвета запомнены — играйте!', 3)
                self.save()
            return
        if ev.button == 1:
            p, d = nearest_pad(self.pads, u, v, self.engine.aspect, sticks=False)
            if p and d < 1.0:
                self.engine.trigger(p, 0.85, None, u, v)

    # ---------- отрисовка
    def draw(self):
        scr = self.screen
        scr.fill((7, 9, 13))
        st = self.stage()
        now = time.perf_counter()
        seq, frame = self.camera.latest()
        if frame is not None and seq != self.frame_seq:
            self.frame_seq = seq
            img = cv2.resize(frame, (st.w, st.h), interpolation=cv2.INTER_LINEAR)
            img = cv2.convertScaleAbs(img, alpha=0.62)
            self._frame_buf = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            self.frame_surf = pygame.image.frombuffer(self._frame_buf.data, (st.w, st.h), 'RGB')
        if self.frame_surf is not None and self.frame_surf.get_size() == st.size:
            scr.blit(self.frame_surf, st.topleft)
        else:
            pygame.draw.rect(scr, (20, 26, 38), st)

        W, H = st.w, st.h
        order = sorted(self.pads, key=lambda p: p['y'])
        for p in order:  # стойки тарелок
            if p['type'] in ('cymbal', 'hihat'):
                art = self.art.get(p, W)
                cx, cy = st.x + p['x'] * W, st.y + p['y'] * H
                pygame.draw.line(scr, (95, 100, 110), (cx, cy + art['ry'] + art['depth']),
                                 (cx, min(st.bottom, cy + art['rx'] * 1.15)), max(2, W // 500))
        for p in order:
            art = self.art.get(p, W)
            flash = clamp(1 - (now - p['hitT']) / 0.26, 0, 1)
            ax, ay = art['anchor']
            pos = (round(st.x + p['x'] * W - ax), round(st.y + p['y'] * H - ay))
            if flash > 0:
                g = self.art.glow(art, p['hitRgb'])
                g.set_alpha(int(255 * flash))
                scr.blit(g, g.get_rect(center=(pos[0] + art['base'].get_width() // 2,
                                               pos[1] + art['base'].get_height() // 2)))
            base = art['base']
            base.set_alpha(int(175 + 80 * flash))
            if p['type'] in ('cymbal', 'hihat') and flash > 0:
                ang = math.sin((now - p['hitT']) * 38) * 4 * flash
                rot = pygame.transform.rotate(base, ang)
                scr.blit(rot, rot.get_rect(center=(pos[0] + base.get_width() // 2, pos[1] + base.get_height() // 2)))
            else:
                scr.blit(base, pos)
            if flash > 0:
                t = self.art.tint(art, p['hitRgb'])
                t.set_alpha(int(255 * flash))
                scr.blit(t, pos)
            if self.editing:
                rx = art['rx']
                ry = rx if p['type'] == 'kick' else rx * 0.85
                pygame.draw.ellipse(scr, (56, 225, 255), ellipse_rect(st.x + p['x'] * W, st.y + p['y'] * H, rx, ry), 2)
        for p in order:
            self.draw_label(p, st)

        ov = pygame.Surface(st.size, pygame.SRCALPHA)
        hands = self.engine.view_hands
        conns = self.engine.hands.connections if self.engine.hands else []
        for hnd in hands:
            for a, b in conns:
                pygame.draw.line(ov, (255, 255, 255, 70), (hnd[a][0] * W, hnd[a][1] * H), (hnd[b][0] * W, hnd[b][1] * H), 2)
        for s in self.engine.sticks:
            if not s.active or now - s.last_seen > 0.4 or not s.trail:
                continue
            pts = list(s.trail)
            n = len(pts)
            for i in range(1, n):
                a, b = pts[i - 1], pts[i]
                pygame.draw.line(ov, s.rgb + (int(150 * i / n),), (a[0] * W, a[1] * H), (b[0] * W, b[1] * H),
                                 max(2, int((2 + 5 * i / n) * W / 1280)))
            cx, cy = s.x * W, s.y * H
            r = max(7, W // 110)
            pygame.draw.circle(ov, s.rgb + (90,), (cx, cy), r * 2)
            pygame.draw.circle(ov, (255, 255, 255, 255) if s.striking else s.rgb + (255,), (cx, cy), r)
            pygame.draw.circle(ov, (255, 255, 255, 230), (cx, cy), r, 2)
        while self.engine.events:
            e = self.engine.events.popleft()
            if e[0] == 'miss':
                self.splashes.append((e[1], e[2], e[3], (150, 160, 175), 0.5))
        keep = []
        for x, y, t0, rgb, size in self.splashes:
            k = (now - t0) / 0.38
            if k < 1:
                keep.append((x, y, t0, rgb, size))
                pygame.draw.circle(ov, rgb + (int(255 * (1 - k)),), (x * W, y * H),
                                   (14 + 80 * k) * size * W / 1280, max(1, int(4 - 3 * k)))
        self.splashes = keep
        scr.blit(ov, st.topleft)

        if self.s['mode'] == 'color' and self.engine.color.mask is not None:
            self.draw_mask(st)
        self.draw_hud(st, now)
        if self.show_help:
            self.draw_help()

    def draw_label(self, p, st):
        W, H = st.w, st.h
        key = (p['id'], self.f_label.get_height())
        surf = self.label_cache.get(key)
        if surf is None:
            text = f'{p["name"]} · {p["key"]}'
            fg = self.f_label.render(text, True, (255, 255, 255))
            bg = self.f_label.render(text, True, (0, 0, 0))
            surf = pygame.Surface((fg.get_width() + 4, fg.get_height() + 4), pygame.SRCALPHA)
            for dx, dy in ((0, 0), (4, 0), (0, 4), (4, 4), (2, 0), (0, 2), (4, 2), (2, 4)):
                surf.blit(bg, (dx, dy))
            surf.blit(fg, (2, 2))
            self.label_cache[key] = surf
        art = self.art.get(p, W)
        cx, cy = st.x + p['x'] * W, st.y + p['y'] * H
        if p['type'] == 'kick':
            y = cy
        elif p['type'] == 'drum':
            y = cy + art['ry'] + art['depth'] + surf.get_height() * 0.75
        else:
            y = cy + art['ry'] + art['depth'] * (0.9 if p['type'] == 'hihat' else 0) + surf.get_height() * 0.8
        self.screen.blit(surf, surf.get_rect(center=(cx, y)))

    def draw_mask(self, st):
        m = self.engine.color.mask
        small = self.engine.color.small
        if small is None or m.shape != small.shape[:2]:
            return
        gray = (cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) // 3).astype(np.uint8)
        rgb = np.dstack([gray, gray, gray])
        for k, c in enumerate(self.s['colors']):
            rgb[m == k + 1] = c['rgb']
        tw = min(220, st.w // 5)
        th = int(tw * m.shape[0] / m.shape[1])
        img = np.ascontiguousarray(cv2.resize(rgb, (tw, th), interpolation=cv2.INTER_NEAREST))
        surf = pygame.image.frombuffer(img.data, (tw, th), 'RGB').copy()
        r = pygame.Rect(st.right - tw - 12, st.bottom - th - 12, tw, th)
        self.screen.blit(surf, r)
        pygame.draw.rect(self.screen, (120, 130, 145), r, 1)

    def text(self, font, s, color, pos, anchor='topleft'):
        surf = font.render(s, True, color)
        r = surf.get_rect(**{anchor: pos})
        self.screen.blit(surf, r)
        return r

    def draw_hud(self, st, now):
        e = self.engine
        mode = 'Руки (ИИ)' if self.s['mode'] == 'hands' else 'Цветные кончики'
        if self.s['mode'] == 'hands' and self.model_state != 'ready':
            mode += ' — загрузка'
        lines = [f'{mode} · {e.fps:.0f} к/с · удары: {e.hit_count}',
                 f'Чувствительность {self.s["sens"]}/10 · громкость {self.s["volume"]}% · '
                 f'метроном {"вкл" if self.metro_on else "выкл"} ({self.s["bpm"]})   H — помощь']
        y = st.y + 10
        for i, ln in enumerate(lines):
            surf = self.f_hud.render(ln, True, (230, 236, 245) if i == 0 else (165, 176, 192))
            bg = pygame.Surface((surf.get_width() + 16, surf.get_height() + 6), pygame.SRCALPHA)
            bg.fill((8, 11, 17, 170))
            self.screen.blit(bg, (st.x + 10, y))
            self.screen.blit(surf, (st.x + 18, y + 3))
            y += bg.get_height() + 2
        if self.metro_on and now - self.metro_flash < 0.09:
            pygame.draw.circle(self.screen, (242, 193, 78), (st.right - 24, st.y + 24), 9)

        msg, err = self.status, self.status_err
        if self.status_until and now > self.status_until:
            self.status = msg = ''
        if not msg:
            if self.camera.error:
                msg, err = self.camera.error, True
            elif self.camera.seq == 0:
                msg = 'Включаю камеру…'
            elif self.editing:
                msg = 'Перетаскивайте барабаны мышью, колёсико — размер. E — готово, R — по умолчанию.'
            elif self.s['mode'] == 'color' and not self.s['colors']:
                msg = 'Кликните по яркому кончику палочки на видео — я запомню его цвет.'
            elif self.s['mode'] == 'hands' and self.model_state == 'ready' and not e.view_hands:
                msg = 'Покажите руки в кадре.'
        if msg:
            surf = self.f_status.render(msg, True, (255, 210, 204) if err else (240, 244, 250))
            if surf.get_width() > st.w - 40:
                surf = pygame.transform.smoothscale(surf, (st.w - 40, int(surf.get_height() * (st.w - 40) / surf.get_width())))
            bg = pygame.Surface((surf.get_width() + 32, surf.get_height() + 14), pygame.SRCALPHA)
            bg.fill((10, 13, 19, 215))
            r = bg.get_rect(midbottom=(st.centerx, st.bottom - 16))
            self.screen.blit(bg, r)
            self.screen.blit(surf, surf.get_rect(center=r.center))

    def draw_help(self):
        sw, sh = self.screen.get_size()
        rows = []
        for s, bold in HELP_LINES:
            rows.append((self.f_help_b if bold else self.f_help).render(s, True, (240, 244, 250) if bold else (205, 214, 226)) if s else None)
        lh = self.f_help.get_height()
        w = max(r.get_width() for r in rows if r) + 64
        h = sum((r.get_height() + 6) if r else lh // 2 for r in rows) + 64 + lh
        panel = pygame.Surface((w, h), pygame.SRCALPHA)
        panel.fill((10, 13, 19, 228))
        pygame.draw.rect(panel, (60, 70, 86, 255), panel.get_rect(), 1, border_radius=12)
        y = 32
        for r in rows:
            if r is None:
                y += lh // 2
                continue
            panel.blit(r, (32, y))
            y += r.get_height() + 6
        hint = self.f_hud.render('Нажмите любую клавишу или кликните, чтобы закрыть', True, (140, 152, 170))
        panel.blit(hint, (32, h - 32 - hint.get_height() // 2))
        self.screen.blit(panel, panel.get_rect(center=(sw // 2, sh // 2)))

    # ---------- цикл
    def run(self):
        clock = pygame.time.Clock()
        t_start = time.perf_counter()
        try:
            while self.alive:
                for ev in pygame.event.get():
                    if ev.type == pygame.KEYDOWN and self.show_help and ev.key not in (pygame.K_h, pygame.K_F1, pygame.K_ESCAPE):
                        self.show_help = False
                    self.handle(ev)
                self.engine.shift = bool(pygame.key.get_mods() & pygame.KMOD_SHIFT)
                if self.help_until and time.perf_counter() > self.help_until:
                    self.show_help, self.help_until = False, 0
                if self.camera.seq and self.status == 'Включаю камеру…':
                    self.status = ''
                self.draw()
                pygame.display.flip()
                clock.tick(60)
                if self.args.quit_after and time.perf_counter() - t_start > self.args.quit_after:
                    if self.args.screenshot:
                        pygame.image.save(self.screen, self.args.screenshot)
                    break
        finally:
            self.alive = False
            self.camera.alive = False
            self.save()
            if self.args.stats:
                with open(self.args.stats, 'w', encoding='utf-8') as f:
                    json.dump({'hits': self.engine.hits_log, 'fps': self.engine.fps, 'det_ms': self.engine.det_ms,
                               'camera': self.camera.info, 'model': self.model_state}, f, ensure_ascii=False)
            if self.engine.hands:
                self.engine.hands.close()
            pygame.quit()


# ============================================================ значок и ярлык

def make_icon_surface(size):
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    c = size // 2
    pygame.draw.circle(s, (43, 10, 18), (c, c), c - 1)
    pygame.draw.circle(s, (212, 72, 95), (c, c), c - 3)
    pygame.draw.circle(s, (245, 243, 236), (c, c), int(c * 0.72))
    pygame.draw.line(s, (56, 225, 255), (int(size * 0.18), int(size * 0.12)), (int(size * 0.62), int(size * 0.6)), max(2, size // 12))
    pygame.draw.line(s, (255, 95, 162), (int(size * 0.82), int(size * 0.12)), (int(size * 0.38), int(size * 0.6)), max(2, size // 12))
    return s


def write_ico(path, size=256):
    """ICO с PNG внутри (Windows Vista+)."""
    import io
    import struct
    pygame.display.init()
    buf = io.BytesIO()
    pygame.image.save(make_icon_surface(size), buf, 'icon.png')
    png = buf.getvalue()
    with open(path, 'wb') as f:
        f.write(struct.pack('<HHH', 0, 1, 1))
        f.write(struct.pack('<BBBBHHII', 0, 0, 0, 0, 1, 32, len(png), 6 + 16))
        f.write(png)


def make_shortcut():
    if os.name != 'nt':
        print('Ярлык создаётся только в Windows.')
        return
    import subprocess
    if getattr(sys, 'frozen', False):  # собранный AirDrums.exe
        target, arguments, workdir = sys.executable, '', os.path.dirname(sys.executable)
    else:
        target = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
        if not os.path.isfile(target):
            target = sys.executable
        script = os.path.abspath(__file__)
        arguments, workdir = f'"{script}"', os.path.dirname(script)
    ico = os.path.join(data_dir(), 'air_drums.ico')
    try:
        write_ico(ico)
    except Exception as e:
        log(f'Значок не создан: {e}')

    def q(s):
        return s.replace("'", "''")

    ps = ("$d=[Environment]::GetFolderPath('Desktop');"
          f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d '{q(APP_NAME)}.lnk'));"
          f"$s.TargetPath='{q(target)}';$s.Arguments='{q(arguments)}';"
          f"$s.WorkingDirectory='{q(workdir)}';$s.IconLocation='{q(ico)}';"
          f"$s.Description='{q(APP_NAME)}';$s.Save()")
    r = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', ps],
                       capture_output=True, text=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    log('Ярлык на рабочем столе создан.' if r.returncode == 0 else 'Не удалось создать ярлык: ' + r.stderr)
    return r.returncode == 0


def show_fatal(msg):
    log(msg)
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, msg[-1500:], APP_NAME, 0x10)
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(description=APP_NAME)
    ap.add_argument('--camera', help='номер камеры (0, 1, …) или путь к видеофайлу')
    ap.add_argument('--make-shortcut', action='store_true', help='создать ярлык на рабочем столе (Windows)')
    ap.add_argument('--no-audio', action='store_true')
    ap.add_argument('--quit-after', type=float, default=0, help=argparse.SUPPRESS)
    ap.add_argument('--screenshot', help=argparse.SUPPRESS)
    ap.add_argument('--stats', help=argparse.SUPPRESS)
    ap.add_argument('--write-ico', help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.make_shortcut:
        make_shortcut()
        return
    if args.write_ico:
        write_ico(args.write_ico)
        return
    if getattr(sys, 'frozen', False) and os.name == 'nt':  # .exe: при первом запуске кладём ярлык на рабочий стол
        st = load_settings()
        if not st.get('shortcut_done'):
            st['shortcut_done'] = True
            save_settings(st)
            threading.Thread(target=make_shortcut, daemon=True).start()
    try:
        App(args).run()
    except Exception:
        show_fatal('Игра остановилась из-за ошибки. Подробности — в файле\n' + LOG_PATH + '\n\n' + traceback.format_exc())
        raise


if __name__ == '__main__':
    main()
