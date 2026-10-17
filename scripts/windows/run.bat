@echo off
REM MeManga launcher for Windows

REM Do not cd: relative paths in user arguments must resolve against the caller's directory.
set "MEMANGA_PY=%~dp0..\..\venv\Scripts\python.exe"

if exist "%MEMANGA_PY%" (
    set "PYTHONPATH=%~dp0..\..;%PYTHONPATH%"
    "%MEMANGA_PY%" -m memanga %*
) else (
    echo Error: Virtual environment not found.
    echo Please run: python setup.py
    echo         or: scripts\windows\setup.bat
    exit /b 1
)
