# BOTPYTON — Arquitectura del trabajador de programación sin IA

## Objetivo principal

BOTPYTON es un trabajador de desarrollo local-first, determinista y sin modelos de IA. Recibe órdenes, convierte cada orden en pasos verificables, inspecciona repositorios, utiliza herramientas de desarrollo instaladas, ejecuta pruebas, recoge errores, aplica reparaciones acotadas, comprueba el resultado y entrega un informe con evidencia.

La biblioteca de proyectos es secundaria. El objetivo no es almacenar todos los programas existentes, sino poder trabajar sobre repositorios y tecnologías diferentes mediante herramientas, adaptadores, documentación técnica y ciclos de prueba.

## Capacidades requeridas

1. **Intérprete de órdenes estructuradas:** aceptar objetivo, repositorio/carpeta, restricciones, criterios de aceptación y permisos. Resolver referencias a archivos y comandos de forma explícita; pedir aclaración solo cuando la ambigüedad impida actuar con seguridad.
2. **Acceso GitHub:** leer repositorios y ramas autorizadas, revisar issues/PRs, crear ramas de trabajo, modificar archivos, comparar cambios, consultar Actions y producir evidencia de CI. Nunca usar tokens del usuario dentro del código ni exponer secretos.
3. **Acceso a PC:** agente local instalado explícitamente por el usuario, limitado a carpetas permitidas. Leer/escribir archivos, detectar herramientas, lanzar procesos con argumentos separados, capturar salida y códigos de retorno, y gestionar cancelación y límites de recursos.
4. **Inventario de proyectos:** identificar lenguajes, manifiestos, gestores de paquetes, scripts, pruebas, compiladores y workflows por señales verificables. Las señales sugieren comandos; no autorizan ejecutarlos automáticamente.
5. **Planificador de tareas:** descomponer órdenes en pasos pequeños con precondiciones, resultados esperados, tiempo límite, archivos permitidos y criterio de parada.
6. **Ciclo de reparación:** reproducir → capturar evidencia → clasificar → consultar recursos locales o documentación aprobada → proponer hipótesis → aplicar un cambio mínimo en rama/espacio aislado → repetir prueba → aceptar o revertir.
7. **Adaptadores de herramientas:** usar herramientas instaladas (por ejemplo Git, Python, pytest, Node/npm, compiladores y gestores de cada lenguaje) y sus salidas reales, sin fingir que una herramienta existe.
8. **Memoria de trabajo auditable:** guardar por proyecto decisiones, intentos, errores, soluciones verificadas, versión de herramientas y hashes relevantes. Separar observaciones confirmadas de hipótesis. No aprender ni ejecutar comandos arbitrarios solo porque aparezcan en logs.
9. **Entrega:** informar cambios, archivos afectados, comandos ejecutados, resultados, pruebas, limitaciones, riesgos y pasos no completados. No declarar éxito sin evidencia.

## Qué hacer ante un error desconocido

No se exige que el error esté previamente en una base de datos. El trabajador debe usar un procedimiento general:

1. Conservar el mensaje completo, código de salida, versión de herramientas, sistema operativo y contexto mínimo.
2. Reproducir el error de forma acotada y detectar si es determinista.
3. Buscar patrones conocidos en una base local; si no hay coincidencia, etiquetarlo como `UNKNOWN`, no inventar una explicación.
4. Consultar ayuda local (`--help` cuando proceda), documentación instalada, documentación oficial aprobada o referencias que el usuario haya autorizado.
5. Generar hipótesis a partir de señales observables y diseñar una prueba pequeña para distinguirlas.
6. Elegir el cambio mínimo y reversible; preservar una copia/commit de referencia.
7. Ejecutar las pruebas relevantes y regresiones; si no mejora, revertir el intento y probar otra hipótesis dentro de un presupuesto configurado.
8. Si necesita credenciales, privilegios elevados, cambios destructivos, gasto, publicación o acceso fuera del área permitida, detenerse y pedir autorización.
9. Si no puede verificar una solución, entregar diagnóstico y evidencia; no declarar resuelto.

