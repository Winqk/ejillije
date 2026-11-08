# -*- coding: utf-8 -*-
"""Пресеты жанров, скины и отрисовка барабанов (с поворотом и кэшем)."""
from __future__ import annotations

import math
import random

import pygame

from sounds import INSTRUMENTS

STICK_RGB = [(56, 225, 255), (255, 95, 162)]
FLASH_RGB = STICK_RGB + [(255, 255, 255)]

# ============================================================ скины

SKINS = {
    'rock': dict(
        name='Рок', style='acoustic', accent=(255, 92, 92), bg=((52, 14, 20), (14, 8, 10)),
        shell=[(43, 10, 18), (141, 29, 51), (212, 72, 95), (141, 29, 51), (43, 10, 18)],
        snare_shell=[(59, 66, 80), (174, 182, 196), (242, 245, 250), (174, 182, 196), (59, 66, 80)],
        head=((255, 255, 255), (207, 202, 189)), target=((214, 208, 194), (176, 170, 156), (150, 144, 130)),
        rim=(233, 237, 243), lug=(225, 230, 240),
        cym=((255, 244, 200), (233, 189, 87), (138, 95, 18)), cym_dark=((217, 195, 138), (179, 139, 53), (94, 64, 12)),
        kick_head=((42, 47, 59), (13, 15, 20)), logo='ROCK', logo_rgb=(255, 92, 92), grain=False, stripe=None),
    'jazz': dict(
        name='Джаз', style='acoustic', accent=(222, 176, 96), bg=((58, 36, 18), (16, 10, 6)),
        shell=[(40, 22, 10), (105, 60, 28), (170, 112, 62), (105, 60, 28), (40, 22, 10)],
        snare_shell=[(40, 22, 10), (105, 60, 28), (170, 112, 62), (105, 60, 28), (40, 22, 10)],
        head=((252, 247, 232), (222, 208, 178)), target=((236, 226, 200), (200, 186, 156), (170, 156, 126)),
        rim=(214, 196, 150), lug=(214, 196, 150),
        cym=((236, 214, 160), (178, 134, 64), (84, 58, 22)), cym_dark=((200, 176, 128), (140, 100, 44), (60, 40, 14)),
        kick_head=((244, 236, 214), (205, 190, 160)), logo='JAZZ', logo_rgb=(120, 70, 30), grain=True, stripe=None),
    'hiphop': dict(
        name='Хип-хоп', style='mpc', accent=(242, 193, 78), bg=((40, 34, 18), (10, 10, 12)),
        body=((64, 64, 72), (24, 24, 28)), bezel=(12, 12, 14),
        led={'kick': (255, 70, 70), 'snare': (255, 160, 60), 'tom': (120, 225, 120), 'hat': (255, 222, 90),
             'cymbal': (80, 200, 255), 'perc': (190, 120, 255)}),
    'electro': dict(
        name='Электроника', style='neon', accent=(56, 225, 255), bg=((26, 12, 48), (6, 6, 16)),
        neon={'kick': (255, 60, 200), 'snare': (56, 225, 255), 'tom': (160, 110, 255), 'hat': (80, 255, 200),
              'cymbal': (255, 220, 90), 'perc': (255, 140, 80)}),
    'metal': dict(
        name='Метал', style='acoustic', accent=(230, 46, 46), bg=((34, 8, 8), (6, 6, 8)),
        shell=[(6, 6, 8), (32, 32, 38), (84, 84, 94), (32, 32, 38), (6, 6, 8)],
        snare_shell=[(20, 20, 24), (120, 124, 134), (210, 214, 222), (120, 124, 134), (20, 20, 24)],
        head=((96, 96, 104), (38, 38, 44)), target=((70, 70, 78), (150, 30, 30), (210, 40, 40)),
        rim=(214, 218, 226), lug=(190, 194, 204),
        cym=((240, 232, 210), (176, 160, 120), (66, 58, 42)), cym_dark=((200, 190, 160), (130, 116, 84), (40, 34, 24)),
        kick_head=((30, 30, 34), (8, 8, 10)), logo='METAL', logo_rgb=(230, 46, 46), grain=False, stripe=(200, 28, 28)),
}
CHROME = (222, 226, 234)
SKINS.update({
    'wine': dict(  # винно-красный лак с текстурой дерева
        name='Винный лак', style='acoustic', accent=(214, 52, 62), bg=((60, 12, 16), (14, 6, 8)),
        shell=[(52, 8, 12), (122, 20, 28), (178, 44, 52), (122, 20, 28), (52, 8, 12)],
        snare_shell=[(52, 8, 12), (122, 20, 28), (178, 44, 52), (122, 20, 28), (52, 8, 12)],
        head=((255, 255, 252), (214, 210, 200)), target=((222, 218, 208), (186, 180, 168), (160, 154, 142)),
        rim=CHROME, lug=CHROME, lugs='block', grain=True, grain_rgb=(40, 4, 8, 70), stripe=None,
        cym=((255, 240, 190), (226, 178, 82), (128, 88, 20)), cym_dark=((214, 190, 130), (172, 132, 50), (90, 60, 12)),
        kick_head=((34, 34, 38), (10, 10, 12)), logo='AIR DRUMS', logo_rgb=(245, 245, 245), logo_style='bold',
        kick_port=True, hoop=True, stand=(170, 176, 188)),
    'metallic': dict(  # серо-стальной металлик с блёстками
        name='Стальной металлик', style='acoustic', accent=(150, 176, 196), bg=((30, 40, 50), (8, 10, 14)),
        shell=[(40, 48, 58), (104, 120, 134), (196, 210, 222), (104, 120, 134), (40, 48, 58)],
        snare_shell=[(40, 48, 58), (104, 120, 134), (196, 210, 222), (104, 120, 134), (40, 48, 58)],
        head=((255, 255, 255), (220, 220, 214)), target=((226, 226, 220), (190, 190, 182), (164, 164, 156)),
        rim=CHROME, lug=CHROME, lugs='tube', sparkle=(235, 245, 255), badge=True, grain=False, stripe=None,
        cym=((255, 242, 196), (232, 186, 88), (136, 94, 20)), cym_dark=((216, 192, 134), (176, 136, 54), (92, 62, 14)),
        kick_head=((30, 30, 34), (8, 8, 10)), logo='Air Drums', logo_rgb=(250, 250, 250), logo_style='script',
        hoop=True, stand=(176, 182, 194)),
    'flames': dict(  # чёрные корпуса с пламенем
        name='Пламя', style='acoustic', accent=(255, 128, 24), bg=((48, 14, 4), (8, 4, 2)),
        shell=[(4, 4, 6), (22, 20, 24), (54, 50, 56), (22, 20, 24), (4, 4, 6)],
        snare_shell=[(4, 4, 6), (22, 20, 24), (54, 50, 56), (22, 20, 24), (4, 4, 6)],
        head=((255, 255, 255), (214, 212, 206)), target=((222, 220, 214), (200, 110, 40), (230, 120, 30)),
        rim=CHROME, lug=CHROME, lugs='block', flames=True, grain=False, stripe=None,
        cym=((255, 246, 200), (240, 196, 90), (150, 104, 24)), cym_dark=((220, 196, 136), (180, 140, 56), (96, 66, 16)),
        kick_head=((26, 26, 30), (6, 6, 8)), logo='AIR DRUMS', logo_rgb=(255, 255, 255), logo_style='metal',
        hoop=True, stand=(176, 182, 194)),
    'ekit': dict(  # электронная установка: чёрные сеточные пэды и резиновые тарелки
        name='Электронная установка', style='ekit', accent=(255, 70, 60), bg=((26, 28, 34), (6, 6, 8)),
        stand=(34, 34, 38)),
})
SKINS['rock']['name'] = 'Красный блеск'
SKINS['jazz']['name'] = 'Орех'
SKINS['metal']['name'] = 'Чёрный лак'
SKINS['electro']['name'] = 'Неон'
SKINS['hiphop']['name'] = 'Пэды драм-машины'
SKIN_ORDER = ['flames', 'metallic', 'wine', 'ekit', 'hiphop', 'rock', 'jazz', 'metal', 'electro']

