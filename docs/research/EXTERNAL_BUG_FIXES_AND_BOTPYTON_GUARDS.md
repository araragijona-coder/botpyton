# BOTPYTON: lecciones de errores reales y defensas aplicadas

Fecha de revisión: 2026-10-17

Actualización adicional de auditoría: 2026-10-17 — se detectó que el plan de cambios locales comprobaba `is_symlink()` pero no el atributo de reparse point de Windows. Se añadió una defensa compartida y una prueba de regresión para rechazar componentes existentes marcados como reparse points. Esto cierra esa brecha concreta de validación; no se presenta como solución completa a todas las carreras TOCTOU.

Este documento registra patrones observados en notas oficiales de versiones y soporte. Son referencias para orientar auditorías; **no significa que todos esos fallos hayan existido en BOTPYTON**.

## Fuentes externas consultadas

- BleachBit, notas de versiones: https://github.com/bleachbit/bleachbit/releases
- BleachBit 6.0.2: https://www.bleachbit.org/news/bleachbit-602
- BleachBit 6.0.3 beta y avisos de seguridad: https://www.bleachbit.org/news/bleachbit-603-beta
- Microsoft, directiva Storage Sense y ámbitos admitidos: https://learn.microsoft.com/en-us/windows/client-management/mdm/policy-csp-storage
- BleachBit, aviso de carga insegura de CleanerML: https://github.com/bleachbit/bleachbit/security/advisories/GHSA-q5ww-c3j6-8j23
- CCleaner, historial de versiones: https://www.ccleaner.com/ccleaner/version-history/
- Microsoft, solución de problemas de Storage Sense: https://learn.microsoft.com/es-es/troubleshoot/windows-server/backup-and-storage/troubleshoot-windows-server-storage-sense
- Python, documentación de shutil y operaciones de archivos: https://docs.python.org/3/library/shutil.html

## Patrones y cómo deben tratarse en BOTPYTON

| Patrón comunicado por el proveedor | Defensa/criterio para BOTPYTON |
| --- | --- |
| BleachBit corrigió un fallo de borrado arbitrario con limpieza elevada (CVE-2026-55567). | No confiar solo en una comprobación previa de ruta; validar límites y tipo de ruta, evitar enlaces/reparse points y probar colisiones. BOTPYTON no debe elevar privilegios para limpiar. |
| BleachBit corrigió problemas de enlaces simbólicos/junctions en limpieza de la papelera y después publicó correcciones de seguridad adicionales. | Las rutas de limpieza deben rechazar symlinks, junctions y reparse points. La auditoría debe incluir enlaces en cada componente de ruta y pruebas de carrera. |
| BleachBit publicó un aviso por carga insegura de archivos CleanerML desde una ubicación escribible por el usuario, y 6.0.3 documenta acciones de comandos/registro desde reglas no confiables. | No ejecutar scripts, XML de acciones ni configuraciones de limpieza como código. Los datos de tareas deben seguir siendo datos; cualquier automatización debe usar acciones fijas y allowlists. |
| BleachBit 6.0.3 corrigió operaciones que seguían symlinks/reparse points, parsing XML/URI inseguro y búsqueda de DLL/ejecutables. | Rechazar reparse points en cada componente de las rutas que BOTPYTON modifica; no confiar en que la ruta final parezca segura. Los procesos deben usar ejecutables permitidos, argumentos separados y `shell=False`; cualquier futuro descargador/parser requerirá validación de esquema, HTTPS y límites explícitos. |
| CCleaner documentó fallos de arranque del servicio, cierres inesperados, problemas con WebView2 y comunicación con el servicio. | La interfaz debe mostrar fallo explícito, no un falso estado de éxito; aislar etapas, limitar tiempos de espera y no depender de servicios residentes para la función básica bajo demanda. |
| Microsoft documenta Storage Sense ausente/inactivo por configuración, versión, tarea programada, políticas, archivos abiertos o interferencia de otras herramientas. | Diagnosticar antes de reparar: distinguir no compatible, no configurado, bloqueado, acceso denegado y timeout. No cambiar políticas, registro ni tareas programadas automáticamente. |
| Los inventarios del sistema pueden exceder límites de tiempo en entornos con muchos dispositivos o proveedores lentos. | Mantener límites estrictos; informar claramente que el resultado está incompleto y que no se hicieron cambios. No convertir un timeout en una lista vacía presentada como éxito. |

## Defensas ya verificadas en el código de BOTPYTON

- La cuarentena de caché está separada de la vista previa de solo lectura y requiere una frase de confirmación explícita.
- La cuarentena crea el nombre de destino de forma exclusiva antes de retirar el original; una colisión no debe sobrescribir datos existentes.
- El plan de cambios locales rechaza componentes de ruta que sean enlaces simbólicos o puntos de reanálisis de Windows, además de validar hashes antes de modificar archivos.
- Las rutas de caché reconocidas se filtran mediante reglas específicas y verificaciones contra enlaces/reparse points.
- El inventario de controladores solo consulta metadatos locales: no descarga, instala, actualiza ni elimina controladores.
- Las operaciones de inventario tienen límites temporales y devuelven estados explícitos. Un timeout significa que no se obtuvo un resultado utilizable, no que el sistema esté sano.

