# Catálogo de Marcha (`marcha.sqlite`)

Base SQLite que es la fuente de verdad del archivo. La arma `armar_catalogo.py` a partir del OCR
(`salida/`) y de la lectura con IA (`prueba-ia-N/`). Se puede regenerar entera en cualquier momento.
La única información cargada a mano, por ahora, es la tabla de seudónimos (`autores_alias.json`).

## Tablas

| Tabla | Una fila por… | Campos principales |
|---|---|---|
| `numeros` | número del semanario (los 1676) | `id`, `numero`, `fecha`, `rollo`, `archivo` (PDF original), `paginas`, `leido_ia` |
| `paginas` | página escaneada (42.302) | `numero_id`, `n` (orden en el escaneo), `impresa` (número impreso), `imagen`, `imagen_hd`, `lectura` (haiku / sonnet / tesseract), `legibilidad`, `bloqueada`, `notas`, `texto_ocr` |
| `articulos` | texto leído con IA: nota, editorial, cuento, aviso… | `numero_id`, `titulo`, `firma` (como está impresa), `autor_id`, `seccion`, `tipo`, `temas` (JSON), `texto`, `union_sin_marca` |
| `articulo_paginas` | página en la que aparece un artículo | `articulo_id`, `numero_id`, `n`, `orden` (un artículo puede seguir en otras páginas) |
| `autores` | persona (autor de textos o dibujante) | `nombre` canónico, `revisado` (1 si viene de la tabla de seudónimos) |
| `seudonimos` | forma de firmar | `forma` ("J. E. SUAREZ"), `clave` normalizada ("j e suarez"), `autor_id` |
| `obras_graficas` | dibujo firmado o pieza de humor gráfico | `numero_id`, `n`, `tipo`, `descripcion`, `firma`, `autor_id`, `retratado`, `caja` (en milésimas de la página), `imagen` (recorte) |

Las páginas que la IA no transcribió (bloqueadas por derechos de autor) entran en `articulos` como un
texto de tipo `ocr` con el texto de Tesseract, así aparecen en la búsqueda junto con el resto.

## Búsqueda

Tres índices FTS5 que ignoran tildes y mayúsculas (`unicode61 remove_diacritics 2`):

- `buscar_articulos` (título, firma, autor, sección, temas, texto); su `rowid` es `articulos.id`.
- `buscar_obras` (descripción, firma, autor, retratado); su `rowid` es `obras_graficas.id`.
- `buscar_paginas` (texto de Tesseract de cada página de toda la colección, incluso las que todavía no
  tienen lectura con IA); su `rowid` es el `rowid` de `paginas`.

## Cómo se unen las firmas

Cada firma se normaliza (sin tildes, mayúsculas, puntuación ni años: "Camnitzer 1969" → "camnitzer") y se
busca en `seudonimos`. Si ya está, se usa ese autor; si no, se crea un autor nuevo con esa firma, marcado
`revisado = 0`. Las firmas `[ilegible]` no crean autor. Para unir variantes ("J. E. Suárez" = "Peloduro"),
se agregan a `autores_alias.json` y se regenera el catálogo.

## Consultas de ejemplo

```sql
-- Todo lo de Peloduro, firme como firme
SELECT o.numero_id, o.n, o.tipo, o.descripcion FROM obras_graficas o
JOIN seudonimos s ON s.autor_id = o.autor_id WHERE s.clave = 'peloduro';

-- Artículos que mencionan a Cortázar (encuentra "Cortázar" y "CORTAZAR")
SELECT a.numero_id, a.titulo FROM buscar_articulos JOIN articulos a ON a.id = buscar_articulos.rowid
WHERE buscar_articulos MATCH 'cortazar';

-- Páginas de toda la colección donde aparece "Frondizi", por fecha
SELECT p.numero_id, n.fecha, p.n FROM buscar_paginas JOIN paginas p ON p.rowid = buscar_paginas.rowid
JOIN numeros n ON n.id = p.numero_id WHERE buscar_paginas MATCH 'frondizi' ORDER BY n.fecha;
```
