"""
Оформление таблиц TrackMate — замена макросов Excel (Macros for tracks.xlsm, Macros for spots.xlsm) без VBA.

Что делает (как макросы):
  • убирает 4 строки заголовка TrackMate (ключи признаков, полные имена, короткие имена, единицы);
  • вставляет столбцы: в треки — SubjectID, ID_Ejaculate, Frame_range (D, E, F); в споты — Frame_range (D);
  • пишет свою шапку: строка 1 — имена столбцов (жирные), строка 2 — описания; данные с 3-й строки;
  • Frame_range = часть имени файла: у треков — до «(», иначе имя без расширения;
                                     у спотов — до «(», иначе до первой точки;
  • закрепляет строку 1 и первый столбец (треки) / первые два столбца (споты), ставит автофильтр,
    подгоняет ширину столбцов; сохраняет .xlsx с тем же именем.

Отличие от макросов (сознательное): макрос вписывал шапку ПО ПОЗИЦИИ и не проверял, что столбцы
TrackMate стоят там, где он ждёт. Если в экспорте был лишний/переставленный столбец, имена «съезжали»
относительно данных — так испортились споты видео 1, 2 и 15 (в QUALITY оказался диапазон кадров).
Здесь столбцы берутся ПО ИМЕНИ из первой строки экспорта; если нужного столбца нет — файл не
оформляется и в журнале пишется, чего не хватает. Числа читаются без Excel, поэтому не превращаются
в даты (RADIUS = 2.5 в русском Excel становился «02.05»).
"""
from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

# ---- шапка из Macros for tracks.xlsm (FinalVideoProcessing_Fixed_Column) — дословно ----
TRACKS_HEADERS = ["LABEL", "TRACK_INDEX", "TRACK_ID", "SubjectID", "ID_Ejaculate", "Frame_range", "NUMBER_SPOTS",
                  "NUMBER_GAPS", "NUMBER_SPLITS", "NUMBER_MERGES", "NUMBER_COMPLEX", "LONGEST_GAP", "TRACK_DURATION",
                  "TRACK_START", "TRACK_STOP", "TRACK_DISPLACEMENT", "TRACK_X_LOCATION", "TRACK_Y_LOCATION",
                  "TRACK_Z_LOCATION", "TRACK_MEAN_SPEED", "TRACK_MAX_SPEED", "TRACK_MIN_SPEED", "TRACK_MEDIAN_SPEED",
                  "TRACK_STD_SPEED", "TRACK_MEAN_QUALITY", "TOTAL_DISTANCE_TRAVELED", "MAX_DISTANCE_TRAVELED",
                  "CONFINEMENT_RATIO", "MEAN_STRAIGHT_LINE_SPEED", "LINEARITY_OF_FORWARD_PROGRESSION",
                  "MEAN_DIRECTIONAL_CHANGE_RATE"]
TRACKS_PART1 = ["Label", "Unique Track index.", "Track ID.", "Subject ID - study participant ID.", "ID ejaculate",
                "Range of frames (from title of csv. file).", "Number of spots in track.",
                "Number of gaps. (connections between two spots which skipped one or more frames).",
                "Number of split events. (when two or more objects came from primary one).",
                "Number of merge events. (when two or more objects were merged in one common track).",
                "Number of complex points. (spots which is connected to more than two spots).",
                "Max number of frames when sperm wasn't detected although track was continous.",
                "Lifetime of track. (frame)", "The frame when sperm emerged. (frame)",
                "The frame when sperm disappeared. (frame)"]
TRACKS_PART2 = ["Lenght from start position to the end of the track. (pixel)",
                "X mean location of all spots of the track. (pixel)",
                "Y mean location of all spots of the track. (pixel)",
                "Z location of geometric center of the track. (pixel)",
                "Arithmetic mean of the instanteous speeds of the track. (pixel/frame)",
                "Max instateous speed of the track. (pixel/frame)", "Min instateous speed of the track. (pixel/frame)",
                "Median value of the speed of the track. (pixel/frame)",
                "Track standard deviation of the speed. (pixel/frame)",
                "Mean confidence in the correctness of sperm detection. (quality)",
                "Lenght of the path which was traveled by sperm. (pixel)",
                "Lenght between start spot and the farthest spot. (pixel)",
                "Ration of displacement to total dista traveled.", "Ration of displacement to duration. (pixel/frame)",
                "Ration of mean straight line speed to mean speed.",
                "Mean instanteous angle change divided by duration. (rad/frame)"]
TRACKS_INSERTED = ["SubjectID", "ID_Ejaculate", "Frame_range"]   # столбцы D:F, которых нет в экспорте TrackMate
TRACKS_PART2_COL = 16    # «P2»
TRACKS_FREEZE = "B2"

