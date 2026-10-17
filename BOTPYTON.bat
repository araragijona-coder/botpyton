@echo off
setlocal
cd /d "%~dp0"
title BOTPYTON - Tu tecnico informatico
echo Iniciando BOTPYTON...
echo La aplicacion solo se ejecuta mientras esta ventana permanezca abierta.
echo No se inicia con Windows ni trabaja en segundo plano.
echo.
python -m bot.simple_gui
if errorlevel 1 (
  echo.
  echo BOTPYTON no pudo abrir la interfaz. Comprueba Python 3.11+ y Tk/Tcl.
  echo Puedes utilizar BOTPYTON-CLI.bat como alternativa.
  pause
)
endlocal
