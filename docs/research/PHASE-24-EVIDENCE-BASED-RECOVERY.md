# Phase 24 — Catálogo de diagnóstico con evidencia y parada segura

## Qué corrige

El motor anterior marcaba varias clases de fallos conocidos como reintentables mientras quedaran intentos. En la cola, el campo `retryable` permite que la misma tarea regrese a `pending`; si ningún dato, código ni hipótesis cambió, una tarea que falla por sintaxis, JSON mal formado o una llamada incompatible puede volver a ejecutar exactamente el mismo trabajo. Es un bucle inútil, aunque esté limitado por un máximo de intentos.

Esta fase cambia la definición: `retryable` significa que el mismo trabajo se puede repetir de forma automática **sin cambiar la entrada ni la hipótesis**. Ninguna de las reglas actuales da esa autorización. Un arreglo verificado, una nueva evidencia o una decisión humana debe cambiar las condiciones antes de reencolar una tarea. Los intentos fuera de límites bloquean el avance.

Esto no afirma que BOTPYTON haya sufrido una ejecución duplicada de estas clases en el equipo del usuario; es una inconsistencia reproducible en la política actual de reintentos.

## Nuevo catálogo de diagnóstico

`bot/recovery.py` mantiene reglas versionadas por ID para firmas frecuentes:

- Python: importación/dependencias, importación de símbolos, indentación, sintaxis, JSON, contratos de llamadas/objetos, nombres, claves/datos, límites de secuencia, rutas, permisos/bloqueos, timeouts, aserciones, recursión y memoria.
- Herramientas de desarrollo: conflictos de Git.
- JavaScript/Node y TypeScript: módulos no resueltos y errores de tipos.
- Go: dependencias de módulos y símbolos indefinidos.
- Rust: errores de compilación con código E.
- Java y C#: símbolos/imports/clase no encontrados.

Cada regla entrega una categoría, una estrategia de diagnóstico, pasos que recopilar, un experimento pequeño, una condición de parada y un enlace a documentación. Los enlaces son orientación por clase de problema, no confirmación del origen de la falla.

El informe de `diagnose_failure()` añade `knowledge_rule_id`, `diagnostic_steps`, `safe_experiment`, `stop_condition`, `reference_url`, `root_cause_confirmed=false` y `automatic_retry_permitted=false`. Las correcciones de código existentes siguen limitadas a patrones exactos aprobados; este catálogo no autoriza nuevas ediciones automáticas, instalaciones de paquetes, cambios de permisos ni comandos arbitrarios.

## Protocolo operativo

1. Preservar el error completo, el comando, el código de salida y el entorno.
2. Reunir evidencia local acotada del punto de fallo.
3. Elegir una hipótesis falsable y un experimento reversible.
4. No reintentar automáticamente la misma tarea si las condiciones no cambiaron.
5. Si faltan datos para distinguir hipótesis, solicitar el traceback, pasos para reproducir, versiones o valores esperados/observados.
6. Detenerse ante límite de intentos, timeout, posible pérdida de datos, falta de autorización o verificación fallida.

Una firma reconocida no equivale a causa raíz confirmada: el informe siempre lo declara explícitamente.

## Prácticas y fuentes externas consultadas

- La documentación de excepciones de Python distingue, por ejemplo, un módulo ausente, un atributo inexistente, una clave ausente y un índice fuera de rango. El catálogo convierte esa distinción en listas de investigación distintas: https://docs.python.org/3/library/exceptions.html
- La documentación de AST de Python advierte que analizar sintaxis no demuestra por sí solo que el programa sea ejecutable o semánticamente correcto: https://docs.python.org/3/library/ast.html
- Las notas oficiales de BleachBit 6.0.4 mencionan fallos reales corregidos cuando archivos desaparecían durante el escaneo, errores en manejo de SQLite y una condición de carrera de configuración. Su changelog muestra el principio aplicado aquí: el tipo de fallo determina la investigación y la prueba, en vez de repetirlo sin cambios: https://www.bleachbit.org/news/bleachbit-604
- Las notas oficiales de pytest documentan arreglos de regresiones de lógica y manejo de casos extremos, apoyando la inclusión de pruebas de regresión centradas en el comportamiento: https://pytest.org/en/stable/changelog.html
- La guía de módulos de Go explica que los errores por módulos no encontrados pueden tener distintas causas, como la ruta importada, la raíz del módulo o la falta de archivos Go en la carpeta actual: https://go.dev/wiki/Modules
- Microsoft documenta CS0103 como un problema de resolución de nombres/alcance y sugiere revisar escritura, directivas de importación y referencias: https://learn.microsoft.com/en-us/dotnet/csharp/language-reference/compiler-messages/cs0103
- La documentación oficial de Node.js define códigos de error de módulos y runtime que se pueden reconocer como pistas sin asumir que un paquete debe instalarse: https://nodejs.org/api/errors.html
- GitHub recomienda limitar permisos e impedir que datos no confiables se interpreten como código en los flujos automatizados: https://docs.github.com/en/actions/reference/security/secure-use y https://docs.github.com/en/actions/concepts/security/script-injections

## Alcance real

La cobertura es un catálogo inicial de firmas literales en logs comunes. No analiza semánticamente todos los compiladores, no descarga documentación mientras diagnostica y puede clasificar mal logs mixtos; por eso entrega pistas y nunca afirma que la causa raíz esté confirmada. Las referencias externas se consultaron durante el desarrollo y forman parte de documentación local, sin peticiones de red en tiempo de ejecución.
