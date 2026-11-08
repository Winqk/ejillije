# -*- coding: utf-8 -*-
"""«Своя песня»: ноты для ритм-игры по звуку файла — без нейросетей, только numpy.

1. Спектрограмма → разделение на ударную и тональную части (HPSS: медианы по времени и по частоте).
2. Темп (автокорреляция ритмической огибающей) и доли (динамическое программирование, как у Эллиса).
3. Удары по полосам: бочка — низы, малый — середина, хай-хэт — верха, тарелка — долгий звон в верхах.
4. Привязка к сетке шестнадцатых и упрощение для лёгкого и среднего уровней.
5. Тональная часть сохраняется отдельным WAV — это «песня с приглушёнными барабанами».
"""
from __future__ import annotations

import wave

import numpy as np

SR = 44100
N_FFT = 2048
HOP = 441            # 10 мс
FPS = SR / HOP
CRASH_DETECT = False   # тарелки по звуку распознаются ненадёжно (точность ~12%) — всё звонкое идёт в хай-хэт


def _frames_rfft(x, start, count):
    win = np.hanning(N_FFT).astype(np.float32)
    idx = np.arange(start, start + count)[:, None] * HOP + np.arange(N_FFT)[None, :]
    seg = x[np.clip(idx, 0, len(x) - 1)] * ((idx >= 0) & (idx < len(x)))
    return np.fft.rfft(seg * win, axis=1)


