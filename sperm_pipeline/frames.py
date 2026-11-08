"""
Шаг 00: видео (.mpg) → кадры (ffmpeg) → отрезки «начало-конец» + _rejected.

Копия вашего GET SEQUENCES IN ORDER FROM VIDEO.py (оригинал — original/). Логика и пороги без изменений;
отличия помечены [SpermPipeline]:
  * ffmpeg запускается без чёрного окна, его вывод не теряется: при ошибке печатаются последние строки;
  * путь к ffmpeg: из настроек, иначе ваш путь ниже, иначе ffmpeg из PATH;
  * run() — вызов из приложения (print идёт в окно журнала, есть кнопка «Остановить» между видео);
  * кадры с калибровочной линейкой (MAX_LINES) уходят в _rejected, а не в отрезки.
"""
import os
import re
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image

FFMPEG = r"D:\Firefox download\ffmpeg-7.1.1-full_build\bin\ffmpeg.exe"

# Папка с видео: 01.mpg, 02.mpg, 13.mpg, ...
VIDEO_DIR = r"D:\Lab\CHECK AUTOMATION\VIDEOS"

# Куда класть кадры: для 01.mpg создастся папка "1 video", для 13.mpg — "13 video"
OUTPUT_DIR = r"D:\Lab\CHECK AUTOMATION\SEQUENCES IN ORDER"

VIDEO_EXTENSIONS = (".mpg", ".mpeg", ".mp4", ".avi", ".mov")

# True — после нарезки сразу разложить кадры по отрезкам
RUN_SPLIT = True

# True  — только показать, какие папки получатся, ничего не перемещать
# False — разложить кадры по папкам "начало-конец"
DRY_RUN = False

# --- Пороги (подобраны по 13 видео) ---------------------------------------
MIN_BRIGHTNESS = 60     # средняя яркость нормального кадра ~130-180, черного ~15-25
MAX_BRIGHTNESS = 240    # белые вспышки при переключении ~250
MAX_STD = 35            # нормальный кадр ~12-17, "снег"/шум ~80
MAX_HF = 6              # резкие перепады между соседними пикселями: норма ~2, шум ~20
MAX_LINES = 15          # вертикальные линии через весь кадр: калибровочная линейка ~29-37, образец <= 7
MIN_SEGMENT_LEN = 100   # отрезки короче (в кадрах) считаются мусором переключения
TRIM = 3                # сколько кадров отрезать с каждого края отрезка (затухание)
REJECT_FOLDER = "_rejected"  # сюда уходят черные/шумные кадры (не удаляются)

# -------------------------------------------------

FRAME_RE = re.compile(r"^frame_(\d+)\.png$", re.IGNORECASE)


def natural_key(text):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", text)]


def folder_name(video_file):
    stem = os.path.splitext(video_file)[0]
    # "01" -> "1 video"; если в имени нет числа — берем имя как есть
    return f"{int(stem)} video" if stem.isdigit() else f"{stem} video"


def extract(video_path, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-stats",
           "-i", video_path, os.path.join(out_dir, "frame_%05d.png")]
    # [SpermPipeline] без окна консоли; вывод ffmpeg — в журнал при ошибке
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        tail = r.stdout.decode("utf-8", "replace").replace("\r", "\n").strip().splitlines()[-5:]
        print("  ffmpeg: " + " | ".join(tail))
    return r.returncode == 0


def frame_stats(path):
    # обрезаем верх с датой/временем и края с черной рамкой
    a = np.asarray(Image.open(path).convert("L"), dtype=np.float32)[60:-20, 20:-20]
    hf = np.abs(np.diff(a, axis=1)).mean()
    # [SpermPipeline] средняя яркость каждого столбца: у линейки линии через весь кадр дают резкие пики
    col = a.mean(axis=0)
    lines = np.abs(col - np.convolve(col, np.ones(9) / 9, mode="same"))[9:-9].max()
    return a.mean(), a.std(), hf, lines


def is_good(mean, std, hf, lines):
    return (MIN_BRIGHTNESS < mean < MAX_BRIGHTNESS and std < MAX_STD and hf < MAX_HF
            and lines < MAX_LINES)


def find_segments(numbers, good):
    """numbers — номера кадров по порядку, good — флаг для каждого.
    Возвращает список (первый, последний) номеров хороших отрезков."""
    segments = []
    start = None
    for i, ok in enumerate(good + [False]):
        # пропуск в нумерации тоже разрывает отрезок
        gap = i > 0 and i < len(numbers) and numbers[i] != numbers[i - 1] + 1
        if start is not None and (not ok or gap):
            s, e = start + TRIM, i - 1 - TRIM
            if e - s + 1 >= MIN_SEGMENT_LEN:
                segments.append((numbers[s], numbers[e]))
            start = None
        if ok and start is None:
            start = i
    return segments


def list_frames(folder):
    files = {}
    for name in os.listdir(folder):
        m = FRAME_RE.match(name)
        if m:
            files[int(m.group(1))] = name
    return files


