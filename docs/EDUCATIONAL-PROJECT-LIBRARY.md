# Biblioteca de proyectos educativos de BOTPYTON

## Objetivo

BOTPYTON incluirá proyectos originales y modificables para aprender cómo se construyen distintas clases de programas. Las referencias a productos conocidos describen una **categoría de software o una función a estudiar**, no una autorización para copiar el producto.

El catálogo estructurado está en `docs/EDUCATIONAL_PROJECT_LIBRARY.json`.

## Catálogo y estado real

| Proyecto educativo | Conceptos que enseña | Estado |
|---|---|---|
| Taller de Imagen | Capas, recorte, filtros, texto, deshacer/rehacer, exportación | Planificado |
| Estudio de Transmisión | Escenas, fuentes, mezcla de audio, grabación, atajos | Planificado |
| Estudio Avatar | Capas 2D, parámetros de ojos/boca, composición, salida transparente | Planificado |
| Canal de Vídeos Local | Biblioteca local, metadatos, listas, búsqueda, subtítulos | Planificado |
| Reproductor Multimedia | Reproducción, listas, volumen, posición, subtítulos | Planificado |
| Mesa de Traducción | Glosarios, memoria de traducción, edición paralela | Planificado |
| Laboratorio de Bots Telegram | Comandos, manejadores, configuración y pruebas | Plantilla inicial disponible |
| Laboratorio RPG de Cartas | Turnos, elementos, criaturas originales y condiciones de victoria | Prototipo web inicial disponible |
| Laboratorio de Aplicaciones de Escritorio | Ventanas, menús, ajustes, archivos y empaquetado | Plantilla inicial disponible |

**Importante:** “plantilla inicial disponible” no significa que exista un producto completo equivalente a OBS, Picsart, un estudio VTuber o YouTube. Cada proyecto deberá desarrollarse y probarse por etapas. BOTPYTON no debe presentarlos como terminados si solo contienen una base mínima.

## Reglas de propiedad intelectual y licencias

1. Implementar las funciones con código propio y una arquitectura independiente.
2. No copiar código propietario, recursos gráficos, logotipos, textos, pantallas ni diseños distintivos de los productos de referencia.
3. No usar nombres de terceros como marca del programa creado; los nombres de referencia solo pueden aparecer en documentación comparativa y de forma clara.
4. Usar recursos propios, de dominio público o con licencias compatibles. Conservar avisos de copyright, atribuciones y archivos de licencia exigidos.
5. Revisar también las licencias de dependencias, frameworks, codecs, modelos, fuentes y formatos de avatar.
6. No asumir que un proyecto queda libre de restricciones por ser educativo, privado o no comercial.
7. En plataformas en línea, respetar las condiciones de uso, permisos, privacidad y API. No añadir scraping, descarga no autorizada ni evasión de controles.
8. Mantener secretos, tokens y credenciales fuera del código y del repositorio.
9. Registrar para cada proyecto sus dependencias, licencias, procedencia de recursos y pruebas.
10. Si no se conoce la licencia de un recurso, marcarlo como `UNKNOWN` y no incorporarlo al paquete distribuible.

## Principios técnicos

- Sin IA obligatoria ni llamadas a servicios de IA.
- Funcionamiento local por defecto cuando el tipo de aplicación lo permita.
- Red desactivada por defecto para los proyectos que no la necesitan.
- No ejecutar comandos detectados o sugeridos automáticamente sin revisión explícita.
- Generar proyectos en carpetas separadas; no sobrescribir archivos existentes.
- Mantener pruebas automatizadas y un manifiesto de dependencias por proyecto.

## Próximo orden de implementación

1. Hacer que la fábrica de proyectos lea y valide el catálogo en vez de duplicar metadatos.
2. Crear una estructura uniforme para cada proyecto: `README`, código fuente, pruebas, manifiesto de dependencias, licencias y estado de funciones.
3. Priorizar una versión local y funcional del **Taller de Imagen** y después el **Estudio de Transmisión**, en incrementos pequeños y verificables.
4. Añadir pruebas de generación, ejecución local, importación/exportación y empaquetado.
5. Solo marcar un proyecto como `available` o `verified` después de demostrarlo con pruebas. No usar esos estados para prototipos sin verificar.
