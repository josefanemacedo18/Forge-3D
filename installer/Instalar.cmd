@echo off
chcp 65001 >nul
title Forge 3D - instalação
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\install.ps1" %*
if errorlevel 1 (
  echo.
  pause
  exit /b 1
)
echo.
echo Esta janela fecha sozinha em 10 segundos.
timeout /t 10 >nul