## Estado de CI y ajuste de prueba

La ejecución Windows asociada a la revisión del 2026-10-17 mostró que `Win32_PnPSignedDriver` puede exceder los 15 segundos en el runner de GitHub. El programa devolvió `status=timeout`, código distinto de cero, y declaró que no hizo cambios. La prueba de humo exigía solo éxito o resultado parcial con código 2, por lo que marcó el trabajo como fallido incluso ante ese timeout correctamente declarado.

El ajuste del workflow acepta ese caso **solo** cuando se cumplen a la vez `status=timeout`, `read_only=true` y `network_used=false`. No cambia el comportamiento del programa ni oculta errores de otros estados. El timeout sigue siendo un resultado incompleto, no un inventario exitoso.

## Próximas auditorías prioritarias

1. Carreras de tiempo entre validar y usar rutas; probar reparse points/junctions anidados y sustitución concurrente entre validación y escritura. La comprobación por componentes reduce la exposición, pero no elimina por sí sola toda carrera TOCTOU.
2. Restauración: integridad, colisiones, permisos, archivos abiertos y manifiestos manipulados.
3. Estados de error de la GUI: ninguna excepción o código de salida distinto de cero debe verse como éxito.
4. Memoria y duración: escaneos acotados, secuenciales y sin procesos residentes.
5. Acciones de mantenimiento: no instalar/actualizar drivers, cambiar registro, borrar descargas ni alterar inicio automático sin una función explícita y confirmación informada.
6. Pruebas de Windows que distingan fallos reales del producto de límites ambientales conocidos, sin rebajar las pruebas de seguridad.

## Alcance

Este documento es una bitácora de investigación y criterios de diseño, no una certificación de seguridad completa. CI en GitHub no sustituye una prueba en el PC del usuario. Las referencias y prioridades deben actualizarse cuando se revisen nuevas notas de versiones y avisos.


## Auditoría adicional — 2026-10-17: colisión concurrente en el registro de auditoría

### Fuentes oficiales consultadas

- BleachBit 6.0.3 beta, errores de carrera al guardar ajustes, enlaces/reparse points, entorno heredado en procesos, URI/SQL, XML y rutas de actualización inseguras: https://www.bleachbit.org/news/bleachbit-603-beta
- CCleaner, historial de versiones: errores de escritura de archivos, cierres durante tareas de fondo, actualización de runtime y exclusiones que no se respetaban en limpiezas programadas: https://www.ccleaner.com/ccleaner/version-history/
- Python, documentación de `shutil.rmtree`: algunas plataformas no tienen una implementación resistente a ataques por enlaces simbólicos; el comportamiento debe comprobarse y no suponerse: https://docs.python.org/3/library/shutil.html

### Hallazgo concreto en BOTPYTON

En `bot/pc_care.py`, `_persist_pc_care_audit_event()` comprobaba que el nombre final no existiera y después usaba `os.replace()`. Entre ambas operaciones, otro proceso podía crear ese nombre y el reemplazo podía sobrescribir el archivo que apareció durante la carrera. Aunque los nombres contienen un identificador temporal, la comprobación previa no garantizaba exclusividad.

**Corrección aplicada en la rama candidata:** el evento se publica mediante creación de hard link exclusiva (`os.link`) desde un archivo temporal del mismo directorio. Si el destino aparece concurrentemente, la operación falla en vez de reemplazarlo; el temporal se limpia incluso ante error. Es un registro inmutable, por lo que no necesita semántica de reemplazo. Si el sistema de archivos no admite hard links, la escritura falla de forma explícita y no recurre a un reemplazo inseguro.

**Prueba de regresión añadida:** simula que otro proceso crea el destino después de la comprobación inicial y comprueba que los bytes preexistentes se conservan y el archivo temporal se elimina.

### Distinción importante

Los puntos sobre variables de entorno heredadas, XML/URI y DLL search path de BleachBit se registran como controles preventivos a mantener en futuras funciones; no se afirma que BOTPYTON reproduzca esos fallos. BOTPYTON no debe añadir reglas de limpieza descargables ni ejecutar definiciones de limpieza suministradas por usuarios como código. Los procesos ya auditados usan `shell=False`; las futuras llamadas deben conservar ejecutables explícitos, argumentos separados y límites temporales.

Esta corrección aún requiere que pasen las pruebas de CI de la rama y las pruebas posteriores a la integración en `main`. La CI no equivale a una prueba en el PC del usuario.


## Auditoría adicional — 2026-10-17: publicación exclusiva y preservación de cambios concurrentes

### Notas oficiales revisadas

