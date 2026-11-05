"""Сверка новой таблицы CASA TRACKS с прежней (например, из CASA TRACKS v2) — по отрезку и номеру трека.
python tools/compare_with_old.py "<новая>.xlsx" "<старая>.xlsx"
Печатает максимальное расхождение по каждому числовому столбцу."""
import sys
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
new, old = (pd.read_excel(p) for p in sys.argv[1:3])
for d in (new, old):
    d['SEGMENT'] = d['SEGMENT'].astype(str)
m = new.merge(old, on=['SEGMENT', 'TRACK_ID'], suffixes=('_new', '_old'))
print(f'треков: новая {len(new)}, старая {len(old)}, совпали по отрезку и номеру {len(m)}')
for c in new.columns:
    if c + '_old' in m and pd.api.types.is_numeric_dtype(m[c + '_new']):
        d = np.nanmax(np.abs(pd.to_numeric(m[c + '_new'], errors='coerce') - pd.to_numeric(m[c + '_old'], errors='coerce')))
        print(f'{c:35s} max |разница| = {d:.3g}')
