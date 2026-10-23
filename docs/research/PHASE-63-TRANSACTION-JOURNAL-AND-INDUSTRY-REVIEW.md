# Fase 63 — diario transaccional y recuperación de cambios locales

## Alcance

- SHA base: `60ec902d102583b959025932fbfb0bd736d17868`
- Rama aislada: `bot-bootstrap/phase-63-transaction-journal-recovery`
- Objetivo: que los planes de cambio multiparchivo dejen evidencia durable antes de escribir el proyecto y permitan inspeccionar/revertir de forma explícita los cambios cuya identidad pueda verificarse.
- `main` no se modifica; no se crea ni fusiona ningún PR.

## Auditoría de arquitectura

BOTPYTON separa la cola JSON, las reglas deterministas, la ejecución limitada, el agente local con permisos explícitos, las operaciones PC-care y la publicación de GitHub. Los cambios declarativos tienen vista previa, rutas relativas canónicas, tipos de archivo permitidos, hashes esperados, copias de seguridad y rollback condicional.

El hueco principal era el alcance de esa recuperación: `apply_local_change_plan` podía revertir excepciones recibidas durante la misma ejecución, pero no disponía de diario persistente por archivo. Tras una interrupción abrupta, no había un registro estructurado que permitiera distinguir un archivo publicado de un archivo pendiente o de un destino modificado por otro proceso.

## Referencias de industria contrastadas

1. **BleachBit — mantenimiento y seguridad de limpieza**
   - Versiones oficiales: https://github.com/bleachbit/bleachbit/releases
   - Aviso de carga insegura de reglas CleanerML: https://github.com/bleachbit/bleachbit/security/advisories/GHSA-q5ww-c3j6-8j23
   - Las notas de 6.0.2 mencionan una corrección de borrado arbitrario al ejecutar limpieza elevada; 4.7 corrige el seguimiento de symlinks/junctions al limpiar la papelera; el aviso GHSA-q5ww-c3j6-8j23 documenta reglas XML maliciosas que podían ejecutar comandos desde una ubicación escribible por el usuario. Aplicación en BOTPYTON: límites de raíz, rechazo de enlaces/reparse points, datos de planes sin ejecutar como comandos y ningún bypass implícito de permisos.
2. **CCleaner — resultados y fallos de mantenimiento**
   - Historial oficial: https://www.ccleaner.com/ccleaner/version-history
   - Las notas v7.12.1526 (29-09-2026) citan seguridad de escritura de archivos y fallos de servicio/cierre; v7.06.1270 documenta una corrección para que las limpiezas programadas respeten las exclusiones, y v7.07.1313 describe resultados más claros cuando el Duplicate Finder no puede borrar algunos archivos. Aplicación en BOTPYTON: registrar estado por archivo, comunicar rollback incompleto y no presentar una operación parcial como éxito global.
3. **restic — copia y restauración**
   - Historial oficial: https://github.com/restic/restic/releases
   - Las notas de restic 0.17.x/0.19.x documentan correcciones para locking, concurrencia, rutas que se solapan con un repositorio, metadatos de Windows y entradas duplicadas. Aplicación en BOTPYTON: lock durante recuperación, validación de ruta/raíz y preservación de conflictos antes de revertir.
4. **pre-commit — automatización reproducible de repositorios**
   - Changelog oficial: https://github.com/pre-commit/pre-commit/blob/main/CHANGELOG.md
   - Las notas 4.6.x registran correcciones de compatibilidad de entornos/herramientas y nombres de archivo duplicados en recorridos de comprobación. Aplicación en BOTPYTON: comandos declarativos fijos y pruebas de regresión sobre casos de entorno/colisión; no interpolar texto de tareas como comandos.

Estas referencias aportan patrones y ejemplos documentados; no implican que BOTPYTON tuviera cada uno de esos fallos externos.

## Código fuente de referencia inspeccionado