- BleachBit 6.0.2 documenta una corrección de seguridad para evitar que la limpieza con privilegios elevados pudiera borrar archivos arbitrarios, además de cambios de robustez para enlaces simbólicos: https://www.bleachbit.org/news/bleachbit-602
- El aviso GHSA-q5ww-c3j6-8j23 explica que una definición de limpieza maliciosa colocada en una ubicación escribible podía ejecutar comandos en versiones anteriores a BleachBit 6.0.3: https://github.com/bleachbit/bleachbit/security/advisories/GHSA-q5ww-c3j6-8j23
- El historial oficial de CCleaner registra una corrección de seguridad relacionada con escritura de archivos, fallos de sincronización al cerrar mientras existen tareas en curso y un arreglo para que las limpiezas programadas respeten las exclusiones: https://www.ccleaner.com/ccleaner/version-history/
- Python documenta que la resistencia de operaciones recursivas frente a enlaces simbólicos varía según la plataforma y la implementación: https://docs.python.org/3/library/shutil.html

### Hallazgo concreto confirmado en BOTPYTON

En `bot/local_changes.py`, el plan de cambios locales validaba que un archivo nuevo no existiera y después `_write_atomic()` publicaba el temporal mediante `os.replace()`. Si otro proceso creaba el destino entre ambas operaciones, el reemplazo podía sobrescribir el archivo que acababa de aparecer. Además, la ruta de copias de seguridad era determinista y la escritura atómica podía reemplazar una copia previa sin comprobar que sus bytes correspondieran al original esperado.

### Correcciones añadidas en la rama candidata

- Los archivos **nuevos** se publican con creación exclusiva mediante `os.link()`: si ya existe el destino, la operación falla y conserva el contenido existente; no se degrada a `os.replace()`.
- Los archivos de respaldo ya existentes se aceptan únicamente cuando son archivos regulares, no contienen enlaces/reparse points y su SHA-256 coincide con los bytes originales que se quieren respaldar. Un respaldo diferente se conserva y bloquea el plan.
- Antes de escribir se rechazan componentes existentes que sean enlaces simbólicos o puntos de reanálisis de Windows en la raíz del proyecto, la ruta de respaldo y los destinos.
- Para reemplazar un archivo existente, la publicación vuelve a comprobar el hash esperado tras preparar el temporal. Si el contenido cambió, aborta.
- Si una operación posterior falla, el rollback verifica que cada destino aún contiene los bytes escritos por BOTPYTON antes de restaurar o retirar ese archivo. Un cambio externo concurrente no debe borrarse silenciosamente durante el rollback.
- Se agregaron pruebas de regresión para colisión en la creación de un archivo nuevo, respaldo previo con contenido diferente, cambio concurrente de un archivo existente y raíz marcada como reparse point.

### Límite explícito

Las operaciones portables de rutas de Python no proporcionan una garantía universal de compare-and-swap de archivo y directorio contra un proceso hostil que compita exactamente en el último instante. Las comprobaciones cercanas a publicación reducen la ventana y las creaciones exclusivas impiden reemplazar destinos nuevos; no deben describirse como una solución matemática a toda carrera TOCTOU. BOTPYTON debe seguir rechazando enlaces/reparse points y abortar cuando detecta una discrepancia.

Estas son correcciones locales, sin servicios residentes, llamadas de IA, nuevas dependencias ni limpieza automática no solicitada. La integración queda condicionada a pruebas de CI; esa CI tampoco sustituye una prueba en el PC del usuario.


## Auditoría adicional — 2026-10-17: puntos de reanálisis al preparar el paquete portable

### Documentación oficial contrastada

- BleachBit publicó correcciones para no seguir symlinks/junctions durante operaciones de limpieza y en BleachBit 6.0.3 beta documenta endurecimiento de seguridad para rutas, enlaces y puntos de reanálisis: https://github.com/bleachbit/bleachbit/releases y https://www.bleachbit.org/news/bleachbit-603-beta
- La documentación de Python distingue los enlaces simbólicos de las junctions de Windows. `Path.is_junction()` existe desde Python 3.12, y `os.walk()` documenta su control de seguimiento de enlaces simbólicos; por eso una implementación que usa solo `is_symlink()` no debe asumir que cubre todos los puntos de reanálisis: https://docs.python.org/3.12/library/pathlib.html y https://docs.python.org/3.12/library/os.html
- CCleaner 7.12.1526, publicada el 29 de septiembre de 2026, documenta tanto una corrección de seguridad relacionada con escritura de archivos como arreglos de sincronización al cerrar la aplicación: https://www.ccleaner.com/ccleaner/version-history/

### Riesgo de código localizado

En `bot/packaging.py`, el recolector del paquete revisaba `Path.is_symlink()` en directorios enumerados por `os.walk()` y en los archivos de la lista fija. En Windows, una junction u otro punto de reanálisis no debe tratarse como si fuera siempre un symlink. Una junction dentro de los árboles `bot`, `docs` o `.cerebro/examples`, o en un componente padre de un archivo requerido, podría hacer que el generador inspeccionara datos que están fuera del árbol del repositorio previsto. No hay evidencia de que la rama `main` contuviera actualmente una junction maliciosa; se trata de un hueco de validación confirmado en el método de enumeración.

### Corrección candidata

