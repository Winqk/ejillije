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

import kits  # noqa: E402
import sounds  # noqa: E402
import ui  # noqa: E402
from kits import PRESETS, PRESET_BY_ID, SKINS, STICK_RGB  # noqa: E402
from sounds import INSTRUMENTS, OPEN_HATS  # noqa: E402


def clamp(x, a, b):
    return a if x < a else b if x > b else x


# ============================================================ настройки

DEFAULTS = {
    'mode': 'hands',        # hands | color
    'sens': 6,              # 1..10
    'volume': 80,           # 0..100
    'bpm': 100,
    'beats': 4,             # долей в такте метронома
    'colors': [],
    'preset': 'rock',       # жанр
    'skin': None,           # None — стиль жанра
    'kits': {},             # своя расстановка для каждого жанра
    'bright_fingers': False,
    'video': 62,            # яркость видео, %
    'camera': 0,
    'cam_mode': None,       # режим, найденный подбором 60 к/с
    'cam_fps60': False,     # подбирать режим 60 к/с (по желанию, меню → Вид)
    'audio_buffer': 256,    # меньше — меньше задержка; если звук трещит — поставьте 512
    'fullscreen': False,
    'shortcut_done': False,
    'help_seen': False,
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
    s['beats'] = s['beats'] if s['beats'] in (2, 3, 4, 6) else 4
    s['video'] = int(clamp(int(s['video']), 10, 100))
    s['bright_fingers'] = bool(s['bright_fingers'])
    s['kits'] = s['kits'] if isinstance(s['kits'], dict) else {}
    return s


def save_settings(s: dict) -> None:
    try:
        tmp = SETTINGS_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(s, f, ensure_ascii=False, indent=1)
        os.replace(tmp, SETTINGS_PATH)
    except OSError as e:
        log(f'Не удалось сохранить настройки: {e}')



# ============================================================ детектор ударов

MODE_CFG = {'hands': {'vOn': 4.0, 'minTravel': 0.22}, 'color': {'vOn': 6.0, 'minTravel': 0.3}}
COLOR_SCALE = 0.12   # «условный размер» кончика, чтобы пороги совпадали по смыслу с режимом рук


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
        # «разогрев»: первый кадр нейросеть считает в разы дольше — делаем это при загрузке, а не во время игры
        self.lm.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.zeros((360, 640, 3), np.uint8)), 0)
        self.last_ts = 0

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



# ============================================================ камера

CAM_NORMAL = (1280, 720, 60)   # так камера включалась в версии, которая у всех работала
CAM_60 = [(1280, 720, 60), (640, 480, 60), (640, 360, 60)]


