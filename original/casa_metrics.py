"""
CASA_METRICS.py
Считает CASA-метрики (VCL, VSL, VAP, LIN, STR, WOB, ALH, BCF) по координатам точек итоговых треков.

Почему так сложно: «super filtration with excel 10 files.py» сохраняет только таблицу треков,
а точки итоговых треков (после сшивки, разрезов у границ, свапов коллизий и фильтра по длине)
нигде не пишет. Поэтому этот скрипт заново прогоняет ТВОЮ фильтрацию (файл не меняется, функции
берутся из него как из модуля) на BEFORE FILTER SPOTS, перехватывает точки каждого итогового трека
и считает по ним метрики. Номера треков те же, что в готовых таблицах (1..N внутри каждого отрезка
видео), поэтому новые колонки просто приклеиваются к таблицам из CONCATENATED TABLES.

Для каждого отрезка проверяется, что повторный прогон дал то же число треков и тот же
TRACK_MEAN_SPEED, что в готовой таблице. Если нет — метрики для этого отрезка не приклеиваются.

Метрики считаются в скользящем окне фиксированной длины (WINDOW кадров), чтобы длинный трек
не выглядел «кривее» короткого. По окнам трека берётся медиана.
  VCL — скорость по реальной (криволинейной) траектории
  VSL — скорость по прямой от первой до последней точки окна
  VAP — скорость по сглаженной траектории (скользящее среднее по VAP_SMOOTH точкам)
  LIN = VSL/VCL, STR = VSL/VAP, WOB = VAP/VCL
  ALH — амплитуда бокового смещения головки: 2 × расстояние точки до сглаженной траектории
        (ALH_mean — среднее, ALH_max — максимум в окне)
  BCF — частота пересечений реальной траектории со сглаженной (раз за кадр, или Гц при заданном FPS)
Пропуски кадров внутри трека заполняются линейной интерполяцией (доля заполненных кадров — GAP_FILLED_FRAC).

Версия 2 (15.10.2026), проверка формул — test_casa_metrics.py:
  • VAP, STR, ALH, BCF считаются только по точкам с полным окном сглаживания (раньше края трека «поджимали»
    сглаженную траекторию: VAP занижен, STR > 1 у 748 треков); STR = прямолинейность сглаженной траектории ≤ 1.
  • ALH — только боковое (перпендикулярное) отклонение от сглаженной траектории, без рывков вдоль пути.
  • WOB = VAP/VCL на одном отрезке; у стоящих треков (путь < 0.001 px/кадр) LIN, STR, WOB = 0.
  • Файлы спотов со сдвинутыми колонками (видео 1, 2, 15: в POSITION_X лежит QUALITY, X — в POSITION_Y,
    Y — в POSITION_Z) исправляются на лету. Старые таблицы этих отрезков тоже были неверными, поэтому
    для них берётся новая таблица треков из повторного прогона, без сверки со старой.
Выход: OUTPUT_DIR / "CASA TRACKS v2" (таблицы треков + CASA-колонки), OUTPUT_DIR / "CASA SPOTS v2" (точки итоговых треков).

Запуск: python casa_metrics.py   (интерпретатор из .venv: нужны pandas, numpy, networkx, openpyxl)
"""
import contextlib, importlib.util, io, re, sys, time
from multiprocessing import Pool
from pathlib import Path
import numpy as np
import pandas as pd

# =============================================================================
# ▶ НАСТРОЙКИ
# =============================================================================
FILTER_SCRIPT = r"C:\Users\DzhalilS\PycharmProjects\pythonProject1\.venv\super filtration with excel 10 files.py"
SPOTS_ROOT    = r"D:\Lab\20 videos\Without grid\BEFORE FILTER SPOTS"
TRACKS_ROOT   = r"D:\Lab\20 videos\Without grid\BEFORE FILTER"
CONCAT_DIR    = r"D:\Lab\20 videos\Without grid\CONCATENATED TABLES\link_g 30, link_sp_diff 0.85, link_m_d 100, thresh 160, col_dist 30, min_dur 20"
OUTPUT_DIR    = r"D:\Lab\20 videos\Without grid\CASA METRICS\link_g 30, link_sp_diff 0.85, link_m_d 100, thresh 160, col_dist 30, min_dur 20"