- Reutiliza el detector compartido de BOTPYTON que comprueba el atributo de reparse point de Windows, además de los enlaces simbólicos.
- Comprueba la cadena de componentes entre cada archivo/directorio seleccionado y la raíz permitida, incluidas las carpetas padre de archivos fijos.
- Poda los puntos de reanálisis durante el recorrido, antes de que `os.walk()` pueda descender a un árbol enlazado.
- Rechaza también que la ruta inicial de origen del paquete atraviese un enlace o punto de reanálisis.
- Añade pruebas para simular un punto de reanálisis en un subdirectorio y un componente padre; añade además una prueba de integración que crea una junction real en Windows cuando el runner lo permite y se salta de forma explícita si no puede crearla.

La solución no agrega dependencias ni modifica el mantenimiento del PC. Se limita al generador del ZIP y a evitar que archivos externos al árbol aprobado entren accidentalmente en el paquete. El PR debe pasar tanto las pruebas base como el humo de Windows antes de fusionarse; la prueba con junction real es especialmente importante porque CI Linux no puede reproducir el comportamiento de reparse points de Windows.


## Auditoría adicional — 2026-10-17: movimiento atómico de la cuarentena de caché

### Referencias oficiales y motivo

- BleachBit 6.0.3 beta documenta varias correcciones relacionadas con operaciones de archivos que seguían symlinks o puntos de reanálisis al borrar, truncar o sobrescribir archivos. La lección pertinente para BOTPYTON es que las validaciones de ruta deben mantenerse seguras hasta la operación que modifica el sistema de archivos: https://www.bleachbit.org/news/bleachbit-603-beta
- CCleaner 7.12.1526 documenta una corrección interna de seguridad en escrituras de archivos y un problema de sincronización durante el cierre. No es el mismo defecto de BOTPYTON, pero refuerza la necesidad de reducir las ventanas entre validación y mutación: https://www.ccleaner.com/ccleaner/version-history/
- La documentación oficial de Python especifica que, en Windows, `os.rename()` falla con `FileExistsError` si el destino ya existe, mientras que en Unix puede reemplazar un archivo de destino. La solución debe aprovechar este comportamiento solo en Windows y no presentarlo como una garantía multiplataforma: https://docs.python.org/3/library/os.html#os.rename y https://docs.python.org/3/library/pathlib.html#pathlib.Path.rename

### Riesgo localizado

La cuarentena y la restauración utilizaban dos operaciones consecutivas: crear un hard link en el destino y después eliminar el nombre de origen. Si un proceso reemplazaba la entrada del origen entre esas llamadas, el hard link podía conservar el archivo que BOTPYTON había validado, pero `unlink()` podía quitar del nombre original una entrada distinta creada concurrentemente. No hay evidencia de que esto hubiera ocurrido en el equipo del usuario; es una ventana de carrera en el diseño.

### Corrección candidata

- En Windows, usar `Path.rename()` como operación única para mover el nombre de archivo. En Windows falla cuando el destino ya existe; no se usa `os.replace()` ni una operación de movimiento que pueda sobrescribir el destino.
- Aplicar la misma operación a cuarentena y restauración. Si el navegador recrea la ruta destino antes de la restauración, la operación debe fallar y dejar intactos tanto el archivo nuevo como la copia verificada en cuarentena.
- Mantener el camino hard-link/unlink exclusivo únicamente para los tests que simulan APIs de Windows sobre un host no Windows. El runtime de cuarentena sigue siendo Windows-only.
- Ampliar las pruebas de colisión para insertar un destino entre la comprobación previa y la llamada de movimiento. La prueba de carrera nativa requiere Windows; no se considerará demostrada por el test sintético de Linux.

La operación atómica puede fallar si origen y destino están en sistemas de archivos diferentes; el fallo se reporta y no se hace un fallback con copia y borrado, para no debilitar la garantía de no sobrescritura. Sin nuevas dependencias, servicios residentes, llamadas de IA ni cambios de limpieza automática.


## Auditoría adicional — 2026-10-17: aislamiento de variables de entorno de carga de código

### Referencias oficiales revisadas

- BleachBit 6.0.3 beta documenta la sanitización de `PATH` y la eliminación de variables de entorno que cargan código para procesos secundarios en contextos privilegiados; 6.0.4 también registra endurecimiento de búsquedas de DLL y mejoras de seguridad de subprocesos: https://www.bleachbit.org/news/bleachbit-603-beta y https://www.bleachbit.org/news/bleachbit-604
- La documentación oficial de Node.js explica que `NODE_OPTIONS` se procesa antes de las opciones de línea de comandos y admite opciones de carga como `--require`: https://nodejs.org/api/cli.html#node_optionsoptions
- La documentación de Python aclara que `subprocess.Popen(..., env=...)` utiliza el mapa de entorno entregado para el nuevo proceso, en lugar de heredar automáticamente el entorno predeterminado: https://docs.python.org/3/library/subprocess.html

### Hallazgo concreto en BOTPYTON

