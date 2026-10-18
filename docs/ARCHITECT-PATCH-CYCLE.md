# Phase 27 — Ciclo seguro para aplicar, probar y revisar un parche

## Objetivo

Conectar tres capacidades que ya existían por separado: el planificador de arquitectura, la creación de worktrees aislados y la ejecución autorizada de perfiles de prueba. El ciclo recibe un diff unificado que una persona ya revisó, valida su alcance, lo aplica en una rama/worktree local desechable y conserva la evidencia de las comprobaciones.

Esta fase no convierte BOTPYTON en un programador general ni aplica automáticamente la respuesta de un modelo remoto. Es el primer puente entre una propuesta de cambio y pruebas reales del proyecto.

## Requisito

El proyecto objetivo debe ser la raíz de un repositorio Git con el árbol de trabajo limpio y un commit inicial. El diff debe ser un archivo UTF-8 de texto plano que empiece con `diff --git`; no debe incluir cercas Markdown. Cada archivo que se modifique debe existir ya, ser regular y estar bajo la lista de extensiones y rutas que permite `bot.local_changes`. Se limitan el parche a 250 KB, 20 archivos y 20.000 líneas, y cada archivo existente/cambiado a 200 KB.

Se rechazan rutas protegidas, escapes de ruta, enlaces simbólicos/reparse points, submódulos, archivos binarios, creación o borrado de archivos, cambios de modo, renombres y copias. El parche se comprueba primero con `git apply --check --whitespace=error`; Git se ejecuta con argumentos separados, sin shell, usando el ejecutable local permitido.

## Uso: solo aplicar y revisar sintaxis

Primero obtené el diff propuesto y leelo en un editor. Guardá únicamente el diff en un archivo `.patch` (idealmente fuera del repositorio para no ensuciar el origen). Después:

```powershell
python -m bot.main --apply-reviewed-patch "C:\propuestas\fix.patch" --patch-task-id fix-parser-001 --project-root "C:\proyectos\mi-app" --workspace-parent "C:\BotPyton\workspaces"
```

El candidato se crea bajo el directorio de workspaces y recibe una rama local `botpyton/...`. En este modo no se ejecuta el proyecto: se valida la aplicabilidad del diff, las rutas modificadas, `git diff --check` y, cuando corresponda, el parseo de Python, JSON o TOML. Los otros tipos de archivo requieren validación propia del proyecto y revisión humana.

## Uso: comparar pruebas originales y candidatas

Descubrí primero los perfiles de prueba sin ejecutarlos:

```powershell
python -m bot.main --list-project-checks --project-root "C:\proyectos\mi-app"
```

Después elegí un `check_id` que la salida haya confirmado y autorizá expresamente su ejecución:

```powershell
python -m bot.main --apply-reviewed-patch "C:\propuestas\fix.patch" --patch-task-id fix-parser-001 --project-root "C:\proyectos\mi-app" --workspace-parent "C:\BotPyton\workspaces" --patch-check python-unittest --execute-project-code --check-timeout 120
```

El programa comprueba primero que Git acepte el parche. Luego ejecuta una sola vez el perfil elegido en un worktree de referencia sin parche y una vez en el candidato (siempre que pasen los controles estáticos). El perfil debe aparecer en la lista de checks conocidos; no se aceptan comandos definidos en el parche o en el brief. Los perfiles ejecutan código del proyecto, así que `--execute-project-code` es un consentimiento explícito, no un detalle opcional. Los límites existentes de timeout, memoria del proceso, salida y entorno siguen activos.

## Estados y cómo interpretarlos

- `candidate_ready_for_review`: el parche se aplicó y pasaron las comprobaciones estáticas acotadas. No se ejecutó la suite del proyecto.
- `candidate_tests_passed`: pasaron las comprobaciones estáticas y el perfil autorizado del candidato.
- `candidate_checks_failed`: la comprobación del candidato falló o no pudo completarse. Se devuelve la evidencia de ejecución y, si hay un log clasificable, un diagnóstico con hipótesis y próximos pasos.
- `candidate_static_verification_failed`: falló una comprobación estática; BOTPYTON no ejecutó el candidato.
- `patch_rejected` / `patch_apply_failed`: el diff no era aplicable o Git no pudo aplicarlo.

El resultado incluye SHA base, hash del diff, lista exacta de archivos, ubicación de la rama/worktree, estado y resumen del diff, resultado de referencia y de candidato, salida limitada de las pruebas y sugerencias de recuperación cuando se puedan clasificar. Las reglas de recuperación son pistas: no confirman por sí mismas la causa raíz.

## Condiciones de parada y recuperación

- No se modifica el árbol de trabajo original ni se crea commit, push o merge automático.
- Si el árbol original está sucio, no se crea candidato.
- Un patch que no pasa `git apply --check` no dispara las pruebas del proyecto.
- Si falla el parseo estático, no se ejecuta el candidato.
- El mismo parche no se reintenta en bucle cuando las pruebas fallan. La nueva iteración debe incorporar evidencia o un cambio de hipótesis.
- Se solicita revisión humana cuando hay rutas protegidas, archivos nuevos/borrados, tipos sin validador, perfil de prueba desconocido, fallos persistentes, timeout o causa ambigua.
- Los worktrees y ramas locales quedan conservados para inspección; BOTPYTON no los elimina automáticamente. Tras guardar cualquier evidencia necesaria, el usuario puede quitarlos con los comandos normales de Git. La ubicación se devuelve en el informe.

## Referencias externas consultadas

- La guía oficial de `git apply` documenta `--check` como comprobación de aplicabilidad sin aplicar el diff y `--whitespace=error` como política de espacios. BOTPYTON usa esa separación como puerta previa a cualquier cambio: https://git-scm.com/docs/git-apply
- Las notas oficiales de [BleachBit 6.0.6](https://www.bleachbit.org/news/bleachbit-606) describen carga diferida de módulos como `psutil` y `urllib.request`, mejoras de arranque/escaneo y una corrección de permisos que impedía guardar preferencias si la aplicación se había iniciado primero como administrador. BOTPYTON aplica el principio de carga diferida al motor opcional de parches: la importación normal de `bot.main` no debe cargar `bot.patch_cycle` hasta que se solicite la función. Una prueba de regresión comprueba esa condición; no se afirma ningún ahorro porcentual de memoria sin medición.
- Python explica que `subprocess` no invoca el shell implícitamente cuando se pasan argumentos separados, mientras `shell=True` obliga al llamador a gestionar metacaracteres y quoting. El ciclo conserva `shell=False` y los ejecutables fijados: https://docs.python.org/3/library/subprocess.html
- La documentación oficial de Python sobre AST advierte que un parseo exitoso no demuestra que el código se pueda ejecutar o que su alcance de nombres sea válido. Por eso el control sintáctico no sustituye los tests: https://docs.python.org/3/library/ast.html
- El changelog de pytest ilustra correcciones de regresiones concretas y fallos de borde; BOTPYTON adopta la práctica de guardar un resultado de referencia y exigir una prueba de comportamiento, en lugar de repetir una operación sin evidencia nueva: https://pytest.org/en/stable/changelog.html
- Las recomendaciones de uso seguro de GitHub destacan los permisos mínimos y tratar los datos externos como no confiables. Este mismo principio se aplica a un parche generado por un asesor: se revisa como datos, se limita por rutas y se prueba aislado: https://docs.github.com/en/actions/reference/security/secure-use

Estas fuentes sostienen las prácticas generales, no garantizan la corrección de cualquier parche. La verificación real sigue dependiendo del proyecto y de la calidad de sus pruebas.