# ============================================================ пресеты жанров
# (инструмент, x, y, радиус в долях ширины, поворот°, бочка «только прямым попаданием»)

PRESETS = [
    dict(id='rock', name='Рок', skin='flames', banks=['rusty'],
         desc='Живая установка 80-х: три тома, крэш и райд', pads=[
        ('crash', .15, .28, .115, 0), ('hihat', .16, .60, .095, 0), ('snare', .36, .74, .10, 0),
        ('tom_hi', .39, .42, .082, 0), ('tom_mid', .61, .42, .082, 0), ('floor', .65, .75, .108, 0),
        ('ride', .85, .34, .125, 0), ('kick', .5, .94, .075, 0, True)]),
    dict(id='jazz', name='Джаз', skin='metallic', banks=['virtuosity', 'frankensnare'],
         desc='Живой джазовый сет: щётки и большой райд', pads=[
        ('crash', .17, .27, .10, -8), ('hihat', .15, .60, .09, 0), ('snare_brush', .37, .73, .095, 0),
        ('tom_mid', .47, .42, .08, 0), ('floor', .66, .74, .10, 0), ('ride_jazz', .80, .36, .14, 6),
        ('kick_jazz', .5, .94, .07, 0, True)]),
    dict(id='hiphop', name='Хип-хоп', skin='hiphop', banks=[],
         desc='Синтез в духе 808: бочка, хлопок, щелчок', pads=[
        ('crash', .2, .36, .075, 0), ('hat808_open', .4, .36, .075, 0), ('snap', .6, .36, .075, 0),
        ('cowbell', .8, .36, .075, 0), ('hat808', .2, .70, .075, 0), ('kick808', .4, .70, .075, 0),
        ('snare', .6, .70, .075, 0), ('clap', .8, .70, .075, 0)]),
    dict(id='electro', name='Электроника', skin='ekit', banks=[],
         desc='Синтез в духе 909: плотная бочка, неон', pads=[
        ('crash', .15, .28, .11, 0), ('hat808', .16, .60, .095, 0), ('snare909', .36, .74, .10, 0),
        ('tom808', .40, .42, .08, 0), ('clap', .60, .42, .08, 0), ('tom808_lo', .66, .75, .10, 0),
        ('ride', .85, .34, .12, 0), ('kick909', .5, .94, .075, 0, True)]),
    dict(id='metal', name='Метал', skin='wine', banks=['drs'],
         desc='Живая тяжёлая установка: 4 тома и чайна', pads=[
        ('crash', .12, .25, .10, -10), ('china', .88, .22, .10, 10), ('hihat', .12, .56, .088, 0),
        ('snare_tight', .33, .75, .098, 0), ('tom_hi', .33, .43, .072, -6), ('tom_mid', .5, .37, .072, 0),
        ('tom_lo', .67, .43, .072, 6), ('floor', .69, .77, .10, 0), ('ride', .87, .52, .105, 0),
        ('kick_tight', .5, .94, .075, 0, True)]),
]
PRESET_BY_ID = {p['id']: p for p in PRESETS}
MAX_PADS = 12

_uid = [0]


def new_pad(inst, x, y, r, rot=0.0, direct=False):
    _uid[0] += 1
    return {'uid': _uid[0], 'inst': inst, 'x': float(x), 'y': float(y), 'r': float(r), 'rot': float(rot),
            'direct': bool(direct), 'hitT': -1e9, 'hitRgb': (255, 255, 255), 'key': ''}


