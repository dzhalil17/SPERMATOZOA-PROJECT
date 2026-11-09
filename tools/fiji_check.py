"""Проверка шага 0 (TrackMate в Fiji без окна) на синтетическом видео, весь путь до кластеризации.
python tools/fiji_check.py "C:\\Fiji.app\\ImageJ-win64.exe" [папка] [ffmpeg.exe]
Синтетика: 60 «сперматозоидов» (неподвижные, быстрые прямые, кружащие) на шумном фоне, 150 кадров.
Два видео: «synthetic» — стек .tif, «synthetic_png» — папка кадров PNG (как SEQUENCES IN ORDER) + папка _rejected.
С ffmpeg — ещё прогон с шага 00: 01.mpg (чёрные кадры, «снег», белая вспышка между двумя хорошими кусками) →
кадры → 2 отрезка + _rejected → TrackMate → … → кластеризация."""
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sperm_pipeline import settings as S, pipeline  # noqa: E402


def make_video(path: Path, n_frames=150, w=400, h=300, seed=1):
    rng = np.random.default_rng(seed)
    objs = []
    for i in range(60):
        kind = 'im' if i < 25 else ('fast' if i < 45 else 'circle')
        objs.append((kind, rng.uniform(40, w - 40), rng.uniform(40, h - 40), rng.uniform(0, 2 * np.pi)))
    yy, xx = np.mgrid[0:h, 0:w]
    frames = []
    for t in range(n_frames):
        img = rng.normal(30, 6, (h, w))
        for kind, x0, y0, a in objs:
            if kind == 'im':
                x, y = x0, y0
            elif kind == 'fast':
                x, y = (x0 + 2.0 * t * np.cos(a)) % w, (y0 + 2.0 * t * np.sin(a)) % h
            else:
                x, y = x0 + 15 * np.cos(a + 0.1 * t), y0 + 15 * np.sin(a + 0.1 * t)
            img += 180 * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 1.5 ** 2))
        frames.append(Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)))
    path.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(path, save_all=True, append_images=frames[1:])
    seq = path.parent.parent / 'synthetic_png' / path.stem          # тот же отрезок как папка кадров
    seq.mkdir(parents=True, exist_ok=True)
    for t, fr in enumerate(frames):
        fr.convert('RGB').save(seq / f'frame_{t:05d}.png')
    rej = path.parent.parent / 'synthetic_png' / '_rejected'         # должна быть пропущена
    rej.mkdir(exist_ok=True)
    frames[0].save(rej / 'frame_99999.png')


def make_mpg(path: Path, ffmpeg: str, w=400, h=300, seed=2):
    """8 чёрных, 140 хороших, 6 «снег», 130 хороших, 5 белых кадров → ожидаются 2 отрезка."""
    rng = np.random.default_rng(seed)
    objs = [(rng.uniform(40, w - 40), rng.uniform(80, h - 40), rng.uniform(0, 2 * np.pi), i < 25) for i in range(60)]
    yy, xx = np.mgrid[0:h, 0:w]
    plan = ['black'] * 8 + ['good'] * 140 + ['snow'] * 6 + ['good'] * 130 + ['white'] * 5
    tmp = path.parent / '_png'
    tmp.mkdir(parents=True, exist_ok=True)
    for t, kind in enumerate(plan):
        if kind == 'black':
            img = rng.normal(20, 2, (h, w))
        elif kind == 'white':
            img = rng.normal(250, 2, (h, w))
        elif kind == 'snow':
            img = rng.normal(128, 80, (h, w))
        else:
            img = rng.normal(150, 2, (h, w))
            for x0, y0, a, still in objs:
                x, y = (x0, y0) if still else ((x0 + 2.0 * t * np.cos(a)) % w, (y0 + 2.0 * t * np.sin(a)) % h)
                img += 100 * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 1.5 ** 2))
        Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(tmp / f'f_{t:05d}.png')
    subprocess.run([ffmpeg, '-y', '-loglevel', 'error', '-framerate', '25', '-i', str(tmp / 'f_%05d.png'),
                    '-c:v', 'mpeg2video', '-q:v', '2', str(path)], check=True)


