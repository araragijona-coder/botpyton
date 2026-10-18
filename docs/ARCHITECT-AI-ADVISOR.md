# BOTPYTON — asesoría arquitectónica opcional con cuota gratuita

## Propósito

El núcleo determinista de BOTPYTON sigue funcionando sin IA, sin API, sin servicios en segundo plano y sin dependencias adicionales. Esta función agrega una segunda opinión opcional para interpretar un encargo de programación, priorizar preguntas, detectar riesgos de implementación y proponer pruebas.

El modelo no convierte a BOTPYTON en un programador autónomo: no recibe herramientas del sistema, no cambia archivos, no ejecuta comandos y no publica ni fusiona cambios. Por defecto no recibe código fuente. Opcionalmente puede revisar hasta cuatro archivos que selecciones por ruta exacta, si además de autorizar la API das un segundo consentimiento específico para subir su contenido. Sus sugerencias siguen siendo recomendaciones humanas revisables.

## Proveedor seleccionado: OpenRouter Free Models Router

La implementación fija el proveedor a `https://openrouter.ai/api/v1/chat/completions` y el modelo a `openrouter/free`. La documentación de OpenRouter describe ese router como gratuito para entrada y salida, y selecciona modelos gratuitos disponibles que sean compatibles con la solicitud. El modelo subyacente puede variar entre consultas; no se debe asumir una calidad constante.

La tarifa del plan Free actualmente publica 50 solicitudes diarias para modelos gratuitos y 20 por minuto. Los intentos fallidos cuentan para el tope diario, por eso BOTPYTON no vuelve a intentar automáticamente una solicitud fallida. Los límites del proveedor y sus condiciones pueden cambiar; hay que consultarlos antes de usarlo.

Fuentes oficiales consultadas el 18-10-2026:
- Router/modelo gratuito: https://openrouter.ai/openrouter/free
- Precios y límites de Free: https://openrouter.ai/pricing
- Documentación de ejecución de modelos gratuitos: https://openrouter.ai/docs

No se promete disponibilidad continua ni una cuota ilimitada. La selección de un router de precio cero y la ausencia de reintentos reducen el riesgo de coste, pero el usuario debe mantener la cuenta en el plan gratuito y no cambiar la configuración a un modelo de pago.

## Configuración en Windows

1. Crea una cuenta y una API key en OpenRouter: https://openrouter.ai/keys.
2. Guarda la clave en la variable de entorno de usuario `BOTPYTON_OPENROUTER_API_KEY`, no en un archivo del repositorio, en un JSON de tarea ni en el código. Una forma de hacerlo de manera temporal es la sesión de PowerShell que aparece abajo.
3. Cierra y vuelve a abrir la consola si agregaste la variable desde la configuración de Windows.

Para pedir la clave sin escribirla como literal en la línea de comandos y usarla solo en la sesión actual de PowerShell:

```powershell
$secure = Read-Host "API key de OpenRouter" -AsSecureString
$env:BOTPYTON_OPENROUTER_API_KEY = [System.Net.NetworkCredential]::new("", $secure).Password
python -m bot.main --architect-ai .cerebro/examples/architect-design-brief.json --allow-external-ai
Remove-Item Env:BOTPYTON_OPENROUTER_API_KEY
```

El ejemplo utiliza el brief de arquitectura incluido en el repositorio; para un proyecto real, prepara un JSON con el esquema descrito en `docs/ARCHITECTURE-KERNEL.md`. El archivo debe representar el encargo concreto, evidencia relevante y criterios de aceptación. La opción `--architect-plan` sigue siendo completamente local y no requiere clave:

```powershell
python -m bot.main --architect-plan .cerebro/examples/architect-design-brief.json
```

## Consentimiento y datos enviados

No se hace ninguna solicitud de red con `--architect-ai` a menos que se incluya `--allow-external-ai`. Sin esa segunda opción, BOTPYTON muestra el aviso de consentimiento y termina sin llamar al proveedor.