def stft_mag(x, progress=None, p0=0.0, p1=0.2):
    n = 1 + len(x) // HOP
    out = np.empty((n, N_FFT // 2 + 1), np.float32)
    for s in range(0, n, 1024):
        c = min(1024, n - s)
        out[s:s + c] = np.abs(_frames_rfft(x, s - N_FFT // (2 * HOP), c))
        if progress:
            progress(p0 + (p1 - p0) * (s + c) / n)
    return out


def _median_filter(a, w, axis, progress=None, p0=0.0, p1=0.0):
    """Скользящая медиана по оси (0 — время, 1 — частота), по кускам, чтобы хватило памяти."""
    pad = [(0, 0), (0, 0)]
    pad[axis] = (w // 2, w // 2)
    ap = np.pad(a, pad, mode='edge')
    out = np.empty_like(a)
    n = a.shape[0]
    step = 256
    for s in range(0, n, step):
        e = min(n, s + step)
        if axis == 0:
            block = np.lib.stride_tricks.sliding_window_view(ap[s:e + w - 1], w, axis=0)
        else:
            block = np.lib.stride_tricks.sliding_window_view(ap[s:e], w, axis=1)
        out[s:e] = np.median(block, axis=-1)
        if progress:
            progress(p0 + (p1 - p0) * e / n)
    return out


def hpss_masks(S, progress=None):
    H = _median_filter(S, 17, 0, progress, 0.2, 0.42)
    P = _median_filter(S, 17, 1, progress, 0.42, 0.64)
    h2, p2 = H.astype(np.float32) ** 2, P.astype(np.float32) ** 2
    denom = h2 + p2 + 1e-9
    return p2 / denom, h2 / denom


def band(S, lo, hi):
    f = np.fft.rfftfreq(N_FFT, 1 / SR)
    return (f >= lo) & (f < hi)


def flux(L, mask):
    d = np.diff(L[:, mask], axis=0, prepend=L[:1, mask])
    return np.maximum(d, 0).sum(axis=1)


def norm(e):
    s = np.percentile(e, 97)
    return e / s if s > 0 else e


def estimate_tempo(env):
    e = env - np.convolve(env, np.ones(100) / 100, mode='same')
    e = np.maximum(e, 0)
    n = len(e)
    E = np.fft.rfft(e, 2 * n)
    ac = np.fft.irfft(E * np.conj(E))[:n]
    lags = np.arange(n)
    bpm = np.where(lags > 0, 60 * FPS / np.maximum(lags, 1), 0)
    valid = (bpm >= 60) & (bpm <= 200)
    weight = np.exp(-0.5 * (np.log2(np.maximum(bpm, 1) / 115) / 0.9) ** 2)
    score = np.where(valid, ac * weight, -np.inf)
    lag = int(np.argmax(score))
    if 1 <= lag < n - 1:  # уточнение параболой
        a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
        den = a - 2 * b + c
        lag = lag + (0.5 * (a - c) / den if den != 0 else 0)
    return 60 * FPS / lag, lag


def track_beats(env, period, tight=100.0):
    """Доли методом динамического программирования (D. Ellis, 2007)."""
    n = len(env)
    loc = np.convolve(env, np.exp(-0.5 * (np.arange(-int(period), int(period) + 1) * 32 / period) ** 2), 'same')
    score = loc.copy()
    back = -np.ones(n, int)
    lo, hi = int(round(period / 2)), int(round(period * 2))
    offs = np.arange(lo, hi + 1)
    pen = -tight * np.log(offs / period) ** 2
    for t in range(hi, n):
        cand = score[t - offs] + pen
        k = int(np.argmax(cand))
        score[t] = loc[t] + cand[k]
        back[t] = t - offs[k]
    tail = slice(max(0, n - int(period * 2)), n)
    t = int(np.argmax(score[tail])) + tail.start
    beats = []
    while t >= 0:
        beats.append(t)
        t = back[t]
    beats = np.array(beats[::-1], float)
    return beats / FPS


def peaks(env, thr_k=1.35, delta=0.08, w=3, avg=60):
    mean = np.convolve(env, np.ones(2 * avg + 1) / (2 * avg + 1), 'same')
    out = []
    for i in range(w, len(env) - w):
        v = env[i]
        if v >= env[i - w:i + w + 1].max() and v > mean[i] * thr_k + delta:
            out.append(i)
    return np.array(out, int)


def analyze(x_stereo, progress=None):
    """x_stereo: float32 (n, 2) 44,1 кГц. Возвращает (ноты по уровням, bpm, маска тональной части, S)."""
    pr = progress or (lambda v: None)
    mono = x_stereo.mean(axis=1).astype(np.float32)
    S = stft_mag(mono, pr, 0.0, 0.2)
    Pm, Hm = hpss_masks(S, pr)
    P = S * Pm
    L = np.log1p(30 * P / (P.max() + 1e-9))
    pr(0.7)
    bands = {'low': band(S, 35, 130), 'mid': band(S, 150, 4000), 'snr': band(S, 1200, 5000), 'high': band(S, 6500, 16000)}
    fx = {k: norm(flux(L, m)) for k, m in bands.items()}
    full = norm(flux(L, band(S, 30, 16000)))
    bpm, lag = estimate_tempo(full)
    if bpm < 75:
        bpm, lag = bpm * 2, lag / 2
    if bpm > 170:
        bpm, lag = bpm / 2, lag * 2
    beats = track_beats(full, lag)
    pr(0.8)
    # сетка шестнадцатых (продлеваем за первую и последнюю долю)
    period = 60 / bpm
    if len(beats) < 4:
        beats = np.arange(0, len(mono) / SR, period)
    pre = np.arange(beats[0] - period, -period / 2, -period)[::-1]
    post = np.arange(beats[-1] + period, len(mono) / SR + period, period)
    allb = np.concatenate([pre, beats, post])
    grid = []
    for a, b in zip(allb[:-1], allb[1:]):
        for k in range(4):
            grid.append((a + (b - a) * k / 4, k))
    grid_t = np.array([g[0] for g in grid])
    grid_k = np.array([g[1] for g in grid])
    step = period / 4

    def snap(times):
        out = {}
        for t in times:
            i = int(np.argmin(np.abs(grid_t - t)))
            if abs(grid_t[i] - t) <= min(0.07, 0.45 * step):
                out[i] = t
        return out

    # удары по полосам; веса и пороги подобраны на песнях с известными нотами
    def pfrac(i, m):
        w = S[i:i + 3][:, m]
        return float((Pm[i:i + 3][:, m] * w).sum() / (w.sum() + 1e-9))

    hits = {}
    body = norm(flux(L, band(S, 150, 400)))
    for i in peaks(fx['low'], 1.3, 0.12):
        if fx['low'][i] + 4 * pfrac(i, bands['low']) + fx['snr'][i] > 1.68:
            hits.setdefault('kick', []).append(i / FPS)
    for i in peaks(fx['snr'], 1.3, 0.1):
        if fx['snr'][i] + pfrac(i, bands['snr']) + 0.5 * body[i] - 0.5 * fx['high'][i] > 2.15:
            hits.setdefault('snare', []).append(i / FPS)
    hen = (S[:, bands['high']] ** 2).sum(axis=1)
    med = float(np.median(hen)) + 1e-9
    crash_last = -9.0
    for i in peaks(fx['high'], 1.25, 0.08):
        if fx['high'][i] < 0.18:
            continue
        after = hen[i + 10:i + 60].mean() if i + 60 < len(hen) else 0
        before = hen[max(0, i - 30):max(1, i - 5)].mean()
        if CRASH_DETECT and fx['high'][i] > 0.5 and after > 3 * med and after > 1.8 * before and i / FPS - crash_last > 1.5:
            hits.setdefault('crash', []).append(i / FPS)
            crash_last = i / FPS
        else:
            hits.setdefault('hihat', []).append(i / FPS)
    full_notes = []
    for lane, ts in hits.items():
        for gi in snap(ts):
            full_notes.append((float(grid_t[gi]), lane, int(grid_k[gi])))
    # тарелка заменяет хай-хэт в той же клетке
    crash_t = {round(t, 3) for t, l, _ in full_notes if l == 'crash'}
    full_notes = [n for n in full_notes if not (n[1] == 'hihat' and round(n[0], 3) in crash_t)]
    full_notes.sort()
    levels = {
        'hard': [[round(t, 3), l] for t, l, _ in full_notes],
        'medium': [[round(t, 3), l] for t, l, k in full_notes if k % 2 == 0 and not (l == 'hihat' and k != 0)],
    }
    easy, last = [], {}
    crash_last = -9.0
    for t, l, k in full_notes:
        if l not in ('kick', 'snare', 'crash') or k != 0:
            continue
        if l == 'crash':
            if t - crash_last < 4:
                continue
            crash_last = t
        if t - last.get(l, -9) < 0.3:
            continue
        last[l] = t
        easy.append([round(t, 3), l])
    levels['easy'] = easy
    pr(0.85)
    return levels, float(bpm), Hm


def harmonic_stem(x_stereo, Hm, progress=None, p0=0.85, p1=1.0):
    """Тональная часть (барабаны приглушены): маска на комплексный спектр каждого канала и обратно."""
    n = len(x_stereo)
    win = np.hanning(N_FFT).astype(np.float32)
    w2 = win ** 2
    frames = Hm.shape[0]
    off = N_FFT // (2 * HOP)
    pad = N_FFT
    out = np.zeros((n + 2 * N_FFT, 2), np.float32)
    wsum = np.zeros(n + 2 * N_FFT, np.float32)
    for ch in range(2):
        x = np.ascontiguousarray(x_stereo[:, ch])
        for s in range(0, frames, 1024):
            c = min(1024, frames - s)
            X = _frames_rfft(x, s - off, c) * Hm[s:s + c]
            y = np.fft.irfft(X, N_FFT, axis=1).astype(np.float32) * win
            starts = (np.arange(s, s + c) - off) * HOP + pad
            for k in range(c):
                a = starts[k]
                out[a:a + N_FFT, ch] += y[k]
                if ch == 0:
                    wsum[a:a + N_FFT] += w2
            if progress:
                progress(p0 + (p1 - p0) * (ch * frames + s + c) / (2 * frames))
    return out[pad:pad + n] / np.maximum(wsum[pad:pad + n, None], 1e-3)


def write_wav(path, x):
    y = (np.clip(x, -1, 1) * 32767).astype('<i2')
    with wave.open(path, 'wb') as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(y.tobytes())


def build_chart(title, x_stereo, progress=None):
    """Полный разбор: ноты трёх уровней, темп и «песня без барабанов»."""
    levels, bpm, Hm = analyze(x_stereo, progress)
    stem = harmonic_stem(x_stereo, Hm, progress)
    gain = 0.95 / max(float(np.abs(stem).max()), 1e-6)
    stem *= min(gain, 1.6)
    chart = {'title': title, 'artist': 'своя песня', 'genre': 'Своя', 'preset': None, 'bpm': round(bpm, 1),
             'length': round(len(x_stereo) / SR, 2), 'charts': levels}
    return chart, stem
