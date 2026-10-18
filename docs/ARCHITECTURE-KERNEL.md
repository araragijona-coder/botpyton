# Phase 23 — Núcleo de arquitectura de software

## Propósito

Añade una primera capacidad verificable para aproximarse al objetivo de BOTPYTON: convertir una orden de programación estructurada en un plan técnico con etapas, evidencia requerida, preguntas pendientes, pruebas y condiciones de parada.

No convierte el programa en un modelo de lenguaje ni promete resolver cualquier petición. El análisis del texto es determinista y basado en reglas locales, y el resultado es un plan que puede revisarse; no realiza una edición de código ni ejecuta el proyecto.

## Uso

Crear un archivo JSON a partir de `.cerebro/examples/architect-design-brief.json` y ajustar los campos a la petición concreta. Ejecutar desde la raíz de BOTPYTON:

```text
python -m bot.main --architect-plan .cerebro/examples/architect-design-brief.json
```

El resultado JSON usa uno de estos estados:

- `ready_to_plan`: el brief contiene suficiente información para generar un plan inicial; aún hace falta revisión y aceptación humana.
- `needs_information`: una pregunta esencial bloquea una decisión responsable, por ejemplo no se incluyó una carpeta de proyecto para un cambio de código o falta el error/reproducción al depurar.
- `blocked`: un dato básico no puede validarse, como una ruta de proyecto que no existe. No se inventa un proyecto ni se crean archivos para sustituirlo.

Un resultado que necesita información retorna código de salida 2, igual que otros resultados de validación bloqueada; un plan de diseño válido retorna 0.

## Contrato de entrada

El brief admite solo los campos siguientes y está sujeto al límite común de lectura JSON de 50 KB:

- `schema_version`: 1.
- `goal`: resultado deseado, no más de 2.000 caracteres.
- `task_type`: `auto`, `implement`, `debug`, `refactor`, `optimize`, `review`, `test`, `migrate`, `document` o `design`.
- `project_root`: ruta local opcional para inventario limitado de nombres/manifiestos.
- `observed_error`, `reproduction_steps`, `acceptance_criteria`, `constraints`, `must_not_change` y `available_evidence`: evidencia o límites facilitados por la persona.

Los campos desconocidos, tipos incorrectos, cantidades que exceden el límite y esquemas incompatibles se rechazan. La inspección del repositorio omite carpetas de dependencias/generadas y no importa ni ejecuta el código del proyecto.

## Cómo decide qué hacer

El núcleo propone pasos comunes: entender el repositorio, definir criterios de aceptación, diagnosticar/diseñar, aplicar una modificación mínima tras aprobación, verificar y revisar el diff. La parte central cambia por tipo de trabajo:

- **Depuración:** reproducir, capturar error y valores esperados/observados, crear hipótesis falsables y añadir regresión.
- **Implementación:** rastrear límites de módulos e interfaces antes de proponer la mínima modificación compatible.
- **Optimización:** medir el estado de referencia y definir la métrica antes de prometer velocidad o ahorro de RAM.
- **Refactor:** conservar el comportamiento observable y cambiar una frontera por vez.
- **Migración:** exigir versiones de origen/destino, compatibilidad, cambios de esquema y reversión.
- **Revisión:** separar hallazgos confirmados de riesgos hipotéticos.
- **Diseño:** comparar alternativas según requisitos, costes, mantenimiento y modos de fallo.

El plan incluye un protocolo de recuperación: preservar evidencia, localizar el fallo, probar una hipótesis por vez, modificar el alcance mínimo, ejecutar pruebas y solicitar ayuda cuando la evidencia no discrimine entre posibles causas.

## Condiciones de parada

El plan debe detener la ejecución —no rellenar los huecos con imaginación— si la meta es ambigua, la raíz del proyecto no es válida, el fallo no se puede reproducir y faltan pruebas, un cambio modifica datos persistidos, requiere permisos amplios, exige instalar dependencias, supera el número de intentos o falla la verificación. El resultado explicita la pregunta o el motivo de bloqueo.

## Capacidad real y límites

- **Sí:** validación de un brief, clasificación heurística, inventario local acotado, plan técnico con puntos de control, preguntas y reglas conservadoras para recuperar fallos.
- **No todavía:** comprensión semántica general como un arquitecto humano, investigación web automática, soporte profundo de cada lenguaje, diseño de parche correcto para un error arbitrario, ejecución de cualquier orden, o prueba matemática de corrección.
- **No se modificó:** el motor de cola, las reparaciones automáticas existentes, el sistema de permisos ni la política de no ejecutar texto arbitrario.
- **Sin coste de runtime nuevo:** biblioteca estándar de Python, sin paquetes, llamadas de IA, servicios en segundo plano ni red para producir un plan.

Esta es una base para crecer en fases. El siguiente avance razonable es ampliar el registro de conocimiento diagnóstico con firmas versionadas y pruebas de regresión; después se puede conectar el plan a operaciones autorizadas ya existentes, sin abrir un shell arbitrario.

## Referencias y prácticas externas

- BleachBit 6.0.5 beta publicó mejoras de arranque mediante carga diferida de módulos y optimizaciones de ordenamiento/procesamiento, además de correcciones de cierres y preferencias. La lección es medir los cuellos reales y cargar módulos cuando se necesitan, no asumir que un motor nuevo será más rápido: https://www.bleachbit.org/news/bleachbit-605-beta
- BleachBit 6.0.4 documenta correcciones por archivos que desaparecen durante el escaneo, errores de SQLite y condiciones de carrera de configuración. Estas notas ejemplifican el formato que BOTPYTON debe imitar: síntoma → causa comprobada → cambio → regresión: https://www.bleachbit.org/news/bleachbit-604
- La documentación de Python sobre AST advierte que analizar sintácticamente no prueba que un programa sea ejecutable ni valida el alcance de nombres; por eso un plan de arquitectura separa parseo, pruebas y evidencia de ejecución: https://docs.python.org/3/library/ast.html
- La guía oficial de GitHub Actions recomienda permisos mínimos y tratar como no confiable el contenido proveniente de títulos/comentarios u otros contextos al evitar inyección en workflows: https://docs.github.com/en/actions/reference/security/secure-use y https://docs.github.com/en/actions/concepts/security/script-injections
- El changelog de pytest muestra correcciones publicadas para regresiones y errores de lógica, apoyando la obligación de incluir una prueba de regresión en vez de repetir el intento sin nueva evidencia: https://pytest.org/en/stable/changelog.html

Estas fuentes orientan prácticas de ingeniería; no prueban que el plan generado sea correcto para todos los proyectos. La verificación sigue dependiendo de las pruebas concretas del repositorio y de la revisión de los cambios.
