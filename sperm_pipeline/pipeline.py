"""
Весь путь от видео до кластеров одной командой:

  00 КАДРЫ        видео .mpg → кадры (ffmpeg) → отрезки + _rejected (GET SEQUENCES IN ORDER FROM VIDEO.py) → frames.py
  0 TRACKMATE      кадры → Fiji без окна: 8-bit, контраст, Gaussian, Find Maxima, LoG + LAP → CSV  → trackmate.py
  1 ТАБЛИЦЫ        оформление экспортов TrackMate (замена макросов Excel)          → tables.py
  2 ФИЛЬТРАЦИЯ     супер-фильтрация каждого отрезка видео (*proc.xlsx + журнал)     → filtration.py
  3 СКЛЕЙКА        таблицы треков всех отрезков одного видео в одну (CONCATTING.py)
  4 CASA           CASA-метрики по точкам итоговых треков (casa_metrics.py v2)      → casa.py
  5 КЛАСТЕРИЗАЦИЯ  HDBSCAN по VAP/STR/LIN (hdbscan casa 2d.py)                       → clustering.py

Шаги 2–4 идут за один проход по каждому отрезку: фильтрация сразу отдаёт точки итоговых треков,
и метрики считаются по ним (раньше casa_metrics.py ради этого заново прогонял фильтрацию).
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import time
import traceback
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import settings as S
from . import tables

DIR_FRAMES = '00 КАДРЫ'
DIR_TRACKMATE = '0 TRACKMATE'
DIR_TABLES = '1 ТАБЛИЦЫ'
DIR_FILTER = '2 ФИЛЬТРАЦИЯ'
DIR_CONCAT = '3 СКЛЕЙКА'
DIR_CASA_TRACKS = '4 CASA TRACKS'
DIR_CASA_SPOTS = '4 CASA SPOTS'
DIR_CLUSTER = '5 КЛАСТЕРИЗАЦИЯ'


class Stopped(Exception):
    pass


# =============================================================================
# поиск отрезков
# =============================================================================

def seg_of(name: str) -> str:
    """Отрезок видео по имени файла: «1223-1622» из «1223-1622_allspots (1).xlsx» (как seg_key в casa_metrics);
    если диапазона кадров в начале имени нет — имя до «(» или «_»."""
    m = re.match(r'^\s*(\d+-\d+)', name)
    if m:
        return m.group(1)
    stem = Path(name).stem
    for ch in '(_':
        if ch in stem:
            stem = stem[:stem.index(ch)]
    return stem.strip()


def is_spots(f: Path) -> bool:
    n = f.name.lower()
    return f.suffix.lower() == '.xlsx' and 'spots' in n and not n.startswith('~$')


def is_tracks(f: Path) -> bool:
    n = f.name.lower()
    return f.suffix.lower() == '.xlsx' and 'tracks' in n and 'spots' not in n and not n.startswith('~$')


def find_videos(spots_root: Path, tracks_root: Path | None) -> list:
    """[(имя видео, [(отрезок, файл спотов, файл треков), ...])]. Видео = папка с файлами спотов
    (сам spots_root или его подпапки). Треки ищутся в tracks_root в подпапке с тем же именем."""
    videos = []
    for d in tables.video_dirs(spots_root, is_spots):
        if tracks_root:
            td = tracks_root if d == spots_root else tracks_root / d.name
        else:
            td = d
        tracks = {}
        if td.exists():
            for f in sorted(td.iterdir()):
                if f.is_file() and is_tracks(f):
                    tracks.setdefault(seg_of(f.name), f)
        segs, seen = [], {}
        for f in sorted(d.iterdir()):
            if f.is_file() and is_spots(f):
                k = seg_of(f.name)
                if k in seen:
                    raise ValueError(f'В папке «{d}» два файла спотов для отрезка {k}: «{seen[k].name}» и «{f.name}». '
                                     f'Оставьте один (иначе треки отрезка посчитаются дважды).')
                seen[k] = f
                # нет файла треков — передаём сам файл спотов (фильтр читает таблицу треков, но не использует)
                segs.append((k, f, tracks.get(k, f)))
        segs.sort(key=lambda s: s[0])   # порядок отрезков как в casa_metrics.py: sorted(set(SEGMENT))
        videos.append((d.name, segs))
    return videos


# =============================================================================
# один отрезок (в отдельном процессе)
# =============================================================================

def run_segment(job: dict) -> dict:
    """Фильтрация + CASA-метрики одного отрезка. Весь вывод фильтра — в журнал отрезка."""
    from . import filtration, casa
    S.apply(job['settings'])
    seg, spots_path, tracks_path = job['seg'], Path(job['spots']), Path(job['tracks'])
    out_dir = Path(job['out_dir'])
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / f'{seg}_журнал.txt'
    t0 = time.time()
    res = {'video': job['video'], 'seg': seg, 'file': spots_path.name}
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            pd.set_option('display.max_columns', None)
            pd.set_option('display.max_rows', None)
            fixed = casa.fix_shifted(spots_path, out_dir)
            if fixed is not None:
                print(f'[SpermPipeline] столбцы спотов сдвинуты — исправленная копия: {fixed}')
            tracks, spots = filtration.process_video(fixed or spots_path, tracks_path,
                                                     out_dir / f'{seg}{filtration.OUTPUT_SUFFIX}.xlsx', seg)
        metrics = pd.DataFrame([{'TRACK_ID': tid, **casa.track_metrics(g)} for tid, g in spots.groupby('TRACK_ID')])
        res.update(ok=True, shift_fixed=fixed is not None, n_tracks=len(tracks), tracks=tracks, spots=spots,
                   metrics=metrics)
    except Exception as e:
        buf.write('\n' + traceback.format_exc())
        res.update(ok=False, error=f'{type(e).__name__}: {e}')
    res['sec'] = round(time.time() - t0, 1)
    log_path.write_text(buf.getvalue(), encoding='utf-8')
    return res


# =============================================================================
# весь прогон
# =============================================================================

def run(cfg: dict, log=print, stop=lambda: False) -> Path:
    g = cfg['general']
    out_root = Path(g['output_dir'])
    if not g['output_dir']:
        raise ValueError('Не задана папка результатов')
    out_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y-%m-%d %H-%M')
    (out_root / f'настройки {stamp}.json').write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
    S.apply(cfg)

    # ---------- 00. нарезка видео на кадры и отрезки ----------
    if g.get('step_frames'):
        from . import frames
        if not g.get('mpg_dir'):
            raise ValueError('Шаг 00 включён, но не задана папка с видео (.mpg)')
        frames_dir = Path(g['frames_dir']) if g.get('frames_dir') else out_root / DIR_FRAMES
        log(f'\n=== 00. НАРЕЗКА ВИДЕО НА КАДРЫ (ffmpeg) И ОТРЕЗКИ ===')
        log(f'Кадры: {frames_dir}')
        try:
            frames.run(g.get('ffmpeg_exe', ''), g['mpg_dir'], str(frames_dir), log, stop)
        except KeyboardInterrupt:
            raise Stopped()
        g['videos_dir'] = str(frames_dir)
        g['step_trackmate'] = True
    if stop():
        raise Stopped()

    # ---------- 0. TrackMate в Fiji без окна ----------
    raw_dir = g.get('raw_dir')
    if g.get('step_trackmate'):
        from . import trackmate
        if not g.get('videos_dir'):
            raise ValueError('Шаг 0 включён, но не задана папка с видео')
        log(f'\n=== 0. TRACKMATE (Fiji без окна) ===')
        trackmate.run(g.get('fiji_exe', ''), Path(g['videos_dir']), out_root / DIR_TRACKMATE, log, stop,
                      n_procs=int(g.get('fiji_procs') or 1))
        raw_dir = str(out_root / DIR_TRACKMATE)
        g['step_tables'] = True
    if stop():
        raise Stopped()

    # ---------- 1. оформление таблиц ----------
    spots_root = Path(g['spots_dir']) if g.get('spots_dir') else None
    tracks_root = Path(g['tracks_dir']) if g.get('tracks_dir') else None
    if g['step_tables']:
        if not raw_dir:
            raise ValueError('Шаг 1 включён, но не задана папка с экспортами TrackMate (.csv)')
        log(f'\n=== 1. ОФОРМЛЕНИЕ ТАБЛИЦ (вместо макросов Excel) ===')
        tables.format_tree(Path(raw_dir), out_root / DIR_TABLES, log)
        spots_root = tracks_root = out_root / DIR_TABLES
    if stop():
        raise Stopped()

    # ---------- 2–4. фильтрация + склейка + CASA ----------
    casa_dir = Path(g['casa_dir']) if g.get('casa_dir') else None
    if g['step_filter']:
        if spots_root is None:
            raise ValueError('Шаг 2 включён, но не задана папка со спотами (*_allspots.xlsx)')
        casa_dir = filter_and_casa(cfg, spots_root, tracks_root, out_root, log, stop)
    if stop():
        raise Stopped()

    # ---------- 5. кластеризация ----------
    if g['step_cluster']:
        if casa_dir is None:
            raise ValueError('Шаг 5 включён, но нет входа: включите шаг 2 или задайте папку CASA TRACKS')
        cluster(cfg, casa_dir, out_root / DIR_CLUSTER, log)
    log(f'\nГОТОВО. Результаты: {out_root}')
    return out_root


def filter_and_casa(cfg, spots_root: Path, tracks_root, out_root: Path, log, stop) -> Path:
    from . import casa, filtration
    videos = find_videos(spots_root, tracks_root)
    if not videos:
        raise FileNotFoundError(f'В {spots_root} нет файлов *spots*.xlsx')
    n_seg = sum(len(s) for _, s in videos)
    log(f'\n=== 2–4. СУПЕР-ФИЛЬТРАЦИЯ, СКЛЕЙКА, CASA-МЕТРИКИ ===')
    log(f'Видео: {len(videos)}, отрезков: {n_seg}. Журнал каждого отрезка — в «{DIR_FILTER}\\<видео>\\<отрезок>_журнал.txt»')
    jobs = [{'video': v, 'seg': seg, 'spots': str(sp), 'tracks': str(tr), 'settings': cfg,
             'out_dir': str(out_root / DIR_FILTER / v)} for v, segs in videos for seg, sp, tr in segs]
    results = {}
    workers = max(1, int(cfg['general'].get('n_workers') or 1))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(run_segment, j) for j in jobs]
        for i, f in enumerate(futs, 1):
            r = f.result()
            results[(r['video'], r['seg'])] = r
            st = (f"OK, треков {r['n_tracks']}" + (', столбцы спотов исправлены' if r['shift_fixed'] else '')
                  if r['ok'] else 'ОШИБКА ' + r['error'])
            log(f"[{i}/{len(jobs)}] {r['video']} / {r['seg']}: {st} ({r['sec']} с)")
            if stop():
                for x in futs:
                    x.cancel()
                raise Stopped()

    concat_dir, tr_dir, sp_dir = out_root / DIR_CONCAT, out_root / DIR_CASA_TRACKS, out_root / DIR_CASA_SPOTS
    for d in (concat_dir, tr_dir, sp_dir):
        d.mkdir(parents=True, exist_ok=True)
    check = []
    for v, segs in videos:
        rows, mets, spts = [], [], []
        for seg, sp, tr in segs:
            r = results[(v, seg)]
            check.append({'видео': v, 'отрезок': seg, 'файл спотов': sp.name,
                          'файл треков': tr.name if tr != sp else '(нет)', 'ok': r['ok'],
                          'столбцы спотов исправлены': r.get('shift_fixed'), 'треков': r.get('n_tracks'),
                          'сек': r['sec'], 'ошибка': r.get('error')})
            if not r['ok']:
                continue
            t = r['tracks'].assign(SEGMENT=seg)
            t.insert(0, 'SOURCE_FILE', f'{seg}{filtration.OUTPUT_SUFFIX}')   # из какого *proc.xlsx строка
            rows.append(t)
            mets.append(r['metrics'].assign(SEGMENT=seg))
            spts.append(r['spots'].assign(SEGMENT=seg))
        if not rows:
            log(f'  {v}: нет ни одного обработанного отрезка — пропуск')
            continue
        tracks = pd.concat(rows, ignore_index=True)
        tracks.drop(columns='SEGMENT').to_excel(concat_dir / f'{v}.xlsx', sheet_name='Tracks', index=False)
        met = casa.to_units(pd.concat(mets, ignore_index=True))
        tracks.merge(met, on=['SEGMENT', 'TRACK_ID'], how='left').to_excel(tr_dir / f'{v}.xlsx', index=False)
        pd.concat(spts, ignore_index=True)[['SEGMENT', 'TRACK_ID', 'FRAME', 'POSITION_X', 'POSITION_Y']] \
            .to_excel(sp_dir / f'{v} spots of final tracks.xlsx', index=False)
        log(f'  {v}: треков {len(tracks)} → «{DIR_CASA_TRACKS}\\{v}.xlsx»')
    pd.DataFrame(check).to_excel(out_root / '_проверка отрезков.xlsx', index=False)
    bad = [c for c in check if not c['ok']]
    if bad:
        log(f'ВНИМАНИЕ: {len(bad)} отрезков с ошибкой — см. «_проверка отрезков.xlsx» и журналы')
    return tr_dir


def cluster(cfg, casa_dir: Path, out_dir: Path, log):
    from . import clustering
    log(f'\n=== 5. КЛАСТЕРИЗАЦИЯ (HDBSCAN) ===')
    log(f'Вход: {casa_dir}')
    S.apply(cfg)
    clustering.INPUT_DIR = str(casa_dir)
    clustering.OUTPUT_DIR = str(out_dir)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        clustering.main()
    log(buf.getvalue().rstrip())
