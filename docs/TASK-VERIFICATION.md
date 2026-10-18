# Verificación de éxito de tareas

Las tareas pueden incluir `verification_checks`, una lista de 1 a 20 comprobaciones declarativas. El campo `success_conditions` sigue siendo una descripción humana; los checks son los criterios que el ejecutor puede comprobar automáticamente.

Tipos admitidos:

- `{"type":"file_exists","path":"src/app.py"}`
- `{"type":"file_sha256","path":"src/app.py","sha256":"<64 caracteres hexadecimales>"}`
- `{"type":"python_syntax"}` — análisis estático de archivos Python dentro del proyecto.
- `{"type":"json_valid","path":"config.json"}` — valida JSON de hasta 200 KB y una profundidad máxima de 64 niveles. La lectura se vuelve a limitar durante la operación; un archivo demasiado grande, cambiado durante la lectura, inválido o excesivamente anidado falla de forma explícita.
- `{"type":"tests_pass"}` — requiere que una acción de pruebas haya terminado correctamente.
- `{"type":"project_check_pass"}` — requiere que un perfil de proyecto termine correctamente.
- `{"type":"local_plan_applied"}` — requiere una aplicación local de plan con escrituras verificadas.
- `{"type":"report_exists"}` — comprueba el informe final de la tarea.

La resolución de rutas de las verificaciones rechaza componentes existentes que sean enlaces simbólicos o puntos de reanálisis de Windows (incluidas junctions). Los componentes inexistentes siguen devolviendo un resultado normal de archivo ausente, y las rutas que escapan del directorio base siguen fallando. La validación reduce riesgos de alias de rutas pero no pretende eliminar todas las carreras TOCTOU frente a procesos concurrentes.

Las tareas que contienen `apply_local_plan` deben incluir `local_plan_applied` y al menos una comprobación de archivo/sintaxis/JSON. Las tareas que contienen `run_project_check` deben incluir `project_check_pass` o `tests_pass`.

El ejecutor escribe primero un informe provisional para poder verificar que existe un artefacto real; después incorpora los resultados de verificación y vuelve a escribir el informe final. Si alguna comprobación falla, la tarea no se marca como correcta. Las verificaciones no ejecutan código de proyecto; la ejecución de tests/builds sigue requiriendo autorización independiente.

La implementación utiliza el lector de tamaño acotado y el control de profundidad reutilizables de `bot/bounded_io.py`. Los errores de lectura y de profundidad del decodificador se registran como una comprobación fallida, sin abortar el procesamiento de las demás comprobaciones.
