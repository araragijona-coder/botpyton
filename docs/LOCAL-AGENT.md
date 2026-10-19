# Agente local seguro de BOTPYTON — Fase 46

## Alcance

Esta fase agrega un núcleo local de archivos, invocado bajo demanda desde la CLI. No instala servicios, no escucha conexiones de red, no se registra al inicio de Windows y no usa IA. No es un terminal remoto ni un agente que ejecute órdenes de texto libre.

El agente admite tres operaciones explícitas: listar una carpeta (un solo nivel), leer un archivo UTF-8 acotado y crear/reemplazar un archivo UTF-8. Toda ruta se interpreta relativa a una raíz declarada en la configuración. Las carpetas no se crean, los archivos no se eliminan, no se ejecutan programas y no se instalan dependencias.

## Configuración

Crea un archivo JSON fuera de las carpetas concedidas, por ejemplo agent.json, ajustando las rutas reales. El directorio padre del registro de auditoría ya debe existir y el registro debe quedar fuera de todas las raíces autorizadas.

~~~json
{
  "schema_version": 1,
  "audit_log": "C:\\Users\\TU_USUARIO\\AppData\\Local\\BOTPYTON\\local-agent-audit.jsonl",
  "roots": [
    {
      "id": "mi_proyecto",
      "path": "C:\\Users\\TU_USUARIO\\Projects\\MiProyecto",
      "permissions": ["list", "read", "write"],
      "max_file_bytes": 200000
    },
    {
      "id": "documentos",
      "path": "D:\\Documentos\\Proyecto",
      "permissions": ["list", "read"],
      "max_file_bytes": 64000
    }
  ]
}
~~~

schema_version debe ser 1. Debe haber entre 1 y 20 raíces. Los IDs son únicos, cada raíz es una carpeta absoluta, existente y sin componentes simbólicos/reparse. Las raíces no pueden solaparse. Permisos disponibles: list, read, write. El límite por archivo puede ser de 1 a 200.000 bytes. No concedas write a una carpeta que no quieras que BOTPYTON modifique. Las carpetas y los archivos sensibles bloqueados por la política base siguen protegidos aunque una raíz tenga permiso de escritura.

El archivo de configuración y el registro de auditoría no pueden estar dentro de las raíces autorizadas. Si la carpeta AppData\Local\BOTPYTON no existe, créala antes de usar el agente. La validación no crea las raíces ni escribe en los archivos del proyecto.

## Uso

Desde la carpeta local de BOTPYTON:

~~~powershell
# Validar configuración y revisar la integridad del registro existente.
python -m bot.main --local-agent-config "C:\BOTPYTON\agent.json"

# Listar solo el primer nivel de una raíz autorizada.
python -m bot.main --local-agent-config "C:\BOTPYTON\agent.json" --local-agent-list mi_proyecto --local-agent-path .

# Leer un archivo UTF-8 (el contenido se muestra en el informe JSON).
python -m bot.main --local-agent-config "C:\BOTPYTON\agent.json" --local-agent-read mi_proyecto --local-agent-path src/main.py

# Crear un archivo; se lee el contenido desde stdin con un límite estricto.
Get-Content -Raw -Encoding utf8 .\nuevo.py | python -m bot.main --local-agent-config "C:\BOTPYTON\agent.json" --local-agent-write mi_proyecto --local-agent-path src/nuevo.py --local-agent-content-stdin

# Verificar el registro de auditoría.
python -m bot.main --local-agent-config "C:\BOTPYTON\agent.json" --local-agent-audit
~~~

Para reemplazar un archivo existente, BOTPYTON exige que esa raíz otorgue tanto read como write, y que se indique el hash exacto del archivo actual. Calcula el hash inmediatamente antes de la operación:

~~~powershell
$hash = (Get-FileHash "C:\Users\TU_USUARIO\Projects\MiProyecto\src\main.py" -Algorithm SHA256).Hash.ToLowerInvariant()
Get-Content -Raw -Encoding utf8 .\main-nuevo.py | python -m bot.main --local-agent-config "C:\BOTPYTON\agent.json" --local-agent-write mi_proyecto --local-agent-path src/main.py --local-agent-content-stdin --local-agent-expected-sha256 $hash
~~~

