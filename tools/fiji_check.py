"""Проверка шага 0 (TrackMate в Fiji без окна) на синтетическом видео, весь путь до кластеризации.
python tools/fiji_check.py "C:\\Fiji.app\\ImageJ-win64.exe" [папка]
Синтетика: 60 «сперматозоидов» (неподвижные, быстрые прямые, кружащие) на шумном фоне, 150 кадров."""
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


def main():
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
    assert summ['n_tracks'].iloc[0] >= 20, 'слишком мало треков'
    print('FIJI CHECK: OK')


if __name__ == '__main__':
    main()
