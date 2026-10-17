@echo off
setlocal
cd /d "%~dp0"
title BOTPYTON - trabajador local bajo demanda

:menu
cls
echo ==========================================
echo  BOTPYTON - TRABAJADOR LOCAL
echo  Se ejecuta solo mientras esta ventana esta abierta.
echo  No instala servicios ni se agrega al inicio de Windows.
echo ==========================================
echo.
echo  1. Inspeccionar un proyecto (solo lectura)
echo  2. Comprobar el nucleo de BOTPYTON
echo  3. Ejecutar las pruebas del propio BOTPYTON
echo  4. Salir
echo.
choice /c 1234 /n /m "Selecciona una opcion: "
if errorlevel 4 goto end
if errorlevel 3 goto tests
if errorlevel 2 goto check
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