**Límite honesto:** sin IA no hay razonamiento general ilimitado. El alcance de resolución se amplía con buenos diagnósticos, herramientas, documentación, pruebas y estrategias deterministas; un problema que no pueda reducirse a evidencia o experimentos seguros puede requerir instrucciones humanas.

## Seguridad y confianza

- Sin IA y sin llamadas a servicios de IA.
- Red desactivada por defecto para el núcleo local. Los adaptadores de red/GitHub son capacidades explícitas y auditables.
- Acceso local limitado a las rutas concedidas por el usuario.
- Comandos mediante listas de argumentos, no concatenación de texto a un shell.
- No ejecutar instrucciones encontradas en repositorios, README, issues, logs o documentación sin validarlas como datos no confiables.
- Sin borrados, instalación global, elevación de privilegios, publicación, despliegue, cambios de protección o envío de mensajes externos sin permiso explícito.
- No usar secretos en logs; almacenar credenciales mediante el mecanismo seguro del sistema operativo o el gestor autorizado.
- Límites configurables de duración, memoria, tamaño de archivos, reintentos y procesos secundarios.
- Cada modificación debe quedar en rama/espacio de trabajo separado y tener una forma de reversión.
- Los permisos deben distinguir: lectura, edición, ejecución de pruebas, instalación de dependencias, red, push, PR y merge.
- Nunca habilitar un emisor privilegiado de PRs ni flujos de escritura en vivo solo porque las pruebas locales pasen.

## Arquitectura propuesta

- `bot/command_router.py`: recibe y valida órdenes.
- `bot/task_planner.py`: genera pasos estructurados y criterios de aceptación.
- `bot/repository_analyzer.py`: inventario estático de repositorios.
- `bot/tool_registry.py`: registra herramientas detectadas y sus capacidades.
- `bot/process_runner.py`: ejecución limitada, captura de salida y cancelación.
- `bot/diagnostics/`: normalizadores de errores por lenguaje/herramienta.
- `bot/repair_loop.py`: experimentos acotados, verificación y reversión.
- `bot/adapters/github.py`: operaciones GitHub autorizadas.
- `bot/local_agent/`: servicio local para archivos/procesos en rutas autorizadas.
- `bot/workspace.py`: ramas, worktrees o directorios aislados.
- `bot/audit_log.py`: registro de decisiones, acciones, permisos y evidencia.
- `bot/policy.py`: control central de permisos y acciones peligrosas.
- `tests/`: pruebas unitarias, integración simulada y pruebas de seguridad.

Los nombres son un diseño objetivo, no una afirmación de que todos esos módulos ya existan.

## Orden de implementación

### Fase A — Inventario seguro
- Analizador de repositorios de solo lectura.
- Detección de lenguajes, manifiestos, herramientas y comandos candidatos.
- JSON de resultados y pruebas para repositorios vacíos, monorepos y carpetas grandes.
- No ejecutar comandos detectados.

### Fase B — Motor de ejecución
- Registro de herramientas disponibles.
- Ejecutor con timeout, cancelación, límites, captura de salida y argumentos separados.
- Lista permitida por operación y pruebas de rutas/argumentos maliciosos.

### Fase C — Ciclo de diagnóstico y reparación
- Reproducción, clasificación, hipótesis, cambios mínimos y regresión.
- Espacio aislado y reversión obligatoria.
- Presupuesto de intentos y parada segura en incertidumbre.

### Fase D — Trabajador GitHub
- Operaciones de lectura primero.
- Rama dedicada y commits verificables.
- CI y comparación de cambios.
- PR únicamente con autorización; merge y despliegue siguen siendo permisos separados.

### Fase E — Agente de PC
- Instalador explícito y permisos por carpeta.
- Comunicación local autenticada con el controlador.
- Confirmación para operaciones sensibles y registro de auditoría.

