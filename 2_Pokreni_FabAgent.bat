@echo off
setlocal
cd /d "%~dp0"
set LOG=pokretanje.log
echo FabAgent pokretanje %DATE% %TIME% > "%LOG%"

if not exist ".venv\Scripts\python.exe" (
  echo GRESKA: .venv ne postoji u ovom folderu >> "%LOG%"
  echo GRESKA: .venv ne postoji u ovom folderu
  pause
  exit /b 1
)

echo Provjera paketa... >> "%LOG%"
".venv\Scripts\python.exe" -c "import fabagent, fastapi, uvicorn" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo GRESKA: nedostaju paketi. Detalji su u pokretanje.log
  type "%LOG%"
  pause
  exit /b 1
)

echo Pokrecem server... >> "%LOG%"
start "FabAgent server" /min "%~dp0_server.bat"

set /a N=0
:CEKAJ
set /a N+=1
".venv\Scripts\python.exe" -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2)" >nul 2>&1
if not errorlevel 1 goto ZIVO
if %N% GEQ 30 goto MRTVO
ping -n 2 127.0.0.1 >nul
goto CEKAJ

:MRTVO
echo GRESKA: server se nije podigao za 30 sekundi. >> "%LOG%"
echo GRESKA: server se nije podigao. Pogledaj server.log
pause
exit /b 1

:ZIVO
echo Server odgovara na /health >> "%LOG%"

rem Upravljanje laserom ide preko Web Serial-a, koji radi samo u Chrome ili Edge.
set "CH=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
if not exist "%CH%" set "CH=C:\Program Files\Google\Chrome\Application\chrome.exe"
if exist "%CH%" (
  start "" "%CH%" "http://127.0.0.1:8000/"
  echo Otvoreno u Chrome-u >> "%LOG%"
) else (
  start "" "http://127.0.0.1:8000/"
  echo Chrome nije nadjen, otvoreno u podrazumijevanom browseru >> "%LOG%"
)

echo.
echo   FabAgent radi:  http://127.0.0.1:8000/
echo   Server pise u server.log
echo   Ovaj prozor se sam zatvara, server ostaje.
echo.
timeout /t 8 >nul