`bot/process_runner.py` filtraba nombres de entorno que parecían contener secretos, pero heredaba opciones de carga de código como `PYTHONPATH`, `NODE_OPTIONS`, `JAVA_TOOL_OPTIONS` y variables de pre-carga de bibliotecas. Así, una llamada diseñada para consultar la versión de una herramienta podía verse afectada por opciones externas antes de procesar los argumentos fijos. Por ejemplo, Node.js procesa `NODE_OPTIONS` antes de las opciones explícitas; `--require` puede cargar un módulo anticipadamente. No se afirma que una ejecución maliciosa haya ocurrido en el equipo del usuario.

### Corrección candidata

- Filtrar, sin distinguir mayúsculas/minúsculas, un conjunto explícito de variables de arranque/carga de código para Python, Node.js, Ruby, Perl, Java, .NET y cargadores nativos POSIX.
- Rechazar overrides explícitos que intenten volver a introducir cualquiera de esas variables, en lugar de aceptarlos silenciosamente.
- Añadir una prueba de integración que coloca un `sitecustomize.py` señuelo en una carpeta temporal mediante `PYTHONPATH` y comprueba que el proceso hijo no lo ejecuta.
- Añadir pruebas de rechazo para overrides de `PYTHONPATH`, `NODE_OPTIONS`, `JAVA_TOOL_OPTIONS` y `LD_PRELOAD`.

El cambio no modifica la selección de herramientas, no agrega dependencias y mantiene `shell=False`, el límite de memoria y los límites de tiempo/salida existentes. No es una defensa completa contra una herramienta instalada deliberadamente de forma maliciosa ni sustituye la validación de su ejecutable; aísla una clase concreta de inyección por entorno heredado. La corrección requiere pasar CI y pruebas de Windows antes de integrar en `main`.


## Auditoría adicional — 2026-10-17: búsqueda de ejecutables y binary planting

### Referencias oficiales

- BleachBit 6.0.4 documenta que cambió a rutas absolutas para `ipconfig` y `taskkill` para prevenir ataques de binary planting a través de la búsqueda de ejecutables. También documenta sanitización de `PATH` y eliminación de variables de carga de código en subprocesos POSIX: https://www.bleachbit.org/news/bleachbit-604
- Python advierte que el uso de una ruta absoluta para el ejecutable ofrece mayor fiabilidad que entregar un nombre sin calificar. La documentación de `shutil.which()` explica que la búsqueda de Windows puede anteponer el directorio actual dependiendo de la API y del entorno: https://docs.python.org/3/library/subprocess.html y https://docs.python.org/3/library/shutil.html#shutil.which
- Microsoft explica el riesgo general de binary planting cuando un programa carga ejecutables/DLL desde directorios controlables en una ruta de búsqueda: https://learn.microsoft.com/en-us/windows/win32/dlls/dynamic-link-library-security

### Hallazgo confirmado en BOTPYTON

`_resolve_approved_executable()` resolvía tanto el ejecutable solicitado como los elementos de su allowlist mediante `shutil.which()`. Si el solicitante y la allowlist usaban el mismo nombre no calificado, ambos podían resolverse al mismo ejecutable del `PATH`, haciendo que la comprobación fuese circular. Además, `run_tool_probe()` buscaba nombres registrados sin suministrar un `PATH` explícito y, por tanto, dependía de las reglas implícitas de búsqueda de la plataforma. Esto podía permitir que un ejecutable con el mismo nombre en el directorio actual u otra entrada inesperada se seleccionara para una consulta de versión. No hay evidencia de que se haya ejecutado un archivo malicioso en el PC del usuario.

### Corrección candidata

- Requerir una ruta absoluta para el ejecutable que se va a ejecutar y para cada elemento de la allowlist; la validación no busca ni resuelve nombres mediante el mismo `PATH`.
- Buscar las herramientas registradas únicamente en directorios absolutos existentes del `PATH`, excluyendo entradas vacías, relativas, duplicadas y el propio directorio de trabajo. La búsqueda verifica archivos candidatos directamente para que no active la búsqueda implícita del directorio actual.
- Sanear también el `PATH` entregado a los procesos secundarios, de modo que estos no vuelvan a resolver ejecutables desde el directorio de trabajo; en Windows establecer `NoDefaultCurrentDirectoryInExePath=1`.
- Añadir pruebas que rechacen una allowlist no calificada, simulen un ejecutable falso en la carpeta actual y verifiquen que el proceso secundario recibe un `PATH` saneado.

Este cambio reduce el riesgo de binary planting desde el directorio de trabajo y hace que la allowlist sea una comparación de rutas absolutas. No puede determinar por sí solo si una carpeta absoluta del `PATH` fue comprometida o si un ejecutable instalado es malicioso; las rutas de herramientas de confianza deben seguir siendo mantenidas por el usuario/sistema. No añade dependencias ni cambia el ámbito de limpieza. La integración requiere CI general y Windows.

## Phase 17 — Actualización de GitHub Actions a correcciones upstream (2026-10-17)

### Hallazgo

