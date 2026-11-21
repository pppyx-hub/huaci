@echo off
chcp 65001 >nul
title Enable local AI (lightweight)
cd /d "%~dp0"

echo.
echo   ============================================================
echo     Huaci  -  optional local AI reading
echo   ============================================================
echo     Model   : qwen2.5:1.5b   (about 1 GB download)
echo     Video RAM: about 1.2 GB while answering (idle: 0)
echo     Idle    : unloaded automatically after 5 minutes
echo     Output  : capped at 220 tokens per answer
echo   ============================================================
echo.
echo   This is OPTIONAL. Huaci works fine without it.
echo.

where ollama >nul 2>nul
if errorlevel 1 (
  echo   [!] Ollama not found.
  echo.
  echo       Opening the download page. Install it first,
  echo       then run this script again.
  echo.
  start https://ollama.com/download/windows
  pause
  exit /b 1
)

echo   [1/2] Ollama found.
echo   [2/2] Pulling qwen2.5:1.5b...
echo.

ollama pull qwen2.5:1.5b
if errorlevel 1 (
  echo.
  echo   [!] Pull failed. Make sure the Ollama app is running
  echo       ^(look for its icon in the system tray^), then retry.
  pause
  exit /b 1
)

echo.
echo   Done. Restart Huaci - a small "AI" button will appear
echo   in the translation popup.
echo.
echo   Need something even lighter? Run:
echo       ollama pull qwen2.5:0.5b
echo   (400 MB, but explanations get noticeably weaker)
echo.
pause