- [BleachBit `FileUtilities.py`](https://github.com/bleachbit/bleachbit/blob/master/bleachbit/FileUtilities.py): extiende la detección de enlaces en Windows a junctions/reparse points y usa un lock del directorio padre para ciertas operaciones de borrado.
- [pre-commit `store.py`](https://github.com/pre-commit/pre-commit/blob/main/pre_commit/store.py): inicializa su base de datos mediante un archivo temporal y movimiento atómico; vuelve a comprobar dentro del lock si otro proceso completó la inicialización.
- [pre-commit `file_lock.py`](https://github.com/pre-commit/pre-commit/blob/main/pre_commit/file_lock.py): separa el mecanismo de lock de POSIX y Windows y documenta espera/adquisición con liberación explícita.
- [restic `doc/design.rst`](https://github.com/restic/restic/blob/master/doc/design.rst): describe el uso de IDs SHA-256, publicación atómica y locks del repositorio para proteger la consistencia ante accesos concurrentes.
- [restic `internal/restorer/restorer.go`](https://github.com/restic/restic/blob/master/internal/restorer/restorer.go): separa las políticas de sobrescritura y expone callbacks de error/aviso en la restauración, en vez de asumir que todos los destinos deben reemplazarse.

Patrones trasladados en esta fase: lock estable durante la recuperación, diario de estado con SHA-256, publicación atómica, creación exclusiva, restauración condicionada y distinción explícita entre éxito completo, conflicto externo y estado ambiguo. BOTPYTON mantiene una implementación de biblioteca estándar y no copia código ni incorpora las dependencias de esos proyectos.

## Cambios aplicados

- Se crea un diario JSON de tamaño limitado en `<backup_root>/.botpyton-transactions/<task>-<fingerprint>.json` antes de publicar el primer archivo del proyecto.
- El diario contiene hashes, rutas canónicas, estados por archivo, raíces declaradas y una identidad de archivo posterior a la publicación; no almacena el contenido nuevo ni los secretos del proyecto.
- El diario se actualiza atómicamente antes y después de cada publicación. El destino del diario se publica en modo exclusivo y una transacción anterior con el mismo identificador no se sobrescribe silenciosamente.
- Se añade `inspect_local_change_transaction()`, de solo lectura, que verifica esquema, límites, fingerprint, raíz, copias de seguridad, hash e identidad actual.
- Se añade `rollback_local_change_transaction()`, que exige confirmación exacta del identificador y sólo restaura/elimina destinos cuyo estado de publicación, SHA-256 e identidad coinciden. Cambios externos, enlaces, copias de seguridad inválidas y estados ambiguos se conservan y se marcan para revisión manual.
- CLI:
  - Inspección sin escrituras: `python -m bot.main --local-transaction-inspect JOURNAL.json --project-root PROJECT`
  - Rollback explícito: `python -m bot.main --local-transaction-rollback JOURNAL.json --project-root PROJECT --confirm-transaction-id SHA256`
- Se corrige la comunicación del resultado del rollback para que `writes_performed` indique si la recuperación realmente modificó archivos.

## Límite deliberado

No es posible hacer una única operación atómica que publique simultáneamente un archivo de proyecto y su diario separado en todas las plataformas. Si el proceso termina justo después de publicar un archivo y antes de registrar su identidad/estado, el diario puede conservar `publishing`. En ese caso, la recuperación declara la ambigüedad y **no** elimina ni sobrescribe el destino automáticamente. Esta decisión prioriza preservar datos externos sobre una reversión especulativa.

Los handles/identidades del sistema de archivos reducen ambigüedades, pero no constituyen un sandbox del kernel frente a cualquier proceso local hostil. CI usa directorios temporales; no prueba las carpetas personales ni la RAM libre del PC objetivo.

## Regresiones y aceptación

- Plan de dos archivos: el diario se crea antes de las escrituras y termina `completed`, con hashes pero sin contenido.
- Rollback explícito: restaura el archivo preexistente y elimina sólo el archivo nuevo publicado por esta transacción.
- ID de confirmación incorrecto: no se efectúan escrituras.
- Modificación externa: la versión externa se conserva y el resultado es `rollback_partial`.
- Estado `publishing` sin identidad verificable: se requiere revisión manual.
- Aceptación: Foundation CI y Windows low-memory smoke deben pasar en el mismo SHA final.
