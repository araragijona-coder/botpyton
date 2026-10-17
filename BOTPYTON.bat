@echo off
setlocal
cd /d "%~dp0"
title BOTPYTON - trabajador local bajo demanda

:menu
cls
echo ==================================================
echo  BOTPYTON - TRABAJADOR LOCAL
echo  Solo se ejecuta mientras esta ventana esta abierta.
echo  No instala servicios ni se agrega al inicio.
echo ==================================================
echo.
echo  1. Inspeccionar un proyecto (solo lectura)
echo  2. Detectar herramientas instaladas
echo  3. Consultar version de una herramienta registrada
echo  4. Validar plan de cambios locales (sin escribir)
echo  5. Aplicar plan local (escritura explicita + backups)
echo  6. Comprobar el nucleo
echo  7. Ejecutar las pruebas del propio BOTPYTON
echo  8. Agregar tarea JSON a la cola
echo  9. Ejecutar una tarea pendiente
echo  A. Ver cola de tareas
echo  B. Salir
echo.
choice /c 123456789AB /n /m "Selecciona una opcion: "
if errorlevel 11 goto end
if errorlevel 10 goto queue
if errorlevel 9 goto workonce
if errorlevel 8 goto enqueue
if errorlevel 7 goto tests
if errorlevel 6 goto check
if errorlevel 5 goto applyplan
if errorlevel 4 goto dryplan
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

:dryplan
call :getplan
if not defined PLAN_PATH goto menu
if not defined PROJECT_PATH goto menu
python -m bot.main --local-plan "%PLAN_PATH%" --project-root "%PROJECT_PATH%"
echo.
pause
goto menu

:applyplan
echo ATENCION: esta opcion escribe archivos en el proyecto.
set "CONFIRM_WRITE="
set /p "CONFIRM_WRITE=Escribe APLICAR para continuar: "
if /i not "%CONFIRM_WRITE%"=="APLICAR" goto menu
call :getplan
if not defined PLAN_PATH goto menu
if not defined PROJECT_PATH goto menu
python -m bot.main --local-plan "%PLAN_PATH%" --project-root "%PROJECT_PATH%" --execute-local-plan
echo.
pause
goto menu

:getplan
set "PLAN_PATH="
set "PROJECT_PATH="
echo.
set /p "PLAN_PATH=Ruta completa del archivo JSON del plan: "
set /p "PROJECT_PATH=Ruta completa de la carpeta del proyecto: "
exit /b 0

:enqueue
echo.
set "TASK_PATH="
set /p "TASK_PATH=Ruta completa del archivo JSON de la tarea: "
if not defined TASK_PATH goto menu
python -m bot.main --enqueue "%TASK_PATH%"
echo.
pause
goto menu

:workonce
python -m bot.main --work-once
echo.
pause
goto menu

:queue
python -m bot.main --queue
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
