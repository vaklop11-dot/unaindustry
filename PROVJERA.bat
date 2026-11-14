@echo off
title FabAgent provjera
set "FA=%USERPROFILE%\Desktop\image-to-lightburn-svg\FabAgent"
cd /d "%FA%" 2>nul || (echo GRESKA: nema foldera %FA% & pause & exit /b 1)
set "LOG=%USERPROFILE%\Desktop\provjera.log"

echo FabAgent provjera %DATE% %TIME% > "%LOG%"
echo.
echo   [1/2] Od SVG fajla do gotovog posla...
".venv\Scripts\python.exe" -I provjera_svg.py >> "%LOG%" 2>&1
echo SVG_EXIT=%ERRORLEVEL% >> "%LOG%"

echo   [2/2] Cijeli set testova ^(ovo traje, ne zatvaraj prozor^)...
echo. >> "%LOG%"
echo ===== TESTOVI ===== >> "%LOG%"
".venv\Scripts\python.exe" -m pytest -v -p no:cacheprovider >> "%LOG%" 2>&1
echo TESTOVI_EXIT=%ERRORLEVEL% >> "%LOG%"

echo.
echo   Gotovo. Rezultat:  %LOG%
echo.
pause
