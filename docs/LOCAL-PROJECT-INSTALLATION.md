# Instalación local de proyectos verificados

## Estado y alcance

BotPyton incluye un primer perfil cerrado de instalación local:

- Perfil: `pdf-to-markdown-quick`
- Repositorio: `jonhararagi/botimagen-referencias-privadas`
- Rama: `main`
- Commit exacto aprobado para instalar: `15e8ab50f93a27445f9fb718a55131ad2fd9af65`
- Subdirectorio instalado: `tools/pdf-to-markdown/`
- Destino predeterminado: `%USERPROFILE%\source\botimagen-referencias-privadas`
- Python requerido: lanzador de Windows `py -3.11`
- Diagnóstico autorizado: exclusivamente `python -m pdf_to_markdown.diagnostics --profile-set quick`

Este perfil es deliberadamente específico. No interpreta texto libre como comandos, no ofrece shell general y no permite elegir repositorios, ramas, paquetes o perfiles arbitrarios. Otros proyectos requieren perfiles revisados y registrados por separado.

## Vista previa y ejecución

Desde la raíz de un checkout de BotPyton en Windows:

Vista previa de solo lectura:

```powershell
py -3.11 -m bot.main --local-installation-profile pdf-to-markdown-quick
```

Ejecución autorizada explícitamente:

```powershell
py -3.11 -m bot.main --local-installation-profile pdf-to-markdown-quick --execute-local-installation
```

La vista previa no clona, no instala y no ejecuta comandos. La ejecución utiliza el destino predeterminado; se puede indicar otro destino absoluto dentro del perfil del usuario:

```powershell
py -3.11 -m bot.main --local-installation-profile pdf-to-markdown-quick --local-installation-destination "$env:USERPROFILE\source\botimagen-referencias-privadas" --execute-local-installation
```

La operación es independiente: BotPyton rechaza combinar estas opciones con otras operaciones CLI.

## Ejecución mediante la cola de tareas

La cola acepta una solicitud restringida. `allow_local_installation: true` solo expresa que el arquitecto solicita la operación; no constituye aprobación humana. Antes de clonar o instalar, el ejecutor exige una terminal interactiva y una confirmación escrita por una persona con la frase exacta `INSTALL pdf-to-markdown-quick <task-id>`. Sin TTY o ante una frase incorrecta, no inicia la instalación. El máximo de intentos debe ser uno:

```json
{
  "id": "INSTALL-PDF-TO-MARKDOWN-QUICK",
  "goal": "Install the approved local PDF to Markdown project and run its quick diagnostic",
  "allowed_actions": ["run_local_installation"],
  "success_conditions": ["local_installation_status_succeeded"],
  "max_attempts": 1,
  "allow_local_installation": true,
  "local_installation_profile": "pdf-to-markdown-quick",
  "local_installation_destination": "%USERPROFILE%\source\botimagen-referencias-privadas"
}
```

El perfil llama al instalador con `execute=True`. Si se bloquea, se interrumpe o devuelve un resultado parcial, la acción falla y no se reintenta automáticamente.

## Secuencia de seguridad

1. Comprueba que se esté ejecutando en Windows.
2. Mide RAM disponible y espacio libre antes de crear carpetas o iniciar instalaciones.
3. Resuelve Git y el lanzador `py` desde entradas absolutas de PATH que no estén dentro del perfil de trabajo ni en directorios descendientes de este.
4. Ejecuta `git --version` y `py -3.11 --version`.
5. Exige un único `credential.helper` oficial (`manager` o `manager-core`), comprueba que GCM responda y rechaza ayudantes específicos de URL.
6. Rechaza reescrituras de URL, cabeceras HTTP personalizadas, comandos SSH, hooks personalizados, fsmonitor, filtros externos e inclusiones de configuración.
7. Crea una configuración Git efímera y aislada: excluye configuración de sistema y usuario, conserva únicamente el GCM verificado, deshabilita hooks/fsmonitor y fuerza la verificación TLS de GitHub.
8. Clona únicamente la URL HTTPS aprobada o verifica el checkout existente: raíz exacta, remoto `origin`, rama `main`, árbol limpio y metadatos normales sin enlaces/reparse points.
9. Exige que `FETCH_HEAD` y el `HEAD` final sean exactamente el SHA aprobado; un commit posterior o anterior no se instala. Solo permite avance rápido y nunca hace merge manual, reset, clean, commit o push.
9. Comprueba los metadatos del paquete y compara las dependencias runtime/build con una lista permitida revisada.
10. Crea `.venv` con Python 3.11 o verifica el entorno existente. Nunca lo elimina ni lo reemplaza.
11. Instala el proyecto editable con pip dentro de ese entorno, sin caché, sin prompts de pip y sin paquetes opcionales.
12. Comprueba imports, versiones, `pip check` y disponibilidad del módulo de diagnóstico.
13. Vuelve a comprobar los umbrales de RAM/disco y ejecuta únicamente el perfil `quick`.
14. Lee y valida el JSON y CSV nuevos, y publica un informe local de instalación.