WINDOW      = 20     # кадров в окне (= минимальная длина трека в фильтре, поэтому значение есть у каждого трека)
WINDOW_STEP = 10     # шаг окна; = WINDOW — окна без перекрытия
VAP_SMOOTH  = 5      # точек в скользящем среднем для сглаженной траектории (как в CASA)

FPS       = None     # кадров в секунду; None → скорости в px/кадр, BCF в разах за кадр
UM_PER_PX = None     # микрон на пиксель; None → расстояния в px

VIDEOS    = None     # например [1, 2, 10]; None = все видео из CONCAT_DIR
N_WORKERS = 8        # параллельных процессов
SPEED_TOL = 1e-4     # допуск при сравнении TRACK_MEAN_SPEED с готовой таблицей

# =============================================================================
# 1. ФАЙЛЫ: какой файл спотов дал какой отрезок готовой таблицы
# =============================================================================
KEY = re.compile(r'^\s*(\d+-\d+)')


def seg_key(name: str):
    m = KEY.match(str(name))
    return m.group(1) if m else None


def concat_tables() -> dict:
    out = {}
    for f in Path(CONCAT_DIR).glob('*.xlsx'):
        if f.name.startswith('~$'):
            continue
        vid = int(re.match(r'(\d+)v', f.name).group(1))
        if VIDEOS and vid not in VIDEOS:
            continue
        df = pd.read_excel(f)
        col = 'SOURCE_FILE' if 'SOURCE_FILE' in df.columns else 'Frame_range'
        df['SEGMENT'] = df[col].map(seg_key)
        out[vid] = (f, df)
    return out


def spot_candidates(vid: int, segments: set) -> dict:
    """Для каждого отрезка — список подходящих файлов спотов (в папке 'N video new' и 'N video').
    Берётся та папка, где совпало больше отрезков; дубли с разными настройками TrackMate проверяются все."""
    best = None
    for folder in [f'{vid} video new', f'{vid} video']:
        d = Path(SPOTS_ROOT) / folder
        if not d.exists():
            continue
        files = [f for f in d.glob('*.xlsx') if 'allspots' in f.name and not f.name.startswith('~$')]
        cands = {}
        for f in files:
            k = seg_key(f.name)
            if k in segments:
                cands.setdefault(k, []).append(f)
        if best is None or len(cands) > len(best[1]):
            best = (folder, cands)
    return best


def tracks_file(spots_file: Path) -> Path:
    """Файл треков из BEFORE FILTER. Твой process_video его читает, но для расчёта не использует;
    если пары нет, передаётся сам файл спотов."""
    d = Path(TRACKS_ROOT) / spots_file.parent.name
    k = seg_key(spots_file.name)
    for f in sorted(d.glob('*.xlsx')) if d.exists() else []:
        if seg_key(f.name) == k and 'tracks' in f.name:
            return f
    return spots_file

# =============================================================================
# 2. ПОВТОРНЫЙ ПРОГОН ФИЛЬТРАЦИИ С ПЕРЕХВАТОМ ТОЧЕК
# =============================================================================

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


def load_filter_module():
    spec = importlib.util.spec_from_file_location('user_filter', FILTER_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)   # блок __main__ не выполняется
    return mod


