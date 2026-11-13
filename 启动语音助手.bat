@echo off
title Voice Assistant Launcher
cd /d "%~dp0"
set PY=
C:\Users\YBB\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0\python.exe
if not exist "%PY%" set PY=python
start "voice-watcher" /min "%PY%" watcher.py
exit /b
