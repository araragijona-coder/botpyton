# Descubrimiento y ejecución de pruebas/builds de proyectos

## Descubrimiento estático

`python -m bot.main --list-project-checks --project-root "C:\\ruta\\proyecto"` detecta perfiles de pruebas/builds a partir de señales verificables: carpeta `tests/`, scripts `test`/`build` en `package.json`, `Cargo.toml`, `go.mod`, `pom.xml`, soluciones/proyectos .NET y archivos de configuración PHP/Ruby. Solo lee manifiestos JSON acotados; no ejecuta los comandos detectados.

## Ejecución explícitamente autorizada

```bash
python -m bot.main --run-project-check python-unittest --project-root "C:\\ruta\\proyecto" --execute-project-code
```

Los identificadores deben aparecer en el inventario del proyecto actual. BOTPYTON no acepta comandos ni argumentos arbitrarios del usuario para estos perfiles. La ejecución pasa por el runner acotado: argumentos separados, `shell=False`, timeout, salida retenida limitada, variables que parezcan credenciales filtradas y presupuesto de memoria adaptativo. En Windows se aplica el límite mediante Job Object; en otros sistemas el límite de memoria todavía no está garantizado.

**Advertencia:** ejecutar pruebas o builds puede ejecutar código del proyecto, scripts de paquetes, plugins o scripts de compilación. Por eso el CLI exige `--execute-project-code`, y el menú de Windows exige escribir `EJECUTAR`. Revisa antes el repositorio y los scripts. No se instalan dependencias ni se utiliza la red automáticamente.

Los perfiles actuales incluyen Python unittest, scripts test/build de npm, Cargo, Go, Maven, .NET, Composer y Rake/Bundler cuando se detectan los manifiestos y herramientas necesarios. No todos los perfiles están disponibles en todos los sistemas, y la detección no garantiza que el proyecto compile o que sus dependencias estén instaladas.
