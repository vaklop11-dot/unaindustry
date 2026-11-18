@echo off
title FabAgent - provjera arhiva
cd /d "%~dp0."

if not exist ".venv\Scripts\python.exe" (
  echo GRESKA: .venv ne postoji u %CD%
  pause
  exit /b 1
)

set "FOLDER=%~1"
if "%FOLDER%"=="" set "FOLDER=%USERPROFILE%\Desktop"

echo.
echo   Provjeravam arhive u:  %FOLDER%
echo   Nista se ne raspakuje i nista se ne mijenja.
echo   Izvjestaj:  %CD%\izvjestaj_arhiva.md
echo.
echo   Moze trajati dugo. Prekini sa Ctrl+C, pa pokreni ponovo -
echo   nastavlja se odakle je stalo.
echo.

".venv\Scripts\python.exe" provjeri_arhive.py "%FOLDER%" --nastavi --izvjestaj "izvjestaj_arhiva.md"

echo.
echo   Gotovo. Otvori izvjestaj_arhiva.md i posalji ga.
pause
