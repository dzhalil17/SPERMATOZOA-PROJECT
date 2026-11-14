"""
Запуск:
  python -m sperm_pipeline                — окно приложения
  python -m sperm_pipeline config.json    — режим для Fiji-плагина (шаг 5 вызывает «python.exe скрипт config.json»)
  python -m sperm_pipeline --selftest     — самопроверка установки на синтетическом видео
"""
from __future__ import annotations

import json
import multiprocessing
import sys
from pathlib import Path


def fiji_mode(config_path: str) -> int:
    from . import settings as S, pipeline
    js = json.loads(Path(config_path).read_text(encoding='utf-8'))
    if 'file_list' in js and 'spots_dir' not in js:
        print('Шаг 6 больше не нужен: SpermPipeline делает кластеризацию на шаге 5 (папка «5 КЛАСТЕРИЗАЦИЯ»).')
        return 0
    cfg = S.load()                                  # параметры — те, что сохранены в окне приложения
    g = cfg['general']
    g.update(spots_dir=js['spots_dir'], tracks_dir=js.get('tracks_dir') or '', output_dir=js['output_dir'],
             step_tables=False, step_filter=True, step_cluster=True)
    pipeline.run(cfg, log=lambda s: print(s, flush=True))
    return 0


def _utf8_console():
    # консоль Windows бывает cp1251/cp866/cp1252 — без этого русский текст и «✓» роняют вывод
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass


def main():
    multiprocessing.freeze_support()                # нужно для .exe (PyInstaller) на Windows
    _utf8_console()
    if '--selftest' in sys.argv:
        from . import selftest
        out = []
        ok = selftest.run(log=lambda m: (out.append(str(m)), print(m, flush=True) if sys.stdout else None))
        Path(sys.argv[0]).resolve().with_name('selftest_log.txt').write_text('\n'.join(out), encoding='utf-8')
        sys.exit(0 if ok else 1)
    args = [a for a in sys.argv[1:] if a.lower().endswith('.json')]
    if args:
        sys.exit(fiji_mode(args[0]))
    from .app import main as gui
    gui()


if __name__ == '__main__':
    main()
