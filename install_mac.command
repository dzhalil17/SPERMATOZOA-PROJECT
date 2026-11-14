#!/bin/bash
# SpermPipeline на Mac: один раз ставит библиотеки в папку .venv рядом и делает самопроверку.
# Нужен Python 3.12 с python.org (в нём уже есть окно tkinter). Запуск: двойной клик (или «Открыть» в меню правой кнопки).
cd "$(dirname "$0")"
PY=python3.12
command -v $PY >/dev/null 2>&1 || PY=python3
if ! $PY -c "import tkinter" 2>/dev/null; then
  echo "В этом Python нет tkinter. Поставьте Python 3.12 с https://www.python.org/downloads/macos/ и запустите снова."
  read -p "Enter — закрыть"; exit 1
fi
$PY -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python SpermPipeline.py --selftest
read -p "Готово. Enter — закрыть"
