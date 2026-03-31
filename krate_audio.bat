@echo off
title KRATE — Servidor de Audio
echo.
echo  ╔══════════════════════════════════════╗
echo  ║   KRATE — Servidor de Audio Local   ║
echo  ╚══════════════════════════════════════╝
echo.

:: Verificar que Python este instalado
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  ERROR: Python no esta instalado.
    echo  Descargalo en https://www.python.org/downloads/
    echo  Asegurate de marcar "Add Python to PATH" al instalar.
    echo.
    pause
    exit /b 1
)

:: Instalar dependencias necesarias si no estan
echo  Verificando dependencias...
pip install flask flask-cors mutagen --quiet

echo  Dependencias listas.
echo.
echo  Servidor de audio corriendo en localhost:5001
echo  Deja esta ventana abierta mientras usas Krate.
echo  Para detener el servidor cierra esta ventana.
echo.
echo  Abre Krate en: https://krate-production.up.railway.app
echo.

:: Correr el servidor de audio
python "%~dp0krate_audio.py"

pause
