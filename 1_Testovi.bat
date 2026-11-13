@echo off
cd /d "%~dp0"
set LOG=testovi.log
echo FabAgent testovi %DATE% %TIME% > "%LOG%"
".venv\Scripts\python.exe" -m pytest -q >> "%LOG%" 2>&1
echo EXITCODE=%ERRORLEVEL% >> "%LOG%"
type "%LOG%"
echo.
echo Rezultat je i u fajlu testovi.log
pause