## Dependencias permitidas

El perfil compara el proyecto remoto con los metadatos revisados:

- Runtime: `PyMuPDF==2.0.0`, `psutil>=6.0,<8`
- Build backend: `setuptools>=75`
- Extras opcionales: ninguno

No solicita ni instala Docling, Tesseract, OCR, OpenCV opcional, CUDA, modelos ni paquetes no declarados. Si el proyecto cambia su lista de dependencias o los archivos esperados, BotPyton se detiene y requiere revisar el perfil antes de continuar.

## Umbrales y control de procesos

- Instalación: al menos 768 MB de RAM disponible y 512 MB de disco libre.
- Antes de quick: al menos 768 MB de RAM disponible y 256 MB de disco libre.
- Cada proceso tiene tiempo máximo, salida acotada y presupuesto de memoria adaptado a la RAM libre.
- En Windows se usa Job Object para limitar memoria y terminar los procesos descendientes al cancelar o exceder el tiempo.
- El proceso Git que puede mostrar la autenticación hereda la consola para permitir que Git Credential Manager abra su flujo oficial. Sus entradas y salidas estándar no se capturan. `GIT_TERMINAL_PROMPT=0` impide que Git solicite una contraseña por su propio prompt.
- Las variables de entorno heredadas `GIT_*`, `GCM_*`, `GIT_ASKPASS` y `SSH_ASKPASS` no se propagan. Git recibe solo `GIT_CONFIG_GLOBAL`, `GIT_CONFIG_NOSYSTEM` y `GIT_TERMINAL_PROMPT` controladas por BotPyton.
- Cada ejecución usa un archivo de configuración Git mínimo y una carpeta de hooks vacía, creada de forma exclusiva para esa sesión. Los comandos Git aplican además overrides de hooks, fsmonitor y TLS en la línea de comandos.
- El perfil no usa multiprocessing y el diagnóstico quick procesa una única configuración de forma secuencial.

Si Git Credential Manager no está instalado o no está configurado como único helper, BotPyton informa del bloqueo y no instala herramientas globales. El usuario debe configurar GCM manualmente y completar el inicio de sesión. BotPyton no pide, lee, captura ni persiste contraseñas o tokens. El propio GCM mantiene su flujo de autenticación del sistema.

## Red y privacidad

La instalación necesita red para clonar/actualizar el repositorio privado y descargar las dependencias declaradas desde el índice de paquetes configurado por pip. No es un servicio de conversión: el diagnóstico PDF se ejecuta localmente.

El instalador no aplica aislamiento de red a nivel del sistema operativo; por tanto, no afirma que el proceso del proyecto sea incapaz de usar la red. El diagnóstico de este conversor declara que no usa red, y el informe valida ese metadato. La prueba emplea un PDF sintético generado temporalmente por el propio conversor. No abre PDF personales.

No hace push, commits, PRs, ni publica archivos. Los resultados permanecen en el equipo.

## Resultados y registros

Informes del conversor:

`tools/pdf-to-markdown/benchmark/results/performance/<run_id>/performance-report.json`

`tools/pdf-to-markdown/benchmark/results/performance/<run_id>/performance-report.csv`

Registros e informe de instalación:

`%USERPROFILE%\source\.botpyton-install-logs\\pdf-to-markdown-quick\\<run_id>\\`

El instalador verifica que JSON y CSV no estén vacíos, se puedan leer, correspondan al run recién creado, indiquen `quick`, describan el fixture sintético F-12 y validen los marcadores/preservación del hash. Un corte interno por RAM o disco se registra como resultado parcial; no se informa como éxito.

## Detenciones obligatorias

El proceso se detiene sin borrar ni sobrescribir datos ante: herramientas globales ausentes; RAM/disco insuficientes; GCM no disponible, ayudantes adicionales o ayudantes por URL; configuración Git de transporte, filtros, hooks o includes no aprobada; remoto, rama o checkout incorrectos; árbol Git sucio; historia no fast-forward; SHA distinto del pin exacto; archivos o dependencias inesperados; entorno virtual existente incompatible; fallo de pip/imports; diagnóstico interrumpido o informes JSON/CSV inconsistentes.

Si un clon se interrumpe y deja un destino parcial, ese destino se conserva para inspección. No se elimina automáticamente.

## Limitaciones actuales

Este es el primer perfil de instalación local, no un instalador genérico universal. No configura Git Credential Manager, no instala Git ni Python, no ofrece aislamiento de red y no ejecuta perfiles `standard`, conversión de PDF personales ni operaciones posteriores. Para soportar otros proyectos, se deben agregar perfiles declarativos específicos, pruebas de seguridad y adaptadores de herramientas sin ampliar el permiso a shell arbitrario.
