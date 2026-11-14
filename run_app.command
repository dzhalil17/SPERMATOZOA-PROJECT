#!/bin/bash
# SpermPipeline на Mac: открывает окно приложения (сначала один раз install_mac.command).
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi
$PY SpermPipeline.py "$@" || read -p "Ошибка — Enter, чтобы закрыть"
