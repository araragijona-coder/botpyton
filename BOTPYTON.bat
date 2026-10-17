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
echo  2. Ver perfil del sistema y presupuesto de memoria
echo  3. Detectar herramientas instaladas
echo  4. Consultar version de una herramienta registrada
echo  5. Validar plan de cambios locales (sin escribir)
echo  6. Aplicar plan local (escritura explicita + backups)
echo  7. Comprobar el nucleo
echo  8. Ejecutar las pruebas del propio BOTPYTON
echo  9. Agregar tarea JSON a la cola
echo  A. Ejecutar una tarea pendiente en un proyecto elegido
echo  B. Ver cola de tareas
echo  C. Detectar pruebas/builds del proyecto (sin ejecutarlos)
echo  D. Ejecutar una prueba/build detectado (requiere aprobacion)
echo  E. Crear workspace Git aislado (requiere repositorio limpio)
echo  F. Inspeccionar estado/diff de un workspace
echo  G. Salir
echo  H. Auditoria del PC (solo lectura, sin borrar ni actualizar nada)
echo  I. Ver procesos que mas memoria usan (solo lectura)
echo.
choice /c 123456789ABCDEFGHI /n /m "Selecciona una opcion: "
if errorlevel 18 goto processcheck
if errorlevel 17 goto pccheck
if errorlevel 16 goto end
if errorlevel 15 goto workspacestatus
if errorlevel 14 goto createworkspace
if errorlevel 13 goto runcheck
if errorlevel 12 goto listchecks
if errorlevel 11 goto queue
if errorlevel 10 goto workonce
if errorlevel 9 goto enqueue
if errorlevel 8 goto tests
if errorlevel 7 goto check
if errorlevel 6 goto applyplan
if errorlevel 5 goto dryplan
if errorlevel 4 goto probe
if errorlevel 3 goto tools
if errorlevel 2 goto systemprofile
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

:systemprofile
python -m bot.main --system-profile
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
echo.
set "PROJECT_PATH="
set /p "PROJECT_PATH=Ruta del proyecto donde se ejecutara la tarea (usa . para BOTPYTON): "
if not defined PROJECT_PATH set "PROJECT_PATH=."
python -m bot.main --work-once --project-root "%PROJECT_PATH%"
echo.
pause
goto menu

:queue
python -m bot.main --queue
echo.
pause
goto menu

:listchecks
echo.
set "PROJECT_PATH="
set /p "PROJECT_PATH=Ruta completa del proyecto a analizar: "
if not defined PROJECT_PATH goto menu
python -m bot.main --list-project-checks --project-root "%PROJECT_PATH%"
echo.
pause
goto menu

:runcheck
echo.
set "PROJECT_PATH="
set /p "PROJECT_PATH=Ruta completa del proyecto: "
if not defined PROJECT_PATH goto menu
set "CHECK_ID="
set /p "CHECK_ID=ID exacto detectado (por ejemplo python-unittest o npm-test): "
if not defined CHECK_ID goto menu
echo ATENCION: el test/build puede ejecutar codigo del proyecto.
set "CONFIRM_CODE="
set /p "CONFIRM_CODE=Escribe EJECUTAR para autorizar: "
if /i not "%CONFIRM_CODE%"=="EJECUTAR" goto menu
python -m bot.main --run-project-check "%CHECK_ID%" --project-root "%PROJECT_PATH%" --execute-project-code
echo.
pause
goto menu

:createworkspace
echo.
set "PROJECT_PATH="
set /p "PROJECT_PATH=Ruta completa del repositorio Git limpio: "
if not defined PROJECT_PATH goto menu
set "TASK_ID="
set /p "TASK_ID=ID de la tarea para la rama aislada: "
if not defined TASK_ID goto menu
set "WORKSPACE_PARENT="
set /p "WORKSPACE_PARENT=Carpeta para workspaces (Enter = predeterminada): "
if defined WORKSPACE_PARENT (
  python -m bot.main --create-workspace "%TASK_ID%" --project-root "%PROJECT_PATH%" --workspace-parent "%WORKSPACE_PARENT%"
) else (
  python -m bot.main --create-workspace "%TASK_ID%" --project-root "%PROJECT_PATH%"
)
echo.
pause
goto menu

:workspacestatus
echo.
set "WORKSPACE_PATH="
set /p "WORKSPACE_PATH=Ruta completa del workspace Git: "
if not defined WORKSPACE_PATH goto menu
python -m bot.main --workspace-status "%WORKSPACE_PATH%"
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


:processcheck
echo.
echo Instantanea de memoria por proceso; no cierra ni cambia procesos.
python -m bot.main --pc-processes
echo.
pause
goto menu

:pccheck
echo.
echo AUDITORIA LOCAL: no borra archivos, no desactiva programas ni instala controladores.
python -m bot.main --pc-check
echo.
pause
goto menu

:end
endlocal
exit /b 0
