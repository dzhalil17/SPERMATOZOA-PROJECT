"""
Шаг 6 — KDE: плотность распределения скорости треков каждого видео (по скрипту «KDE studying.py»).

Ядро гауссово, ширина = std / BANDWIDTH_DIV (в исходном скрипте std / 4), сетка — от 0 до самой большой скорости.
После кластеризации на тот же график ложатся вклады классов (IM, RP, SP, NP, шум): плотность класса × его доля,
так что сумма вкладов = общая кривая. Пунктиром — границы im_cut и rp_cut, которые нашла кластеризация.

На выходе для каждого видео — <видео>\\6 KDE\\KDE <видео>.png и .xlsx (сетка и плотности),
в «_ВСЕ ВИДЕО» — kde_summary.xlsx: ширина ядра, вершины кривой и глубина провала между первыми двумя.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

# =============================================================================
# ▶ НАСТРОЙКИ
# =============================================================================
X_COL = 'CASA_VAP'      # по какой колонке строить плотность
BANDWIDTH_DIV = 4.0     # ширина ядра = std / BANDWIDTH_DIV
GRID_POINTS = 512       # точек на кривой
REFLECT_AT_ZERO = True  # скорость не бывает < 0: часть ядра, ушедшая за 0, отражается обратно.
                        # Без этого пик неподвижных у нуля занижен почти вдвое (половина его ядер — «за нулём»).
LOG_X = False           # строить по log10(скорости); нулевые скорости тогда не попадают на график
BY_CLUSTER = True       # вклады классов из кластеризации (нужна tracks_labeled.xlsx)
SHOW_CUTS = True        # линии im_cut / rp_cut
XLIM_QUANTILE = 0.995   # ось X на графике — до этого перцентиля скорости (один выброс не сжимает всю картинку);
                        # кривая и таблица всё равно считаются до самой большой скорости; 1 — показать всё

COLORS = {'IM': '#2c3e50', 'RP': '#c0392b', 'RP_slow': '#e67e22', 'SP': '#f1c40f', 'NP': '#8e44ad',
          'noise': '#999999'}
ORDER = ['IM', 'SP', 'RP_slow', 'NP', 'RP', 'noise']


def density(x: np.ndarray, grid: np.ndarray, bw: float, lo: float | None) -> np.ndarray:
    """Гауссово KDE на сетке (то же, что KernelDensity(kernel='gaussian') + exp(score_samples)).
    lo — нижняя граница, от которой ядра отражаются (None — без отражения)."""
    if len(x) == 0:
        return np.zeros_like(grid)
    out = np.zeros_like(grid)
    for i in range(0, len(x), 2000):                     # кусками, чтобы не держать матрицу треки × сетка целиком
        xs = x[i:i + 2000][:, None]
        out += np.exp(-0.5 * ((grid[None, :] - xs) / bw) ** 2).sum(0)
        if lo is not None:
            out += np.exp(-0.5 * ((grid[None, :] - (2 * lo - xs)) / bw) ** 2).sum(0)
    return out / (len(x) * bw * np.sqrt(2 * np.pi))


def peaks_and_dip(grid: np.ndarray, d: np.ndarray):
    """Вершины кривой (локальные максимумы) и провал между двумя первыми: минимум между ними / меньшая вершина.
    0 — между пиками пусто (чёткий разрыв), 1 — провала нет. Это описание кривой, а не правило классификации."""
    i = np.where((d[1:-1] > d[:-2]) & (d[1:-1] >= d[2:]))[0] + 1
    if d[0] > d[1]:
        i = np.r_[0, i]
    pk = [float(grid[k]) for k in i]
    dip = None
    if len(i) >= 2:
        a, b = i[0], i[1]
        dip = float(d[a:b + 1].min() / min(d[a], d[b]))
    return pk, dip


def one_video(v: str, x: np.ndarray, classes: np.ndarray | None, cuts: dict, out_dir: Path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    keep = np.isfinite(x)
    if LOG_X:
        keep &= x > 0
    x = x[keep]
    classes = classes[keep] if classes is not None else None
    if LOG_X:
        x = np.log10(x)
    n = len(x)
    if n < 2:
        return None
    bw = float(np.std(x)) / BANDWIDTH_DIV
    if bw <= 0:
        return None
    lo = None if LOG_X or not REFLECT_AT_ZERO else 0.0
    start = float(x.min()) if LOG_X else 0.0
    grid = np.linspace(start, float(x.max()), GRID_POINTS)
    total = density(x, grid, bw, lo)
    table = {'x': grid, 'all': total}

    fig, ax = plt.subplots(figsize=(8, 4.5))
    if classes is not None:
        names = [c for c in ORDER if c in set(classes)] + sorted(set(classes) - set(ORDER))
        for c in names:
            m = classes == c
            part = density(x[m], grid, bw, lo) * m.sum() / n          # вклад класса: плотность × доля
            table[c] = part
            ax.fill_between(grid, part, color=COLORS.get(c, '#5dade2'), alpha=0.35, lw=0,
                            label=f'{c} ({m.mean() * 100:.1f}%)')
    ax.plot(grid, total, color='black', lw=1.6, label=f'все треки (n={n})')
    if SHOW_CUTS:
        for key, style in (('im_cut', '--'), ('rp_cut', ':')):
            c = cuts.get(key)
            if c is not None and np.isfinite(c) and (c > 0 or not LOG_X):
                cx = np.log10(c) if LOG_X else c
                ax.axvline(cx, color='#555555', ls=style, lw=1)
                ax.text(cx, ax.get_ylim()[1] * 0.97, f' {key} {c:.2f}', fontsize=8, va='top', color='#555555')
    pk, dip = peaks_and_dip(grid, total)
    ax.set_xlabel(f'{"log10 " if LOG_X else ""}{X_COL} (px/frame)')
    ax.set_ylabel('плотность')
    ax.set_title(f'{v}: KDE, ширина ядра {bw:.3g} (std/{BANDWIDTH_DIV:g})', fontsize=10)
    ax.set_ylim(bottom=0)
    if 0 < XLIM_QUANTILE < 1:
        ax.set_xlim(start, max(float(np.quantile(x, XLIM_QUANTILE)) * 1.05, grid[1]))
    ax.legend(fontsize=8)
    fig.tight_layout()
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_dir / f'KDE {v}.png', dpi=150)
    plt.close(fig)
    pd.DataFrame(table).to_excel(out_dir / f'KDE {v}.xlsx', index=False)
    un = (lambda p: 10 ** p) if LOG_X else (lambda p: p)
    return {'video': v, 'n_tracks': n, 'bandwidth': round(bw, 4), 'n_peaks': len(pk),
            'peak_1': round(un(pk[0]), 3) if pk else None, 'peak_2': round(un(pk[1]), 3) if len(pk) > 1 else None,
            'dip_1_2': round(dip, 3) if dip is not None else None,
            'im_cut': cuts.get('im_cut'), 'rp_cut': cuts.get('rp_cut')}


def run(casa_files: list, all_dir: Path, out_dir_of, log=print, summary_file: str = 'summary_all_videos.xlsx'):
    """casa_files — таблицы CASA TRACKS (имя файла = видео); если кластеризация уже прошла, треки с классами
    берутся из «_ВСЕ ВИДЕО\\tracks_labeled.xlsx», границы — из сводки. out_dir_of(видео) → папка графиков."""
    all_dir = Path(all_dir)
    labeled, cuts = None, {}
    lab_path = all_dir / 'tracks_labeled.xlsx'
    if BY_CLUSTER and lab_path.exists():
        labeled = pd.read_excel(lab_path, dtype={'video': str})
        if X_COL not in labeled or 'cluster' not in labeled:
            labeled = None
    summ = all_dir / summary_file
    if summ.exists():
        pv = pd.read_excel(summ, sheet_name='per_video', dtype={'video': str})
        cuts = {str(r['video']): {'im_cut': r.get('im_cut'), 'rp_cut': r.get('rp_cut')} for _, r in pv.iterrows()}
    rows = []
    if labeled is not None:
        log(f'Треки с классами: {lab_path.name}')
        groups = [(v, d[X_COL].to_numpy(float), d['cluster'].astype(str).to_numpy())
                  for v, d in labeled.groupby(labeled['video'].astype(str), sort=False)]
    else:
        groups = []
        for f in casa_files:
            d = pd.read_excel(f)
            if X_COL not in d:
                log(f'  {Path(f).name}: нет колонки {X_COL} — пропускаю')
                continue
            groups.append((Path(f).stem, pd.to_numeric(d[X_COL], errors='coerce').to_numpy(float), None))
    for v, x, cl in groups:
        r = one_video(v, x, cl, cuts.get(v, {}), Path(out_dir_of(v)))
        if r is None:
            log(f'  {v}: меньше двух треков или все скорости одинаковы — KDE не строю')
            continue
        rows.append(r)
        log(f'  {v}: треков {r["n_tracks"]}, ширина ядра {r["bandwidth"]}, вершин {r["n_peaks"]}'
            + (f', провал между 1-й и 2-й {r["dip_1_2"]}' if r['dip_1_2'] is not None else ''))
    if rows:
        all_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_excel(all_dir / 'kde_summary.xlsx', index=False)
        log(f'Сводка KDE: {all_dir / "kde_summary.xlsx"}')
    return rows