class Camera(threading.Thread):
    """Читает камеру в отдельном потоке.

    Открывает камеру бережно: сначала обычный режим, при неудаче — настройки камеры по умолчанию,
    другой способ подключения (DirectShow → Media Foundation) и другие камеры (0, 1, 2).
    Подбор режима 60 к/с включается только по желанию (меню → Вид)."""

    BACKENDS = ([('DirectShow', cv2.CAP_DSHOW), ('Media Foundation', cv2.CAP_MSMF), ('авто', cv2.CAP_ANY)]
                if os.name == 'nt' else [('авто', cv2.CAP_ANY)])

    def __init__(self, source, mode=None, probe=False, prev=None):
        super().__init__(daemon=True)
        self.source = source
        self.mode = tuple(mode) if mode and len(mode) == 3 else CAM_NORMAL
        self.probe = probe
        self.prev = prev
        self.cond = threading.Condition()
        self.frame, self.t, self.seq = None, 0.0, 0
        self.error = None
        self.alive = True
        self.info = ''
        self.index = None
        self.chosen = None          # режим, найденный подбором 60 к/с
        self.fps = 0.0              # реальная частота кадров камеры
        self.probing = False
        self.opening = True
        self.started = time.perf_counter()
        self.is_file = isinstance(source, str) and not source.isdigit()

    @staticmethod
    def _apply(cap, mode):
        w, h, f = mode
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        cap.set(cv2.CAP_PROP_FPS, f)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    def _try(self, idx, be, mode):
        """Открыть камеру и дождаться первого кадра. Возвращает (cap, причина неудачи)."""
        try:
            cap = cv2.VideoCapture(idx, be)
        except Exception as e:
            return None, repr(e)
        if not cap.isOpened():
            cap.release()
            return None, 'не открылась'
        if mode:
            self._apply(cap, mode)
        for _ in range(60):  # ~1,5 с на первый кадр
            if not self.alive:
                break
            ok, fr = cap.read()
            if ok and fr is not None and fr.size:
                return cap, None
            time.sleep(0.025)
        cap.release()
        return None, 'нет кадров'

    @staticmethod
    def _measure(cap, n=20):
        stamps = []
        for _ in range(n):
            ok, _f = cap.read()
            if not ok:
                return 0.0
            stamps.append(time.perf_counter())
        d = stamps[-1] - stamps[5]
        return (len(stamps) - 6) / d if d > 0 else 0.0

    def _open(self):
        if self.is_file:
            return cv2.VideoCapture(self.source)
        idx = int(self.source)
        for i in [idx] + [k for k in (0, 1, 2) if k != idx]:
            for bname, be in self.BACKENDS:
                for mode in (self.mode, None):  # сначала нужный режим, потом — как камера настроена сама
                    if not self.alive:
                        return None
                    cap, why = self._try(i, be, mode)
                    log(f'Камера {i}, {bname}, режим {mode or "по умолчанию"}: {"работает" if cap else why}')
                    if cap:
                        self.index, self.backend = i, be
                        if self.probe and mode:
                            cap = self._probe60(cap, i, be)
                        return cap
        return None

    def _probe60(self, cap, idx, be):
        """Подбор режима с наибольшей реальной частотой кадров: каждый режим — с переоткрытием камеры."""
        self.probing = True
        best_mode, best_fps = self.mode, self._measure(cap)
        log(f'Камера: {self.mode} → {best_fps:.0f} к/с')
        for mode in CAM_60:
            if best_fps >= 50 or not self.alive:
                break
            if mode == self.mode:
                continue
            cap.release()
            cap, why = self._try(idx, be, mode)
            fps = self._measure(cap) if cap else 0.0
            log(f'Камера: проба {mode} → {why or f"{fps:.0f} к/с"}')
            if cap:
                cap.release()
            if fps > best_fps + 5:
                best_mode, best_fps = mode, fps
            cap = None
        if cap is None:
            cap, why = self._try(idx, be, best_mode)
            if cap is None:  # на всякий случай возвращаемся к обычному режиму
                best_mode = self.mode
                cap, why = self._try(idx, be, best_mode)
        self.chosen = best_mode
        self.probing = False
        return cap

    def run(self):
        cap = None
        try:
            if self.prev is not None:  # дождаться, пока прежний поток отпустит камеру
                self.prev.alive = False
                self.prev.join(timeout=3)
            cap = self._open()
            self.opening = False
            if cap is None or not cap.isOpened():
                if self.alive:
                    self.error = ('Камера не включилась. Закройте программы, которые могут её занимать (Zoom, Teams, '
                                  'браузер, другую копию игры), и нажмите «Перезапустить камеру» в меню → Вид.')
                return
            file_fps = cap.get(cv2.CAP_PROP_FPS) or 30
            self.info = f'{int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}×{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}'
            log(f'Камера: {self.info}, камера №{self.index}')
            period = 1 / (file_fps if 1 < file_fps < 240 else 30)
            next_t = time.perf_counter()
            fails, n, t_fps = 0, 0, time.perf_counter()
            while self.alive:
                ok, frame = cap.read()
                if not ok or frame is None:
                    if self.is_file:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                    fails += 1
                    if fails > 100:
                        self.error = ('Камера перестала отдавать кадры. Нажмите «Перезапустить камеру» в меню → Вид '
                                      'или переподключите камеру.')
                        break
                    time.sleep(0.01)
                    continue
                fails = 0
                if self.is_file:  # видеофайл (для проверки) проигрываем в реальном темпе
                    next_t += period
                    time.sleep(max(0.0, next_t - time.perf_counter()))
                frame = cv2.flip(frame, 1)  # как в зеркале
                now = time.perf_counter()
                n += 1
                if now - t_fps >= 1.0:
                    self.fps, n, t_fps = n / (now - t_fps), 0, now
                with self.cond:
                    self.frame, self.t = frame, now
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


# ============================================================ движок: кадр → удары

class Engine:
    def __init__(self, settings: dict, pads: list, player=None):
        self.s = settings
        self.pads = pads
        self.player = player
        self.style = 'acoustic'
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
        p, d = kits.nearest_pad(self.pads, h['x'], h['y'], self.aspect, self.style)
        if p and d <= 4.0:
            self.trigger(p, vel, s.i, h['x'], h['y'], t)
        else:
            self.events.append(('miss', h['x'], h['y'], time.perf_counter()))

    def trigger(self, p, vel, stick=None, x=None, y=None, t=None):
        open_ = self.shift and p['inst'] in OPEN_HATS
        if self.player:
            self.player(p['inst'], vel, open_)
        now = time.perf_counter()
        p['hitT'] = now
        p['hitRgb'] = STICK_RGB[stick] if stick is not None else (255, 255, 255)
        self.hit_count += 1
        self.hits_log.append({'id': p['inst'], 'vel': round(vel, 2), 'stick': stick,
                              't': round(t if t is not None else now, 3)})
        if len(self.hits_log) > 2000:
            del self.hits_log[:1000]
        if x is not None:
            self.events.append(('hit', x, y, now, p['hitRgb'], vel))


