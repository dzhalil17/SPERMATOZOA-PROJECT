"""Окно приложения SpermPipeline (tkinter — входит в Python, ничего ставить не надо)."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from . import settings as S
from . import pipeline

TITLE = 'SpermPipeline — видео → TrackMate → таблицы → супер-фильтрация → CASA → HDBSCAN'


def _to_text(v) -> str:
    return 'None' if v is None else str(v)


def _parse(text: str, default):
    t = text.strip()
    if t == '' or t == 'None':
        return None
    for conv in (int, float):
        try:
            return conv(t)
        except ValueError:
            pass
    if isinstance(default, (int, float)) and not isinstance(default, bool):
        raise ValueError(f'«{text}» — не число')
    return t


class ScrollForm(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        canvas = tk.Canvas(self, highlightthickness=0)
        sb = ttk.Scrollbar(self, orient='vertical', command=canvas.yview)
        self.inner = ttk.Frame(canvas)
        self.inner.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.create_window((0, 0), window=self.inner, anchor='nw')
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        canvas.bind_all('<MouseWheel>', lambda e: canvas.yview_scroll(int(-e.delta / 120), 'units'))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(TITLE)
        self.geometry('1000x760')
        self.cfg = S.load()
        self.vars = {}          # (section, name) -> (tk var, default)
        self.q = queue.Queue()
        self.stop_flag = threading.Event()
        self.worker = None

        nb = ttk.Notebook(self)
        nb.pack(fill='both', expand=True, padx=6, pady=6)
        self._run_tab(nb)
        for sec, title, spec in [('frames', 'Нарезка', S.FRAMES), ('trackmate', 'TrackMate', S.TRACKMATE),
                                 ('filtration', 'Супер-фильтрация', S.FILTRATION),
                                 ('casa', 'CASA-метрики', S.CASA),
                                 ('clustering', 'Кластеризация', S.CLUSTERING)]:
            self._param_tab(nb, sec, title, spec)
        self.protocol('WM_DELETE_WINDOW', self._close)
        self.after(150, self._drain)

    # ------------------------------------------------------------------ вкладка «Запуск»
    def _run_tab(self, nb):
        f = ttk.Frame(nb, padding=8)
        nb.add(f, text='Запуск')
        g = self.cfg['general']
        self.gv = {k: (tk.BooleanVar(value=v) if isinstance(v, bool) else tk.StringVar(value=str(v)))
                   for k, v in g.items()}
        r = 0

        def folder_row(key, label, hint=''):
            nonlocal r
            ttk.Label(f, text=label).grid(row=r, column=0, sticky='w', padx=(24, 4))
            e = ttk.Entry(f, textvariable=self.gv[key], width=80)
            e.grid(row=r, column=1, sticky='we')
            ttk.Button(f, text='…', width=3, command=lambda: self._pick(key)).grid(row=r, column=2, padx=2)
            r += 1
            if hint:
                ttk.Label(f, text=hint, foreground='#666').grid(row=r, column=1, sticky='w')
                r += 1
            return e

        def step_row(key, label):
            nonlocal r
            ttk.Checkbutton(f, text=label, variable=self.gv[key], command=self._sync).grid(
                row=r, column=0, columnspan=3, sticky='w', pady=(8, 2))
            r += 1

        step_row('step_frames', '00. Нарезать видео (.mpg) на кадры через ffmpeg и разложить по отрезкам, брак — в _rejected')
        self.e_mpg = folder_row('mpg_dir', 'Видео (.mpg):', 'папка с 01.mpg, 02.mpg … → кадры «1 video», «2 video» …')
        ttk.Label(f, text='ffmpeg.exe:').grid(row=r, column=0, sticky='w', padx=(24, 4))
        self.e_ffmpeg = ttk.Entry(f, textvariable=self.gv['ffmpeg_exe'], width=80)
        self.e_ffmpeg.grid(row=r, column=1, sticky='we')
        ttk.Button(f, text='…', width=3, command=lambda: self._pick_exe('ffmpeg_exe', 'ffmpeg.exe')).grid(
            row=r, column=2, padx=2)
        r += 1
        self.e_frames = folder_row('frames_dir', 'Папка для кадров:',
                                   'пусто — «00 КАДРЫ» в папке результатов; уже нарезанные видео заново не режутся')
        step_row('step_trackmate', '0. TrackMate в Fiji без окна: 8-bit → контраст → Gaussian → Find Maxima → LoG + LAP')
        self.e_videos = folder_row('videos_dir', 'Видео / кадры:', 'отрезок = папка с кадрами (png/jpg/tif) или файл .tif/.avi; папка над ними = видео; «_rejected» пропускается')
        ttk.Label(f, text='Fiji (ImageJ-win64.exe):').grid(row=r, column=0, sticky='w', padx=(24, 4))
        self.e_fiji = ttk.Entry(f, textvariable=self.gv['fiji_exe'], width=80)
        self.e_fiji.grid(row=r, column=1, sticky='we')
        ttk.Button(f, text='…', width=3, command=self._pick_fiji).grid(row=r, column=2, padx=2)
        r += 1
        step_row('step_tables', '1. Оформить экспорты TrackMate (.csv) — вместо макросов Excel')
        self.e_raw = folder_row('raw_dir', 'Экспорты TrackMate:', 'папка с *_spots.csv и *_tracks.csv или папка с подпапками-видео')
        step_row('step_filter', '2–4. Супер-фильтрация → склейка по видео → CASA-метрики (VAP, STR, LIN …)')
        self.e_spots = folder_row('spots_dir', 'Споты (*spots*.xlsx):',
                                  'оформленные таблицы; если шаг 1 включён — берутся из него. Подпапки = видео')
        self.e_tracks = folder_row('tracks_dir', 'Треки (*tracks*.xlsx):',
                                   'не обязательно (фильтр таблицу треков читает, но не использует)')
        step_row('step_cluster', '5. Кластеризация HDBSCAN (IM / RP / SP / NP)')
        self.e_casa = folder_row('casa_dir', 'Таблицы CASA TRACKS:',
                                 'только если шаги 2–4 выключены, например ваша папка CASA TRACKS v2')
        ttk.Separator(f).grid(row=r, column=0, columnspan=3, sticky='we', pady=8)
        r += 1
        ttk.Label(f, text='Папка результатов:').grid(row=r, column=0, sticky='w')
        ttk.Entry(f, textvariable=self.gv['output_dir'], width=80).grid(row=r, column=1, sticky='we')
        ttk.Button(f, text='…', width=3, command=lambda: self._pick('output_dir')).grid(row=r, column=2, padx=2)
        r += 1
        ttk.Label(f, text='Параллельных процессов:').grid(row=r, column=0, sticky='w')
        ttk.Spinbox(f, from_=1, to=32, textvariable=self.gv['n_workers'], width=5).grid(row=r, column=1, sticky='w')
        r += 1

        bar = ttk.Frame(f)
        bar.grid(row=r, column=0, columnspan=3, sticky='w', pady=8)
        r += 1
        self.b_run = ttk.Button(bar, text='▶ Запустить', command=self._start)
        self.b_run.pack(side='left')
        self.b_stop = ttk.Button(bar, text='■ Остановить', command=self._stop, state='disabled')
        self.b_stop.pack(side='left', padx=6)
        ttk.Button(bar, text='Открыть папку результатов', command=self._open_out).pack(side='left', padx=6)
        ttk.Button(bar, text='Настройки по умолчанию', command=self._reset).pack(side='left', padx=6)

        logf = ttk.Frame(f)
        logf.grid(row=r, column=0, columnspan=3, sticky='nsew')
        f.rowconfigure(r, weight=1)
        f.columnconfigure(1, weight=1)
        self.log = tk.Text(logf, wrap='word', height=18, font=('Consolas', 9))
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        self._sync()

    def _sync(self):
        fr = self.gv['step_frames'].get()
        if fr:
            self.gv['step_trackmate'].set(True)       # кадры из шага 00 идут в шаг 0
        for e in (self.e_mpg, self.e_ffmpeg, self.e_frames):
            e.configure(state='normal' if fr else 'disabled')
        if fr and not self.gv['ffmpeg_exe'].get():
            from .frames import find_ffmpeg
            self.gv['ffmpeg_exe'].set(find_ffmpeg())
        tm = self.gv['step_trackmate'].get()
        if tm:
            self.gv['step_tables'].set(True)          # CSV из шага 0 оформляет шаг 1
        self.e_videos.configure(state='normal' if tm and not fr else 'disabled')
        self.e_fiji.configure(state='normal' if tm else 'disabled')
        if tm and not self.gv['fiji_exe'].get():
            from .trackmate import find_fiji
            self.gv['fiji_exe'].set(find_fiji())
        t, flt, cl = (self.gv['step_tables'].get(), self.gv['step_filter'].get(), self.gv['step_cluster'].get())
        self.e_raw.configure(state='normal' if t and not tm else 'disabled')
        self.e_spots.configure(state='normal' if flt and not t else 'disabled')
        self.e_tracks.configure(state='normal' if flt and not t else 'disabled')
        self.e_casa.configure(state='normal' if cl and not flt else 'disabled')

    def _pick_exe(self, key, title):
        f = filedialog.askopenfilename(title=title, filetypes=[('Программа', '*.exe'), ('Все', '*.*')])
        if f:
            self.gv[key].set(os.path.normpath(f))

    def _pick_fiji(self):
        f = filedialog.askopenfilename(title='Fiji: ImageJ-win64.exe', filetypes=[('Fiji', '*.exe'), ('Все', '*.*')])
        if f:
            self.gv['fiji_exe'].set(os.path.normpath(f))

    def _pick(self, key):
        d = filedialog.askdirectory(initialdir=self.gv[key].get() or None)
        if d:
            self.gv[key].set(os.path.normpath(d))

    # ------------------------------------------------------------------ вкладки параметров
    def _param_tab(self, nb, sec, title, spec):
        sf = ScrollForm(nb)
        nb.add(sf, text=title)
        f = sf.inner
        ttk.Label(f, text='По умолчанию — значения из ваших скриптов. Изменённые значения сохраняются между запусками.',
                  foreground='#666').grid(row=0, column=0, columnspan=3, sticky='w', padx=6, pady=(6, 8))
        for i, item in enumerate(spec, start=1):
            name, label = item[0], item[1]
            choices = item[2] if len(item) > 2 else None
            default = S.DEFAULTS[sec][name]
            cur = self.cfg[sec].get(name, default)
            ttk.Label(f, text=label).grid(row=i, column=0, sticky='w', padx=6, pady=1)
            if isinstance(default, bool):
                var = tk.BooleanVar(value=bool(cur))
                ttk.Checkbutton(f, variable=var).grid(row=i, column=1, sticky='w')
            elif choices:
                var = tk.StringVar(value=_to_text(cur))
                ttk.Combobox(f, textvariable=var, values=[_to_text(c) for c in choices], state='readonly',
                             width=18).grid(row=i, column=1, sticky='w')
            else:
                var = tk.StringVar(value=_to_text(cur))
                ttk.Entry(f, textvariable=var, width=20).grid(row=i, column=1, sticky='w')
            ttk.Label(f, text=f'{name}  (по умолч. {_to_text(default)})', foreground='#888').grid(
                row=i, column=2, sticky='w', padx=8)
            self.vars[(sec, name)] = (var, default)

    # ------------------------------------------------------------------ сбор настроек
    def _collect(self) -> dict:
        cfg = S.defaults()
        for (sec, name), (var, default) in self.vars.items():
            v = var.get()
            if isinstance(default, bool):
                cfg[sec][name] = bool(v)
            else:
                try:
                    cfg[sec][name] = _parse(str(v), default)
                except ValueError as e:
                    raise ValueError(f'{name}: {e}')
        for k, var in self.gv.items():
            v = var.get()
            cfg['general'][k] = int(v) if k == 'n_workers' else v
        return cfg

    def _reset(self):
        if not messagebox.askyesno('Настройки', 'Вернуть все параметры к значениям из скриптов?'):
            return
        for (sec, name), (var, default) in self.vars.items():
            var.set(default if isinstance(default, bool) else _to_text(default))

    # ------------------------------------------------------------------ запуск
    def _start(self):
        try:
            cfg = self._collect()
        except ValueError as e:
            messagebox.showerror('Настройки', str(e))
            return
        if not cfg['general']['output_dir']:
            messagebox.showerror('Запуск', 'Укажите папку результатов')
            return
        S.save(cfg)
        self.log.delete('1.0', 'end')
        self.stop_flag.clear()
        self.b_run.configure(state='disabled')
        self.b_stop.configure(state='normal')
        self.worker = threading.Thread(target=self._work, args=(cfg,), daemon=True)
        self.worker.start()

    def _work(self, cfg):
        try:
            pipeline.run(cfg, log=lambda s: self.q.put(s), stop=self.stop_flag.is_set)
            self.q.put(('done', 'Готово'))
        except pipeline.Stopped:
            self.q.put(('done', 'Остановлено'))
        except Exception as e:
            self.q.put(traceback.format_exc())
            self.q.put(('done', f'Ошибка: {e}'))

    def _stop(self):
        self.stop_flag.set()
        self.q.put('Остановка после текущего отрезка…')

    def _drain(self):
        try:
            while True:
                m = self.q.get_nowait()
                if isinstance(m, tuple):
                    self.b_run.configure(state='normal')
                    self.b_stop.configure(state='disabled')
                    self.log.insert('end', f'\n{m[1]}\n')
                else:
                    self.log.insert('end', str(m) + '\n')
                self.log.see('end')
        except queue.Empty:
            pass
        self.after(150, self._drain)

    def _open_out(self):
        d = self.gv['output_dir'].get()
        if d and os.path.isdir(d):
            if sys.platform == 'win32':
                os.startfile(d)
            else:
                subprocess.Popen(['xdg-open', d])

    def _close(self):
        try:
            S.save(self._collect())
        except ValueError as e:
            # раньше настройки молча не сохранялись, если в каком-то поле опечатка
            if not messagebox.askyesno('Настройки не сохранены',
                                       f'{e}\n\nЗакрыть без сохранения настроек?'):
                return
        except Exception:
            pass
        self.destroy()


def main():
    App().mainloop()