def preset_pads(pid):
    pads = []
    for spec in PRESET_BY_ID.get(pid, PRESETS[0])['pads']:
        inst, x, y, r, rot = spec[:5]
        pads.append(new_pad(inst, x, y, r, rot, len(spec) > 5 and spec[5]))
    assign_keys(pads)
    return pads


def pads_from_saved(saved):
    pads = []
    for d in saved or []:
        try:
            if d['inst'] in INSTRUMENTS:
                pads.append(new_pad(d['inst'], d['x'], d['y'], d['r'], d.get('rot', 0), d.get('direct', False)))
        except (KeyError, TypeError, ValueError):
            continue
    assign_keys(pads)
    return pads[:MAX_PADS] if pads else None


def pads_to_saved(pads):
    return [{'inst': p['inst'], 'x': round(p['x'], 4), 'y': round(p['y'], 4), 'r': round(p['r'], 4),
             'rot': round(p['rot'], 1), 'direct': p['direct']} for p in pads]


def assign_keys(pads):
    n = 1
    for p in pads:
        if p['direct']:
            p['key'] = 'Пробел'
        elif n <= 9:
            p['key'] = str(n)
            n += 1
        else:
            p['key'] = ''


def kick_pad(pads):
    for p in pads:
        if p['direct']:
            return p
    return next((p for p in pads if INSTRUMENTS[p['inst']]['cat'] == 'kick'), None)


# ============================================================ геометрия

def pad_local(p, x, y, aspect):
    """Координаты точки в системе барабана (с учётом поворота), в долях высоты кадра."""
    sx, sy = (x - p['x']) * aspect, y - p['y']
    th = math.radians(p['rot'])
    c, s = math.cos(th), math.sin(th)
    return sx * c - sy * s, sx * s + sy * c


def pad_distance(p, x, y, aspect, style):
    rx = p['r'] * aspect
    lx, ly = pad_local(p, x, y, aspect)
    if style == 'mpc':
        return max(abs(lx), abs(ly)) / rx
    ry = rx if INSTRUMENTS[p['inst']]['type'] == 'kick' else rx * 0.85
    return math.hypot(lx / rx, ly / ry)


def nearest_pad(pads, x, y, aspect, style, sticks=True):
    """Ближайший барабан — целиться не нужно. Бочку внизу кадра палочкой — только прямым попаданием."""
    best, bd = None, 1e9
    for p in pads:
        d = pad_distance(p, x, y, aspect, style)
        if sticks and p['direct'] and d > 1.0:
            continue
        if d < bd:
            best, bd = p, d
    return best, bd


def rotate_vec(x, y, deg):
    """Поворот смещения на экране так же, как pygame.transform.rotate (против часовой стрелки)."""
    th = math.radians(deg)
    c, s = math.cos(th), math.sin(th)
    return x * c + y * s, -x * s + y * c


# ============================================================ рисование

def lerp(a, b, k):
    k = 0.0 if k < 0 else 1.0 if k > 1 else k
    return tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(len(a)))


def grad(stops, k):
    k = (0.0 if k < 0 else 1.0 if k > 1 else k) * (len(stops) - 1)
    i = min(int(k), len(stops) - 2)
    return lerp(stops[i], stops[i + 1], k - i)


def erect(cx, cy, rx, ry):
    return pygame.Rect(round(cx - rx), round(cy - ry), max(1, round(2 * rx)), max(1, round(2 * ry)))


def radial(surf, cx, cy, rx, ry, inner, outer, n=26, shift=(-0.25, -0.35)):
    for i in range(n, 0, -1):
        f = i / n
        ox, oy = shift[0] * rx * (1 - f), shift[1] * ry * (1 - f)
        pygame.draw.ellipse(surf, lerp(inner, outer, f), erect(cx + ox, cy + oy, rx * f, ry * f))


def spot(surf, cx, cy, rx, ry, n=24):
    """Пятно вспышки: ярче всего в центре."""
    for i in range(n, 0, -1):
        f = i / n
        pygame.draw.ellipse(surf, (255, 255, 255, int(255 * (1 - f) ** 0.7)), erect(cx, cy, rx * f, ry * f))