def rerun_filter(spots_path: Path, tmp_out: Path):
    """Возвращает (таблица треков как у тебя, точки итоговых треков с новыми TRACK_ID 1..N)."""
    mod = load_filter_module()
    groups = []
    orig = mod.calc_track_stats

    def capture(group):
        g = group.sort_values('FRAME').reset_index(drop=True)
        g = g[~g.duplicated(subset='FRAME', keep='first')]   # так же, как в calc_track_stats
        groups.append(g[['FRAME', 'POSITION_X', 'POSITION_Y']].copy())
        return orig(group)

    mod.calc_track_stats = capture
    with contextlib.redirect_stdout(io.StringIO()):
        mod.process_video(spots_path, tracks_file(spots_path), tmp_out, seg_key(spots_path.name))
    tracks = pd.read_excel(tmp_out)
    # process_video идёт по трекам в порядке возрастания старого TRACK_ID и потом нумерует их 1..N
    # в том же порядке → i-я перехваченная группа = трек i+1
    spots = pd.concat([g.assign(TRACK_ID=i + 1) for i, g in enumerate(groups)], ignore_index=True)
    return tracks, spots

# =============================================================================
# 3. CASA-МЕТРИКИ
# =============================================================================

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

# =============================================================================
# 4. ОДИН ОТРЕЗОК (выполняется в отдельном процессе)
# =============================================================================

def process_segment(job):
    vid, seg, spots_path, expected_speed, tmp_dir = job
    t0 = time.time()
    tmp_out = Path(tmp_dir) / f'{vid}v_{seg}_{spots_path.stem[:40]}.xlsx'
    try:
        fixed = fix_shifted(spots_path, Path(tmp_dir))
        tracks, spots = rerun_filter(fixed or spots_path, tmp_out)
    except Exception as e:
        return {'vid': vid, 'seg': seg, 'file': spots_path.name, 'error': repr(e)}
    got = tracks.sort_values('TRACK_ID').TRACK_MEAN_SPEED.values
    exp = np.asarray(expected_speed)
    same_n = len(got) == len(exp)
    n_speed_ok = int((np.abs(got - exp) <= SPEED_TOL).sum()) if same_n else 0
    metrics = pd.DataFrame([{'TRACK_ID': tid, **track_metrics(g)} for tid, g in spots.groupby('TRACK_ID')])
    # если колонки были сдвинуты, старая таблица тоже неверная: сверять не с чем, берём новую таблицу треков
    return {'vid': vid, 'seg': seg, 'file': spots_path.name, 'folder': spots_path.parent.name,
            'shift_fixed': fixed is not None,
            'n_expected': len(exp), 'n_rerun': len(got), 'n_speed_match': n_speed_ok,
            'ok': fixed is not None or (same_n and n_speed_ok == len(exp)), 'sec': round(time.time() - t0, 1),
            'metrics': metrics, 'spots': spots, 'tracks': tracks}

# =============================================================================
# 5. MAIN
# =============================================================================

