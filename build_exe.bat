@echo off
rem Builds dist\SpermPipeline\SpermPipeline.exe (a folder that runs without Python). Takes several minutes.
cd /d "%~dp0"
set "PY=C:\Users\DzhalilS\PycharmProjects\pythonProject1\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -m pip install -r requirements.txt pyinstaller
"%PY%" -m PyInstaller --noconfirm --clean --windowed --name SpermPipeline --collect-all hdbscan --collect-submodules sklearn --hidden-import sklearn.utils._typedefs SpermPipeline.py
dist\SpermPipeline\SpermPipeline.exe --selftest
type dist\SpermPipeline\selftest_log.txt
pause
