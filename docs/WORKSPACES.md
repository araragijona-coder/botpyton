# Workspaces Git aislados

## Crear un workspace

```bash
python -m bot.main --create-workspace TASK-123 --project-root "C:\\ruta\\repositorio"
```

El repositorio de origen debe ser un repositorio Git limpio: no puede haber archivos modificados ni sin seguimiento. BOTPYTON crea una rama local `botpyton/<tarea>-<huella>` y un worktree fuera del repositorio de origen, por defecto en `~/.botpyton/workspaces`. No hace push, no crea PR y no fusiona cambios.

Durante la creación se deshabilitan los hooks de Git para esa operación de checkout, para no ejecutar un `post-checkout` del repositorio de origen. No se inicializan submódulos ni se instalan dependencias.

## Trabajar en el workspace

Usa la ruta `workspace_path` que devuelve el comando:

```bash
python -m bot.main --work-once --project-root "C:\\Users\\usuario\\.botpyton\\workspaces\\task-123-abcd123456"
python -m bot.main --workspace-status "C:\\Users\\usuario\\.botpyton\\workspaces\\task-123-abcd123456"
```

Las tareas de la cola siguen requiriendo JSON explícito y acciones permitidas. Los cambios permanecen en la rama aislada hasta que una persona revise el diff y decida cómo promoverlos. La creación de workspaces exige que los cambios existentes estén comprometidos o guardados en otro lugar; no copia cambios sin commit de la carpeta original.

El workspace puede ocupar espacio en disco, aunque Git reutiliza los objetos del repositorio. El usuario debe elegir un disco con espacio suficiente. No es un snapshot de archivos sin seguimiento.
