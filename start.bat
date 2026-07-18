@echo off
cd /d "%~dp0server"
start "Hex Tower MUD Server" cmd /k python -m gep.server
timeout /t 2 /nobreak >nul
start "" http://localhost:8080

