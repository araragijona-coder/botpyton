# Investigación determinista de errores desconocidos — Fase 47

## Qué hace

Usa un log de error y el estado estático actual de un proyecto para producir un informe JSON con:
- clasificación por las reglas locales existentes, si hay una firma reconocida;
- huella SHA-256 y última línea saneada del log;
- inventario acotado de archivos y señales de tecnologías conocidas;
- comprobaciones estáticas de sintaxis Python y validez JSON/TOML;
- hipótesis basadas en señales concretas, ordenadas de forma determinista;
- para cada hipótesis, un experimento que permita distinguirla y una condición que la refute;
- evidencia que falta y una decisión de parada explícita.

El objetivo no es escoger una causa por intuición. Si el log y las comprobaciones no permiten distinguir causas plausibles, el resultado es needs_more_evidence, sin propuesta de cambio.

## Uso

Guarda el log original dentro del proyecto o en un subdirectorio de informes. Luego ejecuta:

~~~powershell
python -m bot.main --investigate-failure "reports/failure.log" --project-root "C:\projects\mi-app" --investigation-command "python -m unittest" --investigation-exit-code 1 --investigation-expected "la suite pasa" --investigation-observed "un test falla con VendorError Z-19"
~~~

Todos los campos de contexto son opcionales, pero aportarlos hace más útil la investigación. La ruta del log debe ser relativa y canónica al root seleccionado. --diagnose sigue disponible como diagnóstico anterior; ambos comandos son mutuamente excluyentes.

## Cómo leer el resultado

- investigation_complete: hay al menos una hipótesis apoyada por señales observadas; todavía no significa causa raíz confirmada.
- needs_more_evidence: no hay una hipótesis específica defendible. Entrega al humano los datos faltantes y pide una reproducción mínima.
- blocked_attempt_limit: el contador de intento está fuera del presupuesto configurado; detener el ciclo automático.
- static_evidence: solo contiene resultados de inspecciones no ejecutables. Los hallazgos de sintaxis o JSON son problemas observados en el estado escaneado, pero pueden no ser la causa del log original.
- hypotheses[].experiment: paso discriminante a ejecutar después de revisar el alcance. No es un comando automático ni permiso para cambiar el código.
- recommended_resolution: el siguiente camino conservador, que puede exigir un parche revisado y una regresión específica.

## Límites

El log se limita a 64 KiB. El inventario se limita a 5.000 entradas y 500 archivos candidatos; cada archivo mide como máximo 200 KiB y el presupuesto total de lectura es 2 MiB. Se omiten directorios generados comunes, enlaces simbólicos y puntos de reanálisis detectados. Las comprobaciones de sintaxis Python no importan ni ejecutan el código del proyecto; los parsers JSON/TOML solo leen los bytes seleccionados.

El escaneo es local, secuencial, sin IA y sin llamadas de red. Nunca instala paquetes, reintenta una tarea, escribe código ni ejecuta builds/tests. El registro de causa raíz queda siempre como false. Si se identifica una anomalía estática, el siguiente paso es revisar el archivo exacto y escribir una prueba de regresión; el cambio se aplica mediante el flujo de parche aislado ya existente después de revisión humana.

Los reportes sanea valores que parecen credenciales, pero no pueden detectar todos los secretos de cada dominio. Mantén el log original local y revísalo antes de compartirlo. Un hash de log permite identificar el mismo contenido; no demuestra que el log sea genuino.