HELP_LINES = [
    ('Как играть', True),
    ('Бейте по воздуху сверху вниз — звучит ближайший барабан, целиться не нужно.', False),
    ('Чем быстрее взмах, тем громче. Короткого резкого движения кистью достаточно.', False),
    ('', False),
    ('Клавиши', True),
    ('Esc — меню: жанры, барабаны, звук и метроном, вид', False),
    ('1–9 — барабаны     Пробел — бочка     Shift — открытый хай-хэт', False),
    ('E — расставить барабаны: тянуть — двигать, колёсико — размер, Shift + колёсико — поворот', False),
    ('P — следующий жанр     M — метроном     Tab — руки / цветные кончики', False),
    ('+ / − — чувствительность     ↑ / ↓ — громкость     F11 — во весь экран     H — подсказка', False),
]
FONT_NAMES = 'segoeui,arial,helvetica,dejavusans,liberationsans'


class App:
    def __init__(self, args):
        self.args = args
        self.s = load_settings()
        if self.s['preset'] not in PRESET_BY_ID:
            self.s['preset'] = PRESETS[0]['id']
        if self.s['skin'] not in SKINS:
            self.s['skin'] = None
        self._set_dpi_awareness()
        pygame.display.init()
        pygame.font.init()
        self._fonts = {}
        self.art = kits.PadArt(self.font)
        self.ui = ui.UI(self.font)
        self.menu = ui.Menu(self)
        self.editor = ui.Editor(self)
        self.audio = sounds.Audio(self.s['audio_buffer'], self.s['volume'] / 100, log) if not args.no_audio else None
        pads = self._kit_pads(self.s['preset'])
        self.engine = Engine(self.s, pads, self.audio.play if self.audio else None)
        self.engine.style = self.style
        if self.audio:
            self.audio.prioritize([p['inst'] for p in pads])
        self.metro_on = False
        self.metro_flash = 0.0
        self.metro_beat_i = 0
        self.alive = True
        self.status, self.status_err, self.status_until = '', False, 0.0
        self.show_help = not self.s['help_seen']
        self.help_until = time.perf_counter() + 20 if self.show_help else 0
        self.splashes = []
        self.frame_seq = -1
        self.frame_surf = None
        self._frame_buf = None
        self._ov = None
        self._neon = None
        self.labels = {}
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

        self.cam_src = args.camera if args.camera is not None else str(self.s['camera'])
        self.camera = None
        self.restart_camera()
        self._last_seq, self._last_seq_t, self._restart_t = 0, time.perf_counter(), 0.0
        self.set_status('Включаю камеру…')
        self._engine_thread = threading.Thread(target=self._engine_loop, daemon=True)
        self._engine_thread.start()
        threading.Thread(target=self._metro_loop, daemon=True).start()
        if self.s['mode'] == 'hands':
            self.load_model()

    # ---------- служебное
    def font(self, size, bold=False):
        key = (int(size), bool(bold))
        f = self._fonts.get(key)
        if f is None:
            f = self._fonts[key] = pygame.font.SysFont(FONT_NAMES, max(8, int(size)), bold=bold)
        return f

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
        self.labels.clear()

    def set_status(self, text, secs=0.0, err=False):
        self.status, self.status_err = text, err
        self.status_until = time.perf_counter() + secs if secs else 0.0

    def save(self):
        save_settings(self.s)

    # ---------- набор барабанов, пресеты, скины
    @property
    def skin_id(self):
        sk = self.s.get('skin')
        return sk if sk in SKINS else PRESET_BY_ID[self.s['preset']]['skin']

    @property
    def style(self):
        return SKINS[self.skin_id]['style']

    def _kit_pads(self, pid):
        return kits.pads_from_saved(self.s['kits'].get(pid)) or kits.preset_pads(pid)

    def _set_pads(self, pads):
        kits.assign_keys(pads)
        self.engine.pads = pads   # замена списка целиком — безопасно для потока распознавания
        self.engine.style = self.style
        self.labels.clear()
        if self.audio:
            self.audio.prioritize([p['inst'] for p in pads])

    def apply_preset(self, pid):
        if pid not in PRESET_BY_ID:
            return
        self.s['preset'] = pid
        self.s['skin'] = None          # у каждого жанра свой стиль
        self._set_pads(self._kit_pads(pid))
        self.editor.sel = None
        self.set_status(f'Жанр: {PRESET_BY_ID[pid]["name"]}', 1.5)
        self.save()

    def next_preset(self):
        ids = [p['id'] for p in PRESETS]
        self.apply_preset(ids[(ids.index(self.s['preset']) + 1) % len(ids)])

    def set_skin(self, sid):
        self.s['skin'] = sid
        self.engine.style = self.style
        self.labels.clear()
        self.save()

    def save_kit(self):
        kits.assign_keys(self.engine.pads)
        self.s['kits'][self.s['preset']] = kits.pads_to_saved(self.engine.pads)
        self.labels.clear()
        self.save()

    def reset_kit(self):
        self.s['kits'].pop(self.s['preset'], None)
        self._set_pads(kits.preset_pads(self.s['preset']))
        self.editor.sel = None
        self.save()

    def add_pad(self, inst):
        if len(self.engine.pads) >= kits.MAX_PADS:
            return
        p = kits.new_pad(inst, 0.5, 0.5, 0.085)
        self._set_pads(self.engine.pads + [p])
        self.save_kit()
        self.editor.start(p['uid'])

    def set_pad_inst(self, uid, inst):
        for p in self.engine.pads:
            if p['uid'] == uid:
                p['inst'] = inst
                if INSTRUMENTS[inst]['cat'] != 'kick':
                    p['direct'] = False
        if self.audio:
            self.audio.prioritize([inst])
        self.save_kit()

    def remove_pad(self, uid):
        if len(self.engine.pads) > 1:
            self._set_pads([p for p in self.engine.pads if p['uid'] != uid])
            self.save_kit()

    def audio_preview(self, inst):
        if self.audio:
            self.audio.play(inst, 0.8)

    # ---------- настройки
    def set_volume(self, v):
        self.s['volume'] = int(clamp(v, 0, 100))
        if self.audio:
            self.audio.volume = self.s['volume'] / 100
        self.save()

    def set_sens(self, v):
        self.s['sens'] = int(clamp(v, 1, 10))
        self.engine.refresh_cfg()
        self.save()

    def set_metro(self, on):
        self.metro_on = bool(on)

    def set_bpm(self, v):
        self.s['bpm'] = int(clamp(v, 40, 240))
        self.save()

    def set_beats(self, n):
        self.s['beats'] = int(n)
        self.save()

    def set_option(self, key, value):
        self.s[key] = value
        self.save()

    def toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen
        self.s['fullscreen'] = self.fullscreen
        self._set_mode()
        self.save()

    def set_mode(self, m):
        self.s['mode'] = m
        self.engine.refresh_cfg()
        self.engine.request_reset()
        if m == 'hands':
            self.load_model()
            self.set_status('Отслеживание: руки (ИИ)', 1.5)
        else:
            self.set_status('Цветные кончики: кликните по яркому кончику палочки на видео.'
                            if not self.s['colors'] else 'Отслеживание: цветные кончики', 3)
        self.save()

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
                                'или выберите «Цветные кончики» в меню.', 0, True)
                log(repr(e))

        threading.Thread(target=work, daemon=True).start()

    # ---------- потоки
    def _engine_loop(self):
        last = 0
        while self.alive:
            seq, frame, t = self.camera.wait(last, 0.1)
            if seq == last or frame is None:
                continue
            last = seq
            try:
                t0 = time.perf_counter()
                self.engine.process(frame, t)
                dt = time.perf_counter() - t0
                if dt > 0.15:
                    log(f'Медленная обработка кадра: {dt * 1000:.0f} мс')
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
                n = max(1, int(self.s['beats']))
                self.metro_beat_i = beat % n
                self.audio.click(self.metro_beat_i == 0)
                self.metro_flash = now
                beat += 1
                nxt += 60 / self.s['bpm']
                if nxt < now:
                    nxt = now + 60 / self.s['bpm']
            time.sleep(min(0.002, max(0.0, nxt - time.perf_counter())))

    def restart_camera(self, src=None):
        """(Пере)запуск камеры: новый поток сам дождётся, пока прежний отпустит устройство."""
        if src is not None:
            self.cam_src = str(src)
        fps60 = self.s['cam_fps60']
        mode = self.s['cam_mode'] if fps60 else None
        self.camera = Camera(self.cam_src, mode, probe=fps60 and not self.s['cam_mode'], prev=self.camera)
        self.camera.start()
        self._last_seq, self._last_seq_t, self._restart_t = 0, time.perf_counter(), time.perf_counter()
        self.frame_seq = -1
        self.frame_surf = None

    def set_fps60(self, on):
        self.s['cam_fps60'] = bool(on)
        self.s['cam_mode'] = None
        self.save()
        self.restart_camera()

    def watch_camera(self):
        """Запоминает найденный режим камеры и перезапускает камеру, если она перестала присылать кадры."""
        cam, now = self.camera, time.perf_counter()
        if cam.chosen and not cam.is_file and self.s['cam_fps60'] and self.s.get('cam_mode') != list(cam.chosen):
            self.s['cam_mode'] = list(cam.chosen)
            self.save()
        if cam.seq == 0 and not cam.is_file and not cam.error and now - cam.started > (40 if cam.opening else 15):
            # камера так и не прислала ни кадра (например, зависла при подборе 60 к/с) — возвращаемся к безопасному режиму
            log('Камера не прислала ни одного кадра за 15 с')
            if self.s['cam_fps60']:
                self.s['cam_fps60'], self.s['cam_mode'] = False, None
                self.save()
            cam.error = ('Камера не отвечает. Закройте программы, которые могут её занимать, и перезапустите игру. '
                         'Режим 60 к/с выключен.')
            return
        if cam.seq != self._last_seq:
            self._last_seq, self._last_seq_t = cam.seq, now
        elif (cam.seq > 0 and not cam.is_file and not cam.error and now - self._last_seq_t > 3
              and now - self._restart_t > 10):
            log('Камера не присылает кадры 3 с — перезапускаю её')
            self.set_status('Камера зависла — перезапускаю…', 3)
            cam.alive = False
            self._restart_t = self._last_seq_t = now
            self._last_seq = 0
            self.restart_camera()

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
            self.labels.clear()
        elif ev.type == pygame.KEYDOWN:
            self.on_key(ev)

    DIGITS = {getattr(pygame, f'K_{i}'): str(i) for i in range(1, 10)}
    DIGITS.update({getattr(pygame, f'K_KP{i}'): str(i) for i in range(1, 10)})

    def on_key(self, ev):
        k = ev.key
        self.engine.shift = bool(pygame.key.get_mods() & pygame.KMOD_SHIFT)
        if self.show_help and k not in (pygame.K_h, pygame.K_F1):
            self.hide_help()
            if k == pygame.K_ESCAPE:
                return
        # барабаны с клавиатуры работают всегда — даже в меню, чтобы проверять звук
        if k in self.DIGITS:
            p = next((pp for pp in self.engine.pads if pp['key'] == self.DIGITS[k]), None)
            if p:
                self.engine.trigger(p, 0.9)
            return
        if k in (pygame.K_SPACE, pygame.K_b, pygame.K_KP0):
            p = kits.kick_pad(self.engine.pads)
            if p:
                self.engine.trigger(p, 0.9)
            return
        if k == pygame.K_ESCAPE:
            if self.menu.open:
                if self.menu.picker:
                    self.menu.picker = None
                else:
                    self.menu.close()
            elif self.editor.active:
                self.editor.stop()
            else:
                self.menu.show()
            return
        if k in (pygame.K_F11,) or (k == pygame.K_f and not self.menu.open):
            self.toggle_fullscreen()
            return
        if self.menu.open:
            return
        if self.editor.active and self.editor.key(ev):
            return
        if k in (pygame.K_h, pygame.K_F1):
            self.show_help = not self.show_help
            self.help_until = 0
        elif k == pygame.K_TAB:
            self.set_mode('color' if self.s['mode'] == 'hands' else 'hands')
        elif k in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
            self.set_sens(self.s['sens'] + 1)
            self.set_status(f'Чувствительность: {self.s["sens"]} из 10', 1.2)
        elif k in (pygame.K_MINUS, pygame.K_KP_MINUS):
            self.set_sens(self.s['sens'] - 1)
            self.set_status(f'Чувствительность: {self.s["sens"]} из 10', 1.2)
        elif k in (pygame.K_UP, pygame.K_DOWN):
            self.set_volume(self.s['volume'] + (5 if k == pygame.K_UP else -5))
            self.set_status(f'Громкость: {self.s["volume"]}%', 1.2)
        elif k == pygame.K_m:
            self.set_metro(not self.metro_on)
            self.set_status(f'Метроном {"включён" if self.metro_on else "выключен"}: {self.s["bpm"]} BPM', 1.5)
        elif k in (pygame.K_LEFT, pygame.K_RIGHT):
            self.set_bpm(self.s['bpm'] + (5 if k == pygame.K_RIGHT else -5))
            self.set_status(f'Темп: {self.s["bpm"]} BPM', 1.2)
        elif k == pygame.K_e:
            if self.editor.active:
                self.editor.stop()
            else:
                self.editor.start()
        elif k == pygame.K_p:
            self.next_preset()

    def hide_help(self):
        self.show_help, self.help_until = False, 0
        if not self.s['help_seen']:
            self.s['help_seen'] = True
            self.save()

    def scene_input(self):
        ui_ = self.ui
        if self.menu.open:
            return
        st = self.stage()
        if self.editor.active:
            self.editor.scene(ui_, st)
            return
        if ui_.click and not ui_.consumed:
            u, v = self.to_norm(ui_.click)
            if self.s['mode'] == 'color' and 0 <= u <= 1 and 0 <= v <= 1:
                col = self.engine.color.calibrate(u, v)
                if not col:
                    self.set_status('Слишком блёклый цвет. Нужен яркий кончик: изолента, колпачок, маркер.', 3.5, True)
                else:
                    self.s['colors'] = [col] if len(self.s['colors']) >= 2 else self.s['colors'] + [col]
                    self.engine.request_reset()
                    self.set_status('Цвет запомнен. Палочка другого цвета? Кликните и по ней.'
                                    if len(self.s['colors']) == 1 else 'Оба цвета запомнены — играйте!', 3)
                    self.save()
            else:
                p, d = kits.nearest_pad(self.engine.pads, u, v, self.engine.aspect, self.style, sticks=False)
                if p and d < 1.0:
                    self.engine.trigger(p, 0.85, None, u, v)
        if ui_.rclick and self.s['mode'] == 'color':
            self.s['colors'] = []
            self.engine.request_reset()
            self.set_status('Цвета сброшены. Кликните по кончику палочки.', 2.5)
            self.save()

    # ---------- отрисовка
    def draw(self):
        scr = self.screen
        ui_ = self.ui
        S = ui_.S
        scr.fill((7, 9, 13))
        st = self.stage()
        now = time.perf_counter()
        seq, frame = self.camera.latest()
        if frame is not None and seq != self.frame_seq:
            self.frame_seq = seq
            fh, fw = frame.shape[:2]
            img = cv2.convertScaleAbs(frame, alpha=self.s['video'] / 100)
            if st.w < fw:
                img = cv2.resize(img, st.size, interpolation=cv2.INTER_AREA)
            self._frame_buf = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            bh, bw = self._frame_buf.shape[:2]
            src = pygame.image.frombuffer(self._frame_buf.data, (bw, bh), 'RGB')
            self.frame_surf = src if (bw, bh) == st.size else pygame.transform.scale(src, st.size)
        if self.frame_surf is not None and self.frame_surf.get_size() == st.size:
            scr.blit(self.frame_surf, st.topleft)
        else:
            top, bot = SKINS[self.skin_id]['bg']
            pygame.draw.rect(scr, bot, st)

        W, H = st.w, st.h
        skin, style = self.skin_id, self.style
        pads = self.engine.pads
        order = sorted(pads, key=lambda p: p['y'])
        if style == 'acoustic':  # стойки тарелок
            for p in order:
                if INSTRUMENTS[p['inst']]['type'] in ('cymbal', 'hihat') and not p['direct']:
                    art = self.art.get(skin, p['inst'], p['r'] * W, p['rot'])
                    cx, cy = st.x + p['x'] * W, st.y + p['y'] * H
                    pygame.draw.line(scr, (95, 100, 110), (cx, cy + art['below']),
                                     (cx, min(st.bottom, cy + art['rx'] * 1.15 + art['below'])), max(2, W // 500))
        for p in order:
            art = self.art.get(skin, p['inst'], p['r'] * W, p['rot'])
            flash = clamp(1 - (now - p['hitT']) / 0.26, 0, 1)
            ax, ay = art['anchor']
            bob = -math.sin((now - p['hitT']) * 30) * S(4) * flash if INSTRUMENTS[p['inst']]['type'] in ('cymbal', 'hihat') else 0
            pos = (round(st.x + p['x'] * W - ax), round(st.y + p['y'] * H - ay + bob))
            base = art['base']
            if flash > 0:
                g = art['glow'][p['hitRgb']]
                g.set_alpha(int(255 * flash))
                scr.blit(g, g.get_rect(center=(pos[0] + base.get_width() / 2, pos[1] + base.get_height() / 2)))
            base.set_alpha(int(185 + 70 * flash))
            scr.blit(base, pos)
            if flash > 0:
                t = art['tint'][p['hitRgb']]
                t.set_alpha(int(255 * flash))
                scr.blit(t, pos)
        for p in order:
            self.draw_label(p, st, skin)

        if self._ov is None or self._ov.get_size() != st.size:
            self._ov = pygame.Surface(st.size, pygame.SRCALPHA)
        ov = self._ov
        ov.fill((0, 0, 0, 0))
        self.draw_hands(ov, st)
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
        if self.editor.active:
            self.editor.draw_overlay(scr, st)
        self.draw_hud(st, now)
        if self.editor.active and not self.menu.open:
            self.editor.toolbar(scr, ui_)
        if self.show_help:
            self.draw_help()
            if ui_.click and not self.menu.open:
                self.hide_help()
                ui_.consumed = True
        if self.menu.open:
            self.menu.draw(scr, ui_)

    def draw_hands(self, ov, st):
        hands = self.engine.view_hands
        if not hands or not self.engine.hands:
            return
        W, H = st.w, st.h
        conns = self.engine.hands.connections
        if not self.s['bright_fingers']:
            for hnd in hands:
                for a, b in conns:
                    pygame.draw.line(ov, (255, 255, 255, 70), (hnd[a][0] * W, hnd[a][1] * H), (hnd[b][0] * W, hnd[b][1] * H), 2)
            return
        # «яркие пальцы»: неоновый скелет рук, цвет — как у палочки этой руки
        S = self.ui.S
        if self._neon is None or self._neon.get_size() != st.size:
            self._neon = pygame.Surface(st.size, pygame.SRCALPHA)
        neon = self._neon
        neon.fill((0, 0, 0, 0))
        for hnd in hands:
            kx = sum(hnd[i][0] for i in (5, 9, 13, 17)) / 4
            ky = sum(hnd[i][1] for i in (5, 9, 13, 17)) / 4
            act = [s for s in self.engine.sticks if s.active]
            col = min(act, key=lambda s: (s.x - kx) ** 2 + (s.y - ky) ** 2).rgb if act else STICK_RGB[0]
            P = [(x * W, y * H) for x, y in hnd]
            for a, b in conns:
                pygame.draw.line(neon, col + (255,), P[a], P[b], max(6, S(12)))
            for i in (4, 8, 12, 16, 20):
                pygame.draw.circle(neon, col + (255,), P[i], max(8, S(16)))
            for a, b in conns:
                pygame.draw.line(ov, kits.bright(col, 0.45) + (255,), P[a], P[b], max(2, S(4)))
            for i, pt in enumerate(P):
                big = i in (4, 8, 12, 16, 20)
                pygame.draw.circle(ov, (255, 255, 255, 255), pt, max(3, S(7 if big else 4)))
        self.screen.blit(kits.soft_blur(neon, 6), st.topleft, special_flags=pygame.BLEND_RGBA_ADD)

    def draw_label(self, p, st, skin):
        W, H = st.w, st.h
        info = INSTRUMENTS[p['inst']]
        size = max(13, H // 50)
        text = f'{info["name"]} · {p["key"]}' if p['key'] else info['name']
        key = (text, size)
        surf = self.labels.get(key)
        if surf is None:
            f = self.font(size, True)
            fg = f.render(text, True, (255, 255, 255))
            bg = f.render(text, True, (0, 0, 0))
            surf = pygame.Surface((fg.get_width() + 4, fg.get_height() + 4), pygame.SRCALPHA)
            for dx, dy in ((0, 0), (4, 0), (0, 4), (4, 4), (2, 0), (0, 2), (4, 2), (2, 4)):
                surf.blit(bg, (dx, dy))
            surf.blit(fg, (2, 2))
            self.labels[key] = surf
        art = self.art.get(skin, p['inst'], p['r'] * W, p['rot'])
        cx, cy = st.x + p['x'] * W, st.y + p['y'] * H
        if info['type'] == 'kick' and self.style != 'mpc':
            y = cy + art['rx'] * 0.08   # бочка стоит у края кадра — подпись внутри неё
        else:
            y = cy + art['below'] + surf.get_height() * 0.8
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
        r = pygame.Rect(st.right - tw - 12, st.bottom - th - 96, tw, th)
        self.screen.blit(surf, r)
        pygame.draw.rect(self.screen, (120, 130, 145), r, 1)

    def draw_hud(self, st, now):
        e = self.engine
        ui_ = self.ui
        S = ui_.S
        cam = self.camera
        mode = 'руки (ИИ)' if self.s['mode'] == 'hands' else 'цветные кончики'
        if self.s['mode'] == 'hands' and self.model_state != 'ready':
            mode += ', загрузка'
        cam_txt = f'камера {cam.fps:.0f} к/с' if cam.info else 'камера…'
        lines = [(f'{PRESET_BY_ID[self.s["preset"]]["name"]} · {mode} · {cam_txt} · ИИ {e.fps:.0f} к/с · удары: {e.hit_count}', C_TEXT),
                 (f'Чувствительность {self.s["sens"]}/10 · громкость {self.s["volume"]}% · '
                  f'метроном {"вкл" if self.metro_on else "выкл"} ({self.s["bpm"]})   H — помощь', C_HINT)]
        y = st.y + S(12)
        for txt, col in lines:
            surf = ui_.tsurf(txt, 13, col)
            bg = pygame.Surface((surf.get_width() + S(18), surf.get_height() + S(8)), pygame.SRCALPHA)
            bg.fill((8, 11, 17, 175))
            self.screen.blit(bg, (st.x + S(12), y))
            self.screen.blit(surf, (st.x + S(21), y + S(4)))
            y += bg.get_height() + S(3)
        # кнопка меню
        mb = pygame.Rect(st.right - S(176), st.y + S(12), S(162), S(42))
        if not self.menu.open:
            if ui_.button(self.screen, mb, 'Меню · Esc', icon='menu', size=15):
                self.menu.show()
        if self.metro_on and now - self.metro_flash < 0.09:
            pygame.draw.circle(self.screen, (242, 193, 78) if self.metro_beat_i == 0 else (56, 225, 255),
                               (mb.x - S(20), mb.centery), S(8))

        msg, err = self.status, self.status_err
        if self.status_until and now > self.status_until:
            self.status = msg = ''
        if not msg:
            if self.camera.error:
                msg, err = self.camera.error, True
            elif self.camera.seq == 0:
                msg = 'Подбираю режим камеры для 60 к/с…' if self.camera.probing \
                    else 'Включаю камеру…'
            elif self.editor.active:
                msg = ''
            elif self.s['mode'] == 'color' and not self.s['colors']:
                msg = 'Кликните по яркому кончику палочки на видео — я запомню его цвет.'
            elif self.s['mode'] == 'hands' and self.model_state == 'ready' and not e.view_hands:
                msg = 'Покажите руки в кадре.'
        if msg:
            surf = ui_.tsurf(msg, 16, (255, 210, 204) if err else (240, 244, 250), True)
            if surf.get_width() > st.w - 40:
                surf = pygame.transform.smoothscale(surf, (st.w - 40, int(surf.get_height() * (st.w - 40) / surf.get_width())))
            bg = pygame.Surface((surf.get_width() + S(32), surf.get_height() + S(14)), pygame.SRCALPHA)
            bg.fill((10, 13, 19, 215))
            r = bg.get_rect(midtop=(st.centerx, st.y + S(70)))
            self.screen.blit(bg, r)
            self.screen.blit(surf, surf.get_rect(center=r.center))

    def draw_help(self):
        ui_ = self.ui
        S = ui_.S
        sw, sh = self.screen.get_size()
        rows = [ui_.tsurf(s, 19 if bold else 15, C_TEXT if bold else (205, 214, 226), bold) if s else None
                for s, bold in HELP_LINES]
        lh = ui_.font(15).get_height()
        w = max(r.get_width() for r in rows if r) + S(72)
        h = sum((r.get_height() + S(7)) if r else lh // 2 for r in rows) + S(72) + lh
        panel = pygame.Rect(0, 0, w, h)
        panel.center = (sw // 2, sh // 2)
        self.screen.blit(ui_.shadow(w, h, S(18), S(30)), (panel.x - S(30), panel.y - S(30)))
        pygame.draw.rect(self.screen, (15, 19, 28), panel, border_radius=S(18))
        pygame.draw.rect(self.screen, (48, 58, 80), panel, 1, border_radius=S(18))
        y = panel.y + S(34)
        for r in rows:
            if r is None:
                y += lh // 2
                continue
            self.screen.blit(r, (panel.x + S(36), y))
            y += r.get_height() + S(7)
        ui_.text(self.screen, 'Нажмите любую клавишу или кликните, чтобы закрыть', 13, C_HINT,
                 (panel.x + S(36), panel.bottom - S(30)), 'midleft')

    # ---------- цикл
    def run(self):
        import gc
        gc.collect()
        gc.freeze()  # всё, что создано при запуске, сборщик мусора больше не перебирает — меньше пауз
        clock = pygame.time.Clock()
        t_start = time.perf_counter()
        try:
            while self.alive:
                events = pygame.event.get()
                for ev in events:
                    self.handle(ev)
                self.ui.begin(events, self.screen.get_height())
                self.engine.shift = bool(pygame.key.get_mods() & pygame.KMOD_SHIFT)
                if self.help_until and time.perf_counter() > self.help_until:
                    self.hide_help()
                if self.camera.seq and self.status == 'Включаю камеру…':
                    self.status = ''
                self.watch_camera()
                self.draw()
                self.scene_input()
                self.ui.end()
                pygame.display.flip()
                dt = clock.tick(60)
                if dt > 150 and time.perf_counter() - t_start > 3:
                    log(f'Рывок отрисовки: {dt} мс')
                if self.args.quit_after and time.perf_counter() - t_start > self.args.quit_after:
                    if self.args.screenshot:
                        pygame.image.save(self.screen, self.args.screenshot)
                    break
        finally:
            self.alive = False
            self.camera.alive = False
            self._engine_thread.join(timeout=1.0)  # дождаться кадра в нейросети, потом закрывать её
            self.save()
            if self.args.stats:
                with open(self.args.stats, 'w', encoding='utf-8') as f:
                    json.dump({'hits': self.engine.hits_log, 'fps': self.engine.fps, 'det_ms': self.engine.det_ms,
                               'camera': self.camera.info, 'cam_fps': self.camera.fps, 'model': self.model_state},
                              f, ensure_ascii=False)
            if self.engine.hands:
                self.engine.hands.close()
            pygame.quit()


C_TEXT = (236, 241, 248)
C_HINT = (150, 162, 180)


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