Los cuatro workflows utilizaban referencias inmutables, pero estaban fijados a revisiones antiguas: checkout v4.2.2, setup-python v5.6.0 y upload-artifact v4.6.2. Las notas oficiales anunciaron correcciones posteriores. Esto es deuda de mantenimiento del CI, no evidencia de una vulnerabilidad explotable en BOTPYTON.

### Cambios aplicados

En los cuatro workflows se actualizó:

- `actions/checkout` a v7.0.1 — SHA `3d3c42e5aac5ba805825da76410c181273ba90b1`.
- `actions/setup-python` a v7.0.0 — SHA `5fda3b95a4ea91299a34e894583c3862153e4b97`.

En `.github/workflows/bot-tests.yml` y `.github/workflows/bot-windows-smoke.yml`, también se actualizó:

- `actions/upload-artifact` a v7.0.2 — SHA `cf430e030ddbb5b0abf93d22962f4752f3646cd9`.

Se conserva `persist-credentials: false` en checkout y los permisos/condiciones de escritura existentes. Se añade `tests/test_workflow_action_pins.py` para exigir SHA completos de 40 caracteres para todas las acciones externas.

### Migración deliberadamente aplazada

El workflow `.github/workflows/botpyton-worker.yml` transporta checkpoints entre ejecuciones: usa conjuntamente `upload-artifact` y `download-artifact`, y `github-script` descubre artefactos de ejecuciones anteriores. Los dos jobs de CI que corren automáticamente en este PR prueban las nuevas versiones de checkout, setup-python y upload-artifact, pero no ejecutan el flujo manual completo del worker ni prueban la restauración desde un checkpoint real.

Por ese motivo, esta fase **no** migra las versiones de artefactos del worker ni `github-script`: se conservan los SHA anteriormente usados y compatibles (`upload-artifact` v4.6.2 con `download-artifact` v4, y `github-script` v7.0.1). No afirmo que esas migraciones estén verificadas. Deben actualizarse en una fase separada cuando exista una ejecución end-to-end del ciclo anterior run → checkpoint → siguiente run → restauración.

### Motivación upstream y fuentes oficiales

- Checkout v7.0.1 incorpora protecciones adicionales para ciertos checkouts inseguros de PRs y corrige el manejo de valores pasados a `git --unset`.
- setup-python v7.0.0 actualiza dependencias y valida/reintenta la descarga de manifiestos para reducir fallos silenciosos.
- upload-artifact v7.0.2 mejora los reintentos ante HTTP 429 y respeta encabezados `Retry-After` válidos.

Referencias:
- https://github.com/actions/checkout/releases
- https://github.com/actions/setup-python/releases
- https://github.com/actions/upload-artifact/releases
- https://github.com/actions/download-artifact/releases
- https://github.com/actions/github-script/releases
- https://docs.github.com/en/actions/reference/security/secure-use

Las acciones nuevas usan Node.js 24 y requieren un runner compatible; los workflows regulares del proyecto usan runners hospedados por GitHub. La ejecución del PR verificará su funcionamiento en Linux y Windows.

No cambia el código de limpieza local ni se agregan dependencias de runtime, servicios residentes, llamadas de IA o acciones del sistema operativo.

## Phase 18 — Excluir subdirectorios del proyecto del PATH de procesos secundarios (2026-10-17)

### Hallazgo confirmado

La fase anterior saneaba `PATH` para eliminar la carpeta de trabajo exacta, entradas relativas/vacías y duplicados. Sin embargo, una entrada absoluta como `<proyecto>\\tools` seguía aceptándose aunque fuera un subdirectorio de la carpeta donde se ejecuta el proceso. Si un ejecutable aprobado invoca posteriormente otra herramienta por nombre, esa subcarpeta podría contener un ejecutable auxiliar no confiable que el proceso secundario resolviera desde `PATH`. No hay evidencia de explotación en el equipo del usuario; el defecto era una laguna en el límite que la función de saneamiento pretendía imponer.

Las notas oficiales de BleachBit 6.0.4 describen el uso de rutas absolutas para `ipconfig` y `taskkill` para prevenir binary planting mediante la búsqueda de ejecutables, así como saneamiento de `PATH` para procesos externos. Microsoft explica que `PATH` define los directorios de búsqueda de ejecutables y advierte que un directorio actual/directorio de búsqueda controlable puede introducir riesgos de planting. Son referencias para orientar la defensa; la condición reproducible específica se encontró en `bot/process_runner.py`.

### Corrección

- La lista saneada descarta una entrada si su ruta absoluta normalizada está dentro del directorio de trabajo, no solo cuando coincide exactamente con él.
- También descarta alias de symlink/junction ubicados léxicamente bajo el directorio de trabajo, incluso si su destino resuelto apunta fuera; además, rechaza entradas externas cuyo destino canónico vuelva a caer dentro del árbol de trabajo.
- La comprobación usa rutas normalizadas y compara el límite con un separador de directorio incluido, evitando que un prefijo vecino como `project-tools` se confunda con `project`; se evalúa tanto la ruta léxica como la ruta canónica resuelta y se examinan los antecesores existentes para detectar alias cortos de Windows (8.3) como `RUNNER~1`.
- Se agregaron regresiones para subdirectorios de herramientas, alias simbólicos y el `PATH` observado por un proceso secundario.