def check_from_mpg(fiji: str, ffmpeg: str, root: Path):
    make_mpg(root / 'mpg' / '01.mpg', ffmpeg)
    cfg = S.defaults()
    cfg['trackmate'].update(GAUSSIAN_SIGMA=1.0, NOISE_TOLERANCE=40.0, LOG_QUALITY_THRESHOLD=1.0,
                            LINKING_MAX_DISTANCE=15.0, GAP_CLOSING_MAX_DISTANCE=15.0, MAX_FRAME_GAP=2)
    cfg['general'].update(step_frames=True, mpg_dir=str(root / 'mpg'), ffmpeg_exe=ffmpeg, fiji_exe=fiji,
                          output_dir=str(root / 'out_mpg'), n_workers=2)
    pipeline.run(cfg, log=lambda m: print(m, flush=True))
    vdir = root / 'out_mpg' / '01' / pipeline.DIR_FRAMES          # 01.mpg → папка «01»
    segs = sorted(p.name for p in vdir.iterdir() if p.is_dir() and not p.name.startswith('_'))
    n_rej = len(list((vdir / '_rejected').glob('*.png')))
    print('отрезки:', segs, ' в _rejected:', n_rej)
    assert len(segs) == 2, 'ожидалось 2 отрезка'
    import pandas as pd
    summ = pd.read_excel(root / 'out_mpg' / pipeline.DIR_ALL / pipeline.SUMMARY, dtype={'video': str})
    print(summ.to_string(index=False))
    assert list(summ['video'].astype(str)) == ['01'] and summ['n_tracks'].iloc[0] >= 20
    for d in (pipeline.DIR_TRACKMATE, pipeline.DIR_TABLES, pipeline.DIR_FILTER, pipeline.DIR_CASA_TRACKS):
        assert (root / 'out_mpg' / '01' / d).is_dir(), f'нет папки 01\\{d}'
    assert (root / 'out_mpg' / '01' / pipeline.DIR_CLUSTER / 'итог 01.xlsx').exists()
    print('папки результатов:', sorted(p.name for p in (root / 'out_mpg').iterdir()))


