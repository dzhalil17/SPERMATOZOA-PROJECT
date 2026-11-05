"""
Шаг 0: видео → TrackMate, без окна Fiji.

Приложение запускает вашу Fiji в фоновом режиме (--headless) со скриптом на Jython (он встроен ниже,
отдельно ставить ничего не надо). Скрипт повторяет шаги Fiji-плагина Sperm_Tracking_Pipeline:
  8-bit → контраст (растяжка Min..Max, как B&C «Apply») → Gaussian Blur → Find Maxima (Single Points,
  каждый кадр) → TrackMate: LoG-детектор + LAP-трекер, все признаки → экспорт спотов и треков в CSV
  в том же виде, что кнопка «Export to CSV» в TrackMate (4 строки заголовка).
Дальше эти CSV забирает шаг 1 (оформление таблиц).

Видео: .tif/.tiff/.avi в папке (одно видео) или в её подпапках (подпапка = видео, файл = отрезок).
Имя файла отрезка должно начинаться с диапазона кадров, например «1223-1622.tif».
Координаты принудительно в пикселях, время — в кадрах (как во всех ваших таблицах).
"""
from __future__ import annotations

import json
import os
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

FIJI_CANDIDATES = [
    r'C:\Fiji.app\ImageJ-win64.exe', r'C:\Fiji.app\fiji-windows-x64.exe',
    r'C:\Program Files\Fiji.app\ImageJ-win64.exe', r'D:\Fiji.app\ImageJ-win64.exe',
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


def run(fiji_exe: str, videos_dir: Path, out_dir: Path, log=print, stop=lambda: False):
    fiji = fiji_exe or find_fiji()
    if not fiji or not Path(fiji).exists():
        raise FileNotFoundError('Не найдена Fiji: укажите путь к ImageJ-win64.exe на вкладке «Запуск»')
    out_dir.mkdir(parents=True, exist_ok=True)
    script = out_dir / '_fiji_trackmate.py'
    script.write_text(JYTHON_SCRIPT, encoding='utf-8')
    job = {**params(), 'input_dir': str(videos_dir), 'output_dir': str(out_dir)}
    job_path = out_dir / '_trackmate_params.json'
    job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding='utf-8')
    env = dict(os.environ, SPERMPIPELINE_PARAMS=str(job_path))
    cmd = [fiji, '--ij2', '--headless', '--console', '--run', str(script)]
    log(f'Fiji: {fiji}')
    progress = out_dir / '_progress.txt'
    if progress.exists():
        progress.unlink()
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    with (out_dir / '_fiji_журнал.txt').open('w', encoding='utf-8', errors='replace') as flog:
        proc = subprocess.Popen(cmd, stdout=flog, stderr=subprocess.STDOUT, env=env, creationflags=flags)
        seen, ok, bad = 0, 0, 0
        while True:
            alive = proc.poll() is None
            lines = progress.read_text(encoding='utf-8', errors='replace').splitlines() if progress.exists() else []
            for line in lines[seen:]:
                p = line.split('|')
                if p[0] == 'OK':
                    ok += 1
                    log(f'  OK {p[1]} / {p[2]}: кадров {p[3]}, спотов {p[4]}, треков {p[5]}')
                elif p[0] == 'ERR':
                    bad += 1
                    log(f'  ОШИБКА {" / ".join(p[1:-1])}: {p[-1]}')
                elif p[0] == 'START':
                    log(f'  {p[1]} / {p[2]}: обработка…')
            seen = len(lines)
            if not alive:
                break
            if stop():
                proc.kill()
                break
            time.sleep(0.5)
    log(f'TrackMate: готово {ok}, с ошибкой {bad}. Журнал Fiji: {out_dir / "_fiji_журнал.txt"}')
    if not ok:
        raise RuntimeError(f'Fiji не обработала ни одного видео (код {proc.returncode}); '
                           f'подробности — {out_dir / "_fiji_журнал.txt"}')
    return out_dir


# =============================================================================
# Скрипт для Fiji (Jython 2.7 — синтаксис старого Python 2, без f-строк)
# =============================================================================
JYTHON_SCRIPT = r'''# Generated by SpermPipeline: the Sperm_Tracking_Pipeline Fiji plugin steps, headless. ASCII only (scijava passes it as unicode).
import json, os, sys, traceback
from java.lang import System, Math, Throwable
from java.io import File, OutputStreamWriter, FileOutputStream
from ij import IJ, ImagePlus, ImageStack
from ij.process import StackConverter, ByteProcessor
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


PROG = OutputStreamWriter(FileOutputStream(os.path.join(P['output_dir'], '_progress.txt')), 'UTF-8')


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
    return lut


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
            gb.blurGaussian(ip, P['GAUSSIAN_SIGMA'], P['GAUSSIAN_SIGMA'], 0.02)


def find_maxima(imp):
    mf = MaximumFinder()
    st = imp.getStack()
    out = ImageStack(imp.getWidth(), imp.getHeight())
    for i in range(1, st.getSize() + 1):
        ip = st.getProcessor(i).duplicate()
        if P['LIGHT_BACKGROUND']:
            ip.invert()
        bp = mf.findMaxima(ip, P['NOISE_TOLERANCE'], MaximumFinder.NO_THRESHOLD, MaximumFinder.SINGLE_POINTS,
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
    if not tm.checkInput():
        raise Exception('TrackMate checkInput: ' + str(tm.getErrorMessage()))
    if not tm.process():
        raise Exception('TrackMate process: ' + str(tm.getErrorMessage()))
    return model


def num(v):
    if v is None:
        return ''
    f = float(v)
    if f != f:
        return ''
    if f == int(f) and abs(f) < 1e15:
        return str(int(f))
    return repr(f)


def names(fm, keys, kind):
    if kind == 'spot':
        n, sn = fm.getSpotFeatureNames(), fm.getSpotFeatureShortNames()
    else:
        n, sn = fm.getTrackFeatureNames(), fm.getTrackFeatureShortNames()
    return [n.get(k) or k for k in keys], [sn.get(k) or k for k in keys]


def write_csv(path, rows):
    w = OutputStreamWriter(FileOutputStream(path), 'UTF-8')
    for r in rows:
        w.write(','.join(['"%s"' % c if (',' in c or '"' in c) else c for c in r]) + '\n')
    w.close()


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


def videos(root):
    ext = [e.lower() for e in P['VIDEO_EXT'].split()]
    def files(d):
        return sorted([os.path.join(d, f) for f in os.listdir(d)
                       if os.path.isfile(os.path.join(d, f)) and os.path.splitext(f)[1].lower() in ext])
    if files(root):
        return [(os.path.basename(root.rstrip('/\\')), files(root))]
    out = []
    for d in sorted(os.listdir(root)):
        full = os.path.join(root, d)
        if os.path.isdir(full) and files(full):
            out.append((d, files(full)))
    return out


try:
    todo = videos(P['input_dir'])
    if not todo:
        say('ERR', P['input_dir'], 'no video files (%s)' % P['VIDEO_EXT'])
    for name, fs in todo:
        dst = os.path.join(P['output_dir'], name)
        if not os.path.isdir(dst):
            os.makedirs(dst)
        for f in fs:
            stem = os.path.splitext(os.path.basename(f))[0]
            say('START', name, stem)
            try:
                imp = IJ.openImage(f)
                if imp is None:
                    raise Exception('Fiji could not open the file')
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
