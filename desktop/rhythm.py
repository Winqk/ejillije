# -*- coding: utf-8 -*-
"""Ритм-игра: кольца летят к барабанам, бить нужно в такт. Режимы «клавиатура» и «руки»."""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time

import numpy as np
import pygame

import kits
from sounds import INSTRUMENTS, Synth

LANES = ['kick', 'snare', 'hihat', 'tom1', 'tom2', 'floor', 'crash', 'ride']
LANE_KEYS = {pygame.K_SPACE: 'kick', pygame.K_b: 'kick', pygame.K_d: 'snare', pygame.K_s: 'hihat', pygame.K_f: 'tom1',
             pygame.K_j: 'tom2', pygame.K_k: 'floor', pygame.K_e: 'crash', pygame.K_i: 'crash', pygame.K_l: 'ride'}
KEY_LABEL = {'kick': 'Пробел', 'snare': 'D', 'hihat': 'S', 'tom1': 'F', 'tom2': 'J', 'floor': 'K', 'crash': 'E', 'ride': 'L'}
LANE_NAME = {'kick': 'бочка', 'snare': 'малый', 'hihat': 'хай-хэт', 'tom1': 'том 1', 'tom2': 'том 2', 'floor': 'напольный',
             'crash': 'крэш', 'ride': 'райд'}
LANE_RGB = {'kick': (255, 96, 96), 'snare': (56, 225, 255), 'hihat': (255, 222, 90), 'tom1': (120, 232, 140),
            'tom2': (120, 232, 140), 'floor': (90, 210, 170), 'crash': (255, 166, 60), 'ride': (200, 150, 255)}
WINDOWS = {'keys': (0.045, 0.10), 'hands': (0.07, 0.14)}   # «точно», «хорошо», с
APPROACH = 1.3                                             # кольцо появляется за столько секунд
DIFFS = [('easy', 'Лёгкий'), ('medium', 'Средний'), ('hard', 'Тяжёлый')]
AUDIO_EXT = ('.mp3', '.ogg', '.wav', '.flac', '.opus')


def lane_of_inst(inst):
    info = INSTRUMENTS.get(inst, {})
    cat, t = info.get('cat'), info.get('type')
    if cat == 'kick':
        return 'kick'
    if cat == 'snare':
        return 'snare'
    if cat == 'hat':
        return 'hihat'
    if t == 'floor':
        return 'floor'
    if cat == 'tom':
        return 'tom'
    if cat == 'cymbal':
        return 'ride' if inst.startswith('ride') else 'crash'
    return 'perc'


def lane_map(pads):
    """Дорожка → барабан текущей установки (с заменами, если барабана нет)."""
    by = {}
    toms = []
    for p in pads:
        ln = lane_of_inst(p['inst'])
        if ln == 'tom':
            toms.append(p)
        elif ln not in by:
            by[ln] = p
    toms.sort(key=lambda p: p['x'])
    if toms:
        by['tom1'] = toms[0]
        by['tom2'] = toms[1] if len(toms) > 1 else toms[0]
        if 'floor' not in by:
            by['floor'] = toms[-1]
    fallback = {'tom1': ['tom2', 'snare'], 'tom2': ['tom1', 'floor', 'snare'], 'floor': ['tom2', 'tom1', 'snare'],
                'crash': ['ride', 'hihat'], 'ride': ['crash', 'hihat'], 'hihat': ['ride', 'crash', 'snare'],
                'snare': ['perc', 'tom1'], 'kick': []}
    out = {}
    for lane in LANES:
        p = by.get(lane)
        for alt in fallback[lane]:
            if p is None:
                p = by.get(alt) or out.get(alt)
        if p is None and pads:
            p = pads[0]
        out[lane] = p
    return out


# ============================================================ песни

def slugify(name):
    s = re.sub(r'[^\w\-]+', '_', name.lower(), flags=re.UNICODE).strip('_')
    return (s or 'song')[:40]


