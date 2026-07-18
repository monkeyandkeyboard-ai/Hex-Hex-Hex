@echo off
cd /d "%~dp0server"
start "" http://localhost:8080
python -m gep.server