# ---- шапка из Macros for spots.xlsm (FinalVideoProcessing_Updated) — дословно ----
SPOTS_HEADERS = ["LABEL", "ID", "TRACK_ID", "Frame_range", "QUALITY", "POSITION_X", "POSITION_Y", "POSITION_Z",
                 "POSITION_T", "FRAME", "RADIUS", "VISIBILITY", "MANUAL_SPOT_COLOR", "MEAN_INTENSITY_CH1",
                 "MEDIAN_INTENSITY_CH1", "MIN_INTENSITY_CH1", "MAX_INTENSITY_CH1", "TOTAL_INTENSITY_CH1",
                 "STD_INTENSITY_CH1", "CONTRAST_CH1", "SNR_CH1"]
SPOTS_PART1 = ["Label", "Spot ID", "Track ID which spot belongs to.", "Range of frames (from title of csv. file)",
               "Confidence in the correctness of sperm detection. (quality)", "X-coordinates of the spot. (pixel)",
               "Y-coordinates of the spot. (pixel)", "Z-coordinates of the spot. (pixel)",
               "Time when this spot appeared. (frame)", "Frame when this spot appeared.",
               "Radius of the detection area. (pixel)", "Visibility of the spot (1 = visible 0 = hidden)."]
SPOTS_PART2 = ["Manual parameter which stores a user-assigned color of the spot.",
               "Mean intensity of pixels whithin detection area. (counts)",
               "Median intensity of pixels whithin detection area. (counts)",
               "Intensity of the least intense pixel. (counts)", "Intensity of the most intense pixel. (counts)",
               "Sum of intensities of each pixel within detection area. (counts)",
               "Standard deviation of intensity. (counts)",
               "The difference between intensity inside detection area and intensity of background.", "Ratio"]
SPOTS_INSERTED = ["Frame_range"]
SPOTS_PART2_COL = 13     # «M2»
SPOTS_FREEZE = "C2"

RAW_EXT = ('.csv', '.xls', '.xlsx')
TRACKMATE_HEADER_ROWS = 4   # макрос: ws.Rows("1:4").Delete


def frame_range_tracks(filename: str) -> str:
    """Как в макросе треков: до «(», иначе имя без расширения (до последней точки)."""
    if '(' in filename:
        return filename[:filename.index('(')].strip()
    return filename[:filename.rindex('.')] if '.' in filename else filename


def frame_range_spots(filename: str) -> str:
    """Как в макросе спотов: до «(», иначе до первой точки."""
    ch = '(' if '(' in filename else '.'
    return filename[:filename.index(ch)].strip() if ch in filename else filename.strip()


def read_raw(path: Path) -> pd.DataFrame:
    """Весь файл как таблица строк, без заголовка (как его видит Excel до макроса)."""
    if path.suffix.lower() == '.csv':
        return pd.read_csv(path, header=None, dtype=str, keep_default_na=False, encoding_errors='replace')
    return pd.read_excel(path, header=None, dtype=str).fillna('')


def kind_of(keys) -> str:
    keys = set(keys)
    if {'TRACK_INDEX', 'NUMBER_SPOTS'} <= keys:
        return 'tracks'
    if {'FRAME', 'POSITION_X', 'POSITION_Y'} <= keys:
        return 'spots'
    return ''


def _num(col: pd.Series) -> pd.Series:
    """Строки → числа там, где весь столбец числовой (как Excel открывает CSV), иначе текст."""
    s = col.replace('', np.nan)
    out = pd.to_numeric(s, errors='coerce')
    if out.notna().sum() == s.notna().sum():
        # целые без дробной части — как целые
        if out.notna().any() and (out.dropna() % 1 == 0).all():
            return out.astype('Int64')
        return out
    return col


