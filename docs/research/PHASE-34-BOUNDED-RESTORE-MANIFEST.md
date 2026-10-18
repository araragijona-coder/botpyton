# Auditoría — lectura acotada del manifiesto de restauración

Fecha: 2026-10-18

## Hallazgo reproducible

En bot/application_cache_quarantine.py, la función _manifest_location() comprobaba que el tamaño del manifiesto no superase 256 KiB, pero después llamaba a Path.read_text(), que devuelve el contenido completo del archivo. Si el archivo crecía entre la comprobación de tamaño y la lectura, el límite inicial no imponía un límite real a los bytes leídos. Además, la ruta no imponía una profundidad máxima al JSON. No hay indicios de que esta carrera se haya producido en el equipo del usuario; es una brecha del control defensivo.

## Cambios

- Leer el manifiesto en modo binario con un máximo de MAX_CACHE_MANIFEST_BYTES + 1 bytes.
- Exigir archivo regular sin symlink/reparse point y comparar dispositivo, inode y tamaño entre la comprobación previa, el descriptor abierto y la ruta posterior.
- Detectar cambios de tamaño o fecha de modificación mientras se lee y rechazar el manifiesto si cambia.
- Rechazar JSON de más de 64 niveles y convertir fallos de UTF-8, sintaxis o recursión en un resultado bloqueado, sin continuar con la restauración.
- Añadir pruebas que simulan crecimiento después de la comprobación inicial y anidamiento excesivo. Ambas deben demostrar que el archivo de caché sigue en cuarentena.

## Referencias oficiales

- Python documenta que pathlib.Path.read_text() devuelve el contenido decodificado completo; no expone un límite de tamaño. La lectura por bloques mediante un descriptor permite limitar los bytes efectivamente consumidos: https://docs.python.org/3/library/pathlib.html
- La documentación oficial de Python para json advierte que un JSON malicioso puede consumir recursos considerables y recomienda limitar el tamaño de los datos antes de parsearlos: https://docs.python.org/3/library/json.html
- BleachBit 6.0.6 (17 de octubre de 2026) publica arreglos recientes de robustez en borrado/wiping y comportamiento ante fallos de operaciones con múltiples rutas. No es el mismo defecto de BOTPYTON; sirve como referencia de mantenimiento para hacer explícitos los límites y no ocultar resultados parciales: https://www.bleachbit.org/news/bleachbit-606

## Límites

El cambio impide que el lector del manifiesto consuma más de 256 KiB y rechaza modificaciones detectadas durante la lectura. No elimina todas las posibles carreras de directorios o del sistema de archivos y el checksum SHA-256 no es una firma digital. Una lectura bloqueada nunca autoriza a mover archivos; los datos permanecen en cuarentena hasta una restauración válida.
