# Planes de cambios locales

BOTPYTON puede validar una lista declarativa de archivos que el usuario quiere crear o actualizar. El plan no contiene comandos de shell ni se interpreta como código ejecutable.

## Validación sin escribir (predeterminado)

```bash
python -m bot.main --local-plan "C:\\ruta\\plan.json" --project-root "C:\\ruta\\proyecto"
```

## Aplicación explícita

```bash
python -m bot.main --local-plan "C:\\ruta\\plan.json" --project-root "C:\\ruta\\proyecto" --execute-local-plan
```

El modo de escritura es opt-in y requiere:
- plan JSON con `schema_version: 1`, `task_id` y entre 1 y 20 cambios;
- rutas relativas canónicas dentro del proyecto;
- extensiones incluidas en la lista de archivos permitidos;
- para reemplazar un archivo existente, `expected_sha256` con el hash actual exacto;
- archivos de máximo 200 KB y plan de máximo 500 KB de contenido total.

Los archivos existentes se respaldan fuera de la raíz del proyecto, por defecto en `~/.botpyton/backups/<hash-raiz>/<task_id>/`. Si un archivo cambió desde que se preparó el plan, la operación se bloquea. No se permiten borrados, rutas protegidas, traversal ni componentes de ruta simbólicos. Las escrituras usan archivos temporales y reemplazo atómico; si una escritura falla, el motor intenta restaurar los archivos que ya alcanzó a cambiar.

## Ejemplo de plan para crear un archivo nuevo

```json
{
  "schema_version": 1,
  "task_id": "add-example-module",
  "changes": [
    {
      "path": "src/example.py",
      "content": "def greet(name):\\n    return f'Hello, {name}'\\n"
    }
  ]
}
```

Para actualizar un archivo existente, añade `expected_sha256` con el SHA-256 del contenido exacto que el plan espera reemplazar. No inventes el hash; calcúlalo a partir del archivo real.

Este mecanismo proporciona escritura local controlada, pero no convierte texto libre en código automáticamente ni decide por sí solo qué cambio resolverá un error. El plan debe describir el cambio solicitado y BOTPYTON verifica precondiciones, permisos y hashes antes de aplicarlo.


## Usar el plan como una tarea de la cola

Una tarea de la cola puede incluir `"apply_local_plan"` en `allowed_actions` y el mismo objeto de plan en `local_change_plan`. El `task_id` del plan debe coincidir exactamente con el `id` de la tarea. El ejecutor de tareas aplica las mismas comprobaciones de hash, límites, rutas protegidas y backups; no convierte el campo `goal` en un comando y no interpreta código libre como instrucciones.

Esto permite automatizar una orden ya estructurada y auditable. No es todavía un traductor universal de texto libre a código: sin un modelo de IA, las órdenes genéricas deben convertirse en planes explícitos o encajar en procedimientos deterministas que el trabajador conozca.
