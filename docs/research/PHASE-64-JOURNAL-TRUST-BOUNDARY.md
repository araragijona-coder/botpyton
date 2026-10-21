# Fase 64 — aislamiento de concurrencia e integridad del diario

## Alcance y origen

- SHA base: \`50cf6a8d0d82aedda88563a0560c172a2f3a88cb\`
- Rama aislada: \`bot-bootstrap/phase-64-journal-trust-boundary\`
- No se modifica \`main\`; no se abre ni fusiona ningún PR.
- Auditoría de continuación de Fase 63: serialización de publicación/recuperación y detección de corrupción accidental del diario.

## Hallazgo corregido

Fase 63 usaba un lock de recuperación separado, mientras que \`apply_local_change_plan\` no mantenía el mismo lock durante la transacción. Un rollback podía leer un estado intermedio \`publishing\` y finalizar antes de que la aplicación publicara el archivo. Aunque se verificaban los hashes de cada escritura, la secuencia entre los pasos no se serializaba como una sola transacción.

La aplicación y el rollback ahora usan el mismo sidecar estable \`.<transaction_id>.transaction.lock\` durante toda la operación modificadora. Una segunda aplicación de la misma huella encuentra el diario ya existente y se bloquea antes de publicar archivos. Los hashes y las identidades siguen siendo necesarios: el lock coordina procesos BOTPYTON que respetan el contrato, pero no bloquea un proceso arbitrario que ignore el lock.

## Integridad del diario

El diario usa ahora el esquema 2 y el campo \`journal_sha256\`, calculado sobre una representación JSON canónica de los campos restantes. Al cargarlo, BOTPYTON valida el checksum antes de confiar en estados, identidades o raíces. Una edición accidental, un estado parcial o una corrupción no coordinada hace fallar la inspección y el rollback en modo cerrado.

**Limitación:** este checksum detecta corrupción accidental; no es una firma ni una autenticación criptográfica frente a un atacante que puede modificar el diario y recalcular el checksum. El diario y sus copias de seguridad deben permanecer en un directorio confiable. No se afirma resistencia contra malware con los mismos permisos del usuario.

## Fuentes de referencia consultadas

- [restic 0.19.1 — notas oficiales de versión, 5 de julio de 2026](https://github.com/restic/restic/releases/tag/v0.19.1): correcciones para prevenir montar sobre el directorio del repositorio, manejar rutas inaccesibles sin cerrar inesperadamente y respetar exclusiones en entradas de directorio duplicadas.
- [pre-commit 4.6.2 — changelog oficial, 10 de agosto de 2026](https://github.com/pre-commit/pre-commit/blob/main/CHANGELOG.md): corrección de una regresión con hooks Node y npm 11. Las notas 4.6.1 también documentan nombres de archivo conflictivos duplicados en recorridos \`--all-files\`.
- [BleachBit — fuente \`FileUtilities.py\`](https://github.com/bleachbit/bleachbit/blob/master/bleachbit/FileUtilities.py): tratamiento de junctions/reparse points en Windows y protección frente a carreras con un lock del directorio padre en ciertas operaciones de borrado.
- [pre-commit — \`pre_commit/file_lock.py\`](https://github.com/pre-commit/pre-commit/blob/main/pre_commit/file_lock.py) y [\`pre_commit/store.py\`](https://github.com/pre-commit/pre-commit/blob/main/pre_commit/store.py): locks separados para Windows/POSIX, revalidación después de adquirir el lock, creación temporal y publicación atómica.
- [restic — diseño del repositorio](https://github.com/restic/restic/blob/master/doc/design.rst) y [código de restauración](https://github.com/restic/restic/blob/master/internal/restorer/restorer.go): verificación por ID/hash, publicación atómica, locks y políticas explícitas para sobrescritura/restauración.
- [CCleaner — historial de versiones](https://www.ccleaner.com/ccleaner/version-history/): fuente de notas de errores operativos y correcciones de mantenimiento; se usa como referencia comparativa, no como certificación de equivalencia.

## Cambios y pruebas

- Lock compartido en aplicación y rollback para el mismo ID de transacción.
- Hash canónico del diario para detectar corrupción accidental antes de la recuperación.
- Esquema elevado a 2; los diarios previos que no incluyen el campo de integridad se rechazan por esquema en vez de reinterpretarse silenciosamente.
- Corrección del manejo de \`_PublishedCleanupError\` en el módulo de diario usando la definición del módulo de cambios local.
- Regresión de concurrencia: bloquea una publicación después de pasar a \`publishing\`, inicia rollback en otro hilo y confirma que no finaliza hasta liberar el lock de la aplicación.
- Regresión de integridad: altera el estado persistido sin actualizar su checksum; tanto inspección como rollback deben rechazarlo y preservar el archivo.
- La prueba de interrupción válida recalcula el checksum al construir deliberadamente un diario coherente con estado intermedio.

## Criterios de aceptación

Foundation CI y Windows low-memory smoke deben finalizar correctamente en el mismo SHA. La CI no reemplaza la prueba en una carpeta desechable del PC real. No usar un diario de origen desconocido para autorizar escrituras destructivas.