def soft_blur(surf, k=10):
    w, h = surf.get_size()
    small = pygame.transform.smoothscale(surf, (max(2, w // k), max(2, h // k)))
    return pygame.transform.smoothscale(small, (w, h))


def bright(c, k=0.35):
    return lerp(c, (255, 255, 255), k)


class PadArt:
    """Картинки барабанов: строятся один раз (с 2× сглаживанием), поворачиваются и кэшируются."""
    SS = 2

    def __init__(self, font_fn):
        self.font_fn = font_fn
        self.base = {}
        self.rot = {}

    def clear(self):
        self.base.clear()
        self.rot.clear()

    def get(self, skin_id, inst, rx_px, rot=0.0):
        rx_px = max(8, int(rx_px))
        rot = int(round(rot)) % 360
        key = (skin_id, inst, rx_px, rot)
        art = self.rot.get(key)
        if art is None:
            b = self._base(skin_id, inst, rx_px)
            art = b if rot == 0 else self._rotate(b, rot)
            if len(self.rot) > 400:
                self.rot.clear()
            self.rot[key] = art
        return art

    # ---------- построение
    def _base(self, skin_id, inst, rx_px):
        key = (skin_id, inst, rx_px)
        b = self.base.get(key)
        if b is not None:
            return b
        skin = SKINS[skin_id]
        style = skin['style']
        rx = rx_px * self.SS
        if style == 'mpc':
            s, sp, (cx, cy), ry, below = self._mpc(skin, inst, rx)
        elif style == 'neon':
            s, sp, (cx, cy), ry, below = self._neon(skin, inst, rx)
        elif style == 'ekit':
            s, sp, (cx, cy), ry, below = self._ekit(skin, inst, rx)
        else:
            s, sp, (cx, cy), ry, below = self._acoustic(skin, inst, rx)
        size = (s.get_width() // self.SS, s.get_height() // self.SS)
        base = pygame.transform.smoothscale(s, size)
        sp = pygame.transform.smoothscale(sp, size)
        b = {'base': base, 'spot': sp, 'anchor': (cx / self.SS, cy / self.SS), 'rx': rx_px, 'ry': ry / self.SS,
             'below': below / self.SS, 'glow': {}, 'tint': {}}
        for rgb in FLASH_RGB:  # всё заранее — никаких рывков на первом ударе
            b['glow'][rgb] = self._glow(base, rgb, rx_px)
            t = sp.copy()
            t.fill(rgb + (255,), special_flags=pygame.BLEND_RGBA_MULT)
            b['tint'][rgb] = t
        if len(self.base) > 300:
            self.base.clear()
        self.base[key] = b
        return b

    @staticmethod
    def _glow(base, rgb, rx):
        pad = max(6, rx // 3)
        w, h = base.get_width() + 2 * pad, base.get_height() + 2 * pad
        g = pygame.Surface((w, h), pygame.SRCALPHA)
        sil = pygame.transform.smoothscale(base, (int(base.get_width() * 1.1), int(base.get_height() * 1.2)))
        sil.fill((255, 255, 255, 0), special_flags=pygame.BLEND_RGBA_MAX)
        g.blit(sil, sil.get_rect(center=(w // 2, h // 2)))
        g = soft_blur(g, 10)
        g.fill(rgb + (255,), special_flags=pygame.BLEND_RGBA_MULT)
        return g

    def _rotate(self, b, rot):
        base = pygame.transform.rotozoom(b['base'], rot, 1.0)
        w0, h0 = b['base'].get_size()
        ax, ay = b['anchor']
        vx, vy = rotate_vec(ax - w0 / 2, ay - h0 / 2, rot)
        anchor = (base.get_width() / 2 + vx, base.get_height() / 2 + vy)
        rx, ry, below = b['rx'], b['ry'], b['below']
        ys = [rotate_vec(px, py, rot)[1] for px in (-rx, rx) for py in (-ry, below)]
        art = {'base': base, 'spot': pygame.transform.rotozoom(b['spot'], rot, 1.0), 'anchor': anchor,
               'rx': rx, 'ry': ry, 'below': max(ys), 'glow': {}, 'tint': {}}
        for rgb in FLASH_RGB:
            art['glow'][rgb] = pygame.transform.rotozoom(b['glow'][rgb], rot, 1.0)
            art['tint'][rgb] = pygame.transform.rotozoom(b['tint'][rgb], rot, 1.0)
        return art

    # ---------- акустика: рок, джаз, метал
    def _acoustic(self, skin, inst, rx):
        t = INSTRUMENTS[inst]['type']
        if t in ('drum', 'floor', 'snare'):
            ry = int(rx * 0.42)
            depth = int(rx * (0.6 if t == 'floor' else 0.3 if t == 'snare' else 0.42))
        elif t == 'kick':
            ry, depth = rx, 0
        elif t == 'hihat':
            ry, depth = int(rx * 0.36), int(rx * 0.4)
        elif t == 'cymbal':
            ry, depth = int(rx * 0.36), 0
        else:  # перкуссия — маленький пэд
            ry, depth = int(rx * 0.5), int(rx * 0.16)
        pad = int(rx * 0.3) + 8
        W, H = 2 * rx + 2 * pad, 2 * ry + depth + 2 * pad
        cx, cy = W // 2, pad + ry
        s = pygame.Surface((W, H), pygame.SRCALPHA)
        sp = pygame.Surface((W, H), pygame.SRCALPHA)
        if t in ('drum', 'floor', 'snare'):
            stops = skin['snare_shell'] if t == 'snare' else skin['shell']
            mask = pygame.Surface((W, H), pygame.SRCALPHA)
            for x in range(-rx, rx + 1):
                yy = ry * math.sqrt(max(0.0, 1 - (x / rx) ** 2))
                pygame.draw.line(s, grad(stops, (x + rx) / (2 * rx)), (cx + x, cy), (cx + x, cy + yy + depth))
                pygame.draw.line(mask, (255, 255, 255, 255), (cx + x, cy), (cx + x, cy + yy + depth))
                if skin['stripe']:
                    y0 = cy + yy + depth * 0.45
                    pygame.draw.line(s, skin['stripe'], (cx + x, y0), (cx + x, y0 + max(3, depth * 0.12)))
            rnd = random.Random(hash(inst) & 0xffff)
            layer = pygame.Surface((W, H), pygame.SRCALPHA)
            if skin['grain']:
                for _ in range(22):
                    gx = rnd.uniform(-0.97, 0.97)
                    x0 = cx + rx * gx
                    yy = ry * math.sqrt(max(0.0, 1 - gx * gx))
                    pts = [(x0 + math.sin(k * 0.9 + gx * 7) * rx * 0.012, cy + yy + depth * k / 6) for k in range(7)]
                    pygame.draw.lines(layer, skin.get('grain_rgb', (25, 12, 4, 90)), False, pts, max(2, rx // 90))
            if skin.get('sparkle'):
                sc = skin['sparkle']
                for _ in range(int(rx * (depth + ry) / 18)):
                    gx = rnd.uniform(-1, 1)
                    yy = ry * math.sqrt(max(0.0, 1 - gx * gx))
                    py = cy + yy + rnd.uniform(0, depth)
                    pygame.draw.circle(layer, sc + (rnd.randint(60, 230),), (cx + gx * rx, py), rnd.choice((1, 1, 2, 2, 3)))
            if skin.get('flames'):
                self._flames(layer, rnd, cx, cy, rx, ry, depth)
            layer.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
            s.blit(layer, (0, 0))
            if skin.get('hoop'):  # нижний обод
                pygame.draw.arc(s, skin['rim'], erect(cx, cy + depth, rx, ry), math.pi * 1.02, math.pi * 1.98, max(3, int(rx * 0.045)))
            self._lugs(s, skin, cx, cy, rx, ry, depth)
            if skin.get('badge'):  # овальная табличка на корпусе (без надписей)
                by = cy + ry + depth * 0.5
                bw, bh = rx * 0.16, max(6, depth * 0.24)
                pygame.draw.ellipse(s, (210, 214, 222), erect(cx, by, bw, bh))
                pygame.draw.ellipse(s, (30, 34, 42), erect(cx, by, bw * 0.8, bh * 0.72))
                pygame.draw.line(s, (200, 206, 214), (cx - bw * 0.45, by), (cx + bw * 0.45, by), max(1, rx // 90))
            radial(s, cx, cy, rx, ry, *skin['head'])
            t0, t1, t2 = skin['target']
            pygame.draw.ellipse(s, t0, erect(cx, cy, rx * 0.4, ry * 0.4))
            pygame.draw.ellipse(s, t1, erect(cx, cy, rx * 0.4, ry * 0.4), max(2, rx // 45))
            pygame.draw.ellipse(s, t2, erect(cx, cy, rx * 0.09, ry * 0.09))
            pygame.draw.ellipse(s, skin['rim'], erect(cx, cy, rx, ry), max(3, int(rx * 0.06)))
            spot(sp, cx, cy, rx, ry)
            below = ry + depth
        elif t == 'kick':
            sh = skin['shell']
            radial(s, cx, cy, rx, rx, sh[2] if skin.get('sparkle') else sh[1], sh[0], shift=(0, 0))
            if skin.get('flames'):  # огненный обод: корпус в пламени виден спереди тонким кольцом
                ring = pygame.Surface((W, H), pygame.SRCALPHA)
                n = 24
                for i in range(n, 0, -1):
                    f = i / n
                    col = grad([(255, 236, 140), (255, 150, 24), (200, 40, 8), (40, 8, 4)], f)
                    pygame.draw.circle(ring, col + (255,), (cx, cy), int(rx * (0.86 + 0.14 * f)))
                pygame.draw.circle(ring, (0, 0, 0, 0), (cx, cy), int(rx * 0.86))
                s.blit(ring, (0, 0))
            radial(s, cx, cy, rx * 0.86, rx * 0.86, *skin['kick_head'])
            if skin.get('kick_port'):  # отверстие в переднем пластике
                px, py = cx + rx * 0.45, cy + rx * 0.38
                pygame.draw.circle(s, (4, 4, 5), (px, py), rx * 0.13)
                pygame.draw.circle(s, (70, 70, 76), (px, py), rx * 0.13, max(2, rx // 60))
            self._kick_logo(s, skin, cx, cy, rx)
            pygame.draw.circle(s, skin['rim'], (cx, cy), int(rx * 0.88), max(3, int(rx * 0.05)))
            pygame.draw.circle(s, skin['target'][1], (cx, cy), int(rx * 0.3), max(2, rx // 40))
            spot(sp, cx, cy, rx * 0.86, rx * 0.86)
            below = rx
        elif t in ('cymbal', 'hihat'):
            if t == 'hihat':
                self._cymbal(s, skin, cx, cy + ry * 0.9, rx, ry, dark=True)
            self._cymbal(s, skin, cx, cy, rx, ry, dark=False, china=(inst == 'china'), bell=(inst == 'ride_bell'))
            spot(sp, cx, cy, rx * 0.8, ry * 0.8)
            below = ry + depth
        else:
            pygame.draw.ellipse(s, (14, 14, 16), erect(cx, cy + depth, rx, ry))
            pygame.draw.rect(s, (14, 14, 16), pygame.Rect(cx - rx, cy, 2 * rx, depth))
            radial(s, cx, cy, rx, ry, (70, 70, 78), (30, 30, 34))
            pygame.draw.ellipse(s, skin['accent'], erect(cx, cy, rx, ry), max(3, int(rx * 0.07)))
            pygame.draw.ellipse(s, skin['accent'], erect(cx, cy, rx * 0.12, ry * 0.12))
            spot(sp, cx, cy, rx, ry)
            below = ry + depth
        return s, sp, (cx, cy), ry, below

    @staticmethod
    def _lugs(s, skin, cx, cy, rx, ry, depth):
        style = skin.get('lugs', 'line')
        for a in (-0.78, -0.4, 0.0, 0.4, 0.78) if style != 'line' else (-0.72, -0.25, 0.25, 0.72):
            lx, ly = cx + rx * a, cy + ry * math.sqrt(1 - a * a)
            if style == 'tube':  # длинные трубчатые крепления
                w = max(3, rx // 26)
                r = pygame.Rect(0, 0, w, depth * 0.78)
                r.midtop = (lx, ly + depth * 0.1)
                pygame.draw.rect(s, (90, 94, 102), r.inflate(2, 2), border_radius=w)
                pygame.draw.rect(s, skin['lug'], r, border_radius=w)
                pygame.draw.line(s, (255, 255, 255), (r.centerx - w // 4, r.top + 2), (r.centerx - w // 4, r.bottom - 2), 1)
            elif style == 'block':  # короткие блоки-крепления
                w, h = max(5, int(rx * 0.07)), max(6, int(depth * 0.32))
                r = pygame.Rect(0, 0, w, h)
                r.center = (lx, ly + depth * 0.5)
                pygame.draw.rect(s, (90, 94, 102), r.inflate(2, 2), border_radius=w // 3)
                pygame.draw.rect(s, skin['lug'], r, border_radius=w // 3)
                pygame.draw.line(s, (255, 255, 255), (r.left + w // 3, r.top + 2), (r.left + w // 3, r.bottom - 3), 1)
            else:
                pygame.draw.line(s, skin['lug'], (lx, ly + depth * 0.15), (lx, ly + depth * 0.82), max(2, rx // 40))

    @staticmethod
    def _flames(layer, rnd, cx, cy, rx, ry, depth):
        """Языки пламени, поднимающиеся от нижнего края корпуса."""
        fl = pygame.Surface(layer.get_size(), pygame.SRCALPHA)
        n = 9
        for i in range(n):
            gx = -1 + (i + rnd.uniform(0.2, 0.8)) * 2 / n
            base_y = cy + ry * math.sqrt(max(0.0, 1 - gx * gx)) + depth
            x0 = cx + gx * rx
            w = rx * 2 / n * rnd.uniform(1.0, 1.5)
            hgt = (depth + ry * 0.3) * rnd.uniform(0.55, 1.0)
            lean = rnd.uniform(-0.35, 0.35) * w
            for k, col in enumerate(((200, 30, 8), (255, 110, 16), (255, 205, 60), (255, 245, 170))):
                sc = 1 - k * 0.22
                pts = []
                for j in range(13):
                    v = j / 12
                    yv = base_y - hgt * sc * math.sin(v * math.pi) ** 0.4 * (1 - abs(v - 0.5) * 0.4)
                    pts.append((x0 - w * sc / 2 + w * sc * v + lean * (1 - abs(v - 0.5) * 2) * sc, yv))
                tip = (x0 + lean * sc * 1.2, base_y - hgt * sc * 1.15)
                poly = [(x0 - w * sc / 2, base_y)] + pts[:6] + [tip] + pts[7:] + [(x0 + w * sc / 2, base_y)]
                pygame.draw.polygon(fl, col + (235,), poly)
        layer.blit(soft_blur(fl, 2), (0, 0))
        layer.blit(fl, (0, 0))

    def _kick_logo(self, s, skin, cx, cy, rx):
        style = skin.get('logo_style', 'top')
        if style == 'top':
            f = self.font_fn(max(10, int(rx * 0.3)), True)
            logo = f.render(skin['logo'], True, skin['logo_rgb'])
            s.blit(logo, logo.get_rect(center=(cx, cy - rx * 0.36)))
            return
        size = int(rx * (0.36 if style == 'script' else 0.3))
        f = self.font_fn(max(10, size), True)
        italic = style in ('script', 'metal')
        f.italic = italic
        try:
            logo = f.render(skin['logo'], True, skin['logo_rgb'])
            if style == 'metal':  # контур и свечение
                glow = f.render(skin['logo'], True, (255, 120, 20))
        finally:
            f.italic = False
        if logo.get_width() > rx * 1.2:  # логотип помещается в верхнюю хорду пластика
            k = rx * 1.2 / logo.get_width()
            logo = pygame.transform.smoothscale(logo, (int(logo.get_width() * k), int(logo.get_height() * k)))
            if style == 'metal':
                glow = pygame.transform.smoothscale(glow, logo.get_size())
        c = (cx, cy - rx * 0.42)  # бочка стоит у нижнего края кадра — логотип в верхней, видимой части
        if style == 'metal':
            g = pygame.Surface((logo.get_width() + 40, logo.get_height() + 40), pygame.SRCALPHA)
            for dx, dy in ((-3, 0), (3, 0), (0, -3), (0, 3), (-2, -2), (2, 2), (-2, 2), (2, -2)):
                g.blit(glow, (20 + dx, 20 + dy))
            s.blit(soft_blur(g, 3), g.get_rect(center=c))
        s.blit(logo, logo.get_rect(center=c))

    # ---------- электронная установка
    def _ekit(self, skin, inst, rx):
        t = INSTRUMENTS[inst]['type']
        if t == 'kick':
            ry, depth = rx, 0
        elif t in ('cymbal', 'hihat'):
            ry, depth = int(rx * 0.36), (int(rx * 0.3) if t == 'hihat' else 0)
        else:
            ry, depth = int(rx * 0.42), int(rx * 0.14)
        pad = int(rx * 0.3) + 8
        W, H = 2 * rx + 2 * pad, 2 * ry + depth + 2 * pad
        cx, cy = W // 2, pad + ry
        s = pygame.Surface((W, H), pygame.SRCALPHA)
        sp = pygame.Surface((W, H), pygame.SRCALPHA)
        if t in ('cymbal', 'hihat'):  # резиновая тарелка
            layers = [(cy + ry * 0.8, True)] if t == 'hihat' else []
            layers.append((cy, False))
            for y0, dark in layers:
                radial(s, cx, y0, rx, ry, (58, 58, 64) if not dark else (40, 40, 44), (14, 14, 16), n=24, shift=(-0.1, -0.3))
                grooves = pygame.Surface((W, H), pygame.SRCALPHA)
                for k in (0.3, 0.45, 0.6, 0.75, 0.9):
                    pygame.draw.ellipse(grooves, (90, 92, 100, 70), erect(cx, y0, rx * k, ry * k), max(1, rx // 120))
                s.blit(grooves, (0, 0))
                radial(s, cx, y0 - ry * 0.04, rx * 0.17, ry * 0.24, (70, 70, 76), (24, 24, 26), n=8, shift=(0, -0.3))
                pygame.draw.circle(s, (180, 184, 192), (cx, y0 - ry * 0.06), max(3, rx // 30))
            spot(sp, cx, cy, rx * 0.8, ry * 0.8)
            return s, sp, (cx, cy), ry, ry + depth
        if t == 'kick':  # пэд бочки на стойке, вид спереди
            radial(s, cx, cy, rx, rx, (48, 48, 54), (12, 12, 14), shift=(0, 0))
            self._mesh(s, cx, cy, rx * 0.82, rx * 0.82, W, H)
            pygame.draw.circle(s, (70, 72, 78), (cx, cy), int(rx * 0.84), max(3, int(rx * 0.05)))
            pygame.draw.circle(s, (36, 36, 40), (cx, cy), int(rx * 0.32))
            pygame.draw.circle(s, (80, 82, 88), (cx, cy), int(rx * 0.32), max(2, rx // 50))
            spot(sp, cx, cy, rx * 0.82, rx * 0.82)
            return s, sp, (cx, cy), rx, rx
        # пэд барабана: корпус-обод, сетка, винты
        pygame.draw.ellipse(s, (14, 14, 16), erect(cx, cy + depth, rx, ry))
        pygame.draw.rect(s, (14, 14, 16), pygame.Rect(cx - rx, cy, 2 * rx, depth))
        for x in range(-rx, rx + 1, 2):
            yy = ry * math.sqrt(max(0.0, 1 - (x / rx) ** 2))
            pygame.draw.line(s, grad([(10, 10, 12), (40, 40, 46), (66, 66, 74), (40, 40, 46), (10, 10, 12)], (x + rx) / (2 * rx)),
                             (cx + x, cy), (cx + x, cy + yy + depth), 2)
        pygame.draw.ellipse(s, (54, 56, 62), erect(cx, cy, rx, ry))
        self._mesh(s, cx, cy, rx * 0.86, ry * 0.86, W, H)
        pygame.draw.ellipse(s, (74, 76, 84), erect(cx, cy, rx * 0.92, ry * 0.92), max(3, int(rx * 0.06)))
        for i in range(8):
            ang = i / 8 * 2 * math.pi
            pygame.draw.circle(s, (150, 152, 160), (cx + math.cos(ang) * rx * 0.96, cy + math.sin(ang) * ry * 0.96), max(2, rx // 45))
        if t == 'snare':  # у малого — тонкий цветной ободок зоны
            pygame.draw.ellipse(s, skin['accent'], erect(cx, cy, rx * 0.86, ry * 0.86), max(2, rx // 70))
        spot(sp, cx, cy, rx, ry)
        return s, sp, (cx, cy), ry, ry + depth

    @staticmethod
    def _mesh(s, cx, cy, rx, ry, W, H):
        """Сетчатый пластик: тёмная основа с мелкой сеткой."""
        m = pygame.Surface((W, H), pygame.SRCALPHA)
        radial(m, cx, cy, rx, ry, (40, 40, 44), (22, 22, 24), n=16)
        step = max(4, int(rx / 14))
        for k in range(-int(2 * rx), int(2 * rx), step):
            pygame.draw.line(m, (58, 58, 64, 255), (cx + k - rx, cy - ry), (cx + k + rx, cy + ry), 1)
            pygame.draw.line(m, (58, 58, 64, 255), (cx + k + rx, cy - ry), (cx + k - rx, cy + ry), 1)
        mk = pygame.Surface((W, H), pygame.SRCALPHA)
        pygame.draw.ellipse(mk, (255, 255, 255, 255), erect(cx, cy, rx, ry))
        m.blit(mk, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        s.blit(m, (0, 0))

    @staticmethod
    def _cymbal(s, skin, cx, cy, rx, ry, dark=False, china=False, bell=False):
        inner, mid, outer = skin['cym_dark'] if dark else skin['cym']
        n = 30
        for i in range(n, 0, -1):
            f = i / n
            col = lerp(mid, outer, (f - 0.45) / 0.55) if f > 0.45 else lerp(inner, mid, f / 0.45)
            pygame.draw.ellipse(s, col, erect(cx - 0.15 * rx * (1 - f), cy - 0.4 * ry * (1 - f), rx * f, ry * f))
        rings = pygame.Surface(s.get_size(), pygame.SRCALPHA)
        for k in (0.32, 0.55, 0.78):
            pygame.draw.ellipse(rings, (60, 40, 8, 90), erect(cx, cy, rx * k, ry * k), max(2, rx // 90))
        if china:
            pygame.draw.ellipse(rings, (40, 26, 6, 150), erect(cx, cy, rx, ry), max(4, rx // 14))
        pygame.draw.arc(rings, (255, 240, 200, 140), erect(cx, cy, rx, ry), math.pi * 0.4, math.pi * 0.95, max(2, rx // 70))
        s.blit(rings, (0, 0))
        bk = 0.26 if bell else 0.16
        radial(s, cx, cy - ry * 0.05, rx * bk, ry * bk * 1.4, (255, 248, 220), (201, 150, 46), n=10, shift=(0, -0.3))

    # ---------- драм-машина (хип-хоп)
    def _mpc(self, skin, inst, rx):
        cat = INSTRUMENTS[inst]['cat']
        led = skin['led'][cat]
        side = 2 * rx
        pad = int(rx * 0.3) + 8
        W = H = side + 2 * pad
        cx = cy = W // 2
        s = pygame.Surface((W, H), pygame.SRCALPHA)
        sp = pygame.Surface((W, H), pygame.SRCALPHA)
        outer = pygame.Rect(pad, pad, side, side)
        pygame.draw.rect(s, skin['bezel'], outer, border_radius=int(side * 0.16))
        ring = outer.inflate(-side * 0.07, -side * 0.07)
        glow = pygame.Surface((W, H), pygame.SRCALPHA)
        pygame.draw.rect(glow, led + (200,), ring, max(4, int(side * 0.05)), border_radius=int(side * 0.13))
        s.blit(soft_blur(glow, 8), (0, 0))
        pygame.draw.rect(s, bright(led, 0.25), ring, max(2, int(side * 0.018)), border_radius=int(side * 0.13))
        inner = outer.inflate(-side * 0.2, -side * 0.2)
        top, bot = skin['body']
        n = 18
        for i in range(n, 0, -1):
            f = i / n
            r = inner.inflate(-(1 - f) * inner.w * 0.9, -(1 - f) * inner.h * 0.9)
            r.center = (inner.centerx - (1 - f) * inner.w * 0.08, inner.centery - (1 - f) * inner.h * 0.1)
            pygame.draw.rect(s, lerp(top, bot, f), r, border_radius=max(2, int(r.w * 0.12)))
        for k in range(1, 5):  # фактура резины
            y = inner.top + inner.h * k / 5
            pygame.draw.line(s, lerp(bot, (0, 0, 0), 0.3), (inner.left + inner.w * 0.08, y), (inner.right - inner.w * 0.08, y), 1)
        spot(sp, cx, cy, inner.w * 0.6, inner.h * 0.6)
        return s, sp, (cx, cy), rx, rx

    # ---------- неон (электроника)
    def _neon(self, skin, inst, rx):
        t = INSTRUMENTS[inst]['type']
        col = skin['neon'][INSTRUMENTS[inst]['cat']]
        if t in ('drum', 'floor', 'snare'):
            ry, depth = int(rx * 0.42), int(rx * (0.6 if t == 'floor' else 0.3 if t == 'snare' else 0.42))
        elif t == 'kick':
            ry, depth = rx, 0
        elif t == 'hihat':
            ry, depth = int(rx * 0.36), int(rx * 0.4)
        elif t == 'cymbal':
            ry, depth = int(rx * 0.36), 0
        else:
            ry, depth = int(rx * 0.5), 0
        pad = int(rx * 0.35) + 10
        W, H = 2 * rx + 2 * pad, 2 * ry + depth + 2 * pad
        cx, cy = W // 2, pad + ry
        lines = []  # (вид, параметры)
        if t in ('drum', 'floor', 'snare'):
            lines += [('ell', (cx, cy, rx, ry)), ('line', ((cx - rx, cy), (cx - rx, cy + depth))),
                      ('line', ((cx + rx, cy), (cx + rx, cy + depth))), ('arc', (cx, cy + depth, rx, ry, math.pi, 2 * math.pi)),
                      ('ell', (cx, cy, rx * 0.4, ry * 0.4))]
        elif t == 'kick':
            lines += [('ell', (cx, cy, rx, rx)), ('ell', (cx, cy, rx * 0.72, rx * 0.72)), ('ell', (cx, cy, rx * 0.2, rx * 0.2))]
        elif t in ('cymbal', 'hihat'):
            if t == 'hihat':
                lines += [('ell', (cx, cy + ry * 0.9, rx, ry))]
            lines += [('ell', (cx, cy, rx, ry)), ('ell', (cx, cy, rx * 0.55, ry * 0.55)), ('ell', (cx, cy, rx * 0.16, ry * 0.2))]
        else:
            lines += [('ell', (cx, cy, rx, ry)), ('ell', (cx, cy, rx * 0.15, ry * 0.15))]

        def draw(surf, color, width):
            for kind, a in lines:
                if kind == 'ell':
                    pygame.draw.ellipse(surf, color, erect(*a), width)
                elif kind == 'line':
                    pygame.draw.line(surf, color, a[0], a[1], width)
                else:
                    x, y, rrx, rry, a0, a1 = a
                    pygame.draw.arc(surf, color, erect(x, y, rrx, rry), a0, a1, width)

        s = pygame.Surface((W, H), pygame.SRCALPHA)
        fill_rx, fill_ry = (rx, ry)
        pygame.draw.ellipse(s, col + (34,), erect(cx, cy, fill_rx, fill_ry))
        glow = pygame.Surface((W, H), pygame.SRCALPHA)
        draw(glow, col + (220,), max(6, int(rx * 0.09)))
        s.blit(soft_blur(glow, 9), (0, 0))
        draw(s, bright(col, 0.3) + (255,), max(3, int(rx * 0.028)))
        sp = pygame.Surface((W, H), pygame.SRCALPHA)
        spot(sp, cx, cy, rx * 0.9, ry * 0.9)
        return s, sp, (cx, cy), ry, ry + depth


_preview_cache = {}


def preview(art: PadArt, skin_id, w, h):
    """Картинка для карточки пресета/скина: тарелка, том и малый в этом стиле."""
    key = (skin_id, w, h)
    if key in _preview_cache:
        return _preview_cache[key]
    skin = SKINS[skin_id]
    s = pygame.Surface((w, h))
    top, bot = skin['bg']
    for y in range(h):
        pygame.draw.line(s, lerp(top, bot, y / max(1, h - 1)), (0, y), (w, y))
    if skin['style'] == 'mpc':
        items = [('crash', .3, .34, .17), ('hat808', .7, .34, .17), ('kick808', .3, .74, .17), ('clap', .7, .74, .17)]
    elif skin['style'] == 'neon':
        items = [('crash', .24, .30, .2), ('tom808', .7, .3, .16), ('snare909', .42, .68, .2), ('kick909', .82, .8, .12)]
    else:
        items = [('crash', .24, .30, .2), ('tom_mid', .7, .3, .16), ('snare', .42, .66, .2), ('kick', .84, .8, .12)]
    for inst, x, y, r in items:
        a = art.get(skin_id, inst, r * w)
        b = a['base']
        s.blit(b, (round(x * w - a['anchor'][0]), round(y * h - a['anchor'][1])))
    _preview_cache[key] = s
    return s