def main():
    out_dir = Path(OUTPUT_DIR); out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = out_dir / '_rerun_tracks'; tmp_dir.mkdir(exist_ok=True)
    tracks_dir = out_dir / 'CASA TRACKS v2'; tracks_dir.mkdir(exist_ok=True)
    spots_dir = out_dir / 'CASA SPOTS v2'; spots_dir.mkdir(exist_ok=True)
    tables = concat_tables()

    jobs, mapping = [], []
    for vid, (f, df) in sorted(tables.items()):
        segs = set(df.SEGMENT.dropna())
        folder, cands = spot_candidates(vid, segs)
        for seg in sorted(segs):
            exp = df[df.SEGMENT == seg].sort_values('TRACK_ID').TRACK_MEAN_SPEED.values
            files = cands.get(seg, [])
            mapping.append({'video': vid, 'segment': seg, 'folder': folder, 'candidates': len(files)})
            for sp in files:
                jobs.append((vid, seg, sp, exp, str(tmp_dir)))
    print(f'Видео: {len(tables)}, отрезков: {len(mapping)}, прогонов фильтра: {len(jobs)}', flush=True)

    results = []
    with Pool(N_WORKERS) as pool:
        for i, r in enumerate(pool.imap_unordered(process_segment, jobs), 1):
            results.append(r)
            st = 'ОШИБКА ' + r['error'] if 'error' in r else ('OK' if r['ok'] else
                 f"не совпало: треков {r['n_rerun']} vs {r['n_expected']}, скоростей {r['n_speed_match']}")
            print(f"[{i}/{len(jobs)}] {r['vid']}v {r['seg']} {r['file'][:45]}: {st}", flush=True)

    # для каждого отрезка — лучший из кандидатов (полное совпадение, иначе больше совпавших скоростей)
    best = {}
    for r in results:
        if 'error' in r:
            continue
        k = (r['vid'], r['seg'])
        score = (r['ok'], not r['shift_fixed'], r['n_speed_match'], -abs(r['n_rerun'] - r['n_expected']))
        if k not in best or score > best[k][0]:
            best[k] = (score, r)

    check_rows = []
    for m in mapping:
        r = best.get((m['video'], m['segment']), (None, None))[1]
        err = [x['error'] for x in results if x['vid'] == m['video'] and x['seg'] == m['segment'] and 'error' in x]
        check_rows.append({**m, 'used_file': r['file'] if r else None,
                           'columns_shift_fixed': bool(r and r['shift_fixed']),
                           'n_expected': r['n_expected'] if r else None, 'n_rerun': r['n_rerun'] if r else None,
                           'n_speed_match': r['n_speed_match'] if r else None,
                           'metrics_attached': bool(r and r['ok']), 'error': '; '.join(err) or None})
    check = pd.DataFrame(check_rows)

    for vid, (f, df) in sorted(tables.items()):
        parts, spot_parts, rows = [], [], []
        for seg in sorted(set(df.SEGMENT.dropna())):
            r = best.get((vid, seg), (None, None))[1]
            if r and r['shift_fixed']:
                rows.append(r['tracks'].assign(SEGMENT=seg))      # треки заново, по правильным координатам
            else:
                rows.append(df[df.SEGMENT == seg])
            if r and r['ok']:
                parts.append(r['metrics'].assign(SEGMENT=seg))
                spot_parts.append(r['spots'].assign(SEGMENT=seg))
        out = pd.concat(rows, ignore_index=True)
        if parts:
            met = to_units(pd.concat(parts, ignore_index=True))
            out = out.merge(met, on=['SEGMENT', 'TRACK_ID'], how='left')
        out.to_excel(tracks_dir / f.name, index=False)
        if spot_parts:
            pd.concat(spot_parts, ignore_index=True)[['SEGMENT', 'TRACK_ID', 'FRAME', 'POSITION_X', 'POSITION_Y']] \
              .to_excel(spots_dir / f'{vid}v spots of final tracks.xlsx', index=False)

    units = pd.DataFrame({'параметр': ['WINDOW', 'WINDOW_STEP', 'VAP_SMOOTH', 'FPS', 'UM_PER_PX', 'скорости', 'ALH', 'BCF'],
                          'значение': [WINDOW, WINDOW_STEP, VAP_SMOOTH, FPS, UM_PER_PX,
                                       'мкм/с' if FPS and UM_PER_PX else 'px/с' if FPS else 'мкм/кадр' if UM_PER_PX else 'px/кадр',
                                       'мкм' if UM_PER_PX else 'px', 'Гц' if FPS else 'раз за кадр']})
    with pd.ExcelWriter(out_dir / '_check.xlsx') as xw:
        check.to_excel(xw, sheet_name='проверка отрезков', index=False)
        pd.DataFrame([{k: v for k, v in r.items() if k not in ('metrics', 'spots')} for r in results]) \
          .to_excel(xw, sheet_name='все прогоны', index=False)
        units.to_excel(xw, sheet_name='настройки', index=False)

    ok = check.metrics_attached.sum()
    print(f'\nОтрезков с совпадением и метриками: {ok} из {len(check)}')
    print(check[~check.metrics_attached].to_string(index=False))
    print(f'[✓] {out_dir}')


if __name__ == '__main__':
    main()