class SongLib:
    def __init__(self, builtin_dir, user_dir):
        self.builtin_dir, self.user_dir = builtin_dir, user_dir
        os.makedirs(user_dir, exist_ok=True)
        self.songs = []
        self.reload()

    def reload(self):
        songs = []
        try:
            with open(os.path.join(self.builtin_dir, 'index.json'), encoding='utf-8') as f:
                ids = json.load(f)
        except (OSError, ValueError):
            ids = []
        for sid in ids:
            d = os.path.join(self.builtin_dir, sid)
            s = self._load(d, False)
            if s:
                songs.append(s)
        for name in sorted(os.listdir(self.user_dir)) if os.path.isdir(self.user_dir) else []:
            s = self._load(os.path.join(self.user_dir, name), True)
            if s:
                songs.append(s)
        self.songs = songs

    @staticmethod
    def _load(d, user):
        try:
            with open(os.path.join(d, 'chart.json'), encoding='utf-8') as f:
                s = json.load(f)
            s['dir'], s['user'] = d, user
            s.setdefault('id', os.path.basename(d))
            return s
        except (OSError, ValueError):
            return None

    def remove(self, song):
        if song.get('user'):
            shutil.rmtree(song['dir'], ignore_errors=True)
            self.reload()


class Importer:
    """Своя песня: копирование файла, разбор нот и «песни без барабанов» в фоне."""

    def __init__(self, lib, log):
        self.lib, self.log = lib, log
        self.busy = False
        self.progress = 0.0
        self.stage = ''
        self.error = None
        self.done_title = None

    def start(self, path):
        if self.busy:
            return
        self.busy, self.progress, self.error, self.done_title = True, 0.0, None, None
        self.stage = 'Читаю файл…'
        threading.Thread(target=self._run, args=(path,), daemon=True).start()

    def _run(self, path):
        import autochart
        title = os.path.splitext(os.path.basename(path))[0]
        d = None
        try:
            if not pygame.mixer.get_init():
                raise RuntimeError('звук недоступен')
            base = slugify(title)
            d = os.path.join(self.lib.user_dir, base)
            k = 2
            while os.path.exists(d):
                d = os.path.join(self.lib.user_dir, f'{base}_{k}')
                k += 1
            os.makedirs(d)
            ext = os.path.splitext(path)[1].lower()
            orig = 'original' + ext
            shutil.copyfile(path, os.path.join(d, orig))
            with open(os.path.join(d, orig), 'rb') as f:
                snd = pygame.mixer.Sound(file=f)
            arr = pygame.sndarray.array(snd)
            if arr.ndim == 1:
                arr = np.stack([arr, arr], 1)
            x = arr[:, :2].astype(np.float32)
            if np.issubdtype(arr.dtype, np.integer):
                x /= float(np.iinfo(arr.dtype).max + 1)
            sr = pygame.mixer.get_init()[0]
            if sr != autochart.SR:  # микшер открылся не на 44,1 кГц — привести к частоте анализа
                n = int(len(x) * autochart.SR / sr)
                pos = np.linspace(0, len(x) - 1, n)
                x = np.stack([np.interp(pos, np.arange(len(x)), x[:, c]) for c in range(2)], 1).astype(np.float32)
            if len(x) < autochart.SR * 10:
                raise RuntimeError('песня слишком короткая (нужно хотя бы 10 секунд)')
            self.stage = 'Ищу темп и удары…'

            def pr(v):
                self.progress = v
                if v > 0.85:
                    self.stage = 'Приглушаю барабаны песни…'
            chart, stem = autochart.build_chart(title, x, pr)
            autochart.write_wav(os.path.join(d, 'nodrums.wav'), stem)
            chart.update(id=os.path.basename(d), audio='nodrums.wav', original=orig)
            with open(os.path.join(d, 'chart.json'), 'w', encoding='utf-8') as f:
                json.dump(chart, f, ensure_ascii=False)
            self.lib.reload()
            self.done_title = title
            self.log(f'Своя песня «{title}»: темп {chart["bpm"]}, нот {len(chart["charts"]["hard"])}')
        except Exception as e:
            self.error = f'Не удалось разобрать песню: {e}'
            self.log(self.error)
            if d and os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)
        finally:
            self.busy = False
            self.progress = 1.0


