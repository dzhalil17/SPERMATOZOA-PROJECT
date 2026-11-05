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
    vdir = root / 'out_mpg' / pipeline.DIR_FRAMES / '1 video'
    segs = sorted(p.name for p in vdir.iterdir() if p.is_dir() and not p.name.startswith('_'))
    n_rej = len(list((vdir / '_rejected').glob('*.png')))
    print('отрезки:', segs, ' в _rejected:', n_rej)
    assert len(segs) == 2, 'ожидалось 2 отрезка'
    import pandas as pd
    summ = pd.read_excel(root / 'out_mpg' / pipeline.DIR_CLUSTER / 'summary_all_videos.xlsx')
    print(summ.to_string(index=False))
    assert list(summ['video']) == ['1 video'] and summ['n_tracks'].iloc[0] >= 20


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
                          n_workers=2)
    pipeline.run(cfg, log=lambda m: print(m, flush=True))
    import pandas as pd
    summ = pd.read_excel(root / 'out' / pipeline.DIR_CLUSTER / 'summary_all_videos.xlsx')
    print(summ.to_string(index=False))
    assert set(summ['video']) == {'synthetic', 'synthetic_png'}, 'нет одного из видео'
    assert (summ['n_tracks'] >= 20).all(), 'слишком мало треков'
    assert not (root / 'out' / pipeline.DIR_TRACKMATE / 'synthetic_png' / '_rejected_spots.csv').exists()
    from sperm_pipeline import trackmate                                # повтор: всё уже посчитано
    again = trackmate.run(fiji, root / 'videos', root / 'out' / pipeline.DIR_TRACKMATE, log=print)
    assert len(again['already']) == 2 and not again['ok'], again
    if len(sys.argv) > 3:
        check_from_mpg(fiji, sys.argv[3], root)
    print('FIJI CHECK: OK')


if __name__ == '__main__':
    main()
