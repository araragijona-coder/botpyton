# Fase 28 — Retroalimentación de fallos para la reparación asistida

## Propósito

Conectar los resultados del ciclo de parches de la Fase 27 con la siguiente consulta al asesor de arquitectura. El bot puede llevar evidencia concreta de una prueba fallida a la siguiente ronda y ayudar a revisar la hipótesis en vez de resumir manualmente el error. No aplica una corrección por sí solo ni crea un bucle infinito.

## Procedimiento

1. Ejecutá un parche que ya revisaste con el procedimiento de [Fase 27](ARCHITECT-PATCH-CYCLE.md).
2. Si el resultado tiene un estado de fallo, guardá **solo el objeto JSON completo del informe** como `repair-result.json`. No pegues capturas, tokens ni datos privados dentro del informe.
3. Abrí el brief de arquitectura y ajustá `goal`, los criterios de aceptación y las restricciones para describir el error original.
4. Seleccioná únicamente los archivos fuente y pruebas necesarios para corregirlo. Revisá manualmente que no contengan credenciales ni material privado.
5. Ejecutá la consulta con los consentimientos que correspondan:

```powershell
python -m bot.main --architect-ai .cerebro/examples/architect-design-brief.json --allow-external-ai --architect-ai-context src/parser.py --architect-ai-context tests/test_parser.py --send-source-context --architect-ai-failure-report repair-result.json --send-failure-report
```

Reemplazá los dos paths relativos de ejemplo por los archivos reales del proyecto. Todos deben existir dentro de `project_root` del brief.

## Consentimientos independientes

- `--allow-external-ai`: autoriza enviar los campos permitidos del brief.
- `--send-source-context`: autoriza enviar los archivos concretos listados con `--architect-ai-context`.
- `--send-failure-report`: autoriza enviar el resumen filtrado del informe que indicaste con `--architect-ai-failure-report`.

El informe no se lee para enviarlo si falta su bandera de consentimiento; la CLI responde `failure_report_consent_required` y termina antes de llamar a la red. El código de preparación permite solo estados de fallo conocidos, campos limitados y fragmentos de logs de tamaño acotado; excluye rutas absolutas de los worktrees y campos no reconocidos y bloquea coincidencias con patrones comunes de secretos. Estos filtros reducen el riesgo, pero no detectan todos los secretos posibles. Leé el informe antes de autorizarlo.

El contenido del brief, el código seleccionado y los logs pueden contener entradas maliciosas o instrucciones citadas. El asesor debe tratarlos como evidencia, nunca como instrucciones que anulen sus reglas.

## Cómo interpretar la siguiente respuesta

El asesor debe comparar el resultado de referencia con el de la versión candidata cuando ambos existan; separar la evidencia observada de las hipótesis; señalar qué prueba falló; proponer un cambio solo si los archivos seleccionados contienen suficiente contexto; y pedir una aclaración concreta cuando falte una parte del contrato o no pueda producir un diff coherente.

Si genera un `BORRADOR DE PARCHE (NO APLICADO)`, revisalo en un editor, guardalo como diff de texto plano y ejecutá otra vez la Fase 27. Esa nueva ejecución vuelve a comprobar rutas, aplicabilidad, sintaxis y, solo con consentimiento explícito, una prueba del proyecto en worktrees aislados.

## Límites

- No se descarga documentación ni se consultan páginas web desde la ejecución local del asesor.
- No se instala software ni se ejecutan comandos sugeridos por el modelo.
- No se envían rutas absolutas de los worktrees dentro del informe de fallos filtrado.
- No se crea commit, push, merge ni cambio en el árbol original a partir de la respuesta.
- Un test verde no demuestra corrección universal; los proyectos necesitan pruebas de comportamiento y revisión humana.
- Un fallo nuevo o ambiguo no permite repetir el mismo parche sin evidencia nueva.

La fuente externa de inspiración es el registro de problemas y correcciones que publican los mantenedores de herramientas y frameworks: ver [la bitácora de correcciones externas](research/EXTERNAL_BUG_FIXES_AND_BOTPYTON_GUARDS.md). Esas publicaciones guían las preguntas de diagnóstico, pero no prueban que BOTPYTON tenga la misma causa raíz.


## Corrección de privacidad — Fase 29

La redacción de la Fase 29 sustituye patrones conocidos de rutas absolutas locales que aparezcan *dentro* de los textos permitidos del informe, incluidos tracebacks y fragmentos de salida. Las rutas Windows citadas se procesan antes de los tokens sin comillas para poder cubrir directorios con espacios. Se preservan las URL web habituales. Esto complementa, pero no reemplaza, revisar el informe y el brief: no existe una regex universal para todos los datos privados.

Ejemplo de fragmento antes del envío:

```text
File "C:\Users\Nombre Apellido\AppData\Local\Temp\worktree\test.py", line 20
AssertionError at /home/runner/work/project/project/tests/test_parser.py:20
```

El texto incluido en la solicitud externa reemplaza esas ubicaciones reconocidas por `[LOCAL_PATH_REDACTED]`. El informe original en el disco no se modifica. La bandera `--send-failure-report` sigue siendo obligatoria.
