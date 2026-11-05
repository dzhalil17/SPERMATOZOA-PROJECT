@echo off
rem SpermPipeline: opens the app window.
rem Uses the project .venv if it exists (all packages are already there), otherwise Python from PATH.
cd /d "%~dp0"
set "PY=C:\Users\DzhalilS\PycharmProjects\pythonProject1\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" SpermPipeline.py %*
if errorlevel 1 pause
