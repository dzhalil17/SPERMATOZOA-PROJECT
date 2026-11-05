@echo off
rem Installs the packages SpermPipeline needs into the same Python that run_app.bat uses.
cd /d "%~dp0"
set "PY=C:\Users\DzhalilS\PycharmProjects\pythonProject1\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -m pip install -r requirements.txt
"%PY%" SpermPipeline.py --selftest
pause
