@echo off
setlocal
chcp 65001 >nul
title CarPlug Studio - Stimmen-Klon installieren
color 0C
cd /d "%~dp0"

echo.
echo  ==============================================
echo     Stimmen-Klon (Chatterbox, lokal) installieren
echo     Download ca. 3-4 GB - dauert 10-30 Minuten.
echo  ==============================================
echo.
if not exist ".venv\Scripts\python.exe" (
    echo Bitte zuerst CarPlug-Studio-Installieren.bat ausfuehren.
    pause
    exit /b 1
)
set "PYEXE="
py -3.12 --version >nul 2>&1
if not errorlevel 1 for /f "delims=" %%i in ('py -3.12 -c "import sys;print(sys.executable)"') do set "PYEXE=%%i"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYEXE (
    echo Python 3.12 nicht gefunden. Bitte zuerst CarPlug-Studio-Installieren.bat ausfuehren.
    pause
    exit /b 1
)

echo [1/3] Lege eigene Umgebung fuer den Stimmen-Klon an...
if not exist ".venv-klon\Scripts\python.exe" "%PYEXE%" -m venv .venv-klon
if errorlevel 1 goto :fail

echo [2/3] Installiere Chatterbox (gross, bitte warten)...
".venv-klon\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv-klon\Scripts\python.exe" -m pip install chatterbox-tts
if errorlevel 1 goto :fail

echo [3/3] Installiere Wort-Erkennung fuer exakte Untertitel...
".venv\Scripts\python.exe" -m pip install faster-whisper
if errorlevel 1 goto :fail

echo.
echo  Fertig! Starte CarPlug Studio neu und nimm im Tab "Stimme klonen" deine Stimme auf.
echo  Hinweis: Beim ersten Klon-Video wird das Sprachmodell einmalig heruntergeladen.
pause
exit /b 0

:fail
echo.
echo  Fehler bei der Installation (siehe oben). Internet pruefen und erneut starten.
pause
exit /b 1
