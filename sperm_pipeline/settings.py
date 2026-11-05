"""
Все настройки приложения. Значения по умолчанию берутся ПРЯМО из модулей filtration.py, casa.py и
clustering.py (т.е. из ваших скриптов), поэтому по умолчанию результат тот же, что у скриптов.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import filtration, casa, clustering

# (модуль, имя, подпись). Тип значения определяется по значению по умолчанию; choices — список вариантов.
FILTRATION = [
    ('LINK_MAX_GAP', 'Сшивка: макс. пропуск кадров между фрагментами'),
    ('LINK_SPEED_DIFF', 'Сшивка: макс. относительная разница средних скоростей'),
    ('BASE_SEARCH_RADIUS', 'Сшивка: радиус поиска при gap=0, px'),
    ('MAX_SPEED_PX', 'Сшивка: физический предел скорости, px/кадр'),
    ('LINK_MAX_DIST_ABS', 'Сшивка: жёсткий потолок расстояния, px'),
    ('MIN_LINK_SPEED', 'Сшивка: мин. средняя скорость фрагмента, px/кадр'),
    ('MIN_LINK_SPOTS', 'Сшивка: мин. число спотов во фрагменте'),
    ('GAP_SCALE_PAR', 'Разрез у границ: разрыв больше N кадров'),
    ('BORDER_X_MIN', 'Разрез у границ: X min, px'),
    ('BORDER_X_MAX', 'Разрез у границ: X max, px'),
    ('BORDER_Y_MIN', 'Разрез у границ: Y min, px'),
    ('BORDER_Y_MAX', 'Разрез у границ: Y max, px'),
    ('THRESHOLD', 'Разрез у границ: резать, если угол < (°)'),
    ('BORDER_VELOCITY_TAIL', 'Разрез у границ: точек для вектора скорости'),
    ('COLLISION_DISTANCE_THRESHOLD', 'Коллизии: расстояние, px'),
    ('MIN_FRAGMENT_SIZE', 'Коллизии: мин. размер фрагмента'),
    ('SHORT_FRAGMENT_SIZE', 'Коллизии: «короткий» фрагмент, спотов'),
    ('SHORT_POST_SPOTS', 'Коллизии: SHORT_POST_SPOTS'),
    ('LONG_POST_SPOTS', 'Коллизии: режим FULL, если после коллизии > N спотов'),
    ('VELOCITY_TAIL', 'Коллизии: точек для вектора скорости'),
    ('CROSS_SPEED_THRESHOLD', 'Коллизии: CROSS_SPEED_THRESHOLD'),
    ('MIN_TRACK_DURATION', 'Фильтр: мин. длительность трека, кадров'),
    ('MIN_SPOTS', 'Фильтр: мин. число спотов в треке'),
]
CASA = [
    ('WINDOW', 'Окно, кадров'),
    ('WINDOW_STEP', 'Шаг окна, кадров'),
    ('VAP_SMOOTH', 'Сглаживание VAP, точек'),
    ('FPS', 'Кадров в секунду (None — скорости в px/кадр)'),
    ('UM_PER_PX', 'Микрон на пиксель (None — расстояния в px)'),
]
CLUSTERING = [
    ('X_COL', 'Ось «скорость»'),
    ('Y_COL', 'Ось «прямолинейность»'),
    ('Z_COL', 'Третий признак (None — только два)'),
    ('MODE', 'Режим', ['per_video', 'pooled']),
    ('SCALER', 'Стандартизация', ['standard', 'robust', 'yeo-johnson']),
    ('WEIGHT_X', 'Вес X'),
    ('WEIGHT_Y', 'Вес Y'),
    ('WEIGHT_Z', 'Вес Z'),
    ('IM_FROM_2D', 'IM из отдельного 2D-прогона (X+Y)'),
    ('hdbscan.min_cluster_size', 'HDBSCAN min_cluster_size (доля < 1 или число)'),
    ('hdbscan.min_samples', 'HDBSCAN min_samples'),
    ('hdbscan.cluster_selection_method', 'HDBSCAN cluster_selection_method', ['eom', 'leaf']),
    ('hdbscan.cluster_selection_epsilon', 'HDBSCAN cluster_selection_epsilon'),
    ('hdbscan.metric', 'HDBSCAN metric'),
    ('IM_MERGE', 'IM: сливать медленные кластеры и шум'),
    ('IM_QUANTILE', 'IM: перцентиль скорости опорного кластера'),
    ('IM_REF', 'IM: опорный кластер', ['largest_slow', 'slowest']),
    ('IM_LIMIT_BY_RP', 'IM: граница не дальше середины IM–RP'),
    ('RP_MODE', 'RP: добор шума', ['quantile', 'prob', None]),
    ('RP_PROB', 'RP: порог вероятности (для prob)'),
    ('RP_QUANTILE', 'RP: перцентиль (для quantile)'),
    ('RP_TRIM', 'RP: срезать медленную часть ниже перцентиля (None — нет)'),
    ('SP_MODE', 'SP (b): верх для шума', ['to_trim', 'to_rp_start', None]),
    ('NP_MODE', 'NP (c): выделять'),
    ('NP_QUANTILE', 'NP (c): перцентиль'),
    ('SOFT_ASSIGN', 'Шум → самый вероятный кластер при вероятности ≥ (None — нет)'),
    ('MIN_TRACKS', 'Мин. треков в видео'),
    ('SAVE_PLOTS', 'Сохранять графики'),
    ('SAVE_TRACK_LABELS', 'Сохранять метки треков'),
]
GENERAL_DEFAULTS = {
    'raw_dir': '',          # сырые экспорты TrackMate (.csv) — для шага 1
    'spots_dir': '',        # оформленные *_allspots.xlsx (или папки-видео с ними) — вход шага 2, если шаг 1 выключен
    'tracks_dir': '',       # оформленные *_tracks.xlsx (не обязательно: фильтр их читает, но не использует)
    'casa_dir': '',         # готовые таблицы CASA TRACKS — вход шага 5, если шаги 2–4 выключены
    'output_dir': '',
    'step_tables': True,
    'step_filter': True,
    'step_cluster': True,
    'n_workers': max(1, min(8, (os.cpu_count() or 2) - 1)),
}


def _module_default(mod, name):
    if name.startswith('hdbscan.'):
        return mod.HDBSCAN_PARAMS[name.split('.', 1)[1]]
    return getattr(mod, name)


SECTIONS = [('filtration', filtration, FILTRATION), ('casa', casa, CASA), ('clustering', clustering, CLUSTERING)]

# значения по умолчанию снимаются при импорте, до того как приложение что-либо поменяет
DEFAULTS = {sec: {item[0]: _module_default(mod, item[0]) for item in spec} for sec, mod, spec in SECTIONS}
DEFAULTS['general'] = dict(GENERAL_DEFAULTS)


def defaults() -> dict:
    return json.loads(json.dumps(DEFAULTS))


def apply(settings: dict):
    """Переписывает константы модулей значениями из настроек (в текущем процессе)."""
    for sec, mod, spec in SECTIONS:
        vals = settings.get(sec, {})
        for item in spec:
            name = item[0]
            if name not in vals:
                continue
            v = vals[name]
            if name.startswith('hdbscan.'):
                mod.HDBSCAN_PARAMS[name.split('.', 1)[1]] = v
            else:
                setattr(mod, name, v)


def config_path() -> Path:
    base = Path(os.environ.get('APPDATA') or Path.home())
    return base / 'SpermPipeline' / 'settings.json'


def load() -> dict:
    s = defaults()
    try:
        saved = json.loads(config_path().read_text(encoding='utf-8'))
        for sec in s:
            s[sec].update({k: v for k, v in saved.get(sec, {}).items() if k in s[sec]})
    except Exception:
        pass
    return s


def save(settings: dict):
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding='utf-8')
