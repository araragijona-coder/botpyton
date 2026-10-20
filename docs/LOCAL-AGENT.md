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

Todas las escrituras de una misma raíz autorizada comparten un lock lateral estable para coordinar hilos y procesos. BOTPYTON vuelve a comprobar la existencia del destino y el `expected_sha256` dentro del lock, justo antes de preparar/publicar el cambio: dos reemplazos con el mismo hash no pueden validarse ambos después de que el primero cambie el archivo. Una creación concurrente también deja un solo ganador; la otra operación se rechaza. El lock se mantiene como archivo interno persistente para evitar que dos procesos terminen bloqueando archivos distintos por eliminación/recreación del lock. Antes de publicar, BOTPYTON vuelve a validar la ruta, la identidad del destino y el hash esperado. Escribe un temporal de nombre único, sincroniza sus bytes y verifica tamaño y SHA-256 desde el handle de escritura; después publica y vuelve a leer el archivo de forma acotada para verificar el hash final. En sistemas POSIX, la carpeta padre se abre y las operaciones de publicación se hacen relativas a ese descriptor: reemplazar usa `rename` relativo y crear usa `link` exclusivo relativo a la misma carpeta. En Windows, BOTPYTON usa `SetFileInformationByHandle` con `FileRenameInfoEx` y semántica POSIX de reemplazo para el handle del temporal, junto con un destino absoluto. Esta variante permite reemplazar el nombre aunque el destino tenga el handle de lectura de la guarda de publicación abierto; si el sistema de archivos no admite la clase/flags, la operación falla cerradamente. `RootDirectory` se deja en NULL porque el sistema puede rechazar el handle de directorio con `ERROR_INVALID_PARAMETER`; por eso Windows conserva una comprobación de identidad de la ruta antes y después, pero no elimina por completo la carrera de resolución del destino. Si el handle o la identidad no se puede verificar, la operación se bloquea. La fase 53 repite la comprobación de `expected_sha256` y de la identidad de la cadena de destino dentro de la rutina de publicación, junto a la operación nativa final; las regresiones simulan una escritura externa y una sustitución de ruta justo antes de entrar a esa frontera. En Windows, al reemplazar un archivo existente, BOTPYTON intenta mantener abierto un handle de lectura que permite compartir lectura y eliminación, pero no escritura, durante la revalidación y el rename. Si el sistema no permite ese handle, la escritura falla cerradamente. Esto dificulta las aperturas concurrentes para escritura durante el tramo crítico, pero no equivale a un bloqueo universal: la compartición de eliminación es necesaria para reemplazar atómicamente el destino y deja una posible carrera por renombre externo. En POSIX, la comprobación final estrecha la ventana, pero no impide que un proceso no cooperativo escriba sobre el mismo inode justo después de la comprobación y antes del syscall. Ninguna plataforma garantiza aquí un compare-and-swap universal por hash. 

No se crean automáticamente carpetas padre.


## Recuperación posterior a la publicación — Fase 54

Una escritura puede publicar el temporal correctamente y fallar después al verificar el destino o registrar el éxito. BOTPYTON conserva en memoria el contenido anterior del archivo reemplazado y, ante un fallo posterior a la publicación, intenta restaurarlo **solo** si puede demostrar que el destino sigue siendo el mismo objeto que BOTPYTON publicó y que aún contiene exactamente los bytes nuevos solicitados. La restauración se prepara en otro temporal verificado y se publica usando la misma comprobación de identidad/hash; no se degrada a una escritura directa.

Si la identidad del destino cambió, el contenido difiere, la lectura falla o no puede validarse la ruta padre, BOTPYTON omite la restauración y conserva el estado externo observado. No pisa ciegamente un cambio más reciente. Cuando se había creado un archivo nuevo, no existía una versión anterior para restaurar y BOTPYTON no elimina automáticamente ese archivo: informa de que hay que inspeccionarlo. Un fallo durante la restauración también requiere inspección manual del archivo y del registro de auditoría.

Esta compensación no es una transacción ACID ni revierte cambios externos hechos después de la comprobación final. La política se limita a evitar que el propio mecanismo de recuperación sobrescriba un destino cuya identidad/contenido ya no coincide con la publicación de BOTPYTON; los procesos externos no cooperativos conservan una pequeña ventana de carrera inherente al sistema de archivos. Tras un error posterior a la publicación, lee el mensaje de resultado y verifica tanto el hash del archivo como el registro antes de reintentar.

## Límites de seguridad

