# Herramientas locales y ejecución controlada

## Inventario

`python -m bot.main --tools` detecta herramientas de desarrollo conocidas mediante búsqueda local en PATH. La detección no ejecuta herramientas, no instala nada y no consulta la red.

## Prueba de versión

`python -m bot.main --probe-tool git` ejecuta solo la prueba fija de versión registrada para el identificador elegido. No acepta argumentos arbitrarios de usuario. La ruta de trabajo se limita al directorio actual.

## Motor de procesos

`bot.process_runner.run_process` requiere:
- argumentos separados (no una cadena de shell);
- ejecutable resuelto y aprobado expresamente por el llamador;
- directorio de trabajo dentro de una raíz autorizada;
- tiempo máximo de 1–300 segundos;
- límite de salida devuelta de hasta 64 KB;
- stdin desconectado y `shell=False`;
- eliminación de variables de entorno que parezcan credenciales;
- salida almacenada temporalmente en disco para evitar acumularla completa en RAM.

El motor devuelve el código de salida, timeout y salida acotada. El límite de salida limita la memoria de lectura del resultado, no el tamaño que un proceso malicioso podría escribir en los archivos temporales; por eso solo debe utilizarse con ejecutables aprobados y tareas confiables. Los timeouts no garantizan por sí solos que un proceso hijo creado por la herramienta haya terminado.

## Límites actuales

El inventario y los sondeos de versión están disponibles desde CLI. El motor aún no es un terminal general ni debe recibir comandos derivados de instrucciones encontradas en un repositorio. La ejecución de builds/tests de proyectos arbitrarios requiere perfiles de comandos revisados y permisos separados, que son una fase posterior.