def same_tracking(spots_a: Path, spots_b: Path) -> bool:
    """Одинаков ли результат TrackMate. Номера спотов и треков (ID) — счётчик Java, зависят от числа потоков и
    порядка работы, поэтому сравниваются сами споты (кадр, x, y, все признаки), состав каждого трека и признаки
    треков (до 9 значащих цифр: суммы в другом порядке дают разницу в последнем знаке double)."""
    import pandas as pd

    def r9(v):
        try:
            return f'{float(v):.9g}'
        except ValueError:
            return v

    def load(spots):
        sp = pd.read_csv(spots, skiprows=[1, 2, 3], dtype=str).fillna('')
        feats = [c for c in sp.columns if c not in ('LABEL', 'ID', 'TRACK_ID')]
        key = sp[feats].agg('|'.join, axis=1)
        tracks = sorted(tuple(sorted(key[sp['TRACK_ID'] == t])) for t in sp['TRACK_ID'].unique() if t != '')
        tr = pd.read_csv(Path(str(spots).replace('_spots.csv', '_tracks.csv')), skiprows=[1, 2, 3], dtype=str).fillna('')
        tfeats = [c for c in tr.columns if c not in ('LABEL', 'TRACK_INDEX', 'TRACK_ID')]
        exact = sorted(tr[tfeats].agg('|'.join, axis=1))
        rounded = sorted(tr[tfeats].map(r9).agg('|'.join, axis=1))
        return sorted(key), tracks, rounded, exact, tfeats

    a, b = load(spots_a), load(spots_b)
    name = f'{spots_a.parent.name}/{spots_a.name}'
    for what, x, y in zip(('споты', 'состав треков', 'признаки треков'), a, b):
        if x != y:
            print(f'  {name}: различаются {what}')
            if what == 'признаки треков':
                for ra, rb in zip(x, y):
                    if ra != rb:
                        print('   ', [(c, u, v) for c, u, v in zip(a[4], ra.split('|'), rb.split('|')) if u != v][:6])
                        break
            return False
    if a[3] != b[3]:
        diff = {c for ra, rb in zip(a[3], b[3]) for c, u, v in zip(a[4], ra.split('|'), rb.split('|')) if u != v}
        print(f'  {name}: признаки треков совпали до 9 знаков; в последних знаках расходятся: {sorted(diff)}')
    return True


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    fiji = sys.argv[1]
    root = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(tempfile.mkdtemp(prefix='SpermPipeline_fiji_'))
    make_video(root / 'videos' / 'synthetic' / '0-149.tif')
    cfg = S.defaults()
    cfg['trackmate'].update(GAUSSIAN_SIGMA=1.0, NOISE_TOLERANCE=40.0, LOG_QUALITY_THRESHOLD=1.0,
                            LINKING_MAX_DISTANCE=15.0, GAP_CLOSING_MAX_DISTANCE=15.0, MAX_FRAME_GAP=2)
    cfg['general'].update(step_trackmate=True, videos_dir=str(root / 'videos'), fiji_exe=fiji,
                          output_dir=str(root / 'out'), step_tables=True, step_filter=True, step_cluster=True,
                          n_workers=2, fiji_procs=2)               # 2 отрезка → 2 Fiji одновременно
    pipeline.run(cfg, log=lambda m: print(m, flush=True))
    assert (root / 'out' / pipeline.DIR_ALL / 'журналы Fiji' / '_fiji_журнал_2.txt').exists(), 'вторая Fiji не запускалась'
    import pandas as pd
    summ = pd.read_excel(root / 'out' / pipeline.DIR_ALL / pipeline.SUMMARY)
    print(summ.to_string(index=False))
    assert set(summ['video']) == {'synthetic', 'synthetic_png'}, 'нет одного из видео'
    assert (summ['n_tracks'] >= 20).all(), 'слишком мало треков'
    assert not (root / 'out' / 'synthetic_png' / pipeline.DIR_TRACKMATE / '_rejected_spots.csv').exists()
    for v in ('synthetic', 'synthetic_png'):
        assert (root / 'out' / v / pipeline.DIR_CLUSTER / f'{v}_scatter.png').exists(), f'нет графика {v}'
    from sperm_pipeline import trackmate                                # повтор: всё уже посчитано
    again = trackmate.run(fiji, root / 'videos', root / 'out_again', log=print,
                          dest=lambda v: root / 'out' / v / pipeline.DIR_TRACKMATE)
    assert len(again['already']) == 2 and not again['ok'], again
    one = trackmate.run(fiji, root / 'videos', root / 'out_one_fiji', log=print, n_procs=1)   # то же одной Fiji
    assert len(one['ok']) == 2, one
    for f in sorted((root / 'out').glob(f'*/{pipeline.DIR_TRACKMATE}/*_spots.csv')):
        g = root / 'out_one_fiji' / f.parent.parent.name / f.name
        assert same_tracking(f, g), f'2 Fiji и 1 Fiji дали разные {f.parent.name}/{f.name}'
    print('2 Fiji одновременно = 1 Fiji: те же споты, те же треки, те же признаки треков')
    # пределы площади яркого пятна: точек не больше, чем без них, и не ноль; слишком тесный предел убирает почти всё
    lines = []
    trackmate.MIN_OBJECT_AREA, trackmate.MAX_OBJECT_AREA, trackmate.OBJECT_AREA_LEVEL = 3.0, 60.0, 100.0
    area = trackmate.run(fiji, root / 'videos', root / 'out_area', log=lambda m: (lines.append(m), print(m)), n_procs=1)
    trackmate.MAX_OBJECT_AREA = 4.0
    tight = trackmate.run(fiji, root / 'videos', root / 'out_area_tight', log=print, n_procs=1)
    trackmate.MIN_OBJECT_AREA = trackmate.MAX_OBJECT_AREA = trackmate.OBJECT_AREA_LEVEL = 0.0
    assert len(area['ok']) == 2 and len(tight['ok']) == 2, (area, tight)
    assert sum('убрано по площади' in m for m in lines) == 2, 'нет строки о площади'
    n_all, n_area, n_tight = (sum(x[3] for x in r['ok']) for r in (one, area, tight))
    print(f'спотов: без пределов {n_all}, площадь 3–60 px {n_area}, площадь 3–4 px {n_tight}')
    assert 0.5 * n_all < n_area <= n_all, 'предел площади 3–60 px убрал слишком много или ничего не оставил'
    assert n_tight < 0.5 * n_area, 'тесный предел площади не подействовал'
    if len(sys.argv) > 3:
        check_from_mpg(fiji, sys.argv[3], root)
    print('FIJI CHECK: OK')


if __name__ == '__main__':
    main()