### Fuentes oficiales

- BleachBit 6.0.4, notas oficiales — defensa contra binary planting y saneamiento de PATH: https://www.bleachbit.org/news/bleachbit-604
- Microsoft Learn, comando PATH — define los directorios de búsqueda de ejecutables: https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/path
- Python, `shutil.which()` — especifica cómo se consideran PATH y directorio actual en Windows: https://docs.python.org/3/library/shutil.html

La corrección afecta solo al entorno de los procesos secundarios; no cambia las acciones de limpieza del PC, no instala dependencias y no añade servicios residentes. Requiere suite completa y smoke de Windows antes de integrarse.

## Phase 19 — Limitar el consumo de memoria al restaurar checkpoints JSON (2026-10-17)

### Hallazgo confirmado

`restore_checkpoint()` verificaba la huella SHA-256 y validaba el esquema de la cola, pero primero cargaba con `read_text()` los dos archivos completos y los pasaba al decodificador JSON de Python sin un límite de bytes. Un artefacto de checkpoint demasiado grande o un JSON profundamente anidado podía consumir recursos excesivos o terminar con una excepción de recursión sin convertirse en un error de checkpoint controlado. La comprobación de hash no es un límite de tamaño: solo verifica si los bytes leídos coinciden con el digest que figura en el manifiesto.

No hay evidencia de que se haya producido una condición de agotamiento de memoria en este proyecto; es una vía de entrada reproducible que carecía de límites explícitos y contradecía los objetivos de consumo reducido del programa.

### Corrección aplicada

- Se define un máximo de 4 MiB para `state.json` y 32 KiB para `manifest.json`.
- Antes de parsear se comprueba que cada archivo sea regular, no un enlace/reparse point, y que su tamaño esté dentro del límite.
- La lectura usa un límite duro de `cap + 1` bytes y compara la identidad del archivo abierto con la ruta antes y después, para detectar sustituciones durante la lectura.
- Después de la lectura acotada, se normalizan `CRLF`/`CR` a `LF` antes de verificar el hash, conservando la compatibilidad entre checkpoints creados en Windows y Linux; se incluye una regresión específica para saltos de línea `CRLF`.
- Los archivos que exceden el límite se rechazan antes del parseo; se rechaza cualquier JSON cuya profundidad estructural supere 64 niveles; las excepciones de recursión del decodificador/serializador también se convierten en `CheckpointError` controlado.
- La exportación también rechaza un estado que exceda el límite admitido, evitando producir un checkpoint que la restauración no pueda aceptar.
- Se añadieron pruebas para estado excesivo, manifiesto excesivo, JSON profundamente anidado y archivo de estado simbólico. Las pruebas verifican que una restauración fallida no sobrescriba la memoria existente.

### Fuentes oficiales

- Documentación de Python, módulo `json`: advierte que JSON malicioso puede consumir mucha CPU/memoria y recomienda limitar el tamaño antes de parsear: https://docs.python.org/3.12/library/json.html
- OWASP Input Validation Cheat Sheet: aplicar límites de tamaño antes de almacenar o parsear y configurar límites del parser para evitar agotamiento de recursos: https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html
- MITRE CWE-789: asignaciones de memoria excesivas por entradas sin límites, mitigadas validando cantidades y limitando recursos: https://cwe.mitre.org/data/definitions/789.html

El cambio no agrega dependencias ni servicios. Mantiene intactos el esquema del checkpoint y el flujo de tareas válidas; añade límites explícitos para restauración y exportación que se pueden verificar con CI.


## Phase 20 — Límites efectivos al leer JSON local y memoria persistente (2026-10-17)

### Referencias oficiales revisadas

- La documentación oficial de Python advierte que analizar JSON sin límites puede consumir cantidades considerables de CPU y memoria; recomienda limitar el tamaño de los datos y señala que la biblioteca estándar no establece por sí sola un límite general de anidamiento: https://docs.python.org/3/library/json.html
- OWASP enumera los límites de tamaño total y la prevención de asignaciones impulsadas por entradas como defensas frente al agotamiento de recursos: https://cheatsheetseries.owasp.org/cheatsheets/Denial_of_Service_Cheat_Sheet.html
- La documentación de pathlib describe `read_bytes()` y `read_text()` como operaciones que devuelven el contenido del archivo completo, mientras `Path.open()` permite una lectura incremental y acotada: https://docs.python.org/3/library/pathlib.html

### Hallazgo concreto

Las funciones `bot/main.py::_read_bounded_json` y `bot/local_changes.py::read_local_plan` consultaban primero `stat().st_size`, pero luego utilizaban `read_bytes()`, que lee el archivo completo. El segundo control de longitud se ejecutaba después de la asignación. Si un archivo crecía o era reemplazado entre ambas operaciones, el tamaño de lectura real podía exceder el límite pretendido antes de que BOTPYTON lo rechazara. El diagnóstico de logs también leía el texto completo tras una comprobación de tamaño previa. Además, `JsonMemory.load()` leía la memoria persistente completa sin límite de bytes ni de profundidad JSON; esta función se usa habitualmente al consultar o modificar la cola de tareas.

