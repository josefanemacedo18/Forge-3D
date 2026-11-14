@echo off
chcp 65001 >nul
title Forge 3D - módulo de IA (opcional)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\install-ia.ps1" %*
echo.
pause
