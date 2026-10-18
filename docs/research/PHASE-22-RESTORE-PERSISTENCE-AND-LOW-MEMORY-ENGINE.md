# Phase 22 — Recuperación segura y decisión sobre un motor interno

## Conclusión de arquitectura: no añadir otro motor por ahora

BOTPYTON ya se ejecuta sobre el runtime de Python y utiliza Tkinter/Tcl para la interfaz, con funciones de la biblioteca estándar. No necesita instalar un motor de terceros para ejecutar las comprobaciones de PC. Un nuevo runtime, framework o motor propio no reduce por definición el uso de RAM: añadiría código, inicialización, mantenimiento y pruebas.

La estructura actual ya hace una carga diferida importante: `bot.gui._call_main()` importa `bot.main` solo cuando se inicia una operación, en vez de cargar todo el despachador al abrir la ventana. Las comprobaciones del PC también se documentan como secuenciales y bajo demanda. Se mantiene esa arquitectura ligera.

No se puede afirmar un porcentaje de reducción sin una medición repetible del arranque y de las operaciones en el equipo objetivo. La siguiente mejora de rendimiento debería empezar por un benchmark comparativo con RSS/tiempo de arranque y duración por etapa, usando el mismo conjunto de operaciones y caché fría/caliente. Mantener la suite de diagnósticos serial, los límites de entrada y los límites de procesos es preferible a introducir paralelismo que eleve el pico de RAM.

### ¿Y usar SQLite como motor de cola?

Python incluye el módulo `sqlite3`, que da acceso a la base SQLite con transacciones; no exige instalar un servicio externo. Podría resultar conveniente si la cola creciera mucho y necesitara consultas/indexación frecuentes, pero BOTPYTON ya mantiene una memoria JSON acotada a 4 MiB y protege las transiciones con bloqueo entre procesos. Cambiarla ahora requeriría migración, compatibilidad, pruebas de recuperación y nuevos modos de fallo. No hay mediciones que justifiquen ese costo, por lo que no se migra en esta fase.

## Hallazgo corregido: fallaba la persistencia del manifiesto al restaurar caché

En `restore_application_cache_quarantine()`, tras mover cada archivo desde la cuarentena a su ubicación original, BOTPYTON marcaba el elemento como `restored` y guardaba el manifiesto. El error de escritura quedaba atrapado por el manejador general de errores del elemento, que incrementaba el contador de omitidos y continuaba el bucle. Eso permitía seguir moviendo archivos aunque no se pudiera registrar con seguridad la recuperación del anterior.

No es evidencia de pérdida de datos ocurrida en el PC del usuario; era un defecto reproducible de manejo de errores.

### Corrección

- Si falla escribir el manifiesto inmediatamente después de restaurar un archivo, se detiene el bucle y no se mueven más elementos.
- Se intenta una sola escritura final del estado parcial después de detener los movimientos. Si falla también, la operación retorna un estado parcial y una advertencia que pide revisar el manifiesto persistido y ambas rutas.
- Los elementos que quedaron sin intentar se reflejan en `files_skipped`.
- Se agrega una prueba de regresión con dos archivos en raíces de caché permitidas y un fallo de escritura simulado: se debe restaurar como máximo uno, dejar el segundo en cuarentena y registrar el estado parcial cuando el siguiente guardado funciona.

## Investigación externa de rendimiento y errores

- BleachBit publicó en sus notas de versión 6.0.4 mejoras de arranque y escaneo, además de correcciones para fallos de enumeración de procesos, archivos que desaparecen durante una comprobación y fugas de descriptores. Sus notas beta 6.0.5 también describen mejoras de velocidad por carga diferida de módulos y optimización de ordenamiento/procesamiento. La idea aplicable aquí es medir las rutas reales y cargar lo costoso cuando se necesita, no agregar un motor ajeno: https://www.bleachbit.org/news/bleachbit-604 y https://github.com/bleachbit/bleachbit/releases
- La documentación oficial de Tkinter explica que los manejadores de eventos deben responder rápido y que el trabajo prolongado no debería ejecutarse directamente dentro de un manejador, porque bloquea el procesamiento de eventos: https://docs.python.org/3/library/tkinter.html
- La documentación oficial de Python describe `sqlite3` y su control transaccional, referencia útil si en el futuro las mediciones justifican una cola SQL: https://docs.python.org/3/library/sqlite3.html

Estas fuentes inspiran criterios de ingeniería, no prueban que una optimización específica de BOTPYTON vaya a reducir un porcentaje determinado de RAM. Esta fase no añade paquetes, no instala servicios, no usa APIs de IA y no modifica el alcance de las operaciones de limpieza.

## Verificación requerida

La prueba nueva debe ejecutarse junto con toda la suite en Linux y Windows. La promoción a `main` queda condicionada a los checks del PR y a los checks posteriores al merge.
