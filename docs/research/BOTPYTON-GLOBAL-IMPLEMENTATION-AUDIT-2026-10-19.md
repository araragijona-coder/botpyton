# BOTPYTON — auditoría global de implementación

**Corte de auditoría:** 2026-10-19  
**Repositorio:** `araragijona-coder/botpyton`  
**Rama por defecto:** `main`  
**Naturaleza:** inventario documental de funcionalidades y evidencia de CI; no es una certificación formal de seguridad ni una prueba en el PC del usuario.

## 1. Identificadores y evidencia verificada

| Elemento | Estado en el corte | Evidencia |
|---|---|---|
| `main` | SHA `dd1652d00d45c0d6e73e1cd5ec099ee09244e7b4` | [Foundation tests: PASS](https://github.com/araragijona-coder/botpyton/actions/runs/39169014431) · [Windows low-memory smoke: PASS](https://github.com/araragijona-coder/botpyton/actions/runs/39169014421) |
| Candidato Fase 48 | SHA `0e3cb0dd9a3fae037c8c20ddba80ca647f4a9bd5`; 13 commits ahead, 0 behind en el corte | [Foundation tests: PASS](https://github.com/araragijona-coder/botpyton/actions/runs/39297417039) · [Windows low-memory smoke: PASS](https://github.com/araragijona-coder/botpyton/actions/runs/39297416912) |
| Fase 49 | Comienza desde el SHA de Fase 48 en `bot-bootstrap/phase-49-windows-handle-access` | En desarrollo en el momento de crear esta auditoría; no incluida en `main` |
| Pull requests | No se abrió ni fusionó ningún PR durante este trabajo | Consulta de PR abiertos en el corte: ninguno |

Los trabajos de Fase 48 pasaron en el SHA exacto indicado. Eso demuestra ejecución satisfactoria del conjunto automatizado, no que todas las funciones se hayan probado con datos personales o bajo todas las condiciones de Windows.

## 2. Lo que figura implementado en `main`

El README, la CLI y los módulos de prueba describen estas capacidades:

- **Trabajador determinista y cola JSON:** estados, dependencias, idempotencia, intentos limitados, recuperación tras interrupciones y validación de registros persistidos.
- **Ejecución y verificación acotadas:** pruebas fijas con límites de tiempo, informes, checkpoints, recuperación basada en evidencia y reparaciones conocidas y exactas.
- **Operaciones GitHub declarativas:** validar planes, crear rama aislada, crear un commit y abrir un PR borrador sujeto a configuración y autorización explícita; no se fusiona automáticamente.
- **Análisis de repositorios y propuestas de cambios:** inventario estático limitado, descubrimiento de perfiles de prueba y ciclo de parche revisado en un worktree local. El análisis estático no demuestra que una aplicación funcione correctamente.
- **Interfaz Windows/PC Care:** comprobaciones locales de solo lectura, previsualización de limpieza, cuarentena/restauración reversible, inventario de recursos, aplicaciones, almacenamiento, duplicados, controladores y destinos de inicio.
- **Límites de ejecución:** perfiles de comprobación, aislamiento de workspaces, límites de memoria/tiempo donde están disponibles y limpieza de árboles de procesos tras timeout.
- **Asesor de arquitectura remoto opcional:** el modo normal no requiere IA, pero el repositorio sí incluye un asesor externo optativo, sujeto a consentimiento y a límites de envío. Por tanto, no debe describirse todo el repositorio como estrictamente offline en cualquier modo.

Inventario observado en `main` durante el corte: 39 módulos de prueba, 4 workflows de GitHub Actions y 18 documentos de investigación en `docs/research`. Es un conteo de archivos, no una métrica de cobertura ni una prueba de que cada ruta se haya ejercitado.

## 3. Funcionalidades candidatas que aún no pertenecen a `main`

| Fase | Alcance | Estado |
|---|---|---|
| 46 | Agente local de carpetas autorizadas; operaciones explícitas de listar/leer/escribir, límites, política base y auditoría encadenada | Implementada en el candidato, CI verde en Fase 48; no está en `main` |
| 47 | Investigación determinista de errores desconocidos con límites de exploración/lectura y parada por falta de evidencia | Implementada en el candidato, CI verde en Fase 48; no está en `main` |
| 48 | Comprobaciones de identidad de componentes de ruta y regresión por sustitución de carpeta | Implementada en el candidato, CI verde en el SHA documentado; mitigación, no sandbox completo |
| 49 | Apertura de lectura mediante handle nativo de Windows, rechazo de reparse points de hoja y prueba específica | En curso en la rama citada; queda pendiente CI final |

La rama candidata no se debe considerar parte del producto de `main` hasta que un humano revise y autorice una integración posterior.

## 4. Pendientes o no verificados

1. **Prueba en el PC real:** no se ha ejecutado BOTPYTON contra las carpetas personales del usuario. El workflow Windows es un runner CI y no mide la RAM libre del PC objetivo.
2. **Presupuesto de memoria real:** la documentación incluye límites y ejecución secuencial, pero no hay evidencia aportada aquí de consumo medido en el Ryzen 5 5600G con los aproximadamente 2 GB disponibles que indicó el usuario.
3. **Resistencia ante procesos locales hostiles:** los controles de ruta y los handles mitigan carreras; las operaciones padre y de escritura no constituyen una frontera atómica de seguridad frente a otro proceso con el mismo usuario.
4. **Privilegios y reglas de `main`:** las reglasets consultadas devolvieron una lista vacía. La API de protección clásica de rama no fue accesible para esta integración (403); por eso esta auditoría **no puede certificar** protección o ausencia de protección de `main`.
5. **Generalidad de programación:** BOTPYTON aplica reglas deterministas, perfiles conocidos y parches revisados. No es un programador general capaz de construir y verificar cualquier producto sin entradas, herramientas, revisión o límites.
6. **Modo IA opcional:** requiere consentimiento explícito separado; un usuario que necesite operación estrictamente sin IA debe evitar los comandos del asesor remoto.

## 5. Reglas de operación para el trabajo siguiente

- Toda modificación continúa en `bot-bootstrap/phase-49-windows-handle-access` o una rama aislada descendiente.
- No modificar `main`, crear PR ni fusionar sin autorización específica.
- No afirmar PASS hasta comprobar ambas workflows en el mismo SHA final.
- Si las pruebas Windows encuentran un fallo, corregirlo antes de declarar la fase aceptada.
- Probar únicamente con directorios temporales en CI y usar una carpeta de prueba desechable en el equipo local.
- Mantener separados: **IMPLEMENTADO**, **CI VERIFICADO**, **PROBADO EN PC REAL** y **PENDIENTE**.
