"""
CASA-метрики для SpermPipeline — функции перенесены из casa_metrics.py (версия 2, 15.10.2026) БЕЗ ИЗМЕНЕНИЙ:
seg_key, fix_shifted, moving_average, window_metrics, track_metrics, to_units.

Чего здесь нет и почему: повторного прогона фильтрации с перехватом точек (rerun_filter) и сверки
с готовыми таблицами. В приложении фильтрация сама возвращает точки итоговых треков, поэтому
метрики считаются по тем же точкам за один проход.
Метрики — в скользящем окне WINDOW кадров с шагом WINDOW_STEP, по окнам трека берётся медиана.
"""
import re
from pathlib import Path
import numpy as np
import pandas as pd

# ▶ НАСТРОЙКИ (значения по умолчанию; приложение переписывает их из окна настроек)
WINDOW      = 20     # кадров в окне
WINDOW_STEP = 10     # шаг окна
VAP_SMOOTH  = 5      # точек в скользящем среднем для сглаженной траектории
FPS         = None   # кадров в секунду; None → скорости в px/кадр
UM_PER_PX   = None   # микрон на пиксель; None → расстояния в px

KEY = re.compile(r'^\s*(\d+-\d+)')


def seg_key(name: str):
    m = KEY.match(str(name))
    return m.group(1) if m else None


SHIFT = ['QUALITY', 'POSITION_X', 'POSITION_Y', 'POSITION_Z', 'POSITION_T', 'FRAME', 'RADIUS']


def fix_shifted(spots_path, tmp_dir):
    """В части файлов спотов (видео 1, 2, 15) значения сдвинуты на одну колонку вправо относительно
    заголовков, начиная с QUALITY: в QUALITY лежит диапазон кадров, в POSITION_X — QUALITY, в POSITION_Y —
    настоящий X, в POSITION_Z — настоящий Y. Если файл такой — пишет исправленную копию и возвращает её путь."""
    df = pd.read_excel(spots_path)
    if not df['QUALITY'].iloc[1:50].astype(str).str.match(r'^\d+-\d+$').mean() > 0.9:
        return None
    body = df.iloc[1:].copy()
    for a, b in zip(SHIFT[:-1], SHIFT[1:]):
        body[a] = df[b].iloc[1:].values
    dst = tmp_dir / '_fixed_spots' / spots_path.parent.name / spots_path.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    pd.concat([df.iloc[:1], body]).to_excel(dst, index=False)
    return dst


def moving_average(xy: np.ndarray, k: int):
    """Центрированное скользящее среднее по k точкам. Возвращает (сглаженные точки, маска «окно полное»).
    На краях трека полного окна нет: там сглаженная точка смещена внутрь трека, и такие точки
    в VAP, STR, ALH и BCF не используются (иначе путь «поджимается» и STR > 1)."""
    n = len(xy)
    if k <= 1 or n < 2:
        return xy.astype(float).copy(), np.ones(n, bool)
    h = k // 2
    cs = np.vstack([np.zeros((1, 2)), np.cumsum(xy, axis=0)])
    lo = np.clip(np.arange(n) - h, 0, n)
    hi = np.clip(np.arange(n) + h + 1, 0, n)
    avg = (cs[hi] - cs[lo]) / (hi - lo)[:, None]
    full = (np.arange(n) >= h) & (np.arange(n) <= n - 1 - h)
    return avg, full


def window_metrics(raw: np.ndarray, avg: np.ndarray, full: np.ndarray) -> dict:
    n = len(raw) - 1                                         # шагов = кадров в окне
    vcl = np.linalg.norm(np.diff(raw, axis=0), axis=1).sum() / n
    vsl = np.linalg.norm(raw[-1] - raw[0]) / n                 # по ВОЗ: первая и последняя точка трека
    # сглаженная траектория — только точки с полным окном сглаживания
    idx = np.flatnonzero(full)
    if len(idx) < 2:
        idx = np.arange(len(raw))
    a = avg[idx[0]:idx[-1] + 1]
    r = raw[idx[0]:idx[-1] + 1]
    n_a = idx[-1] - idx[0]
    path_avg = np.linalg.norm(np.diff(a, axis=0), axis=1).sum()
    path_raw_in = np.linalg.norm(np.diff(r, axis=0), axis=1).sum()   # реальный путь на том же отрезке, что VAP
    vap = path_avg / n_a
    eps = 1e-3 * n                                             # путь < 0.001 px/кадр — трек стоит: LIN/STR/WOB = 0
    str_ = np.linalg.norm(a[-1] - a[0]) / path_avg if path_avg > eps else 0.0   # ≤ 1
    # боковое (перпендикулярное) отклонение от сглаженной траектории, со знаком — с какой стороны
    tang = np.gradient(a, axis=0)
    tn = np.linalg.norm(tang, axis=1)
    tn[tn == 0] = np.nan
    off = r - a
    lateral = (tang[:, 0] * off[:, 1] - tang[:, 1] * off[:, 0]) / tn
    lat = np.abs(lateral[np.isfinite(lateral)])
    s = np.sign(lateral[np.isfinite(lateral) & (lateral != 0)])
    cross = int((s[1:] != s[:-1]).sum()) if len(s) > 1 else 0
    return {'VCL': vcl, 'VSL': vsl, 'VAP': vap,
            'BCF': cross / n_a,
            'LIN': vsl / vcl if vcl * n > eps else 0.0,
            'STR': str_,
            'WOB': path_avg / path_raw_in if path_raw_in > eps else 0.0,   # VAP/VCL на одном отрезке
            'ALH_mean': 2 * lat.mean() if len(lat) else 0.0,
            'ALH_max': 2 * lat.max() if len(lat) else 0.0}


def track_metrics(g: pd.DataFrame) -> dict:
    g = g.sort_values('FRAME').drop_duplicates('FRAME')
    f = g.FRAME.values
    full = np.arange(f[0], f[-1] + 1)
    raw = np.column_stack([np.interp(full, f, g.POSITION_X.values), np.interp(full, f, g.POSITION_Y.values)])
    avg, ok = moving_average(raw, VAP_SMOOTH)
    starts = list(range(0, len(full) - WINDOW, WINDOW_STEP)) or [0]
    rows = [window_metrics(raw[s:s + WINDOW + 1], avg[s:s + WINDOW + 1], ok[s:s + WINDOW + 1]) for s in starts]
    w = pd.DataFrame(rows)
    out = {f'CASA_{c}': w[c].median() for c in w.columns}
    out['CASA_N_WINDOWS'] = len(w)
    out['CASA_GAP_FILLED_FRAC'] = 1 - len(f) / len(full)
    return out


def to_units(df: pd.DataFrame) -> pd.DataFrame:
    k = (UM_PER_PX or 1) * (FPS or 1)
    for c in ['CASA_VCL', 'CASA_VSL', 'CASA_VAP']:
        df[c] *= k
    for c in ['CASA_ALH_mean', 'CASA_ALH_max']:
        df[c] *= (UM_PER_PX or 1)
    df['CASA_BCF'] *= (FPS or 1)
    return df
