@echo off
title FabAgent
set "FA=%USERPROFILE%\Desktop\image-to-lightburn-svg\FabAgent"
cd /d "%FA%" 2>nul
if errorlevel 1 (
  echo GRESKA: ne nalazim folder
  echo   %FA%
  pause
  exit /b 1
)

set LOG=pokretanje.log
echo FabAgent pokretanje %DATE% %TIME% > "%LOG%"

if not exist ".venv\Scripts\python.exe" (
  echo GRESKA: .venv ne postoji u %FA%
  pause
  exit /b 1
)

echo   Provjeravam pakete...
".venv\Scripts\python.exe" -c "import fabagent, fastapi, uvicorn" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo GRESKA: nedostaju paketi. Detalji su u pokretanje.log
  type "%LOG%"
  pause
  exit /b 1
)

REM --------------------------------------------------------------------------
REM Stari server odgovara na /health isto kao novi, pa bi se otvorio browser
REM na procesu koji servira STARI kod - nova stranica se tada cini da ne
REM postoji. Zato se postojeci server prvo zaustavi.
REM --------------------------------------------------------------------------
echo   Trazim stari server na portu 8000...
set "STARI="
for /f "tokens=5" %%P in ('netstat -ano -p TCP ^| findstr /r /c:"LISTENING" ^| findstr ":8000 "') do set "STARI=%%P"

if defined STARI (
  echo   Zaustavljam stari server ^(PID %STARI%^)...
  echo Zaustavljen stari server PID %STARI% >> "%LOG%"
  taskkill /PID %STARI% /T /F >> "%LOG%" 2>&1
  ping -n 3 127.0.0.1 >nul
) else (
  echo   Nema starog servera.
)

echo   Dizem server...
start "FabAgent server" /min "%FA%\_server.bat"

set /a N=0
:CEKAJ
set /a N+=1
".venv\Scripts\python.exe" -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2)" >nul 2>&1
if not errorlevel 1 goto ZIVO
if %N% GEQ 30 goto MRTVO
ping -n 2 127.0.0.1 >nul
goto CEKAJ

:MRTVO
echo GRESKA: server se nije podigao za 30 sekundi. Pogledaj server.log
pause
exit /b 1

:ZIVO
REM Ispisi koji je kod ucitan. Ako ovo vrijeme nije skoro, server je star.
for /f "delims=" %%K in ('".venv\Scripts\python.exe" -c "import json,urllib.request;print(json.load(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3)).get('kod',''))"') do set "KOD=%%K"

set "CH=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
if not exist "%CH%" set "CH=C:\Program Files\Google\Chrome\Application\chrome.exe"
if exist "%CH%" (
  start "" "%CH%" "http://127.0.0.1:8000/razgovor"
) else (
  start "" "http://127.0.0.1:8000/razgovor"
)

echo.
echo   FabAgent radi:  http://127.0.0.1:8000/
echo   Ucitan kod od:  %KOD%
echo.
echo   Razgovor sa asistentom:  /razgovor
echo   Upravljanje laserom:     /machine
echo.
echo   Ako u navigaciji stoji drugo vrijeme od gornjeg, server je star -
echo   zatvori ovaj prozor i pokreni ponovo.
echo.
timeout /t 8 >nul