Por defecto, el módulo remite solamente estos campos estructurados del brief, si están presentes: `schema_version`, `goal`, `task_type`, `observed_error`, `reproduction_steps`, `acceptance_criteria`, `constraints`, `must_not_change` y `available_evidence), junto con un resumen del plan determinista local. Excluye `project_root` y el inventario local del repositorio por defecto; no adjunta logs automáticamente. El contenido de archivos solo se añade mediante rutas seleccionadas y el consentimiento adicional descrito abajo. El detector de secretos cubre patrones comunes, no todos los formatos posibles. Sin embargo, un usuario puede pegar código, credenciales, nombres privados o rutas dentro de campos del brief: esos valores sí serían parte de la solicitud. Revísalos y redáctalos antes de autorizar el envío.

### Contexto de código explícitamente seleccionado (Phase 26)

Para aportar evidencia real al diagnóstico sin subir el repositorio entero, puedes añadir hasta cuatro rutas relativas a `project_root`. Hace falta `--allow-external-ai` y, de forma separada, `--send-source-context`; si falta cualquiera, no se envía el contenido del archivo. Ejemplo:

```powershell
python -m bot.main --architect-ai .cerebro/examples/architect-design-brief.json --allow-external-ai --architect-ai-context bot/queue.py --architect-ai-context tests/test_queue.py --send-source-context
```

Cada archivo debe ser regular, de texto UTF-8, tener una extensión de código/documentación admitida y no superar 16.000 bytes; se permiten cuatro archivos como máximo y 40.000 bytes de contexto en total. El cuerpo de la solicitud completa se limita a 70.000 bytes. Las rutas deben usar separadores `/`, ser relativas a `project_root` y no pueden atravesar directorios. Se rechazan enlaces simbólicos/reparse points, ubicaciones generadas/privadas, archivos con nombres sensibles y patrones comunes que parecen credenciales. Esos filtros reducen el riesgo, pero **no garantizan que cada secreto imaginable sea detectado**.

El módulo transmite solo el contenido de las rutas elegidas; no hace un recorrido automático ni sube el inventario completo. Aun así, revisar el diff/contexto antes de enviar sigue siendo responsabilidad del usuario. El texto del código es entrada no confiable: las instrucciones incrustadas en comentarios, strings, fixtures o documentación no pueden cambiar las reglas del asesor. Cuando recibe contexto de código, el modelo también puede proponer un diff unificado claramente marcado `BORRADOR DE PARCHE (NO APLICADO)`, limitado a las rutas elegidas. Ese diff es texto no confiable, no se aplica ni se ejecuta automáticamente; revisa cada línea y prueba la modificación por separado. Si la evidencia es insuficiente, el modelo debe indicar qué dato bloquea el parche en vez de inventar uno.

La clave se lee de una variable de entorno; nunca se acepta como argumento de la CLI ni se escribe en un informe. La solicitud usa HTTPS, tamaño de petición y respuesta acotados, tiempo límite, límite de tokens y cero reintentos. Los errores de red/cuota se informan sin intentar evadir el límite. Las políticas de retención de los proveedores seleccionados por el router se rigen por sus propias condiciones; no envíes información confidencial si no puedes aceptar esas condiciones.

## Límites del asesor

- No garantiza que una orden sea siempre entendida ni que el código generado por un modelo sea correcto.
- No afirma que un diagnóstico, prueba o cambio se haya hecho; el texto es una recomendación.
- Si faltan datos importantes, el flujo local conserva las preguntas de bloqueo y las condiciones de parada; el modelo no autoriza a saltárselas.
- La respuesta del modelo se trata como contenido no confiable: no se parsea como plan ejecutable, no se convierte en comando y no modifica tareas de la cola.
- La cuota gratuita puede agotarse, un modelo puede dejar de estar disponible o el proveedor puede devolver 429/403. El usuario puede seguir usando las funciones locales sin configurar IA.

## Pruebas

La suite de regresión simula las respuestas de red: verifica consentimiento explícito, endpoint/modelo gratuito fijo, exclusión de la ruta local, límite de tokens, ausencia de reintentos cuando se recibe HTTP 429, bloqueo local sin red, configuración faltante, segundo consentimiento de contexto, límites de rutas y rechazo de contenido con apariencia de secreto. Las pruebas no necesitan una clave real y no consumen cuota del proveedor.
