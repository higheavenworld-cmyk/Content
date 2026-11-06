@echo off
setlocal
chcp 65001 >nul
title CarPlug Studio - Installation
color 0C
cd /d "%~dp0"

echo.
echo  ==============================================
echo     CarPlug Studio wird eingerichtet
echo     Beim ersten Mal dauert das 5-10 Minuten.
echo  ==============================================
echo.

rem ---------- 1. Python 3.12 finden oder installieren ----------
echo [1/4] Suche Python...
set "PYEXE="
py -3.12 --version >nul 2>&1
if not errorlevel 1 for /f "delims=" %%i in ('py -3.12 -c "import sys;print(sys.executable)"') do set "PYEXE=%%i"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYEXE (
    echo       Python 3.12 nicht gefunden - wird jetzt installiert...
    winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
if not defined PYEXE goto :nopython
echo       OK: %PYEXE%

rem ---------- 2. Eigene Umgebung anlegen ----------
echo [2/4] Lege Programm-Umgebung an...
if not exist ".venv\Scripts\python.exe" "%PYEXE%" -m venv .venv
if errorlevel 1 goto :fail

rem ---------- 3. Pakete installieren ----------
echo [3/4] Installiere Pakete (das dauert etwas)...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :fail

rem ---------- 3b. Eigene Stimme: Klon-Modul automatisch mitinstallieren ----------
if exist "stimmen\*.wav" if not exist ".venv-klon\Scripts\python.exe" (
    echo [3b] Deine Stimme wurde gefunden - Stimmen-Klon wird installiert ^(einmalig, 3-4 GB^)...
    call "%~dp0Stimme-klonen-installieren.bat"
)

rem ---------- 4. Desktop-Verknuepfung ----------
echo [4/4] Erstelle Desktop-Verknuepfung...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\CarPlug Studio.lnk'); $s.TargetPath='%~dp0CarPlug-Studio-Starten.bat'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%~dp0assets\carplug.ico'; $s.Save()"

echo.
echo  Fertig! Ab jetzt startest du CarPlug Studio ueber das Symbol auf dem Desktop.
echo  Es startet jetzt automatisch...
echo.
call "%~dp0CarPlug-Studio-Starten.bat"
exit /b 0

:nopython
echo.
echo  Python konnte nicht automatisch installiert werden.
echo  Bitte installiere Python 3.12 von https://www.python.org/downloads/
echo  (Haken bei "Add python.exe to PATH" setzen) und starte diese Datei erneut.
pause
exit /b 1

:fail
echo.
echo  Bei der Installation ist ein Fehler aufgetreten (siehe oben).
echo  Pruefe deine Internetverbindung und starte diese Datei erneut.
pause
exit /b 1