- El acceso se limita a una raíz identificada por ID y a un permiso explícito.
- Se rechazan traversal (..), rutas absolutas, rutas de unidad/flujo de Windows, nombres de dispositivo reservados, enlaces simbólicos y puntos de reanálisis.
- Se bloquean por defecto directorios del sistema/proyecto como .git, .ssh, .cerebro, .venv, venv, node_modules, __pycache__, .idea y .vscode, así como nombres y extensiones comunes de secretos.
- Las listas son de un solo nivel y están limitadas a 500 entradas. Lecturas y escrituras se limitan por raíz y nunca superan 200 KB.
- Cada operación registra intención y resultado en JSONL, sin guardar el contenido de los archivos. Cada evento incluye el hash del evento anterior; antes de añadir un evento se valida toda la cadena.
- Las escrituras cooperativas se serializan por raíz mediante `.botpyton-write-<hash>.lock`. Ese sidecar es intencionalmente persistente, se oculta de los listados y el agente lo protege frente a lectura/escritura por su interfaz. No lo borres mientras haya procesos que puedan escribir: eliminar un lock activo puede romper la coordinación. Los procesos externos que ignoren el lock o un atacante del mismo usuario pueden seguir creando carreras; esto no es un bloqueo global del sistema de archivos.
- Si el registro falta, no puede escribirse, excede sus límites o no supera la validación, la operación se bloquea antes del acceso solicitado. El registro nunca rota automáticamente; al alcanzar el límite hay que archivarlo de forma consciente y configurar un nuevo registro.
- El registro encadenado ayuda a detectar ediciones accidentales o alteraciones parciales. No es invulnerable frente a un administrador local o un atacante que pueda cambiar tanto el registro como la aplicación.
- En Windows, las lecturas acotadas abren el archivo con un handle nativo de Win32, `FILE_FLAG_OPEN_REPARSE_POINT` y un modo conservador de compartición. Se rechazan las hojas que son reparse points y se lee del objeto ya abierto; esto reduce la posibilidad de que el archivo-hoja sea sustituido entre comprobaciones. La identidad de la ruta se vuelve a comprobar por el agente.
- En Windows, el listado de carpetas y las escrituras intentan mantener handles abiertos para cada componente de directorio desde el ancla de la ruta hasta la carpeta objetivo, sin `FILE_SHARE_DELETE`; verifican que los IDs de archivo (cuando el sistema de archivos los proporciona) y atributos del handle coincidan con la ruta. Windows puede permitir ciertos renombres aun con esos handles abiertos, así que no se debe tratar como bloqueo del directorio ni como transacción handle-relative. Si los handles o sus identidades no se pueden verificar, la operación falla cerrada.
- El listado compara la identidad de la cadena de directorios antes y después de enumerar; una sustitución detectada hace que se descarte el resultado. Las escrituras verifican tamaño/hash del temporal y hash del archivo publicado. Para un destino nuevo, la publicación exclusiva no reemplaza un nombre que ya exista.
- En POSIX, la publicación relativa al descriptor evita volver a resolver los directorios padre durante el rename/link y repite la comparación de identidad/hash dentro de la función de publicación. Es una ventana menor, no un compare-and-swap: un proceso externo aún puede escribir sobre el mismo inode entre esa comprobación y el syscall. En Windows, el handle fija el temporal de origen y el destino se especifica por ruta absoluta porque `SetFileInformationByHandle(FileRenameInfo)` puede rechazar un `RootDirectory` no nulo. Para reemplazos existentes se mantiene además un handle del destino con compartición de lectura/eliminación pero sin compartición de escritura mientras se valida y publica; las aperturas incompatibles fallan o hacen que BOTPYTON falle cerradamente. Como permitir la eliminación/renombrado es necesario para el reemplazo atómico, una modificación externa del nombre de destino todavía puede competir con la operación. Ninguna variante ofrece una operación universal de comparación-y-reemplazo (compare-and-swap) basada en el SHA-256 del archivo destino. En Windows, la API de rename puede fallar por permisos, sharing modes o límites del sistema de archivos; BOTPYTON falla cerradamente en vez de degradar a publicación por ruta. En POSIX, si el sistema no soporta los argumentos `dir_fd`, la publicación se rechaza en vez de recurrir a una operación no anclada. Una escritura que falle al verificar o registrar después de publicar puede haber cambiado ya el destino; revisa el archivo y el registro antes de reintentar.
- Estas medidas no son una transacción atómica para todos los sistemas de archivos y no convierten la función en un sandbox frente a otro proceso malicioso del mismo usuario. No uses raíces controladas por software no confiable para operaciones de escritura. La validación de CI no sustituye probar las rutas y permisos elegidos en el PC real.

Fase 46 no implementa un servidor remoto, un instalador de agente, ACL de Windows, elevación administrativa, acceso a carpetas no configuradas, ejecución de procesos ni modificación automática de archivos de configuración o auditoría. La validación de CI no sustituye probar las rutas y permisos elegidos en el PC real.