# ============================================================ игра

def grade(acc):
    for g, a in (('S', 0.95), ('A', 0.85), ('B', 0.7), ('C', 0.55)):
        if acc >= a:
            return g
    return 'D'


class RhythmGame:
    def __init__(self, app, song, diff, mode):
        self.app, self.song, self.diff, self.mode = app, song, diff, mode
        self.lock = threading.Lock()
        self.auto_kick = mode == 'hands' and app.s['rg_autokick']
        self.notes = [{'t': float(t), 'lane': l, 'st': None, 'auto': self.auto_kick and l == 'kick'}
                      for t, l in song['charts'][diff]]
        self.scored = [n for n in self.notes if not n['auto']]
        self.perfect_w, self.good_w = WINDOWS[mode]
        self.calib = app.s['calib_' + mode] / 1000
        self.score = self.combo = self.max_combo = 0
        self.counts = {'perfect': 0, 'good': 0, 'miss': 0, 'extra': 0}
        self.popups = []
        self.state = 'loading'
        self.paused_at = None
        self.t_start = 0.0
        self.sounds = None
        self.channels = []
        self.result = None
        self.next_i = 0
        threading.Thread(target=self._load, daemon=True).start()

    # ---------- звук
    def _load(self):
        try:
            s = self.song
            guide = self.app.s['rg_guide']
            if s.get('user'):
                name = s['original'] if guide else s['audio']
                files = [(name, 1.0)]
            else:
                files = [(s['audio'], 1.0)] + ([(s['drums'], 0.35)] if guide and s.get('drums') else [])
            out = []
            for fn, vol in files:
                with open(os.path.join(s['dir'], fn), 'rb') as f:
                    out.append((pygame.mixer.Sound(file=f), vol))
            self.sounds = out
            self.state = 'countdown'
            self.t_start = time.perf_counter() + 3.0
        except Exception as e:
            self.app.set_status(f'Не удалось загрузить песню: {e}', 4, True)
            self.state = 'error'

    def _play(self):
        self.channels = []
        for i, (snd, vol) in enumerate(self.sounds):
            ch = pygame.mixer.Channel(i)
            ch.set_volume(vol * self.app.s['volume'] / 100)
            ch.play(snd)
            self.channels.append(ch)

    def stop_audio(self):
        for ch in self.channels:
            ch.stop()
        self.channels = []

    # ---------- время
    def song_time(self, now=None):
        if self.paused_at is not None:
            return self.paused_at
        return (now or time.perf_counter()) - self.t_start

    def pause(self):
        if self.state in ('countdown', 'playing') and self.paused_at is None:
            self.paused_at = self.song_time()
            for ch in self.channels:
                ch.pause()

    def resume(self):
        if self.paused_at is not None:
            self.t_start = time.perf_counter() - self.paused_at
            if self.paused_at < 0:  # пауза во время отсчёта
                self.state = 'countdown'
            self.paused_at = None
            for ch in self.channels:
                ch.unpause()

    # ---------- удары
    def on_lanes(self, lanes, t_event):
        """Удар по барабанам, обслуживающим эти дорожки (из камеры, клавиатуры или мыши)."""
        if self.state != 'playing' or self.paused_at is not None:
            return
        st = t_event - self.t_start - self.calib
        with self.lock:
            best, bd = None, 1e9
            for n in self.notes[self.next_i:]:
                if n['t'] - st > self.good_w:
                    break
                if n['st'] is None and not n['auto'] and n['lane'] in lanes:
                    d = abs(n['t'] - st)
                    if d < bd:
                        best, bd = n, d
            if best is None or bd > self.good_w:
                self.counts['extra'] += 1
                return
            kind = 'perfect' if bd <= self.perfect_w else 'good'
            best['st'] = kind
            self.counts[kind] += 1
            self.combo += 1
            self.max_combo = max(self.max_combo, self.combo)
            self.score += (300 if kind == 'perfect' else 120) * (1 + min(3, self.combo // 10))
            self.popups.append((best['lane'], 'Точно!' if kind == 'perfect' else 'Хорошо', time.perf_counter(),
                                (255, 214, 90) if kind == 'perfect' else (120, 220, 255)))

    def on_pad(self, pad, t_event):
        lm = self.app.rg_lanes
        lanes = [ln for ln, p in lm.items() if p is pad] or [lane_of_inst(pad['inst'])]
        self.on_lanes(lanes, t_event)

    def update(self):
        now = time.perf_counter()
        if self.state == 'countdown' and self.paused_at is None and now >= self.t_start:
            self._play()
            self.state = 'playing'
        if self.state != 'playing' or self.paused_at is not None:
            return
        st = self.song_time(now)
        autos = 0
        with self.lock:
            while self.next_i < len(self.notes) and self.notes[self.next_i]['st'] is not None:
                self.next_i += 1
            for n in self.notes[self.next_i:]:
                if n['t'] > st + 0.05:
                    break
                if n['st'] is not None:
                    continue
                if n['auto'] and n['t'] <= st:
                    n['st'] = 'auto'
                    autos += 1
                elif n['t'] < st - self.good_w:
                    n['st'] = 'miss'
                    self.counts['miss'] += 1
                    self.combo = 0
                    self.popups.append((n['lane'], 'Мимо', now, (255, 110, 100)))
        p = self.app.rg_lanes.get('kick') if autos else None
        if p:  # вне блокировки и без уведомления: автобочка — не удар игрока
            self.app.engine.trigger(p, 0.85, notify=False)
        last = self.notes[-1]['t'] if self.notes else 0
        if st > max(last + 1.5, min(self.song['length'], last + 4)):
            self.finish()

    def accuracy(self):
        done = self.counts['perfect'] + self.counts['good'] + self.counts['miss']
        return (self.counts['perfect'] + 0.6 * self.counts['good']) / done if done else 1.0

    def finish(self):
        self.stop_audio()
        acc = self.accuracy()
        g = grade(acc)
        key = f'{self.mode}:{self.diff}'
        rec = self.app.s['scores'].setdefault(self.song['id'], {})
        best = rec.get(key)
        new = best is None or self.score > best.get('score', 0)
        if new:
            rec[key] = {'score': self.score, 'acc': round(acc, 4), 'grade': g, 'combo': self.max_combo}
            self.app.save()
        self.result = {'score': self.score, 'acc': acc, 'grade': g, 'combo': self.max_combo, 'new': new,
                       **self.counts}
        self.state = 'results'


class Calibration:
    """Калибровка задержки: 4 щелчка — приготовиться, затем 8 ударов в такт."""
    BEAT = 0.6
    COUNT_IN = 4
    N = 8

    def __init__(self, app, mode):
        self.app, self.mode = app, mode
        self.offsets = []
        self.done = False
        self.result_ms = None
        sr = pygame.mixer.get_init()[0] if pygame.mixer.get_init() else 44100
        syn = Synth(sr, 5)
        total = int(sr * self.BEAT * (self.COUNT_IN + self.N + 1))
        x = np.zeros(total)
        for k in range(self.COUNT_IN + self.N):
            c = syn.click(k % 4 == 0) * (0.5 if k < self.COUNT_IN else 0.42)
            i = int(k * self.BEAT * sr)
            x[i:i + len(c)] += c
        arr = np.ascontiguousarray((np.stack([x, x], 1) * 32767).astype(np.int16))
        self.sound = pygame.sndarray.make_sound(arr) if pygame.mixer.get_init() else None
        self.t0 = time.perf_counter() + 0.4
        self.started = False

    def update(self):
        now = time.perf_counter()
        if not self.started and now >= self.t0 and self.sound:
            ch = pygame.mixer.Channel(0)
            ch.set_volume(self.app.s['volume'] / 100)
            ch.play(self.sound)
            self.started = True
        if not self.done and now > self.t0 + self.BEAT * (self.COUNT_IN + self.N + 0.6):
            self.finish()

    def on_hit(self, t_event):
        if not self.started or self.done:
            return
        k = round((t_event - self.t0) / self.BEAT)
        if self.COUNT_IN <= k < self.COUNT_IN + self.N:
            self.offsets.append(t_event - (self.t0 + k * self.BEAT))
        if len(self.offsets) >= self.N:
            self.finish()

    def finish(self):
        self.done = True
        if len(self.offsets) >= 4:
            self.result_ms = int(round(float(np.median(self.offsets)) * 1000))
            self.app.s['calib_' + self.mode] = max(-150, min(350, self.result_ms))
            self.app.save()


# ============================================================ отрисовка

def draw_game(app, surf, st_rect, ui):
    g = app.rhythm
    S = ui.S
    W, H = st_rect.w, st_rect.h
    now = time.perf_counter()
    stime = g.song_time(now)
    style = app.style
    ov = pygame.Surface(st_rect.size, pygame.SRCALPHA)
    # кольца нот
    with g.lock:
        upcoming = [n for n in g.notes[g.next_i:g.next_i + 80] if n['st'] is None and n['t'] - stime <= APPROACH]
    for n in sorted(upcoming, key=lambda n: -n['t']):
        p = app.rg_lanes.get(n['lane'])
        if not p:
            continue
        k = max(0.0, (n['t'] - stime) / APPROACH)  # 1 → только появилось, 0 → пора бить
        rx = p['r'] * W
        is_kick = INSTRUMENTS[p['inst']]['type'] == 'kick' and style != 'mpc'
        ry = rx if (is_kick or style == 'mpc') else rx * 0.42
        cx, cy = p['x'] * W, p['y'] * H
        sc = 1 + 1.6 * k
        col = LANE_RGB[n['lane']]
        alpha = int(255 * min(1, (1 - k) * 2.2))
        width = max(3, S(6) if k > 0.05 else S(9))
        r = kits.erect(cx, cy, rx * sc, ry * sc)
        if style == 'mpc':
            pygame.draw.rect(ov, col + (alpha,), r, width, border_radius=int(r.w * 0.16))
        else:
            pygame.draw.ellipse(ov, col + (alpha,), r, width)
        if n['auto']:
            continue
        if g.mode == 'keys' and k < 0.9:
            lab = ui.tsurf(KEY_LABEL[n['lane']], 15, (10, 12, 18), True)
            br = lab.get_rect(center=(cx, cy - ry * sc - S(14)))
            pygame.draw.rect(ov, col + (alpha,), br.inflate(S(12), S(6)), border_radius=S(8))
            lab.set_alpha(alpha)
            ov.blit(lab, br)
    # надписи «Точно / Хорошо / Мимо»
    keep = []
    for lane, text, t0, col in g.popups:
        k = (now - t0) / 0.6
        if k >= 1:
            continue
        keep.append((lane, text, t0, col))
        p = app.rg_lanes.get(lane)
        if not p:
            continue
        s = ui.tsurf(text, 22, col, True).copy()
        s.set_alpha(int(255 * (1 - k)))
        ov.blit(s, s.get_rect(center=(p['x'] * W, p['y'] * H - S(30) - S(40) * k)))
    g.popups = keep[-40:]
    surf.blit(ov, st_rect.topleft)

    # верхняя панель: песня, прогресс, очки, серия
    bar = pygame.Rect(st_rect.centerx - S(330), st_rect.y + S(12), S(660), S(58))
    pygame.draw.rect(surf, (10, 13, 19), bar, border_radius=S(14))
    ui.text(surf, g.song['title'], 15, (236, 241, 248), (bar.x + S(16), bar.y + S(9)), bold=True)
    ui.text(surf, f'{dict(DIFFS)[g.diff]} · {"клавиатура" if g.mode == "keys" else "руки"}', 12, (146, 158, 178),
            (bar.x + S(16), bar.y + S(31)))
    ui.text(surf, f'{g.score:,}'.replace(',', ' '), 22, (255, 255, 255), (bar.right - S(16), bar.y + S(6)), 'topright', True)
    ui.text(surf, f'серия {g.combo} · {int(g.accuracy() * 100)}%', 12, (146, 158, 178), (bar.right - S(16), bar.y + S(34)),
            'topright')
    prog = pygame.Rect(bar.x + S(14), bar.bottom - S(7), bar.w - S(28), S(3))
    pygame.draw.rect(surf, (44, 52, 70), prog, border_radius=2)
    frac = min(1.0, max(0.0, stime / max(1.0, g.song['length'])))
    pygame.draw.rect(surf, (56, 225, 255), (prog.x, prog.y, int(prog.w * frac), prog.h), border_radius=2)
    if g.combo >= 10:
        ui.text(surf, f'×{1 + min(3, g.combo // 10)}', 18, (255, 214, 90), (bar.right + S(12), bar.y + S(14)), bold=True)
    if g.state == 'loading':
        ui.text(surf, 'Загружаю песню…', 30, (255, 255, 255), st_rect.center, 'center', True)
    elif g.state == 'countdown' and g.paused_at is None:
        left = g.t_start - now
        ui.text(surf, str(max(1, int(left) + 1)), 96, (255, 255, 255), st_rect.center, 'center', True)
        hint = ('Клавиши: S — хай-хэт, D — малый, F J K — томы, E — крэш, L — райд, Пробел — бочка' if g.mode == 'keys'
                else 'Бейте по барабанам, когда кольцо сожмётся до края барабана' +
                (', бочка играет сама' if g.auto_kick else ', бочка — Пробел'))
        ui.text(surf, hint, 15, (220, 228, 240), (st_rect.centerx, st_rect.centery + S(80)), 'center')


def draw_calibration(app, surf, ui):
    c = app.calib
    S = ui.S
    sw, sh = surf.get_size()
    P = pygame.Rect(0, 0, S(640), S(236))
    P.midtop = (sw // 2, S(110))  # сверху, чтобы барабаны и руки оставались видны
    pygame.draw.rect(surf, (15, 19, 28), P, border_radius=S(18))
    pygame.draw.rect(surf, (48, 58, 80), P, 1, border_radius=S(18))
    ui.text(surf, 'Калибровка задержки', 24, (236, 241, 248), (P.x + S(30), P.y + S(24)), bold=True)
    how = 'клавишей (любой барабан)' if c.mode == 'keys' else 'палочками по любому барабану'
    ui.para(surf, f'Сначала 4 щелчка — приготовьтесь. Затем бейте {how} точно в такт следующим 8 щелчкам.',
            14, (170, 182, 200), P.x + S(30), P.y + S(64), P.w - S(60))
    now = time.perf_counter()
    k = int((now - c.t0) / c.BEAT) if c.started else -1
    for i in range(c.COUNT_IN + c.N):
        x = P.x + S(44) + i * S(46)
        on = i == k
        col = (255, 214, 90) if i < c.COUNT_IN else (56, 225, 255)
        pygame.draw.circle(surf, col if on else (52, 62, 84), (x, P.y + S(132)), S(12) if on else S(8))
        if i >= c.COUNT_IN and i - c.COUNT_IN < len(c.offsets):
            pygame.draw.circle(surf, (255, 255, 255), (x, P.y + S(132)), S(4))
    if c.done:
        msg = (f'Готово: ваша задержка {c.result_ms:+d} мс — игра будет её учитывать.' if c.result_ms is not None
               else 'Мало ударов в такт — попробуйте ещё раз.')
        ui.text(surf, msg, 16, (236, 241, 248), (P.centerx, P.y + S(172)), 'center', True)
        ui.text(surf, 'Нажмите любую клавишу или кликните', 13, (146, 158, 178), (P.centerx, P.y + S(204)), 'center')
    return P


def draw_pause(app, surf, ui):
    S = ui.S
    sw, sh = surf.get_size()
    dim = pygame.Surface((sw, sh), pygame.SRCALPHA)
    dim.fill((4, 6, 10, 150))
    surf.blit(dim, (0, 0))
    P = pygame.Rect(0, 0, S(380), S(300))
    P.center = (sw // 2, sh // 2)
    pygame.draw.rect(surf, (15, 19, 28), P, border_radius=S(18))
    ui.text(surf, 'Пауза', 26, (236, 241, 248), (P.centerx, P.y + S(40)), 'center', True)
    out = None
    for i, (key, label, kind) in enumerate((('resume', 'Продолжить', 'primary'), ('restart', 'Заново', 'normal'),
                                            ('quit', 'К песням', 'ghost'))):
        r = pygame.Rect(P.x + S(40), P.y + S(84) + i * S(62), P.w - S(80), S(48))
        if ui.button(surf, r, label, kind):
            out = key
    ui.block(pygame.Rect(0, 0, sw, sh))
    return out


def draw_results(app, surf, ui):
    g = app.rhythm
    r = g.result
    S = ui.S
    sw, sh = surf.get_size()
    dim = pygame.Surface((sw, sh), pygame.SRCALPHA)
    dim.fill((4, 6, 10, 170))
    surf.blit(dim, (0, 0))
    P = pygame.Rect(0, 0, S(560), S(440))
    P.center = (sw // 2, sh // 2)
    pygame.draw.rect(surf, (15, 19, 28), P, border_radius=S(20))
    pygame.draw.rect(surf, (48, 58, 80), P, 1, border_radius=S(20))
    ui.text(surf, g.song['title'], 22, (236, 241, 248), (P.centerx, P.y + S(30)), 'center', True)
    ui.text(surf, f'{dict(DIFFS)[g.diff]} · {"клавиатура" if g.mode == "keys" else "руки"}', 13, (146, 158, 178),
            (P.centerx, P.y + S(60)), 'center')
    gc = {'S': (255, 214, 90), 'A': (120, 230, 140), 'B': (56, 225, 255), 'C': (200, 150, 255), 'D': (255, 110, 100)}[r['grade']]
    ui.text(surf, r['grade'], 96, gc, (P.x + S(110), P.y + S(170)), 'center', True)
    x = P.x + S(220)
    y = P.y + S(100)
    for label, val in (('Очки', f'{r["score"]:,}'.replace(',', ' ')), ('Точность', f'{r["acc"] * 100:.1f}%'),
                       ('Лучшая серия', str(r['combo'])), ('Точно / Хорошо / Мимо', f'{r["perfect"]} / {r["good"]} / {r["miss"]}')):
        ui.text(surf, label, 13, (146, 158, 178), (x, y))
        ui.text(surf, val, 20, (236, 241, 248), (x, y + S(18)), bold=True)
        y += S(52)
    if r['new']:
        ui.text(surf, 'Новый рекорд!', 16, (255, 214, 90), (P.x + S(110), P.y + S(250)), 'center', True)
    out = None
    b1 = pygame.Rect(P.x + S(40), P.bottom - S(76), (P.w - S(100)) // 2, S(48))
    b2 = pygame.Rect(b1.right + S(20), b1.y, b1.w, b1.h)
    if ui.button(surf, b1, 'Ещё раз', 'primary'):
        out = 'restart'
    if ui.button(surf, b2, 'К песням', 'normal'):
        out = 'quit'
    ui.block(pygame.Rect(0, 0, sw, sh))
    return out
