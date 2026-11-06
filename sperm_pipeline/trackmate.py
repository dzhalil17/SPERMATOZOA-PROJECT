"""
Шаг 0: видео → TrackMate, без окна Fiji.

Приложение запускает вашу Fiji в фоновом режиме (--headless) со скриптом на Jython (он встроен ниже,
отдельно ставить ничего не надо). Скрипт повторяет шаги Fiji-плагина Sperm_Tracking_Pipeline:
  8-bit → контраст (растяжка Min..Max, как B&C «Apply») → Gaussian Blur → Find Maxima (Single Points,
  каждый кадр) → TrackMate: LoG-детектор + LAP-трекер, все признаки → экспорт спотов и треков в CSV
  в том же виде, что кнопка «Export to CSV» в TrackMate (4 строки заголовка).
Дальше эти CSV забирает шаг 1 (оформление таблиц).

Отрезок, как в плагине: файл .tif/.tiff/.avi ИЛИ папка с кадрами (.png/.jpg/.bmp/.tif, открывается как
File > Import > Image Sequence). Папка с отрезками = одно видео; папка с такими папками = несколько видео:
  SEQUENCES IN ORDER\1 video\1230-1640\frame_01230.png …  → видео «1 video», отрезок «1230-1640».
Папки, имя которых начинается с «_» (например, _rejected), пропускаются. Имя отрезка должно начинаться с
диапазона кадров. .mpg/.mp4 Fiji без FFmpeg не читает — нужны папки с кадрами.
Координаты принудительно в пикселях, время — в кадрах (как во всех ваших таблицах).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

# ▶ НАСТРОЙКИ (значения по умолчанию; приложение переписывает их из окна настроек)
DO_8BIT = True
CONTRAST_MIN = 0.0          # растяжка контраста: Min → 0, Max → 255 (0 и 255 = без изменений)
CONTRAST_MAX = 255.0
GAUSSIAN_SIGMA = 4.0        # 0 = без размытия
NOISE_TOLERANCE = 25.0      # Find Maxima: prominence (noise tolerance)
EXCLUDE_EDGE_MAXIMA = False
LIGHT_BACKGROUND = False
LOG_RADIUS = 2.5            # TrackMate LoG: радиус (диаметр 5)
LOG_QUALITY_THRESHOLD = 8.0
LOG_SUBPIXEL = False        # в ваших таблицах координаты целые
LOG_MEDIAN_FILTER = False
LINKING_MAX_DISTANCE = 40.0
GAP_CLOSING_MAX_DISTANCE = 40.0
MAX_FRAME_GAP = 20
VIDEO_EXT = '.tif .tiff .avi'
FRAME_EXT = {'.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff'}   # кадры в папке-отрезке (как в плагине)
_RANGE = re.compile(r'^\d+-\d+')

FIJI_CANDIDATES = [
    r'C:\Fiji.app\ImageJ-win64.exe', r'C:\Fiji.app\fiji-windows-x64.exe',
    r'C:\Program Files\Fiji.app\ImageJ-win64.exe', r'D:\Fiji.app\ImageJ-win64.exe',
    r'D:\Fiji\ImageJ-win64.exe', r'D:\Fiji\Fiji.app\ImageJ-win64.exe',
    r'D:\Fiji.app\fiji-windows-x64.exe', r'D:\Fiji\fiji-windows-x64.exe',
    r'~\Fiji.app\ImageJ-win64.exe', r'~\Desktop\Fiji.app\ImageJ-win64.exe', r'~\Downloads\Fiji.app\ImageJ-win64.exe',
    r'~\Fiji.app\fiji-windows-x64.exe', r'~\Desktop\Fiji.app\fiji-windows-x64.exe',
]


def find_fiji() -> str:
    for c in FIJI_CANDIDATES:
        p = Path(os.path.expanduser(c))
        if p.exists():
            return str(p)
    return ''


def params() -> dict:
    keys = ['DO_8BIT', 'CONTRAST_MIN', 'CONTRAST_MAX', 'GAUSSIAN_SIGMA', 'NOISE_TOLERANCE', 'EXCLUDE_EDGE_MAXIMA',
            'LIGHT_BACKGROUND', 'LOG_RADIUS', 'LOG_QUALITY_THRESHOLD', 'LOG_SUBPIXEL', 'LOG_MEDIAN_FILTER',
            'LINKING_MAX_DISTANCE', 'GAP_CLOSING_MAX_DISTANCE', 'MAX_FRAME_GAP', 'VIDEO_EXT']
    return {k: globals()[k] for k in keys}


def _is_frames(d: Path) -> bool:
    """Папка с кадрами одного отрезка. Папка только с .tif — кадры, если её имя начинается с диапазона кадров
    (иначе это папка-видео с отрезками-стеками .tif)."""
    try:
        fs = [p for p in d.iterdir() if p.is_file() and p.suffix.lower() in FRAME_EXT]
    except OSError:
        return False
    if not fs:
        return False
    return any(p.suffix.lower() not in ('.tif', '.tiff') for p in fs) or bool(_RANGE.match(d.name))


def _segments(d: Path, skipped: list, prefix=''):
    vext = {e.lower() for e in VIDEO_EXT.split()}
    segs = []
    for p in sorted(d.iterdir(), key=lambda p: p.name):
        if p.is_dir():
            if p.name.startswith('_'):
                skipped.append(prefix + p.name)
            elif _is_frames(p):
                segs.append((p.name, str(p), 'seq'))
        elif p.is_file() and p.suffix.lower() in vext:
            segs.append((p.stem, str(p), 'file'))
    return segs


def find_jobs(root: Path):
    """[(видео, отрезок, путь, 'file'|'seq')], [пропущенные папки]"""
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f'Нет папки с видео: {root}')
    skipped = []
    if _is_frames(root) and not any(p.is_dir() for p in root.iterdir()):
        return [(root.parent.name, root.name, str(root), 'seq')], skipped      # указали прямо папку-отрезок
    segs = _segments(root, skipped)
    if segs:
        return [(root.name, *s) for s in segs], skipped
    jobs = []
    for v in sorted((p for p in root.iterdir() if p.is_dir() and not p.name.startswith('_')), key=lambda p: p.name):
        jobs += [(v.name, *s) for s in _segments(v, skipped, v.name + '\\')]
    if not jobs:
        found = sorted({p.suffix.lower() or p.name for p in root.iterdir() if p.is_file()})
        hint = ''
        if any(e in ('.mpg', '.mpeg', '.mp4', '.mov', '.wmv', '.mkv') for e in found):
            hint = ('\nFiji без FFmpeg не читает .mpg/.mp4. Укажите папку с последовательностями кадров '
                    '(как во входе плагина: <видео>\\<отрезок>\\frame_*.png).')
        raise FileNotFoundError(f'В папке {root} нет ни видео ({VIDEO_EXT}), ни папок с кадрами '
                                f'({" ".join(sorted(FRAME_EXT))}). Найдено: {", ".join(found) or "пусто"}.{hint}')
    return jobs, skipped


def total_ram_gb() -> float:
    """Объём оперативной памяти компьютера, ГБ."""
    try:
        if os.name == 'nt':
            import ctypes

            class _MS(ctypes.Structure):
                _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong)] + \
                           [(n, ctypes.c_ulonglong) for n in ('total', 'avail', 'tpf', 'apf', 'tv', 'av', 'aev')]
            m = _MS()
            m.dwLength = ctypes.sizeof(_MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.total / 2 ** 30
        return os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / 2 ** 30
    except Exception:
        return 8.0


def default_procs() -> int:
    """Сколько Fiji запускать одновременно: ~3 потока процессора и ~4 ГБ памяти на одну Fiji."""
    return max(1, min((os.cpu_count() or 2) // 3, int(total_ram_gb() // 4)))


def _png_size(path: Path):
    with open(path, 'rb') as fh:
        head = fh.read(24)
    if head[:8] != b'\x89PNG\r\n\x1a\n':
        return None
    return int.from_bytes(head[16:20], 'big'), int.from_bytes(head[20:24], 'big')


def _job_info(job):
    """→ (вес для раздачи по процессам — байты на диске, оценка памяти Fiji на этот отрезок в ГБ или None)."""
    path, kind = job[2], job[3]
    p = Path(path)
    if kind == 'file':
        return p.stat().st_size, None
    frames = sorted(f for f in p.iterdir() if f.suffix.lower() in FRAME_EXT)
    size = sum(f.stat().st_size for f in frames)
    wh = _png_size(frames[0]) if frames and frames[0].suffix.lower() == '.png' else None
    # кадры открываются как RGB (4 байта/пиксель) + 8-bit копия + стек максимумов; запас 30 % и 0.5 ГБ на саму Fiji
    need = len(frames) * wh[0] * wh[1] * 6 * 1.3 / 2 ** 30 + 0.5 if wh else None
    return size, need


def _split(jobs, n):
    """Раздать отрезки на n процессов поровну по объёму (самые большие — первыми, каждому самый свободный)."""
    info = {tuple(j): _job_info(j) for j in jobs}
    groups, load = [[] for _ in range(n)], [0] * n
    for j in sorted(jobs, key=lambda j: -info[tuple(j)][0]):
        i = load.index(min(load))
        groups[i].append(j)
        load[i] += info[tuple(j)][0]
    order = {tuple(j): k for k, j in enumerate(jobs)}          # внутри процесса — в исходном порядке
    groups = [sorted(g, key=lambda j: order[tuple(j)]) for g in groups if g]
    needs = [v[1] for v in info.values() if v[1]]
    return groups, (max(needs) if needs else None)


def _script_path(out_dir: Path, fiji: str) -> Path:
    """Куда положить скрипт для Fiji. Лаунчер ImageJ-win64.exe теряет аргумент --run, если в пути есть кириллица
    («Ignoring invalid argument: --run»), поэтому скрипт кладётся в папку с путём только из латиницы."""
    import tempfile
    for d in (out_dir, Path(tempfile.gettempdir()) / 'SpermPipeline', Path(fiji).parent / 'SpermPipeline',
              Path(os.environ.get('PUBLIC', r'C:\Users\Public')) / 'SpermPipeline'):
        if str(d).isascii():
            try:
                d.mkdir(parents=True, exist_ok=True)
                f = d / f'_fiji_trackmate_{os.getpid()}.py'
                f.write_text('', encoding='utf-8')
                return f
            except OSError:
                continue
    raise RuntimeError('Нет папки с путём только из латиницы для скрипта Fiji (лаунчер Fiji не понимает кириллицу '
                       'в пути скрипта). Укажите папку результатов без русских букв.')


def run(fiji_exe: str, videos_dir, out_dir: Path, log=print, stop=lambda: False, raise_if_none=True,
        n_procs: int = 1, dest=None, rename=None) -> dict:
    """→ {'ok': [(видео, отрезок, кадров, спотов, треков)], 'bad': [(видео, отрезок, ошибка)], 'already': [...],
          'videos': {видео: папка его CSV}}.
    videos_dir — папка с видео/отрезками или список [(имя видео, папка с его отрезками)].
    dest(видео) — куда класть CSV этого видео (по умолчанию out_dir\\видео); rename — имя видео по имени папки.
    В out_dir — служебное: скрипт Fiji, журналы. Отрезки, у которых уже есть оба CSV, посчитанные с теми же
    параметрами, не пересчитываются (продолжение после сбоя)."""
    fiji = fiji_exe or find_fiji()
    if not fiji or not Path(fiji).exists():
        raise FileNotFoundError('Не найдена Fiji: укажите путь к ImageJ-win64.exe на вкладке «Запуск»')
    out_dir = Path(out_dir)
    dest = dest or (lambda v: out_dir / v)
    if isinstance(videos_dir, (list, tuple)):
        jobs, skipped = [], []
        for name, src in videos_dir:
            j, sk = find_jobs(Path(src))
            jobs += [(name, *x[1:]) for x in j]
            skipped += sk
    else:
        jobs, skipped = find_jobs(Path(videos_dir))
        if rename:
            jobs = [(rename(j[0]), *j[1:]) for j in jobs]
    jobs = [(*j, str(dest(j[0]))) for j in jobs]
    out_dir.mkdir(parents=True, exist_ok=True)
    # продолжение после сбоя — только если параметры те же, что у уже посчитанных CSV (файл в папке каждого видео)
    cur = {k: v for k, v in params().items() if k != 'VIDEO_EXT'}
    vids = list(dict.fromkeys(j[0] for j in jobs))
    already, n_del = [], 0
    for v in vids:
        vj = [j for j in jobs if j[0] == v]
        vdst = Path(vj[0][4])
        vdst.mkdir(parents=True, exist_ok=True)
        pfile = vdst / '_параметры TrackMate.json'
        try:
            same = json.loads(pfile.read_text(encoding='utf-8')) == cur
        except Exception:
            same = False
        csvs = [(j, Path(j[4]) / f'{j[1]}_spots.csv', Path(j[4]) / f'{j[1]}_tracks.csv') for j in vj]
        if same:
            already += [j for j, a, b in csvs if a.exists() and b.exists()]
        else:
            for _, a, b in csvs:
                for f in (a, b):
                    if f.exists():
                        f.unlink()
                        n_del += 1
        pfile.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding='utf-8')
    if n_del:
        log(f'Параметры TrackMate не те, что у прежних CSV — удалил {n_del} старых CSV, считаю заново')
    if already:
        log(f'Уже посчитаны раньше (есть оба CSV), пропускаю: {len(already)} из {len(jobs)} отрезков')
        jobs = [j for j in jobs if j not in already]
    res = {'ok': [], 'bad': [], 'already': [(j[0], j[1]) for j in already],
           'videos': {v: dest(v) for v in vids}}
    if not jobs:
        return res
    for v in dict.fromkeys(j[0] for j in jobs):
        segs = [j for j in jobs if j[0] == v]
        kinds = {'seq': 'папки с кадрами', 'file': 'файлы'}
        log(f'Видео «{v}»: отрезков {len(segs)} ({", ".join(sorted({kinds[j[3]] for j in segs}))})')
    if skipped:
        log(f'Пропущены папки (имя начинается с «_»): {", ".join(skipped)}')
    script = _script_path(out_dir, fiji)
    script.write_text(JYTHON_SCRIPT, encoding='utf-8')
    for f in list(out_dir.glob('_progress*.txt')) + list(out_dir.glob('_trackmate_params*.json')):
        f.unlink()

    # несколько Fiji одновременно: у каждой свой список отрезков, свой файл прогресса и журнал, своя доля памяти
    ram = total_ram_gb()
    n = max(1, min(int(n_procs or 1), len(jobs)))
    groups, need = _split(jobs, n)
    budget = ram * 0.75                                  # четверть памяти — Windows и остальным программам
    if need and n > 1 and need > budget / n:
        n_fit = max(1, int(budget // need))
        if n_fit < n:
            log(f'Самому длинному отрезку нужно ~{need:.1f} ГБ памяти, а на {n} Fiji при {ram:.0f} ГБ ОЗУ '
                f'приходится по {budget / n:.1f} ГБ — запускаю {n_fit} одновременно')
            n = n_fit
            groups, need = _split(jobs, n)
    n = len(groups)
    heap_mb = max(1024, int(budget / n * 1024))
    threads = max(1, (os.cpu_count() or 2) // n)
    if need and need * 1024 > heap_mb:
        log(f'ВНИМАНИЕ: самому длинному отрезку нужно ~{need:.1f} ГБ, а Fiji получит {heap_mb / 1024:.1f} ГБ — '
            f'на нём может не хватить памяти (будет «ОШИБКА … OutOfMemory», остальные отрезки посчитаются)')
    log(f'Fiji: {fiji}')
    log(f'Запускаю Fiji: {n} одновременно, у каждой {heap_mb / 1024:.1f} ГБ памяти и {threads} потоков '
        f'(ОЗУ {ram:.0f} ГБ, потоков процессора {os.cpu_count()})')
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    procs = []
    ok, bad = 0, 0
    try:
        for i, grp in enumerate(groups, 1):
            sfx = '' if n == 1 else f'_{i}'
            progress = out_dir / f'_progress{sfx}.txt'
            job = {**params(), 'input_dir': str(videos_dir), 'output_dir': str(out_dir), 'jobs': [list(j) for j in grp],
                   'progress_file': str(progress), 'THREADS': threads}
            job_path = out_dir / f'_trackmate_params{sfx}.json'
            job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding='utf-8')
            env = dict(os.environ, SPERMPIPELINE_PARAMS=str(job_path))
            cmd = [fiji, f'--mem={heap_mb}m', '--ij2', '--headless', '--console', '--run', str(script)]
            flog = (out_dir / f'_fiji_журнал{sfx}.txt').open('a', encoding='utf-8', errors='replace')
            src = ', '.join(str(x[1]) for x in videos_dir) if isinstance(videos_dir, (list, tuple)) else videos_dir
            flog.write(f'\n===== {time.strftime("%Y-%m-%d %H:%M:%S")}  {src}  (Fiji {i} из {n}, '
                       f'отрезков {len(grp)})\n')
            flog.flush()
            proc = subprocess.Popen(cmd, stdout=flog, stderr=subprocess.STDOUT, env=env, creationflags=flags)
            procs.append({'proc': proc, 'progress': progress, 'flog': flog, 'seen': 0,
                          'tag': '' if n == 1 else f'[Fiji {i}] '})
            if n > 1:
                log(f'  Fiji {i}: отрезков {len(grp)}')
        while True:
            alive = [p['proc'].poll() is None for p in procs]
            for p in procs:
                pr = p['progress']
                lines = pr.read_text(encoding='utf-8', errors='replace').splitlines() if pr.exists() else []
                for line in lines[p['seen']:]:
                    q = line.split('|')
                    if q[0] == 'OK':
                        ok += 1
                        res['ok'].append((q[1], q[2], int(q[3]), int(q[4]), int(q[5])))
                        log(f'  {p["tag"]}OK {q[1]} / {q[2]}: кадров {q[3]}, спотов {q[4]}, треков {q[5]}')
                    elif q[0] == 'ERR':
                        bad += 1
                        res['bad'].append((q[1], q[2] if len(q) > 3 else '', q[-1]))
                        log(f'  {p["tag"]}ОШИБКА {" / ".join(q[1:-1])}: {q[-1]}')
                    elif q[0] == 'START':
                        log(f'  {p["tag"]}{q[1]} / {q[2]}: обработка…')
                p['seen'] = len(lines)
            if not any(alive):
                break
            if stop():
                for p in procs:
                    if p['proc'].poll() is None:
                        p['proc'].kill()
                break
            time.sleep(0.5)
    finally:
        for p in procs:                       # после «Стоп» или сбоя приложения Fiji не остаются висеть
            if p['proc'].poll() is None:
                p['proc'].kill()
                p['proc'].wait()
            p['flog'].close()
    codes = [p['proc'].poll() for p in procs]
    try:
        script.unlink()
    except OSError:
        pass
    log(f'TrackMate: готово {ok}, с ошибкой {bad}. Журнал Fiji: {out_dir / "_fiji_журнал.txt"}'
        + (f' (и _2 … _{n})' if n > 1 else ''))
    if stop():
        return res
    if not ok and not already and raise_if_none:
        raise RuntimeError(f'Fiji не обработала ни одного видео (коды {codes}); '
                           f'подробности — {out_dir / "_fiji_журнал.txt"}')
    return res


# =============================================================================
# Скрипт для Fiji (Jython 2.7 — синтаксис старого Python 2, без f-строк)
# =============================================================================
JYTHON_SCRIPT = r'''# Generated by SpermPipeline: the Sperm_Tracking_Pipeline Fiji plugin steps, headless. ASCII only (scijava passes it as unicode).
import json, os, sys, traceback
import jarray
from java.lang import System, Math, Throwable
from java.io import File, OutputStreamWriter, FileOutputStream
from java.nio.file import Files, Paths, StandardCopyOption
from ij import IJ, ImagePlus, ImageStack
from ij.plugin import FolderOpener
from ij.process import StackConverter, ByteProcessor, ImageProcessor
from ij.plugin.filter import GaussianBlur, MaximumFinder
from fiji.plugin.trackmate import Model, Settings, TrackMate, Logger
from fiji.plugin.trackmate.detection import LogDetectorFactory
try:
    from fiji.plugin.trackmate.tracking.jaqaman import SparseLAPTrackerFactory
except ImportError:
    from fiji.plugin.trackmate.tracking.sparselap import SparseLAPTrackerFactory

P = json.loads(open(System.getenv('SPERMPIPELINE_PARAMS')).read())

SPOT_KEYS = ['QUALITY', 'POSITION_X', 'POSITION_Y', 'POSITION_Z', 'POSITION_T', 'FRAME', 'RADIUS', 'VISIBILITY',
             'MANUAL_SPOT_COLOR', 'MEAN_INTENSITY_CH1', 'MEDIAN_INTENSITY_CH1', 'MIN_INTENSITY_CH1',
             'MAX_INTENSITY_CH1', 'TOTAL_INTENSITY_CH1', 'STD_INTENSITY_CH1', 'CONTRAST_CH1', 'SNR_CH1']
TRACK_KEYS = ['TRACK_INDEX', 'TRACK_ID', 'NUMBER_SPOTS', 'NUMBER_GAPS', 'NUMBER_SPLITS', 'NUMBER_MERGES',
              'NUMBER_COMPLEX', 'LONGEST_GAP', 'TRACK_DURATION', 'TRACK_START', 'TRACK_STOP', 'TRACK_DISPLACEMENT',
              'TRACK_X_LOCATION', 'TRACK_Y_LOCATION', 'TRACK_Z_LOCATION', 'TRACK_MEAN_SPEED', 'TRACK_MAX_SPEED',
              'TRACK_MIN_SPEED', 'TRACK_MEDIAN_SPEED', 'TRACK_STD_SPEED', 'TRACK_MEAN_QUALITY',
              'TOTAL_DISTANCE_TRAVELED', 'MAX_DISTANCE_TRAVELED', 'CONFINEMENT_RATIO', 'MEAN_STRAIGHT_LINE_SPEED',
              'LINEARITY_OF_FORWARD_PROGRESSION', 'MEAN_DIRECTIONAL_CHANGE_RATE']


PROG = OutputStreamWriter(FileOutputStream(P.get('progress_file') or os.path.join(P['output_dir'], '_progress.txt')),
                          'UTF-8')


def safe(x):
    try:
        return unicode(x)
    except Exception:
        try:
            return unicode(x.toString())
        except Exception:
            return u'?'


def say(*parts):
    """Progress line for the app: UTF-8 file (non-ASCII names survive) + ASCII copy to the Fiji log."""
    line = u'|'.join([safe(p).replace(u'|', u'/').replace(u'\n', u' ') for p in parts])
    PROG.write(line + u'\n')
    PROG.flush()
    print(line.encode('ascii', 'replace'))
    sys.stdout.flush()


def stretch_lut(lo, hi):
    lut = []
    for v in range(256):
        if hi <= lo:
            lut.append(v)
        else:
            x = int(Math.round((v - lo) / float(hi - lo) * 255.0))
            lut.append(0 if x < 0 else (255 if x > 255 else x))
    return jarray.array(lut, 'i')


def preprocess(imp):
    if P['DO_8BIT'] and imp.getBitDepth() != 8:
        StackConverter(imp).convertToGray8()
    st = imp.getStack()
    lut = stretch_lut(P['CONTRAST_MIN'], P['CONTRAST_MAX'])
    gb = GaussianBlur()
    for i in range(1, st.getSize() + 1):
        ip = st.getProcessor(i)
        if not (P['CONTRAST_MIN'] == 0 and P['CONTRAST_MAX'] == 255):
            ip.applyTable(lut)
        if P['GAUSSIAN_SIGMA'] and P['GAUSSIAN_SIGMA'] > 0:
            gb.blurGaussian(ip, float(P['GAUSSIAN_SIGMA']), float(P['GAUSSIAN_SIGMA']), 0.02)


def find_maxima(imp):
    mf = MaximumFinder()
    st = imp.getStack()
    out = ImageStack(imp.getWidth(), imp.getHeight())
    for i in range(1, st.getSize() + 1):
        ip = st.getProcessor(i).duplicate()
        if P['LIGHT_BACKGROUND']:
            ip.invert()
        bp = mf.findMaxima(ip, float(P['NOISE_TOLERANCE']), ImageProcessor.NO_THRESHOLD, MaximumFinder.SINGLE_POINTS,
                           P['EXCLUDE_EDGE_MAXIMA'], False)
        if bp is None:
            bp = ByteProcessor(imp.getWidth(), imp.getHeight())
        out.addSlice('maxima_%d' % i, bp)
    res = ImagePlus(imp.getTitle() + '_SingleMaxima', out)
    cal = res.getCalibration()
    cal.pixelWidth = cal.pixelHeight = cal.pixelDepth = 1.0
    cal.setUnit('pixel')
    cal.frameInterval = 1.0
    res.setDimensions(1, 1, out.getSize())
    return res


def track(imp):
    model = Model()
    model.setLogger(Logger.VOID_LOGGER)
    s = Settings(imp)
    s.detectorFactory = LogDetectorFactory()
    s.detectorSettings = {'RADIUS': float(P['LOG_RADIUS']), 'TARGET_CHANNEL': 1,
                          'THRESHOLD': float(P['LOG_QUALITY_THRESHOLD']),
                          'DO_SUBPIXEL_LOCALIZATION': bool(P['LOG_SUBPIXEL']),
                          'DO_MEDIAN_FILTERING': bool(P['LOG_MEDIAN_FILTER'])}
    s.trackerFactory = SparseLAPTrackerFactory()
    ts = s.trackerFactory.getDefaultSettings()
    ts['LINKING_MAX_DISTANCE'] = float(P['LINKING_MAX_DISTANCE'])
    ts['GAP_CLOSING_MAX_DISTANCE'] = float(P['GAP_CLOSING_MAX_DISTANCE'])
    ts['MAX_FRAME_GAP'] = int(P['MAX_FRAME_GAP'])
    ts['ALLOW_TRACK_SPLITTING'] = False
    ts['ALLOW_TRACK_MERGING'] = False
    s.trackerSettings = ts
    s.addAllAnalyzers()
    tm = TrackMate(model, s)
    if P.get('THREADS'):
        tm.setNumThreads(int(P['THREADS']))   # several Fiji at once share the CPU
    if not tm.checkInput():
        raise Exception('TrackMate checkInput: ' + str(tm.getErrorMessage()))
    if not tm.process():
        raise Exception('TrackMate process: ' + str(tm.getErrorMessage()))
    return model


def num(v):
    if v is None:
        return ''
    f = float(v)
    if f != f or abs(f) == float('inf'):
        return ''
    if abs(f) < 1e15 and f == int(f):
        return str(int(f))
    return repr(f)


def names(fm, keys, kind):
    try:
        if kind == 'spot':
            n, sn = fm.getSpotFeatureNames(), fm.getSpotFeatureShortNames()
        else:
            n, sn = fm.getTrackFeatureNames(), fm.getTrackFeatureShortNames()
        return [n.get(k) or k for k in keys], [sn.get(k) or k for k in keys]
    except (Exception, Throwable):
        return list(keys), list(keys)


def write_csv(path, rows):
    # via a .part file: a crash never leaves a half-written CSV that the resume logic would trust
    w = OutputStreamWriter(FileOutputStream(path + '.part'), 'UTF-8')
    for r in rows:
        w.write(','.join(['"%s"' % c if (',' in c or '"' in c) else c for c in r]) + '\n')
    w.close()
    Files.move(Paths.get(path + '.part'), Paths.get(path), StandardCopyOption.REPLACE_EXISTING)


def export(model, out_base):
    fm, tm = model.getFeatureModel(), model.getTrackModel()
    # all spots (like TrackMate's 'All spots' table), with track id when the spot is in a track
    n1, n2 = names(fm, SPOT_KEYS, 'spot')
    rows = [['LABEL', 'ID', 'TRACK_ID'] + SPOT_KEYS, ['Label', 'Spot ID', 'Track ID'] + n1,
            ['Label', 'Spot ID', 'Track ID'] + n2, ['', '', ''] + [''] * len(SPOT_KEYS)]
    spots = sorted(list(model.getSpots().iterable(False)), key=lambda sp: (sp.getFeature('FRAME'), sp.ID()))
    for sp in spots:
        tid = tm.trackIDOf(sp)
        rows.append([sp.getName() or ('ID%d' % sp.ID()), str(sp.ID()), '' if tid is None else str(tid)]
                    + [num(sp.getFeature(k)) for k in SPOT_KEYS])
    write_csv(out_base + '_spots.csv', rows)
    n1, n2 = names(fm, TRACK_KEYS, 'track')
    rows = [['LABEL'] + TRACK_KEYS, ['Label'] + n1, ['Label'] + n2, [''] + [''] * len(TRACK_KEYS)]
    for tid in sorted(tm.trackIDs(True)):
        rows.append([tm.name(tid)] + [num(fm.getTrackFeature(tid, k)) for k in TRACK_KEYS])
    write_csv(out_base + '_tracks.csv', rows)
    return len(spots), len(rows) - 4


def open_segment(path, kind):
    if kind == 'seq':
        return FolderOpener().openFolder(path)    # as the plugin: File > Import > Image Sequence
    return IJ.openImage(path)


try:
    for name, stem, f, kind, dst in P['jobs']:
        if not os.path.isdir(dst):
            os.makedirs(dst)
        say('START', name, stem)
        try:
            imp = open_segment(f, kind)
            if imp is None:
                raise Exception('Fiji could not open ' + kind)
            n = imp.getStackSize()
            preprocess(imp)
            mx = find_maxima(imp)
            model = track(mx)
            ns, nt = export(model, os.path.join(dst, stem))
            say('OK', name, stem, n, ns, nt)
            imp.close()
            mx.close()
        except (Exception, Throwable), e:
            say('ERR', name, stem, e)
            traceback.print_exc()
except (Exception, Throwable), e:
    say('ERR', 'all', e)
    traceback.print_exc()
say('END')
PROG.close()
System.exit(0)
'''
