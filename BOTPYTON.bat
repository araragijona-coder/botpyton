@echo off
setlocal
cd /d "%~dp0"
title BOTPYTON - trabajador local bajo demanda

:menu
cls
echo ==========================================
echo  BOTPYTON - TRABAJADOR LOCAL
echo  Solo se ejecuta mientras esta ventana esta abierta.
echo  No instala servicios ni se agrega al inicio.
echo ==========================================
echo.
echo  1. Inspeccionar un proyecto (solo lectura)
echo  2. Detectar herramientas instaladas
echo  3. Consultar version de una herramienta registrada
echo  4. Comprobar el nucleo
echo  5. Ejecutar las pruebas del propio BOTPYTON
echo  6. Salir
echo.
choice /c 123456 /n /m "Selecciona una opcion: "
if errorlevel 6 goto end
if errorlevel 5 goto tests
if errorlevel 4 goto check
if errorlevel 3 goto probe
if errorlevel 2 goto tools
if errorlevel 1 goto inspect

:inspect
echo.
set "PROJECT_PATH="
set /p "PROJECT_PATH=Ruta completa del proyecto a inspeccionar: "
if not defined PROJECT_PATH goto menu
python -m bot.main --inspect-repository "%PROJECT_PATH%"
if errorlevel 1 echo La inspeccion termino con un error.
echo.
pause
goto menu

:tools
python -m bot.main --tools
echo.
pause
goto menu

:probe
echo.
set "TOOL_ID="
set /p "TOOL_ID=ID registrado (por ejemplo git, python, node): "
if not defined TOOL_ID goto menu
python -m bot.main --probe-tool "%TOOL_ID%"
echo.
pause
goto menu

:check
python -m bot.main --check
echo.
pause
goto menu

:tests
python -m unittest discover -s tests -v
echo.
pause
goto menu

:end
endlocal
exit /b 0
