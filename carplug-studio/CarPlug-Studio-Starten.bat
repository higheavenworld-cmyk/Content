@echo off
chcp 65001 >nul
title CarPlug Studio
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo CarPlug Studio ist noch nicht installiert. Bitte zuerst CarPlug-Studio-Installieren.bat ausfuehren.
    pause
    exit /b 1
)
echo.
echo  CarPlug Studio laeuft. Der Browser oeffnet sich gleich automatisch.
echo  Falls nicht: http://127.0.0.1:7860 im Browser oeffnen.
echo  Dieses Fenster offen lassen - Schliessen beendet das Programm.
echo  (Video-Downloader wird kurz aktualisiert...)
".venv\Scripts\python.exe" -m pip install -q -U yt-dlp yt-dlp-ejs >nul 2>&1
echo.
".venv\Scripts\python.exe" run.py
pause