Antes de publicar, BOTPYTON vuelve a validar la ruta, la identidad del destino y el hash esperado. Primero escribe un temporal en la misma carpeta, vacía y sincroniza sus bytes, y verifica tamaño y SHA-256; después publica el archivo y vuelve a leerlo de forma acotada para verificar el hash final. La creación de archivos nuevos usa publicación exclusiva para no sobrescribir un destino que aparezca concurrentemente. En Windows, mantiene handles de la cadena de directorios mientras lista o publica, y compara identidad/atributos Win32 antes y después; si no puede adquirirlos de forma segura, bloquea la operación. Esto es detección adicional de carreras, no un bloqueo garantizado contra renombres. No se crean automáticamente carpetas padre.

## Límites de seguridad

- El acceso se limita a una raíz identificada por ID y a un permiso explícito.
- Se rechazan traversal (..), rutas absolutas, rutas de unidad/flujo de Windows, nombres de dispositivo reservados, enlaces simbólicos y puntos de reanálisis.
- Se bloquean por defecto directorios del sistema/proyecto como .git, .ssh, .cerebro, .venv, venv, node_modules, __pycache__, .idea y .vscode, así como nombres y extensiones comunes de secretos.
- Las listas son de un solo nivel y están limitadas a 500 entradas. Lecturas y escrituras se limitan por raíz y nunca superan 200 KB.
- Cada operación registra intención y resultado en JSONL, sin guardar el contenido de los archivos. Cada evento incluye el hash del evento anterior; antes de añadir un evento se valida toda la cadena.
- Si el registro falta, no puede escribirse, excede sus límites o no supera la validación, la operación se bloquea antes del acceso solicitado. El registro nunca rota automáticamente; al alcanzar el límite hay que archivarlo de forma consciente y configurar un nuevo registro.
- El registro encadenado ayuda a detectar ediciones accidentales o alteraciones parciales. No es invulnerable frente a un administrador local o un atacante que pueda cambiar tanto el registro como la aplicación.
- En Windows, las lecturas acotadas abren el archivo con un handle nativo de Win32, `FILE_FLAG_OPEN_REPARSE_POINT` y un modo conservador de compartición. Se rechazan las hojas que son reparse points y se lee del objeto ya abierto; esto reduce la posibilidad de que el archivo-hoja sea sustituido entre comprobaciones. La identidad de la ruta se vuelve a comprobar por el agente.
- En Windows, el listado de carpetas y las escrituras intentan mantener handles abiertos para cada componente de directorio desde el ancla de la ruta hasta la carpeta objetivo, sin `FILE_SHARE_DELETE`; verifican que los IDs de archivo (cuando el sistema de archivos los proporciona) y atributos del handle coincidan con la ruta. Windows puede permitir ciertos renombres aun con esos handles abiertos, así que no se debe tratar como bloqueo del directorio ni como transacción handle-relative. Si los handles o sus identidades no se pueden verificar, la operación falla cerrada.
- El listado compara la identidad de la cadena de directorios antes y después de enumerar; una sustitución detectada hace que se descarte el resultado. Las escrituras verifican tamaño/hash del temporal y hash del archivo publicado. Para un destino nuevo, la publicación exclusiva no reemplaza un nombre que ya exista.
- Límite importante: la sustitución atómica de un archivo existente sigue usando una operación por ruta después de comprobar el hash. Mantener abierto el handle del archivo destino sin compartir borrado impediría la propia sustitución; por eso permanece una ventana residual ante una carrera hostil sobre la hoja del archivo. En sistemas no Windows, la protección de directorios es detección por identidad antes/después, no un bloqueo del kernel. Una escritura que falle al verificar después de publicar puede haber cambiado ya el destino; revisa el archivo y el registro de auditoría antes de reintentar.
- Estas medidas no son una transacción atómica para todos los sistemas de archivos y no convierten la función en un sandbox frente a otro proceso malicioso del mismo usuario. No uses raíces controladas por software no confiable para operaciones de escritura. La validación de CI no sustituye probar las rutas y permisos elegidos en el PC real.

Fase 46 no implementa un servidor remoto, un instalador de agente, ACL de Windows, elevación administrativa, acceso a carpetas no configuradas, ejecución de procesos ni modificación automática de archivos de configuración o auditoría. La validación de CI no sustituye probar las rutas y permisos elegidos en el PC real.
