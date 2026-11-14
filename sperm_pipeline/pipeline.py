r"""
Весь путь от видео до кластеров одной командой:

  00 КАДРЫ        видео .mpg → кадры (ffmpeg) → отрезки + _rejected (GET SEQUENCES IN ORDER FROM VIDEO.py) → frames.py
  0 TRACKMATE      кадры → Fiji без окна: 8-bit, контраст, Gaussian, Find Maxima, LoG + LAP → CSV  → trackmate.py
  1 ТАБЛИЦЫ        оформление экспортов TrackMate (замена макросов Excel)          → tables.py
  2 ФИЛЬТРАЦИЯ     супер-фильтрация каждого отрезка видео (*proc.xlsx + журнал)     → filtration.py
  3 СКЛЕЙКА        таблицы треков всех отрезков одного видео в одну (CONCATTING.py)
  4 CASA           CASA-метрики по точкам итоговых треков (casa_metrics.py v2)      → casa.py
  5 КЛАСТЕРИЗАЦИЯ  HDBSCAN по VAP/STR/LIN (hdbscan casa 2d.py)                       → clustering.py
  6 KDE            плотность скорости каждого видео + вклады классов (KDE studying.py) → kde.py

Шаги 2–4 идут за один проход по каждому отрезку: фильтрация сразу отдаёт точки итоговых треков,
и метрики считаются по ним (раньше casa_metrics.py ради этого заново прогонял фильтрацию).

Раскладка результатов — по папке на видео, имя как у файла видео (01.mpg → «01»):
  <папка результатов>\01\00 КАДРЫ, 0 TRACKMATE, 1 ТАБЛИЦЫ, 2 ФИЛЬТРАЦИЯ, 3 СКЛЕЙКА, 4 CASA TRACKS, 4 CASA SPOTS,
                        5 КЛАСТЕРИЗАЦИЯ (график и итог этого видео), 6 KDE
  <папка результатов>\_ВСЕ ВИДЕО\  сводка по всем видео, проверка отрезков, настройки прогона, журналы Fiji
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
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
DIR_KDE = '6 KDE'
DIR_ALL = '_ВСЕ ВИДЕО'
SUMMARY = 'summary_all_videos.xlsx'


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
# шаги 00 + 0 по одному видео: кадры → TrackMate → кадры удаляются
# =============================================================================
DONE_MARK = '_готово, кадры удалены.json'
CUT_MARK = '_кадры нарезаны приложением.txt'
SKIPPED = '_отрезки, снятые сторожем.txt'


def one_by_one(g: dict, out_root: Path, all_dir: Path, frames_of, vdir, log, stop) -> list:
    """Видео за видео: нарезка → TrackMate → удаление кадров этого видео. На диске одновременно лежат кадры
    только одного видео (~2 ГБ), а не всех. Видео, у которых TrackMate уже прошёл с теми же параметрами
    (метка DONE_MARK в папке CSV), при повторном запуске пропускаются — прогон можно продолжить после сбоя.
    Кадры, которые лежали в папке ещё до запуска, не удаляются. → [(видео, папка с CSV TrackMate)]"""
    from . import frames, trackmate
    cur = {k: v for k, v in trackmate.params().items() if k != 'VIDEO_EXT'}
    files = frames.list_videos(g['mpg_dir'])
    log(f'Режим «по одному видео»: {len(files)} видео; кадры каждого удаляются после TrackMate')
    out, failed = [], []
    t0 = time.time()
    for k, f in enumerate(files, 1):
        if stop():
            raise Stopped()
        v = Path(f).stem
        fdir = Path(frames_of(f))
        tm_dst = vdir(v) / DIR_TRACKMATE
        mark = tm_dst / DONE_MARK
        try:
            if json.loads(mark.read_text(encoding='utf-8')) == cur:
                log(f'\n[{k}/{len(files)}] {f}: TrackMate уже был с этими параметрами — пропускаю')
                out.append((v, tm_dst))
                continue
        except Exception:
            pass
        log(f'\n[{k}/{len(files)}] {f}')
        ours = tm_dst / CUT_MARK               # кадры нарезаны этим режимом (в прошлый раз TrackMate не дошёл)
        had_frames = fdir.is_dir() and any(fdir.iterdir()) and not ours.exists()
        try:
            frames.run(g.get('ffmpeg_exe', ''), g['mpg_dir'], str(out_root), log, stop,
                       dest=lambda x: str(frames_of(x)), only=[f], quiet=k > 1)
        except KeyboardInterrupt:
            raise Stopped()
        if not fdir.is_dir():
            log(f'  {f}: кадров нет (ошибка ffmpeg?) — пропускаю')
            failed.append(f)
            continue
        if not had_frames:
            tm_dst.mkdir(parents=True, exist_ok=True)
            ours.write_text(str(fdir), encoding='utf-8')
        res = trackmate.run(g.get('fiji_exe', ''), [(v, fdir)], all_dir / 'журналы Fiji', log, stop,
                            raise_if_none=False, n_procs=int(g.get('fiji_procs') or 1), dest=lambda _: tm_dst)
        if stop():
            raise Stopped()
        n_ok = len(res['ok']) + len(res['already'])
        hung = [b for b in res['bad'] if 'FIJI_TIMEOUT_MIN' in b[2]]
        if hung and len(hung) == len(res['bad']) and n_ok:
            # отрезки, на которых Fiji зависла, — пропускаем насовсем (при повторе зависнет снова); список — в журнал
            with open(all_dir / SKIPPED, 'a', encoding='utf-8') as fh:
                for b in hung:
                    fh.write(f'{datetime.now():%Y-%m-%d %H:%M}\t{b[0]}\t{b[1]}\n')
            log(f'  {f}: отрезков снято сторожем {len(hung)} — видео считаю без них (список: {all_dir / SKIPPED})')
            res = {**res, 'bad': []}
        if res['bad'] or not n_ok:
            log(f'  {f}: TrackMate с ошибками (готово {n_ok}, ошибок {len(res["bad"])}) — кадры оставляю: {fdir}')
            failed.append(f)
            if n_ok:
                out.append((v, tm_dst))
            continue
        mark.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding='utf-8')
        out.append((v, tm_dst))
        if had_frames:
            log(f'  кадры лежали в {fdir} ещё до запуска — не удаляю')
        elif fdir.name in (DIR_FRAMES, v) and fdir != out_root:
            shutil.rmtree(fdir, ignore_errors=True)
            log(f'  кадры удалены: {fdir}')
            ours.unlink(missing_ok=True)
        left = (time.time() - t0) / k * (len(files) - k)
        log(f'  прошло {(time.time() - t0) / 60:.0f} мин, осталось примерно {left / 60:.0f} мин')
    if failed:
        log(f'\nНе прошли нарезку/TrackMate: {len(failed)} — {", ".join(failed)}')
    if not out:
        raise RuntimeError('Ни одно видео не прошло TrackMate — см. журнал выше и журналы Fiji')
    return out


# =============================================================================
# весь прогон
# =============================================================================

def run(cfg: dict, log=print, stop=lambda: False) -> Path:
    g = cfg['general']
    out_root = Path(g['output_dir'])
    if not g['output_dir']:
        raise ValueError('Не задана папка результатов')
    all_dir = out_root / DIR_ALL
    all_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y-%m-%d %H-%M')
    (all_dir / f'настройки {stamp}.json').write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
    S.apply(cfg)

    def vdir(v: str) -> Path:                  # папка видео в папке результатов
        return out_root / v

    # имя видео = имя файла .mpg (01.mpg → «01»); папки кадров «1 video» из старого скрипта нарезки — тоже «01»
    rename = {}
    if g.get('mpg_dir') and Path(g['mpg_dir']).is_dir():
        from . import frames
        for f in frames.list_videos(g['mpg_dir']):
            rename[frames.folder_name(f)] = Path(f).stem

    def name_of(folder: str) -> str:
        return rename.get(folder, folder)

    # ---------- 00. нарезка видео на кадры и отрезки ----------
    tm_src = None
    raw_videos = None                          # [(видео, папка с CSV TrackMate)]
    if g.get('step_frames'):
        from . import frames
        if not g.get('mpg_dir'):
            raise ValueError('Шаг 00 включён, но не задана папка с видео (.mpg)')
        custom = Path(g['frames_dir']) if g.get('frames_dir') else None

        def frames_of(video_file: str) -> Path:
            v = Path(video_file).stem
            return custom / v if custom else vdir(v) / DIR_FRAMES
        log(f'\n=== 00. НАРЕЗКА ВИДЕО НА КАДРЫ (ffmpeg) И ОТРЕЗКИ ===')
        log(f'Кадры: {custom or ("<папка результатов>" + chr(92) + "<видео>" + chr(92) + DIR_FRAMES)}')
        g['step_trackmate'] = True
        if g.get('frames_one_by_one'):
            raw_videos = one_by_one(g, out_root, all_dir, frames_of, vdir, log, stop)
            tm_src = 'done'
        else:
            try:
                frames.run(g.get('ffmpeg_exe', ''), g['mpg_dir'], str(out_root), log, stop,
                           dest=lambda f: str(frames_of(f)))
            except KeyboardInterrupt:
                raise Stopped()
            tm_src = [(Path(f).stem, frames_of(f)) for f in frames.list_videos(g['mpg_dir'])
                      if frames_of(f).is_dir()]
    if stop():
        raise Stopped()

    # ---------- 0. TrackMate в Fiji без окна ----------
    if tm_src == 'done':
        g['step_tables'] = True                # TrackMate уже прошёл внутри шага 00 (по одному видео)
    elif g.get('step_trackmate'):
        from . import trackmate
        if tm_src is None:
            if not g.get('videos_dir'):
                raise ValueError('Шаг 0 включён, но не задана папка с видео')
            tm_src = Path(g['videos_dir'])
        log(f'\n=== 0. TRACKMATE (Fiji без окна) ===')
        res = trackmate.run(g.get('fiji_exe', ''), tm_src, all_dir / 'журналы Fiji', log, stop,
                            n_procs=int(g.get('fiji_procs') or 1), dest=lambda v: vdir(v) / DIR_TRACKMATE,
                            rename=name_of)
        raw_videos = list(res['videos'].items())
        g['step_tables'] = True
    elif g.get('step_tables'):
        if not g.get('raw_dir'):
            raise ValueError('Шаг 1 включён, но не задана папка с экспортами TrackMate (.csv)')
        raw_videos = [(name_of(d.name), d) for d in tables.video_dirs(Path(g['raw_dir']), tables.is_raw)]
        if not raw_videos:
            raise FileNotFoundError(f'В {g["raw_dir"]} нет файлов .csv/.xls/.xlsx')
    if stop():
        raise Stopped()

    # ---------- 1. оформление таблиц ----------
    videos = None                              # [(видео, [(отрезок, споты, треки)])]
    if g['step_tables']:
        log(f'\n=== 1. ОФОРМЛЕНИЕ ТАБЛИЦ (вместо макросов Excel) ===')
        videos = []
        for v, src in raw_videos:
            log(f'Видео «{v}»:')
            dst = vdir(v) / DIR_TABLES
            for f in sorted(Path(src).iterdir()):
                if f.is_file() and tables.is_raw(f):
                    try:
                        tables.format_file(f, dst, log)
                    except Exception as e:
                        log(f'  ОШИБКА {f.name}: {e!r}')
            if dst.is_dir():
                videos += [(v, segs) for _, segs in find_videos(dst, None)]
    elif g.get('spots_dir'):
        tracks_root = Path(g['tracks_dir']) if g.get('tracks_dir') else None
        videos = [(name_of(v), segs) for v, segs in find_videos(Path(g['spots_dir']), tracks_root)]
    if stop():
        raise Stopped()

    # ---------- 2–4. фильтрация + склейка + CASA ----------
    casa_files = None                          # [файл CASA TRACKS видео]; имя файла = имя видео
    if g['step_filter']:
        if videos is None:
            raise ValueError('Шаг 2 включён, но не задана папка со спотами (*_allspots.xlsx)')
        if not videos:
            raise FileNotFoundError('Не найдено ни одного файла *spots*.xlsx')
        casa_files = filter_and_casa(cfg, videos, out_root, log, stop)
    elif g.get('casa_dir'):
        from . import clustering
        casa_files = sorted(f for f in Path(g['casa_dir']).glob(clustering.FILE_PATTERN)
                            if not f.name.startswith('~$'))
    if stop():
        raise Stopped()

    # ---------- 5. кластеризация ----------
    if g['step_cluster']:
        if not casa_files:
            raise ValueError('Шаг 5 включён, но нет входа: включите шаг 2 или задайте папку CASA TRACKS')
        cluster(cfg, casa_files, out_root, log)
    if stop():
        raise Stopped()

    # ---------- 6. KDE ----------
    if g.get('step_kde'):
        if not casa_files:
            raise ValueError('Шаг 6 включён, но нет входа: включите шаг 2 или задайте папку CASA TRACKS')
        from . import kde
        log(f'\n=== 6. KDE (плотность скорости) ===')
        kde.run(casa_files, out_root / DIR_ALL, lambda v: out_root / v / DIR_KDE, log, SUMMARY)
    log(f'\nГОТОВО. Результаты: {out_root} (папка на каждое видео; сводка — «{DIR_ALL}»)')
    return out_root


def filter_and_casa(cfg, videos: list, out_root: Path, log, stop) -> list:
    """videos = [(видео, [(отрезок, споты, треки)])]. Пишет всё в <out_root>\\<видео>\\... → [файлы CASA TRACKS]."""
    from . import casa, filtration
    n_seg = sum(len(s) for _, s in videos)
    log(f'\n=== 2–4. СУПЕР-ФИЛЬТРАЦИЯ, СКЛЕЙКА, CASA-МЕТРИКИ ===')
    log(f'Видео: {len(videos)}, отрезков: {n_seg}. Журнал каждого отрезка — в «<видео>\\{DIR_FILTER}\\<отрезок>_журнал.txt»')
    jobs = [{'video': v, 'seg': seg, 'spots': str(sp), 'tracks': str(tr), 'settings': cfg,
             'out_dir': str(out_root / v / DIR_FILTER)} for v, segs in videos for seg, sp, tr in segs]
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

    check, out = [], []
    for v, segs in videos:
        concat_dir, tr_dir, sp_dir = (out_root / v / d for d in (DIR_CONCAT, DIR_CASA_TRACKS, DIR_CASA_SPOTS))
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
        for d in (concat_dir, tr_dir, sp_dir):
            d.mkdir(parents=True, exist_ok=True)
        tracks = pd.concat(rows, ignore_index=True)
        tracks.drop(columns='SEGMENT').to_excel(concat_dir / f'{v}.xlsx', sheet_name='Tracks', index=False)
        met = casa.to_units(pd.concat(mets, ignore_index=True))
        tracks.merge(met, on=['SEGMENT', 'TRACK_ID'], how='left').to_excel(tr_dir / f'{v}.xlsx', index=False)
        pd.concat(spts, ignore_index=True)[['SEGMENT', 'TRACK_ID', 'FRAME', 'POSITION_X', 'POSITION_Y']] \
            .to_excel(sp_dir / f'{v} spots of final tracks.xlsx', index=False)
        out.append(tr_dir / f'{v}.xlsx')
        log(f'  {v}: треков {len(tracks)} → «{v}\\{DIR_CASA_TRACKS}\\{v}.xlsx»')
    pd.DataFrame(check).to_excel(out_root / DIR_ALL / '_проверка отрезков.xlsx', index=False)
    bad = [c for c in check if not c['ok']]
    if bad:
        log(f'ВНИМАНИЕ: {len(bad)} отрезков с ошибкой — см. «{DIR_ALL}\\_проверка отрезков.xlsx» и журналы')
    return out


def cluster(cfg, casa_files: list, out_root: Path, log):
    """Кластеризация (hdbscan casa 2d.py) по всем видео разом: сводка — в «_ВСЕ ВИДЕО», график и итог
    каждого видео — в «<видео>\\5 КЛАСТЕРИЗАЦИЯ»."""
    from . import clustering
    log(f'\n=== 5. КЛАСТЕРИЗАЦИЯ (HDBSCAN) ===')
    log(f'Видео: {len(casa_files)}')
    S.apply(cfg)
    all_dir = out_root / DIR_ALL
    clustering.FILES = [str(f) for f in casa_files]
    clustering.OUTPUT_DIR = str(all_dir)
    clustering.PLOT_DIR = lambda v: out_root / v / DIR_CLUSTER
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            clustering.main()
    finally:
        clustering.FILES = clustering.PLOT_DIR = None
    log(buf.getvalue().rstrip())
    # итог каждого видео — рядом с его графиком
    book = pd.read_excel(all_dir / SUMMARY, sheet_name=None, dtype={'video': str})   # «01» не превращать в 1
    lab_path = all_dir / 'tracks_labeled.xlsx'
    labeled = pd.read_excel(lab_path, dtype={'video': str}) if lab_path.exists() else None
    for v in book['per_video']['video'].astype(str):
        d = out_root / v / DIR_CLUSTER
        d.mkdir(parents=True, exist_ok=True)
        with pd.ExcelWriter(d / f'итог {v}.xlsx') as xw:
            for sheet, df in book.items():
                (df[df['video'].astype(str) == v] if 'video' in df else df).to_excel(xw, sheet_name=sheet, index=False)
            if labeled is not None:
                labeled[labeled['video'].astype(str) == v].to_excel(xw, sheet_name='tracks', index=False)
