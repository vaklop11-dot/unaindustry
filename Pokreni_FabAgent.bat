@echo off
rem FabAgent web interfejs - otvara http://127.0.0.1:8000/ u browseru
cd /d "%~dp0"
".venv\Scripts\python.exe" -m fabagent.web
pause
