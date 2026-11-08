# -*- coding: utf-8 -*-
"""Меню и редактор расстановки: небольшой immediate-mode интерфейс на pygame."""
from __future__ import annotations

import math
import time

import pygame

import kits
from kits import SKINS, SKIN_ORDER, PRESETS, PRESET_BY_ID, MAX_PADS, lerp, rotate_vec, soft_blur
from sounds import INSTRUMENTS, PICKER_GROUPS, SELECTABLE

C = dict(
    panel=(15, 19, 28), side=(11, 14, 21), card=(24, 30, 43), card_hi=(33, 41, 59), line=(46, 55, 74),
    text=(236, 241, 248), hint=(146, 158, 178), dim_text=(98, 108, 126),
    acc=(56, 225, 255), acc2=(255, 95, 162), gold=(242, 193, 78), danger=(255, 110, 100),
)
TYPE_NAMES = {'kick': 'бочка', 'snare': 'малый барабан', 'drum': 'том', 'floor': 'напольный том',
              'cymbal': 'тарелка', 'hihat': 'хай-хэт', 'perc': 'перкуссия'}


def rrect_mask(w, h, radius):
    m = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.rect(m, (255, 255, 255, 255), m.get_rect(), border_radius=radius)
    return m


class UI:
    def __init__(self, font_fn):
        self.font_fn = font_fn
        self.cache = {}
        self.gcache = {}
        self.active = None
        self.s = 1.0
        self.click = self.rclick = None
        self.released = False
        self.wheel = 0
        self.mouse = (0, 0)
        self.down = False
        self.consumed = False
        self.wheel_used = False

    # ---------- кадр
    def begin(self, events, screen_h):
        self.s = max(0.72, min(1.7, screen_h / 900))
        self.mouse = pygame.mouse.get_pos()
        self.down = pygame.mouse.get_pressed()[0]
        self.click = self.rclick = None
        self.released = False
        self.wheel = 0
        self.consumed = False
        self.wheel_used = False
        for ev in events:
            if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                self.click = ev.pos
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 3:
                self.rclick = ev.pos
            elif ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
                self.released = True
            elif ev.type == pygame.MOUSEWHEEL:
                self.wheel += ev.y

    def end(self):
        if self.released or not self.down:
            self.active = None

    def S(self, v):
        return int(round(v * self.s))

    def font(self, size, bold=False):
        return self.font_fn(self.S(size), bold)

    def tsurf(self, text, size, color, bold=False):
        key = (text, self.S(size), color, bold)
        s = self.cache.get(key)
        if s is None:
            if len(self.cache) > 800:
                self.cache.clear()
            s = self.cache[key] = self.font(size, bold).render(text, True, color)
        return s

    def text(self, surf, text, size, color, pos, anchor='topleft', bold=False):
        s = self.tsurf(text, size, color, bold)
        r = s.get_rect(**{anchor: pos})
        surf.blit(s, r)
        return r

    def wrap(self, text, size, maxw, bold=False):
        f = self.font(size, bold)
        lines, cur = [], ''
        for w in text.split():
            t = (cur + ' ' + w).strip()
            if f.size(t)[0] <= maxw or not cur:
                cur = t
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines

    def para(self, surf, text, size, color, x, y, maxw):
        for ln in self.wrap(text, size, maxw):
            r = self.text(surf, ln, size, color, (x, y))
            y = r.bottom + self.S(2)
        return y

    # ---------- ввод
    def hovered(self, r):
        return r.collidepoint(self.mouse)

    def pressed(self, r):
        if self.click and not self.consumed and r.collidepoint(self.click):
            self.consumed = True
            return True
        return False

    def block(self, r):
        if self.click and r.collidepoint(self.click):
            self.consumed = True
        if self.wheel and r.collidepoint(self.mouse):
            self.wheel_used = True

    # ---------- графика
    def gradient(self, w, h, c1, c2, radius):
        key = (w, h, c1, c2, radius)
        g = self.gcache.get(key)
        if g is None:
            g = pygame.Surface((w, h), pygame.SRCALPHA)
            for x in range(w):
                pygame.draw.line(g, lerp(c1, c2, x / max(1, w - 1)) + (255,), (x, 0), (x, h))
            g.blit(rrect_mask(w, h, radius), (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
            if len(self.gcache) > 200:
                self.gcache.clear()
            self.gcache[key] = g
        return g

    def shadow(self, w, h, radius, spread):
        key = ('shadow', w, h, radius, spread)
        g = self.gcache.get(key)
        if g is None:
            g = pygame.Surface((w + 2 * spread, h + 2 * spread), pygame.SRCALPHA)
            pygame.draw.rect(g, (0, 0, 0, 170), pygame.Rect(spread, spread + spread // 3, w, h), border_radius=radius)
            g = soft_blur(g, max(4, spread // 3))
            self.gcache[key] = g
        return g

    # ---------- виджеты
    def button(self, surf, r, label='', kind='normal', active=False, icon=None, size=15, enabled=True):
        hov = self.hovered(r) and enabled
        rad = self.S(10)
        if kind == 'primary' and enabled:
            c1, c2 = (C['acc'], C['acc2']) if not hov else (kits.bright(C['acc'], 0.2), kits.bright(C['acc2'], 0.2))
            surf.blit(self.gradient(r.w, r.h, c1, c2, rad), r)
            fg = (8, 14, 22)
        else:
            if kind == 'danger':
                bg, fg = ((74, 30, 32) if hov else (56, 24, 27)), C['danger']
            elif kind == 'ghost':
                bg, fg = ((30, 37, 52) if hov else C['panel']), C['text']
            else:
                bg, fg = ((40, 49, 69) if hov else C['card_hi']), C['text']
            if not enabled:
                fg = C['dim_text']
            pygame.draw.rect(surf, bg, r, border_radius=rad)
            border = C['acc'] if active else C['line'] if kind == 'ghost' else None
            if border:
                pygame.draw.rect(surf, border, r, max(1, self.S(1.5)), border_radius=rad)
        tw = self.tsurf(label, size, fg, True).get_width() if label else 0
        isz = self.S(size + 2)
        gap = self.S(8) if (icon and label) else 0
        x = r.centerx - (tw + (isz if icon else 0) + gap) / 2
        if icon:
            draw_icon(surf, icon, (x + isz / 2, r.centery), isz, fg, max(2, self.S(2)))
            x += isz + gap
        if label:
            self.text(surf, label, size, fg, (x, r.centery), 'midleft', True)
        return enabled and self.pressed(r)

    def slider(self, surf, uid, r, value, lo, hi, step=1.0):
        cy = r.centery
        th = self.S(6)
        track = pygame.Rect(r.x, cy - th // 2, r.w, th)
        k = (value - lo) / (hi - lo) if hi > lo else 0
        pygame.draw.rect(surf, (40, 48, 66), track, border_radius=th)
        fw = max(th, int(track.w * k))
        surf.blit(self.gradient(fw, th, C['acc'], lerp(C['acc'], C['acc2'], k), th // 2), track.topleft)
        hit = r.inflate(self.S(16), self.S(14))
        if self.pressed(hit):
            self.active = uid
        if self.active == uid and self.down:
            k = min(1.0, max(0.0, (self.mouse[0] - r.x) / max(1, r.w)))
            value = lo + round(k * (hi - lo) / step) * step
            k = (value - lo) / (hi - lo)
        kx = r.x + k * r.w
        rad = self.S(11 if (self.active == uid or self.hovered(hit)) else 9)
        pygame.draw.circle(surf, (8, 12, 18), (kx, cy + self.S(2)), rad + self.S(2))
        pygame.draw.circle(surf, (255, 255, 255), (kx, cy), rad)
        pygame.draw.circle(surf, lerp(C['acc'], C['acc2'], k), (kx, cy), rad, max(2, self.S(3)))
        return value

    def toggle(self, surf, r, on):
        rad = r.h // 2
        if on:
            surf.blit(self.gradient(r.w, r.h, C['acc'], C['acc2'], rad), r)
        else:
            pygame.draw.rect(surf, (44, 53, 72), r, border_radius=rad)
        kx = r.right - rad if on else r.x + rad
        pygame.draw.circle(surf, (255, 255, 255), (kx, r.centery), rad - self.S(3))
        return (not on) if self.pressed(r.inflate(self.S(6), self.S(6))) else on

    def segmented(self, surf, r, options, idx):
        pygame.draw.rect(surf, (20, 25, 36), r, border_radius=self.S(10))
        pygame.draw.rect(surf, C['line'], r, 1, border_radius=self.S(10))
        n = len(options)
        w = (r.w - self.S(8)) / n
        for i, opt in enumerate(options):
            cell = pygame.Rect(round(r.x + self.S(4) + i * w), r.y + self.S(4), round(w), r.h - self.S(8))
            if i == idx:
                surf.blit(self.gradient(cell.w, cell.h, C['acc'], C['acc2'], self.S(8)), cell)
                col = (8, 14, 22)
            else:
                if self.hovered(cell):
                    pygame.draw.rect(surf, (36, 44, 62), cell, border_radius=self.S(8))
                col = C['text']
            self.text(surf, opt, 14, col, cell.center, 'center', True)
            if self.pressed(cell):
                idx = i
        return idx

    def chip(self, surf, r, label, active=False, swatch=None):
        hov = self.hovered(r)
        pygame.draw.rect(surf, (42, 52, 74) if active else (34, 42, 60) if hov else C['card_hi'], r, border_radius=r.h // 2)
        if active:
            pygame.draw.rect(surf, C['acc'], r, max(1, self.S(2)), border_radius=r.h // 2)
        x = r.x + self.S(12)
        if swatch:
            pygame.draw.circle(surf, swatch, (x + self.S(6), r.centery), self.S(7))
            x += self.S(18)
        self.text(surf, label, 14, C['text'], (x, r.centery), 'midleft', active)
        return self.pressed(r)

    def chip_width(self, label, swatch=False):
        return self.font(14, True).size(label)[0] + self.S(26) + (self.S(18) if swatch else 0)


# ============================================================ иконки

def draw_icon(surf, name, center, size, color, w=2):
    cx, cy = center
    h = size / 2

    def L(a, b, ww=w):
        pygame.draw.line(surf, color, (cx + a[0] * h, cy + a[1] * h), (cx + b[0] * h, cy + b[1] * h), ww)

    if name == 'presets':
        q = h * 0.42
        for dx in (-1, 1):
            for dy in (-1, 1):
                pygame.draw.rect(surf, color, pygame.Rect(cx + dx * h * 0.5 - q, cy + dy * h * 0.5 - q, 2 * q, 2 * q),
                                 0 if dx == dy == -1 else w, border_radius=max(1, int(q * 0.4)))
    elif name == 'drums':
        pygame.draw.ellipse(surf, color, kits.erect(cx, cy - h * 0.35, h * 0.85, h * 0.35), w)
        L((-0.85, -0.35), (-0.85, 0.45))
        L((0.85, -0.35), (0.85, 0.45))
        pygame.draw.arc(surf, color, kits.erect(cx, cy + h * 0.45, h * 0.85, h * 0.35), math.pi, 2 * math.pi, w)
    elif name == 'sound':
        for i, (x, k) in enumerate(((-0.6, 0.3), (0, -0.4), (0.6, 0.15))):
            L((x, -0.9), (x, 0.9))
            pygame.draw.circle(surf, color, (cx + x * h, cy + k * h), max(2, int(h * 0.22)))
    elif name == 'view':
        pygame.draw.ellipse(surf, color, kits.erect(cx, cy, h * 0.95, h * 0.55), w)
        pygame.draw.circle(surf, color, (cx, cy), max(2, int(h * 0.3)))
    elif name == 'close':
        L((-0.6, -0.6), (0.6, 0.6))
        L((-0.6, 0.6), (0.6, -0.6))
    elif name in ('plus', 'minus'):
        L((-0.65, 0), (0.65, 0))
        if name == 'plus':
            L((0, -0.65), (0, 0.65))
    elif name in ('left', 'right'):
        d = -1 if name == 'left' else 1
        L((-0.25 * d, -0.6), (0.35 * d, 0))
        L((0.35 * d, 0), (-0.25 * d, 0.6))
    elif name in ('rot_l', 'rot_r', 'reset'):
        d = -1 if name in ('rot_l', 'reset') else 1
        r = kits.erect(cx, cy, h * 0.7, h * 0.7)
        a0, a1 = (math.pi * 0.15, math.pi * 1.65) if d < 0 else (-math.pi * 0.65, math.pi * 0.85)
        pygame.draw.arc(surf, color, r, a0, a1, w)
        ang = a1 if d < 0 else a0
        ex, ey = cx + math.cos(ang) * h * 0.7, cy - math.sin(ang) * h * 0.7
        t = h * 0.38
        pygame.draw.polygon(surf, color, [(ex - t, ey - t * 0.2 * d), (ex + t, ey - t * 0.2 * d), (ex, ey + t * 0.9 * (1 if d < 0 else -1))])
    elif name == 'trash':
        pygame.draw.rect(surf, color, pygame.Rect(cx - h * 0.55, cy - h * 0.4, h * 1.1, h * 1.25), w, border_radius=max(1, int(h * 0.15)))
        L((-0.85, -0.6), (0.85, -0.6))
        L((-0.25, -0.85), (0.25, -0.85))
    elif name == 'edit':
        L((-0.7, 0.7), (0.55, -0.55), max(w, int(h * 0.3)))
        pygame.draw.polygon(surf, color, [(cx - h * 0.85, cy + h * 0.85), (cx - h * 0.55, cy + h * 0.35), (cx - h * 0.35, cy + h * 0.55)])
    elif name == 'play':
        pygame.draw.polygon(surf, color, [(cx - h * 0.45, cy - h * 0.65), (cx - h * 0.45, cy + h * 0.65), (cx + h * 0.65, cy)])
    elif name == 'menu':
        for y in (-0.55, 0, 0.55):
            L((-0.75, y), (0.75, y))
    elif name == 'check':
        pygame.draw.lines(surf, color, False, [(cx - h * 0.6, cy), (cx - h * 0.15, cy + h * 0.5), (cx + h * 0.65, cy - h * 0.5)], w + 1)
    elif name == 'exit':
        pygame.draw.rect(surf, color, pygame.Rect(cx - h * 0.8, cy - h * 0.8, h * 0.9, h * 1.6), w)
        L((-0.1, 0), (0.9, 0))
        L((0.55, -0.35), (0.9, 0))
        L((0.55, 0.35), (0.9, 0))
    elif name == 'metro':
        pygame.draw.polygon(surf, color, [(cx - h * 0.55, cy + h * 0.85), (cx + h * 0.55, cy + h * 0.85), (cx + h * 0.2, cy - h * 0.85),
                                          (cx - h * 0.2, cy - h * 0.85)], w)
        L((0, 0.5), (0.6, -0.7))


# ============================================================ меню

class Menu:
    TABS = [('presets', 'Пресеты', 'presets'), ('drums', 'Барабаны', 'drums'),
            ('sound', 'Звук и ритм', 'sound'), ('view', 'Вид', 'view')]

    def __init__(self, app):
        self.app = app
        self.open = False
        self.tab = 'presets'
        self.picker = None      # None | ('add', None) | ('change', uid)
        self.scroll = {}
        self._dim = None
        self.panel = pygame.Rect(0, 0, 0, 0)

    def show(self, tab=None, picker=None):
        self.open = True
        if tab:
            self.tab = tab
        self.picker = picker

    def close(self):
        self.open = False
        self.picker = None

    def draw(self, surf, ui):
        app = self.app
        sw, sh = surf.get_size()
        S = ui.S
        if self._dim is None or self._dim.get_size() != (sw, sh):
            self._dim = pygame.Surface((sw, sh), pygame.SRCALPHA)
            self._dim.fill((4, 6, 10, 165))
        surf.blit(self._dim, (0, 0))
        pw, ph = min(sw - S(32), S(1040)), min(sh - S(32), S(690))
        P = pygame.Rect((sw - pw) // 2, (sh - ph) // 2, pw, ph)
        self.panel = P
        rad = S(20)
        sp = S(36)
        surf.blit(ui.shadow(P.w, P.h, rad, sp), (P.x - sp, P.y - sp))
        pygame.draw.rect(surf, C['panel'], P, border_radius=rad)
        side = pygame.Rect(P.x, P.y, S(230), P.h)
        pygame.draw.rect(surf, C['side'], side, border_top_left_radius=rad, border_bottom_left_radius=rad)
        pygame.draw.line(surf, C['line'], (side.right, P.y + S(18)), (side.right, P.bottom - S(18)))
        pygame.draw.rect(surf, (40, 48, 66), P, 1, border_radius=rad)

        # логотип
        lx, ly = side.x + S(22), P.y + S(26)
        for i in range(S(18)):
            pygame.draw.circle(surf, lerp(C['acc'], C['acc2'], i / S(18)), (lx + S(9), ly + S(9)), S(9) - i // 2)
        ui.text(surf, 'Воздушные', 17, C['text'], (lx + S(28), ly - S(3)), bold=True)
        ui.text(surf, 'барабаны', 17, C['text'], (lx + S(28), ly + S(17)), bold=True)

        # вкладки
        y = P.y + S(96)
        for tid, label, icon in self.TABS:
            r = pygame.Rect(side.x + S(12), y, side.w - S(24), S(48))
            act = self.tab == tid
            if act:
                pygame.draw.rect(surf, (30, 38, 56), r, border_radius=S(12))
                surf.blit(ui.gradient(S(4), r.h - S(20), C['acc'], C['acc2'], S(2)), (r.x, r.y + S(10)))
            elif ui.hovered(r):
                pygame.draw.rect(surf, (22, 28, 41), r, border_radius=S(12))
            col = C['text'] if act else C['hint']
            draw_icon(surf, icon, (r.x + S(28), r.centery), S(20), C['acc'] if act else col, max(2, S(2)))
            ui.text(surf, label, 16, col, (r.x + S(52), r.centery), 'midleft', act)
            if ui.pressed(r):
                self.tab, self.picker = tid, None
            y += S(54)

        # низ боковой панели: текущий пресет
        pr = PRESET_BY_ID[app.s['preset']]
        bx = pygame.Rect(side.x + S(14), P.bottom - S(116), side.w - S(28), S(64))
        pygame.draw.rect(surf, (20, 25, 36), bx, border_radius=S(12))
        ui.text(surf, 'Сейчас', 12, C['dim_text'], (bx.x + S(14), bx.y + S(10)))
        ui.text(surf, pr['name'], 16, C['text'], (bx.x + S(14), bx.y + S(28)), bold=True)
        pygame.draw.circle(surf, SKINS[app.skin_id]['accent'], (bx.right - S(20), bx.centery), S(7))
        ui.text(surf, 'Esc — закрыть меню', 12, C['dim_text'], (side.x + S(22), P.bottom - S(34)))

        # закрыть
        cr = pygame.Rect(P.right - S(54), P.y + S(18), S(36), S(36))
        if ui.hovered(cr):
            pygame.draw.rect(surf, (36, 44, 62), cr, border_radius=S(10))
        draw_icon(surf, 'close', cr.center, S(16), C['hint'], max(2, S(2)))
        if ui.pressed(cr):
            self.close()

        # содержимое с прокруткой
        area = pygame.Rect(side.right + S(30), P.y + S(24), P.right - side.right - S(30) - S(70), P.h - S(48))
        key = (self.tab, self.picker is not None)
        off = self.scroll.get(key, 0)
        surf.set_clip(area)
        end_y = getattr(self, 'tab_' + self.tab)(surf, ui, area, area.y - off)
        surf.set_clip(None)
        content_h = end_y + off - area.y
        max_off = max(0, content_h - area.h)
        if ui.wheel and area.collidepoint(ui.mouse):
            off = min(max_off, max(0, off - ui.wheel * S(60)))
            ui.wheel_used = True
        self.scroll[key] = min(off, max_off)
        if max_off > 0:  # полоса прокрутки
            bar_h = max(S(40), area.h * area.h / content_h)
            by = area.y + (area.h - bar_h) * (off / max_off)
            pygame.draw.rect(surf, (52, 62, 84), pygame.Rect(P.right - S(18), by, S(5), bar_h), border_radius=S(3))

        # клик мимо панели закрывает меню; клики внутри не уходят в игру
        if ui.click and not ui.consumed and not P.collidepoint(ui.click):
            self.close()
        ui.block(pygame.Rect(0, 0, sw, sh))

    # ---------- вкладки
    def header(self, surf, ui, area, y, title, hint=None):
        r = ui.text(surf, title, 26, C['text'], (area.x, y), bold=True)
        y = r.bottom + ui.S(6)
        if hint:
            y = ui.para(surf, hint, 14, C['hint'], area.x, y, area.w)
        return y + ui.S(18)

    def tab_presets(self, surf, ui, area, y):
        S = ui.S
        app = self.app
        y = self.header(surf, ui, area, y, 'Жанр и звучание',
                        'Пресет меняет набор барабанов, их звук и стиль. Свою расстановку игра запоминает для каждого пресета.')
        cols = 3 if area.w >= S(640) else 2
        gap = S(16)
        cw = (area.w - gap * (cols - 1)) // cols
        prev_h = int(cw * 0.5)
        chh = prev_h + S(86)
        for i, pr in enumerate(PRESETS):
            r = pygame.Rect(area.x + (i % cols) * (cw + gap), y + (i // cols) * (chh + gap), cw, chh)
            act = app.s['preset'] == pr['id']
            hov = ui.hovered(r)
            pygame.draw.rect(surf, C['card_hi'] if hov else C['card'], r, border_radius=S(16))
            pv = self._rounded_preview(ui, pr['skin'], cw - S(16), prev_h)
            surf.blit(pv, (r.x + S(8), r.y + S(8)))
            ui.text(surf, pr['name'], 18, C['text'], (r.x + S(16), r.y + prev_h + S(18)), bold=True)
            yy = r.y + prev_h + S(44)
            for ln in ui.wrap(pr['desc'], 13, cw - S(32))[:2]:
                yy = ui.text(surf, ln, 13, C['hint'], (r.x + S(16), yy)).bottom + S(1)
            if act:
                pygame.draw.rect(surf, C['acc'], r, max(2, S(2)), border_radius=S(16))
                badge = pygame.Rect(r.right - S(96), r.y + S(16), S(80), S(26))
                surf.blit(ui.gradient(badge.w, badge.h, C['acc'], C['acc2'], badge.h // 2), badge)
                ui.text(surf, 'выбран', 13, (8, 14, 22), badge.center, 'center', True)
            if pr['id'] in app.s['kits']:
                ui.text(surf, 'своя расстановка', 12, C['gold'], (r.right - S(16), r.y + prev_h + S(22)), 'topright')
            if ui.pressed(r):
                app.apply_preset(pr['id'])
        rows = (len(PRESETS) + cols - 1) // cols
        return y + rows * (chh + gap)

    def _rounded_preview(self, ui, skin, w, h):
        key = ('pv', skin, w, h)
        s = ui.gcache.get(key)
        if s is None:
            s = kits.preview(self.app.art, skin, w, h).convert_alpha()
            s.blit(rrect_mask(w, h, ui.S(12)), (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
            ui.gcache[key] = s
        return s

    def tab_drums(self, surf, ui, area, y):
        S = ui.S
        app = self.app
        pads = app.engine.pads
        if self.picker:
            return self.instrument_picker(surf, ui, area, y)
        pr = PRESET_BY_ID[app.s['preset']]
        y = self.header(surf, ui, area, y, 'Барабаны в наборе',
                        f'Пресет «{pr["name"]}», барабанов: {len(pads)} из {MAX_PADS}. Нажмите «Изменить расстановку», '
                        'чтобы двигать, поворачивать и менять размер барабанов прямо на экране.')
        bw = (area.w - S(24)) // 3
        b1 = pygame.Rect(area.x, y, bw, S(46))
        b2 = pygame.Rect(b1.right + S(12), y, bw, S(46))
        b3 = pygame.Rect(b2.right + S(12), y, bw, S(46))
        if ui.button(surf, b1, 'Изменить расстановку', 'primary', icon='edit'):
            self.close()
            app.editor.start()
        if ui.button(surf, b2, 'Добавить барабан', icon='plus', enabled=len(pads) < MAX_PADS):
            self.picker = ('add', None)
        if ui.button(surf, b3, 'Как в пресете', 'ghost', icon='reset'):
            app.reset_kit()
        y = b1.bottom + S(22)
        rh = S(54)
        for p in list(pads):
            info = INSTRUMENTS[p['inst']]
            r = pygame.Rect(area.x, y, area.w, rh)
            pygame.draw.rect(surf, C['card_hi'] if ui.hovered(r) else C['card'], r, border_radius=S(12))
            kb = pygame.Rect(r.x + S(12), r.centery - S(15), S(64) if p['key'] == 'Пробел' else S(30), S(30))
            pygame.draw.rect(surf, (16, 20, 30), kb, border_radius=S(8))
            pygame.draw.rect(surf, C['line'], kb, 1, border_radius=S(8))
            ui.text(surf, p['key'] or '—', 13, C['text'], kb.center, 'center', True)
            ui.text(surf, info['name'], 16, C['text'], (kb.right + S(16), r.y + S(8)), bold=True)
            extra = f'{TYPE_NAMES.get(info["type"], "")} · размер {round(p["r"] * 200)}% · поворот {round(p["rot"])}°'
            ui.text(surf, extra, 12, C['hint'], (kb.right + S(16), r.y + S(31)))
            xr = r.right - S(12)
            dl = pygame.Rect(xr - S(40), r.centery - S(18), S(40), S(36))
            if ui.button(surf, dl, '', 'danger', icon='trash', enabled=len(pads) > 1):
                app.remove_pad(p['uid'])
            ch = pygame.Rect(dl.x - S(118), dl.y, S(110), S(36))
            if ui.button(surf, ch, 'Сменить', icon='drums', size=14):
                self.picker = ('change', p['uid'])
            pl = pygame.Rect(ch.x - S(48), dl.y, S(40), S(36))
            if ui.button(surf, pl, '', icon='play'):
                app.engine.trigger(p, 0.85)
            y += rh + S(8)
        return y

    def instrument_picker(self, surf, ui, area, y):
        S = ui.S
        app = self.app
        mode, uid = self.picker
        cur = None
        if mode == 'change':
            cur = next((p for p in app.engine.pads if p['uid'] == uid), None)
            if cur is None:
                self.picker = None
                return y
        title = 'Добавить барабан' if mode == 'add' else f'Заменить «{INSTRUMENTS[cur["inst"]]["name"]}»'
        y = self.header(surf, ui, area, y, title, 'Нажмите на инструмент — он сразу прозвучит.')
        back = pygame.Rect(area.right - S(130), area.y, S(130), S(40))
        if ui.button(surf, back, 'Назад', 'ghost', icon='left'):
            self.picker = None
            return y
        for gname, keys in PICKER_GROUPS:
            ui.text(surf, gname.upper(), 12, C['dim_text'], (area.x, y), bold=True)
            y += S(24)
            x = area.x
            ch = S(38)
            for k in keys:
                label = INSTRUMENTS[k]['name']
                w = ui.chip_width(label)
                if x + w > area.right:
                    x = area.x
                    y += ch + S(8)
                r = pygame.Rect(x, y, w, ch)
                if ui.chip(surf, r, label, active=(cur is not None and cur['inst'] == k)):
                    if mode == 'add':
                        app.add_pad(k)
                        self.close()
                    else:
                        app.set_pad_inst(uid, k)
                        self.picker = None
                    app.audio_preview(k)
                x += w + S(8)
            y += ch + S(22)
        return y

    def tab_sound(self, surf, ui, area, y):
        S = ui.S
        app = self.app
        s = app.s
        y = self.header(surf, ui, area, y, 'Звук и ритм')

        def row(title, value_text, y):
            ui.text(surf, title, 16, C['text'], (area.x, y), bold=True)
            ui.text(surf, value_text, 16, C['acc'], (area.right, y), 'topright', True)
            return y + S(30)

        y = row('Громкость', f'{s["volume"]}%', y)
        v = ui.slider(surf, 'vol', pygame.Rect(area.x + S(8), y, area.w - S(16), S(24)), s['volume'], 0, 100, 1)
        if v != s['volume']:
            app.set_volume(int(v))
        y += S(46)
        y = row('Чувствительность', f'{s["sens"]} из 10', y)
        v = ui.slider(surf, 'sens', pygame.Rect(area.x + S(8), y, area.w - S(16), S(24)), s['sens'], 1, 10, 1)
        if v != s['sens']:
            app.set_sens(int(v))
        y += S(34)
        y = ui.para(surf, 'Выше — срабатывают даже лёгкие взмахи кистью. Ниже — меньше случайных ударов.', 13, C['hint'],
                    area.x, y, area.w) + S(26)

        # метроном
        card = pygame.Rect(area.x, y, area.w, S(212))
        pygame.draw.rect(surf, C['card'], card, border_radius=S(16))
        draw_icon(surf, 'metro', (card.x + S(30), card.y + S(32)), S(22), C['acc'], max(2, S(2)))
        ui.text(surf, 'Метроном', 18, C['text'], (card.x + S(52), card.y + S(20)), bold=True)
        on = ui.toggle(surf, pygame.Rect(card.right - S(72), card.y + S(18), S(52), S(28)), app.metro_on)
        if on != app.metro_on:
            app.set_metro(on)
        ui.text(surf, f'{s["bpm"]}', 40, C['text'], (card.x + S(24), card.y + S(58)), bold=True)
        ui.text(surf, 'ударов в минуту', 13, C['hint'], (card.x + S(26), card.y + S(110)))
        v = ui.slider(surf, 'bpm', pygame.Rect(card.x + S(190), card.y + S(76), card.w - S(214), S(24)), s['bpm'], 40, 240, 1)
        if v != s['bpm']:
            app.set_bpm(int(v))
        opts = ['2/4', '3/4', '4/4', '6/8']
        beats = [2, 3, 4, 6]
        idx = beats.index(s['beats']) if s['beats'] in beats else 2
        ni = ui.segmented(surf, pygame.Rect(card.x + S(24), card.y + S(146), S(280), S(44)), opts, idx)
        if ni != idx:
            app.set_beats(beats[ni])
        # точки долей
        dx = card.x + S(330)
        for i in range(s['beats']):
            lit = app.metro_on and app.metro_beat_i == i and time.perf_counter() - app.metro_flash < 0.15
            col = (C['gold'] if i == 0 else C['acc']) if lit else (52, 62, 84)
            pygame.draw.circle(surf, col, (dx + i * S(30), card.y + S(168)), S(9) if i == 0 else S(7))
        return card.bottom + S(10)

    def tab_view(self, surf, ui, area, y):
        S = ui.S
        app = self.app
        s = app.s
        y = self.header(surf, ui, area, y, 'Вид и управление')

        def toggle_row(title, hint, value, y):
            ui.text(surf, title, 16, C['text'], (area.x, y), bold=True)
            nv = ui.toggle(surf, pygame.Rect(area.right - S(56), y - S(2), S(52), S(28)), value)
            y2 = ui.para(surf, hint, 13, C['hint'], area.x, y + S(26), area.w - S(80))
            return nv, y2 + S(22)

        # камера
        cam = app.camera
        ui.text(surf, 'Камера', 16, C['text'], (area.x, y), bold=True)
        state = (f'{cam.info} · {cam.fps:.0f} к/с' if cam.seq else 'не работает' if cam.error
                 else 'подбираю 60 к/с…' if cam.probing else 'включается…')
        ui.text(surf, state, 14, C['acc'] if cam.seq else C['danger'] if cam.error else C['hint'], (area.right, y), 'topright', True)
        y += S(30)
        idx = int(app.cam_src) if str(app.cam_src).isdigit() else 0
        wide = area.w >= S(620)
        seg = pygame.Rect(area.x, y, S(330) if wide else area.w, S(44))
        ni = ui.segmented(surf, seg, ['Камера 1', 'Камера 2', 'Камера 3'], min(idx, 2))
        if ni != idx:
            app.s['camera'] = ni
            app.save()
            app.restart_camera(ni)
        rb = pygame.Rect(seg.right + S(14), y, min(S(260), area.right - seg.right - S(14)), S(44)) if wide \
            else pygame.Rect(area.x, seg.bottom + S(10), area.w, S(44))
        if ui.button(surf, rb, 'Перезапустить камеру', icon='reset', size=14):
            app.restart_camera()
        y = rb.bottom + S(14)
        if cam.error:
            y = ui.para(surf, cam.error, 13, C['danger'], area.x, y, area.w) + S(10)
        v, y = toggle_row('Пробовать 60 к/с', 'Игра подберёт режим, в котором камера даёт 60 кадров в секунду (обычно '
                          'в меньшем разрешении). Если после этого камера не включается — выключите.', app.s['cam_fps60'], y)
        if v != app.s['cam_fps60']:
            app.set_fps60(v)
        ui.text(surf, 'Стиль барабанов', 16, C['text'], (area.x, y), bold=True)
        y += S(30)
        x = area.x
        chips = [(None, 'Как в пресете', None)] + [(k, SKINS[k]['name'], SKINS[k]['accent']) for k in SKIN_ORDER]
        for key, label, sw in chips:
            w = ui.chip_width(label, swatch=bool(sw))
            if x + w > area.right:
                x = area.x
                y += S(46)
            if ui.chip(surf, pygame.Rect(x, y, w, S(38)), label, active=(s['skin'] == key), swatch=sw):
                app.set_skin(key)
            x += w + S(8)
        y += S(64)

        v, y = toggle_row('Яркие пальцы', 'Неоновый скелет рук поверх видео: видно, как нейросеть видит ваши руки и пальцы.',
                          s['bright_fingers'], y)
        if v != s['bright_fingers']:
            app.set_option('bright_fingers', v)
        ui.text(surf, 'Яркость видео', 16, C['text'], (area.x, y), bold=True)
        ui.text(surf, f'{s["video"]}%', 16, C['acc'], (area.right, y), 'topright', True)
        v = ui.slider(surf, 'video', pygame.Rect(area.x + S(8), y + S(34), area.w - S(16), S(24)), s['video'], 10, 100, 1)
        if v != s['video']:
            app.set_option('video', int(v))
        y += S(80)
        ui.text(surf, 'Отслеживание палочек', 16, C['text'], (area.x, y), bold=True)
        y += S(30)
        idx = 0 if s['mode'] == 'hands' else 1
        ni = ui.segmented(surf, pygame.Rect(area.x, y, min(area.w, S(460)), S(44)), ['Руки (ИИ)', 'Цветные кончики'], idx)
        if ni != idx:
            app.set_mode('hands' if ni == 0 else 'color')
        y += S(52)
        y = ui.para(surf, 'Цветные кончики — самый точный режим: обмотайте кончики яркой изолентой и кликните по ним '
                          'на видео (правый клик — сбросить цвета).', 13, C['hint'], area.x, y, area.w) + S(22)
        v, y = toggle_row('Во весь экран', 'То же самое — клавиша F11.', app.fullscreen, y)
        if v != app.fullscreen:
            app.toggle_fullscreen()
        r = pygame.Rect(area.x, y, S(200), S(44))
        if ui.button(surf, r, 'Выйти из игры', 'danger', icon='exit'):
            app.alive = False
        return r.bottom + S(10)


# ============================================================ редактор расстановки

class Editor:
    def __init__(self, app):
        self.app = app
        self.active = False
        self.sel = None
        self.drag = None
        self.bar = pygame.Rect(0, 0, 0, 0)

    def start(self, uid=None):
        self.active = True
        self.sel = uid
        self.drag = None

    def stop(self):
        self.active = False
        self.sel = None
        self.drag = None
        self.app.save_kit()

    def selected(self):
        return next((p for p in self.app.engine.pads if p['uid'] == self.sel), None)

    def geom(self, p, st):
        """Центр барабана на экране, радиусы и положения ручек поворота и размера."""
        app = self.app
        W, H = st.w, st.h
        cx, cy = st.x + p['x'] * W, st.y + p['y'] * H
        rx = p['r'] * W
        if app.style == 'mpc':
            ry = rx
        else:
            ry = rx if INSTRUMENTS[p['inst']]['type'] == 'kick' else rx * 0.85
        S = app.ui.S
        hx, hy = rotate_vec(0, -(ry + S(38)), p['rot'])
        sx, sy = rotate_vec(rx + S(16), 0, p['rot'])
        return (cx, cy), rx, ry, (cx + hx, cy + hy), (cx + sx, cy + sy)

    def outline(self, surf, p, st, color, width):
        (cx, cy), rx, ry, _, _ = self.geom(p, st)
        if self.app.style == 'mpc':
            pts = [rotate_vec(x, y, p['rot']) for x, y in ((-rx, -ry), (rx, -ry), (rx, ry), (-rx, ry))]
        else:
            pts = [rotate_vec(rx * math.cos(a), ry * math.sin(a), p['rot']) for a in (i * math.pi / 24 for i in range(48))]
        pygame.draw.lines(surf, color, True, [(cx + x, cy + y) for x, y in pts], width)

    def draw_overlay(self, surf, st):
        S = self.app.ui.S
        for p in self.app.engine.pads:
            if p['uid'] != self.sel:
                self.outline(surf, p, st, (90, 160, 190), max(1, S(1.5)))
        p = self.selected()
        if p:
            self.outline(surf, p, st, C['acc'], max(2, S(3)))
            c, rx, ry, hr, hs = self.geom(p, st)
            pygame.draw.line(surf, C['acc'], c, hr, max(2, S(2)))
            for pos, icon in ((hr, 'rot_r'), (hs, 'plus')):
                pygame.draw.circle(surf, (8, 12, 18), pos, S(17))
                pygame.draw.circle(surf, C['acc'], pos, S(15))
                draw_icon(surf, icon, pos, S(16), (8, 14, 22), max(2, S(2)))

    def toolbar(self, surf, ui):
        app = self.app
        S = ui.S
        sw, sh = surf.get_size()
        p = self.selected()
        bh = S(66)
        bw = min(sw - S(24), S(1060) if p else S(640))
        bar = pygame.Rect((sw - bw) // 2, sh - bh - S(18), bw, bh)
        self.bar = bar
        surf.blit(ui.shadow(bar.w, bar.h, S(18), S(20)), (bar.x - S(20), bar.y - S(20)))
        pygame.draw.rect(surf, C['panel'], bar, border_radius=S(18))
        pygame.draw.rect(surf, (48, 58, 80), bar, 1, border_radius=S(18))
        x = bar.x + S(14)
        cy = bar.centery
        bhh = S(42)

        def btn(w, label='', kind='normal', icon=None, enabled=True):
            nonlocal x
            r = pygame.Rect(x, cy - bhh // 2, w, bhh)
            x += w + S(8)
            return ui.button(surf, r, label, kind, icon=icon, size=14, enabled=enabled)

        def sep():
            nonlocal x
            pygame.draw.line(surf, C['line'], (x + S(2), cy - S(16)), (x + S(2), cy + S(16)))
            x += S(12)

        if p:
            if btn(S(40), icon='left'):
                self.cycle(p, -1)
            name = INSTRUMENTS[p['inst']]['name']
            nr = pygame.Rect(x, cy - bhh // 2, S(170), bhh)
            pygame.draw.rect(surf, (20, 25, 36), nr, border_radius=S(10))
            ui.text(surf, name, 14, C['text'], nr.center, 'center', True)
            x += nr.w + S(8)
            if btn(S(40), icon='right'):
                self.cycle(p, +1)
            sep()
            if btn(S(40), icon='minus'):
                self.resize(p, 1 / 1.08)
            ui.text(surf, f'{round(p["r"] * 200)}%', 14, C['hint'], (x + S(26), cy), 'center', True)
            x += S(52)
            if btn(S(40), icon='plus'):
                self.resize(p, 1.08)
            sep()
            if btn(S(40), icon='rot_l'):
                self.rotate(p, -10)
            ui.text(surf, f'{round(p["rot"])}°', 14, C['hint'], (x + S(24), cy), 'center', True)
            x += S(48)
            if btn(S(40), icon='rot_r'):
                self.rotate(p, 10)
            sep()
            if btn(S(44), kind='danger', icon='trash', enabled=len(app.engine.pads) > 1):
                app.remove_pad(p['uid'])
                self.sel = None
        else:
            ui.text(surf, 'Кликните по барабану, чтобы выбрать его', 15, C['hint'], (x + S(6), cy), 'midleft')
            x = bar.right - S(14) - S(150) - S(8) - S(120)
        if btn(S(150), 'Добавить', icon='plus', enabled=len(app.engine.pads) < MAX_PADS):
            app.menu.show('drums', ('add', None))
        if btn(S(120), 'Готово', 'primary', icon='check'):
            self.stop()
        ui.block(bar)

    def scene(self, ui, st):
        """Клики и перетаскивание по сцене (то, что не забрали меню и панель)."""
        app = self.app
        if ui.click and not ui.consumed:
            mx, my = ui.click
            p = self.selected()
            self.drag = None
            if p:
                c, rx, ry, hr, hs = self.geom(p, st)
                if math.dist((mx, my), hr) <= ui.S(20):
                    self.drag = ('rot', p['uid'])
                elif math.dist((mx, my), hs) <= ui.S(20):
                    self.drag = ('size', p['uid'])
            if not self.drag:
                u, v = (mx - st.x) / st.w, (my - st.y) / st.h
                q, d = kits.nearest_pad(app.engine.pads, u, v, app.engine.aspect, app.style, sticks=False)
                if q and d < 1.15:
                    self.sel = q['uid']
                    self.drag = ('move', q['uid'], u - q['x'], v - q['y'])
                else:
                    self.sel = None
            ui.consumed = True
        if self.drag and ui.down:
            p = self.selected()
            if p:
                mx, my = ui.mouse
                if self.drag[0] == 'move':
                    u, v = (mx - st.x) / st.w, (my - st.y) / st.h
                    p['x'] = min(0.97, max(0.03, u - self.drag[2]))
                    p['y'] = min(0.98, max(0.04, v - self.drag[3]))
                elif self.drag[0] == 'rot':
                    (cx, cy), *_ = self.geom(p, st)
                    ang = math.degrees(math.atan2(-(my - cy), mx - cx)) - 90
                    ang = (ang + 180) % 360 - 180
                    p['rot'] = 0.0 if abs(ang) < 3 else round(ang)
                else:
                    (cx, cy), *_ = self.geom(p, st)
                    rx = max(ui.S(10), math.dist((mx, my), (cx, cy)) - ui.S(16))
                    p['r'] = min(0.22, max(0.035, rx / st.w))
        elif self.drag and not ui.down:
            self.drag = None
            app.save_kit()
        if ui.wheel and not ui.wheel_used:
            p = self.selected()
            if p is None:
                u, v = (ui.mouse[0] - st.x) / st.w, (ui.mouse[1] - st.y) / st.h
                q, d = kits.nearest_pad(app.engine.pads, u, v, app.engine.aspect, app.style, sticks=False)
                if q and d < 1.2:
                    self.sel, p = q['uid'], q
            if p:
                if pygame.key.get_mods() & pygame.KMOD_SHIFT:
                    self.rotate(p, 5 * ui.wheel)
                else:
                    self.resize(p, 1.06 ** ui.wheel)

    def key(self, ev):
        p = self.selected()
        k = ev.key
        if k == pygame.K_ESCAPE:
            self.stop()
            return True
        if p is None:
            return False
        shift = ev.mod & pygame.KMOD_SHIFT
        if k in (pygame.K_DELETE, pygame.K_BACKSPACE):
            if len(self.app.engine.pads) > 1:
                self.app.remove_pad(p['uid'])
                self.sel = None
            return True
        if k == pygame.K_LEFTBRACKET:
            self.rotate(p, -5)
            return True
        if k == pygame.K_RIGHTBRACKET:
            self.rotate(p, 5)
            return True
        step = 0.02 if shift else 0.005
        moves = {pygame.K_LEFT: (-step, 0), pygame.K_RIGHT: (step, 0), pygame.K_UP: (0, -step), pygame.K_DOWN: (0, step)}
        if k in moves:
            dx, dy = moves[k]
            p['x'] = min(0.97, max(0.03, p['x'] + dx))
            p['y'] = min(0.98, max(0.04, p['y'] + dy))
            self.app.save_kit()
            return True
        return False

    def cycle(self, p, d):
        i = SELECTABLE.index(p['inst']) if p['inst'] in SELECTABLE else 0
        inst = SELECTABLE[(i + d) % len(SELECTABLE)]
        self.app.set_pad_inst(p['uid'], inst)
        self.app.audio_preview(inst)

    def resize(self, p, k):
        p['r'] = min(0.22, max(0.035, p['r'] * k))
        self.app.save_kit()

    def rotate(self, p, deg):
        r = (p['rot'] + deg + 180) % 360 - 180
        p['rot'] = 0.0 if abs(r) < 1 else r
        self.app.save_kit()