def split_folder(frames_dir, dry_run):
    files = list_frames(frames_dir)
    numbers = sorted(files)

    print(f"Кадров: {len(numbers)} ({numbers[0]}-{numbers[-1]}). Анализирую...")
    good = []
    for k, n in enumerate(numbers):
        good.append(is_good(*frame_stats(os.path.join(frames_dir, files[n]))))
        if (k + 1) % 500 == 0:
            print(f"  {k + 1}/{len(numbers)}")

    segments = find_segments(numbers, good)
    print(f"\nНайдено отрезков: {len(segments)}")
    for s, e in segments:
        print(f"  {s}-{e}  ({e - s + 1} кадров)")

    if dry_run:
        print("DRY_RUN = True — ничего не перемещено. Поставьте False, чтобы разложить.")
        return

    kept = set()
    for s, e in segments:
        folder = os.path.join(frames_dir, f"{s}-{e}")
        os.makedirs(folder, exist_ok=True)
        for n in range(s, e + 1):
            shutil.move(os.path.join(frames_dir, files[n]), os.path.join(folder, files[n]))
            kept.add(n)

    reject = os.path.join(frames_dir, REJECT_FOLDER)
    os.makedirs(reject, exist_ok=True)
    rest = [n for n in numbers if n not in kept]
    for n in rest:
        shutil.move(os.path.join(frames_dir, files[n]), os.path.join(reject, files[n]))

    print(f"{len(segments)} папок, {len(rest)} кадров в {REJECT_FOLDER}.")


def main(video_dir, output_dir):
    videos = [f for f in os.listdir(video_dir)
              if os.path.isfile(os.path.join(video_dir, f)) and f.lower().endswith(VIDEO_EXTENSIONS)]
    videos.sort(key=natural_key)
    if not videos:
        print(f"Нет видео в: {video_dir}")
        return

    print(f"Видео: {len(videos)}")
    done, skipped, failed = [], [], []
    for k, video in enumerate(videos, 1):
        if _STOP():                                    # [SpermPipeline] кнопка «Остановить»
            raise KeyboardInterrupt
        out_dir = _DEST(video) if _DEST else os.path.join(output_dir, folder_name(video))   # [SpermPipeline]
        # уже нарезанные видео не трогаем, чтобы не затереть разложенные кадры
        if os.path.isdir(out_dir) and os.listdir(out_dir):
            print(f"[{k}/{len(videos)}] {video} — папка {out_dir} уже есть, не нарезаю")
            skipped.append(video)
            continue
        print(f"[{k}/{len(videos)}] {video} -> {out_dir}")
        if extract(os.path.join(video_dir, video), out_dir):
            done.append(video)
        else:
            print(f"  ❌ ffmpeg завершился с ошибкой: {video}")
            failed.append(video)

    print(f"\nНарезано: {len(done)}, уже были: {len(skipped)}, ошибок: {len(failed)}")

    if RUN_SPLIT:
        # раскладываем и новые, и ранее нарезанные, но еще не разложенные видео
        for video in done + skipped:
            if _STOP():                                # [SpermPipeline]
                raise KeyboardInterrupt
            out_dir = _DEST(video) if _DEST else os.path.join(output_dir, folder_name(video))   # [SpermPipeline]
            if list_frames(out_dir):
                print(f"\n=== {os.path.splitext(video)[0] if _DEST else folder_name(video)} ===")
                split_folder(out_dir, DRY_RUN)

    print("\n✅ Готово!")


# =============================================================================
# [SpermPipeline] вызов из приложения
# =============================================================================
_STOP = lambda: False   # noqa: E731
_DEST = None            # видео → папка его кадров (приложение кладёт кадры в папку видео с именем как у .mpg)
FFMPEG_DEFAULT = FFMPEG


def find_ffmpeg(path=""):
    import shutil as _sh
    for c in (path, FFMPEG_DEFAULT, _sh.which("ffmpeg") or ""):
        if c and os.path.isfile(c):
            return c
    return ""


class _LineLog:
    """print() → окно журнала построчно (нарезка идёт долго, ждать конца не надо)."""
    def __init__(self, log):
        self.log, self.buf = log, ""

    def write(self, s):
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self.log(line)
        return len(s)

    def flush(self):
        if self.buf:
            self.log(self.buf)
            self.buf = ""


def list_videos(video_dir):
    """Видео в папке в том же порядке, что и main()."""
    return sorted((f for f in os.listdir(video_dir)
                   if os.path.isfile(os.path.join(video_dir, f)) and f.lower().endswith(VIDEO_EXTENSIONS)),
                  key=natural_key)


def run(ffmpeg_exe, video_dir, output_dir, log=print, stop=lambda: False, dest=None):
    """dest(имя файла видео) → папка его кадров; без dest — как в исходном скрипте: output_dir\\«1 video»."""
    global FFMPEG, _STOP, _DEST
    import contextlib
    exe = find_ffmpeg(ffmpeg_exe)
    if not exe:
        raise FileNotFoundError("Не найден ffmpeg.exe: укажите путь на вкладке «Запуск»")
    if not os.path.isdir(video_dir):
        raise FileNotFoundError(f"Нет папки с видео: {video_dir}")
    FFMPEG, _STOP, _DEST = exe, stop, dest
    os.makedirs(output_dir, exist_ok=True)
    log(f"ffmpeg: {exe}")
    real = sys.stdout

    def to_log(m):                    # log может сам печатать в stdout — пишем туда, а не в перехватчик
        with contextlib.redirect_stdout(real):
            log(m)
    w = _LineLog(to_log)
    with contextlib.redirect_stdout(w):
        main(video_dir, output_dir)
    w.flush()
    return output_dir


if __name__ == "__main__":
    # можно запускать и из консоли: python "GET SEQUENCES IN ORDER FROM VIDEO.py" <папка с видео> <папка для кадров>
    if len(sys.argv) > 2:
        main(sys.argv[1], sys.argv[2])
    else:
        main(VIDEO_DIR, OUTPUT_DIR)
