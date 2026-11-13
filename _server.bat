@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m fabagent.web >> "server.log" 2>&1
