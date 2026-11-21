@echo off
chcp 65001 >nul
title Huaci Translator

rem ---- Locate program folder: prefer this bat's folder, else fall back ----
set "APPDIR=%~dp0"
if not exist "%APPDIR%huaci.py" set "APPDIR=C:\Users\Administrator\WorkBuddy\2026-11-03-19-36-01\huaci\"

if not exist "%APPDIR%huaci.py" (
  echo.
  echo   [ERROR] huaci.py not found.
  echo.
  echo   Looked in:
  echo     %~dp0
  echo     C:\Users\Administrator\WorkBuddy\2026-11-03-19-36-01\huaci\
  echo.
  echo   Put this .bat in the same folder as huaci.py.
  echo.
  pause
  exit /b 1
)

cd /d "%APPDIR%"

echo.
echo   Starting Huaci Translator (word-select translator)...
echo.

set "PYEXE="
call :try "C:\Users\Administrator\anaconda3\python.exe"
call :try "C:\Users\Administrator\PycharmProjects\PythonProject\.venv\Scripts\python.exe"
call :try "C:\Users\Administrator\anaconda3\envs\pythonproject1\python.exe"
call :try "C:\Users\Administrator\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
if not defined PYEXE (
  where python >nul 2>nul
  if not errorlevel 1 call :try "python"
)
if not defined PYEXE (
  where py >nul 2>nul
  if not errorlevel 1 call :try "py"
)

if not defined PYEXE goto :nopython

echo   Using: %PYEXE%
echo.
"%PYEXE%" -u "%APPDIR%huaci.py"

echo.
echo   Stopped.
pause
exit /b 0

:try
if defined PYEXE goto :eof
"%~1" -c "import tkinter" >nul 2>nul
if errorlevel 1 goto :eof
set "PYEXE=%~1"
goto :eof

:nopython
echo   [ERROR] No Python with tkinter found.
echo.
echo   Install Python 3.8+ from https://www.python.org/downloads/
echo   During setup, tick "tcl/tk and IDLE".
echo.
pause
exit /b 1
