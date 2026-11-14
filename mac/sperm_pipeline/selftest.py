"""Самопроверка установки: синтетическое видео (неподвижные, быстрые прямые, медленные кривые треки)
→ супер-фильтрация → CASA → кластеризация. Ничего не знает о ваших данных; проверяет только, что всё
установлено и запускается. Запуск: SpermPipeline.exe --selftest (или python SpermPipeline.py --selftest)."""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from . import settings as S, pipeline, tables


def make_video(dst_dir: Path, seg: str = '100-299', seed: int = 0):
    rng = np.random.default_rng(seed)
    rows, sid = [], 0
    n_frames = 200
    kinds = ['im'] * 40 + ['fast'] * 30 + ['slow'] * 20
    for tid, kind in enumerate(kinds):
        x, y = rng.uniform(150, 550), rng.uniform(150, 400)
        ang = rng.uniform(0, 2 * np.pi)
        for fr in range(n_frames):
            if kind == 'im':
                px, py = x + rng.integers(-1, 2), y + rng.integers(-1, 2)
            elif kind == 'fast':
                px, py = x + 0.6 * fr * np.cos(ang) + rng.normal(0, .8), y + 0.6 * fr * np.sin(ang) + rng.normal(0, .8)
            else:
                a = ang + 0.08 * fr
                px, py = x + 25 * np.cos(a) + 0.1 * fr, y + 25 * np.sin(a)
            px, py = float(np.clip(round(px), 30, 690)), float(np.clip(round(py), 25, 550))
            rows.append({'LABEL': f'ID{sid}', 'ID': sid, 'TRACK_ID': tid, 'Frame_range': seg, 'QUALITY': 16.6,
                         'POSITION_X': px, 'POSITION_Y': py, 'POSITION_Z': 0.0, 'POSITION_T': fr, 'FRAME': fr,
                         'RADIUS': 2.5, 'VISIBILITY': 1})
            sid += 1
    data = pd.DataFrame(rows).reindex(columns=tables.SPOTS_HEADERS)
    dst_dir.mkdir(parents=True, exist_ok=True)
    tables.write_formatted(data, tables.SPOTS_HEADERS, tables.SPOTS_PART1, tables.SPOTS_PART2,
                           tables.SPOTS_PART2_COL, tables.SPOTS_FREEZE, dst_dir / f'{seg}_allspots.xlsx')


def run(log=print) -> bool:
    tmp = Path(tempfile.mkdtemp(prefix='SpermPipeline_selftest_'))
    make_video(tmp / 'in' / 'synthetic')
    cfg = S.defaults()
    cfg['general'].update(spots_dir=str(tmp / 'in'), output_dir=str(tmp / 'out'), step_tables=False,
                          step_filter=True, step_cluster=True, n_workers=1)
    pipeline.run(cfg, log=log)
    summ = pd.read_excel(tmp / 'out' / pipeline.DIR_ALL / pipeline.SUMMARY)
    v = tmp / 'out' / 'synthetic'                      # папка видео: всё про него — в ней
    ok = (len(summ) == 1 and summ['n_tracks'].iloc[0] > 0
          and (v / pipeline.DIR_CASA_TRACKS / 'synthetic.xlsx').exists()
          and (v / pipeline.DIR_CLUSTER / 'итог synthetic.xlsx').exists())
    log(f'\nСАМОПРОВЕРКА: {"OK" if ok else "ОШИБКА"} — папка {tmp}')
    return ok