No hay evidencia de explotación en el equipo del usuario. Es un hueco de robustez y control de recursos, relevante para un proceso que debe funcionar con memoria limitada.

### Corrección implementada en la rama candidata

- Se añade `bot/bounded_io.py`: abre archivos regulares, compara identidad y metadatos antes/después de la apertura, lee como máximo `límite + 1` bytes y falla explícitamente si detecta crecimiento o cambio durante la lectura.
- El CLI, los planes de cambio local y la lectura de logs de diagnóstico emplean ahora la lectura acotada, incluso si cambia el tamaño entre la comprobación previa y la lectura.
- La entrada JSON acepta una profundidad máxima de 64 niveles y transforma JSON inválido, demasiado profundo o cambiante en errores controlados; el recorrido de profundidad usa una pila de iteradores para evitar crear una gran lista auxiliar.
- La memoria persistente se limita a 4 MiB, alineada con el tamaño máximo actual de checkpoint, y a 64 niveles JSON. La escritura también verifica el límite en streaming antes de reemplazar el archivo original, de modo que un guardado sobredimensionado falla sin destruir la última memoria válida.
- Se agregan pruebas para archivos sobredimensionados, una discrepancia simulada entre `stat` y la apertura, JSON profundo y preservación de la memoria anterior cuando el guardado supera el límite.

### Consideración operativa

El archivo de memoria conserva las tareas históricas; al alcanzar 4 MiB, BOTPYTON informa un error en lugar de seguir creciendo indefinidamente o consumir memoria sin límite. Antes de mantener una cola histórica de mayor tamaño, será necesario diseñar una política explícita de archivo/retención compatible con las dependencias entre tareas. No se borra historial automáticamente ni se agregan llamadas de red, IA, servicios residentes o dependencias externas.

Esta fase queda sujeta a las pruebas de CI Linux y Windows de la rama candidata y a la verificación posterior a la fusión en `main`.


## Phase 21 — Evitar la doble reclamación de tareas por procesos concurrentes (2026-10-17)

### Referencias externas revisadas

- OWASP describe las condiciones de carrera como accesos concurrentes al mismo estado compartido sin coordinación, que pueden producir colisiones e inconsistencias: https://community.owasp.org/pages/vulnerabilities/race_conditions
- Microsoft documenta los bloqueos exclusivos por rangos de bytes y su uso para evitar que varias aplicaciones sobrescriban el trabajo de la otra: https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex
- La documentación oficial de Python documenta `fcntl.flock` para bloqueos asesorados de archivos en sistemas Unix: https://docs.python.org/3/library/fcntl.html
- SQLite explica que las operaciones transaccionales serializan escrituras para evitar que dos clientes publiquen cambios incompatibles: https://www.sqlite.org/isolation.html

### Hallazgo

La cola de BOTPYTON guardaba cada cambio mediante reemplazo atómico del JSON, pero las secuencias completas de leer → comprobar dependencias → cambiar estado → guardar no tenían bloqueo transaccional. Dos instancias del worker podían leer la misma tarea como `pending` a la vez, reclamarla ambas y ejecutar dos veces una acción que debía ocurrir una sola vez. El reemplazo atómico evitaba archivos JSON a medio escribir, pero no evitaba que un proceso sobrescribiera los cambios del otro.

Es un riesgo de concurrencia del diseño, no evidencia de que una tarea se haya ejecutado doble en el equipo del usuario.

### Corrección

- Añadido `bot/file_lock.py` con bloqueo asesor entre procesos para Unix y Windows y un bloqueo reentrante por hilo para las llamadas anidadas de `JsonMemory`.
- El bloqueo utiliza un archivo lateral estable junto al archivo de memoria y no lo elimina al salir, para no dividir a otros procesos entre distintos archivos/inodos de bloqueo.
- `JsonMemory.load()` y `save()` toman el bloqueo para el acceso individual; `TaskQueue` lo mantiene durante toda la mutación de la cola: `enqueue_many`, `claim_next`, `fail`, finalización y recuperación.
- `claim_next` ya no usa recursión para saltar registros con intentos agotados; los marca como fallidos por lotes y vuelve a evaluar dependencias.
- Nueva prueba de integración con dos procesos independientes iniciados a la vez: sólo uno debe reclamar la única tarea disponible; el otro debe recibir “sin tarea disponible”, y el contador de intentos debe quedar en 1.

### Alcance y límites

Este bloqueo coordina instancias de BOTPYTON que usan la misma ruta local de memoria y comparten un sistema de archivos que respeta los bloqueos asesorados. No convierte el JSON en una base de datos distribuida y no protege contra programas externos que deliberadamente ignoren el bloqueo lateral. No se agregan servicios en segundo plano, llamadas de red, dependencias externas ni borrado automático de historial.

La fase queda sujeta a la suite general y a la prueba de Windows antes de integrarse en `main`.
