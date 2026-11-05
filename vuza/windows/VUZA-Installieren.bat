@echo off
setlocal
chcp 65001 >nul
title VUZA Installation
color 0B

set "VUZA_DIR=%LOCALAPPDATA%\VUZA"
set "ZIP_URL=https://github.com/AliRash3ed/VUZA-Free-AI-Video-Creator-and-Pinterest-Video-Scraper/archive/refs/heads/main.zip"
set "TMP_ZIP=%TEMP%\vuza_download.zip"
set "TMP_DIR=%TEMP%\vuza_extract"

echo.
echo  ==============================================
echo     VUZA wird eingerichtet - bitte warten
echo     Das dauert beim ersten Mal 5-15 Minuten.
echo  ==============================================
echo.

rem ---------- 1. Python finden oder installieren ----------
echo [1/6] Suche Python...
set "PYEXE="
py -3 --version >nul 2>&1
if not errorlevel 1 for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)"') do set "PYEXE=%%i"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"

if not defined PYEXE (
    echo       Python nicht gefunden - wird jetzt installiert...
    winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
if not defined PYEXE goto :nopython
echo       OK: %PYEXE%

rem ---------- 2. VUZA herunterladen ----------
echo [2/6] Lade VUZA herunter...
if exist "%TMP_DIR%" rmdir /s /q "%TMP_DIR%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -UseBasicParsing -Uri $env:ZIP_URL -OutFile $env:TMP_ZIP; Expand-Archive -Force -Path $env:TMP_ZIP -DestinationPath $env:TMP_DIR"
if errorlevel 1 goto :fail
if not exist "%VUZA_DIR%" mkdir "%VUZA_DIR%"
for /d %%d in ("%TMP_DIR%\*") do robocopy "%%d" "%VUZA_DIR%\app" /E /NFL /NDL /NJH /NJS /NP >nul
if not exist "%VUZA_DIR%\app\app.py" goto :fail
rmdir /s /q "%TMP_DIR%" 2>nul
del "%TMP_ZIP%" 2>nul

rem ---------- 3. Python-Umgebung ----------
echo [3/6] Erstelle Python-Umgebung...
if not exist "%VUZA_DIR%\venv\Scripts\python.exe" "%PYEXE%" -m venv "%VUZA_DIR%\venv"
if errorlevel 1 goto :fail
set "VPY=%VUZA_DIR%\venv\Scripts\python.exe"

rem ---------- 4. Pakete ----------
echo [4/6] Installiere Pakete (das dauert am laengsten)...
"%VPY%" -m pip install --upgrade pip --quiet
"%VPY%" -m pip install -r "%VUZA_DIR%\app\requirements.txt" --quiet
if errorlevel 1 goto :fail

rem ---------- 5. Browser fuer Pinterest ----------
echo [5/6] Installiere Browser fuer Pinterest-Scraping...
"%VPY%" -m playwright install chromium
if errorlevel 1 goto :fail

rem ---------- 6. Start-Datei und Desktop-Verknuepfung ----------
echo [6/6] Erstelle Desktop-Verknuepfung "VUZA"...
set "STARTER=%VUZA_DIR%\VUZA-Starten.bat"
> "%STARTER%" echo @echo off
>> "%STARTER%" echo title VUZA - dieses Fenster offen lassen, solange du VUZA nutzt
>> "%STARTER%" echo cd /d "%%~dp0app"
>> "%STARTER%" echo echo VUZA startet... der Browser oeffnet sich gleich.
>> "%STARTER%" echo echo Zum Beenden dieses Fenster schliessen.
>> "%STARTER%" echo start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep 6; Start-Process 'http://localhost:8000'"
>> "%STARTER%" echo "%%~dp0venv\Scripts\python.exe" -c "import uvicorn, app; uvicorn.run(app.app, host='127.0.0.1', port=8000)"
>> "%STARTER%" echo pause

powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\VUZA.lnk'); $s.TargetPath=$env:STARTER; $s.WorkingDirectory=$env:VUZA_DIR; $s.Description='VUZA starten'; $s.Save()"

echo.
echo  ==============================================
echo     Fertig! VUZA startet jetzt.
echo     Spaeter einfach "VUZA" auf dem Desktop
echo     doppelklicken.
echo  ==============================================
echo.
start "" "%STARTER%"
timeout /t 5 >nul
exit /b 0

:nopython
echo.
echo  FEHLER: Python konnte nicht automatisch installiert werden.
echo  Bitte installiere Python von https://www.python.org/downloads/
echo  (Haken bei "Add python.exe to PATH" setzen) und starte
echo  diese Datei danach noch einmal.
echo.
pause
exit /b 1

:fail
echo.
echo  FEHLER bei der Installation. Mach bitte einen Screenshot
echo  von diesem Fenster und schick ihn an Claude.
echo.
pause
exit /b 1