### Fase F — Especialización por tareas
- Bots de Telegram, juegos, editores de medios, utilidades de escritorio, traductores y aplicaciones móviles mediante adaptadores y plantillas originales.
- Cada especialización añade herramientas y pruebas; no reemplaza el motor general.

## Criterios de aceptación del trabajador

- Una orden se convierte en un plan auditable con criterios de éxito.
- Puede inspeccionar un repositorio que no conoce sin asumir un lenguaje.
- No ejecuta automáticamente comandos inferidos de un manifiesto o de texto externo.
- Puede ejecutar una prueba permitida, capturar un error desconocido y producir un diagnóstico basado en evidencia.
- Puede hacer un cambio mínimo en un espacio aislado, demostrar si corrige el fallo y revertirlo si no lo corrige.
- Puede operar sobre una rama GitHub autorizada y presentar SHA, diff y estado de CI.
- No puede leer o modificar rutas fuera del permiso concedido.
- Informa claramente `VERIFIED`, `FAILED`, `BLOCKED` o `UNKNOWN`; no confunde ausencia de evidencia con éxito.


## Estado real de implementación

Este apartado distingue capacidades presentes de objetivos futuros; no declara BOTPYTON terminado.

### Implementado en la rama de desarrollo

- CLI bajo demanda y lanzador `BOTPYTON.bat`; no instala un servicio ni se registra en el inicio de Windows.
- Inventario de repositorios con límites de archivos/profundidad, exclusión de carpetas generadas y sin ejecutar código del proyecto.
- Descubrimiento local de herramientas y sondeos de versión fijos.
- Ejecutor de procesos con argumentos separados, `shell=False`, timeout, salida retenida acotada, eliminación de variables que parezcan secretos y presupuesto de memoria calculado desde la RAM disponible.
- En Windows, el runner intenta imponer un límite de memoria por Job Object y terminar descendientes al cerrar el trabajo. **Este camino específico de Windows todavía necesita verificación en un equipo Windows**; la CI actual usa Linux y marca el límite de memoria como no aplicado en ese sistema.
- Cola persistente, planificación por dependencias, informes JSON, ciclo acotado de prueba/reparación y diagnóstico que recopila contexto de traceback desconocido sin editar código.
- Planes de cambios locales con rutas protegidas, hashes previos obligatorios al reemplazar archivos, backups fuera del proyecto y reversión ante errores de escritura.
- Perfiles de pruebas/build para varias tecnologías: descubrimiento estático por defecto y ejecución solo con autorización explícita.
- Operaciones GitHub mediante token de entorno o sesión local autenticada de GitHub CLI; la promoción/merge automático sigue deshabilitada.
- Biblioteca educativa de proyectos y fábrica de plantillas iniciales.

### Pendiente antes de declarar el trabajador listo para uso cotidiano

- Prueba de instalación y ejecución en el PC Windows del usuario, incluyendo consumo real de RAM y Job Objects.
- Agente de acceso local con permisos configurables por carpeta y registro de auditoría más completo. El lanzador actual ejecuta el núcleo local directamente; no es un servicio remoto ni un instalador independiente.
- Mayor cobertura de herramientas, lenguajes, formatos y entornos de compilación.
- Estrategias de experimentación para errores desconocidos. Hoy el sistema recopila evidencia y detiene la reparación automática cuando no hay una regla segura; no promete resolver cualquier error.
- Flujo de trabajo local más completo con workspaces/ramas aisladas para cambios complejos.
- Validación de los flujos reales de GitHub desde la cuenta local y pruebas de seguridad adicionales antes de habilitar cualquier escritura privilegiada.

### Límite de órdenes sin IA

Sin un modelo de IA, el núcleo no convierte cualquier frase libre en una solución de código arbitraria. Ejecuta órdenes estructuradas, perfiles conocidos y planes explícitos; puede investigar errores no registrados mediante evidencia y experimentos acotados, pero debe pedir instrucciones o detenerse cuando no haya una hipótesis verificable. No se debe presentar esta limitación como una función ya resuelta.