def format_file(src: Path, dst_dir: Path, log=print) -> Path | None:
    raw = read_raw(src)
    if raw.empty:
        log(f'  ПРОПУСК {src.name}: пустой файл')
        return None
    keys = [str(k).strip() for k in raw.iloc[0].tolist()]
    kind = kind_of(keys)
    if not kind:
        log(f'  ПРОПУСК {src.name}: не похоже на экспорт TrackMate (нет столбцов спотов/треков в 1-й строке)')
        return None
    if 'Frame_range' in keys:
        log(f'  ПРОПУСК {src.name}: файл уже оформлен (есть столбец Frame_range)')
        return None
    # проверка, что после строки ключей действительно 3 строки описаний, а не данные
    first_after = str(raw.iloc[1, 0]) if len(raw) > 1 else ''
    if first_after.startswith(('ID', 'Track_')) and first_after[-1:].isdigit():
        log(f'  ПРОПУСК {src.name}: после строки имён сразу идут данные — это не сырой экспорт TrackMate '
            f'(возможно, файл уже оформлен)')
        return None
    if kind == 'tracks':
        headers, part1, part2, p2col, freeze, inserted = (TRACKS_HEADERS, TRACKS_PART1, TRACKS_PART2,
                                                          TRACKS_PART2_COL, TRACKS_FREEZE, TRACKS_INSERTED)
        frange = frame_range_tracks(src.name)
    else:
        headers, part1, part2, p2col, freeze, inserted = (SPOTS_HEADERS, SPOTS_PART1, SPOTS_PART2,
                                                          SPOTS_PART2_COL, SPOTS_FREEZE, SPOTS_INSERTED)
        frange = frame_range_spots(src.name)

    expected = [h for h in headers if h not in inserted]
    missing = [h for h in expected if h not in keys]
    if missing:
        log(f'  ОШИБКА {src.name}: в экспорте нет столбцов {missing} — файл не оформлен')
        return None
    if keys[:len(expected)] != expected:
        log(f'  ВНИМАНИЕ {src.name}: порядок столбцов отличается от ожидаемого макросом; '
            f'столбцы взяты по имени (макрос здесь «сдвинул» бы шапку)')
    extra = [k for k in keys if k and k not in expected]
    if extra:
        log(f'  ВНИМАНИЕ {src.name}: лишние столбцы {extra} отброшены')

    body = raw.iloc[TRACKMATE_HEADER_ROWS:].reset_index(drop=True)
    body.columns = keys
    data = pd.DataFrame(index=body.index)
    for h in headers:
        if h == 'Frame_range':
            data[h] = frange if len(body) else []
        elif h in inserted:
            data[h] = None
        else:
            data[h] = _num(body[h])

    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / (src.name[:src.name.rindex('.')] + '.xlsx')
    write_formatted(data, headers, part1, part2, p2col, freeze, dst)
    log(f'  OK {src.name} → {dst.name}  ({kind}, строк: {len(data)}, Frame_range = {frange})')
    return dst


def write_formatted(data: pd.DataFrame, headers, part1, part2, p2col, freeze, dst: Path):
    wb = Workbook()
    ws = wb.active
    ws.title = 'Sheet1'
    ws.append(list(headers))
    row2 = [None] * len(headers)
    row2[:len(part1)] = part1
    row2[p2col - 1:p2col - 1 + len(part2)] = part2
    ws.append(row2)
    for row in data.itertuples(index=False):
        ws.append([None if (v is None or v is pd.NA or (isinstance(v, float) and np.isnan(v))) else
                   (v.item() if hasattr(v, 'item') else v) for v in row])
    bold = Font(bold=True)
    for c in ws[1]:
        c.font = bold
    ws.freeze_panes = freeze
    ws.auto_filter.ref = f'A1:{get_column_letter(len(headers))}{max(ws.max_row, 1)}'
    for i, h in enumerate(headers, start=1):     # «AutoFit»: по самой длинной ячейке столбца
        lens = [len(str(h)), len(str(row2[i - 1] or ''))]
        col = data.iloc[:, i - 1]
        if len(col) and col.notna().any():
            lens.append(int(col.dropna().astype(str).str.len().max()))
        ws.column_dimensions[get_column_letter(i)].width = min(max(lens) + 2, 100)
    wb.save(dst)


def video_dirs(root: Path, is_member) -> list:
    """Папки-видео: сам root, если в нём есть нужные файлы, иначе его подпапки с такими файлами."""
    def has(d):
        return any(is_member(f) for f in d.iterdir() if f.is_file())
    if has(root):
        return [root]
    return sorted([d for d in root.iterdir() if d.is_dir() and has(d)], key=lambda d: d.name)


def is_raw(f: Path) -> bool:
    return f.suffix.lower() in RAW_EXT and not f.name.startswith('~$')


def format_tree(raw_root: Path, out_root: Path, log=print) -> list:
    """Оформляет все экспорты TrackMate в raw_root (или в его подпапках-видео). Возвращает список папок-видео."""
    out = []
    dirs = video_dirs(raw_root, is_raw)
    if not dirs:
        raise FileNotFoundError(f'В {raw_root} нет файлов .csv/.xls/.xlsx')
    for d in dirs:
        name = d.name
        log(f'Видео «{name}»:')
        dst_dir = out_root / name
        n = 0
        for f in sorted(d.iterdir()):
            if f.is_file() and is_raw(f):
                try:
                    if format_file(f, dst_dir, log):
                        n += 1
                except Exception as e:
                    log(f'  ОШИБКА {f.name}: {e!r}')
        if n:
            out.append(dst_dir)
    return out
