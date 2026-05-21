@echo off
title JAV Manager
cd /d "%~dp0"

start "" "http://localhost:5000"

powershell -NoProfile -NoExit -Command "cd '%~dp0'; python -m http.server 5000"
