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
