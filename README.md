# Marcha online

Colección digital del semanario **Marcha** (Montevideo, 1939–1974) y de **Cuadernos de Marcha**, buscable por texto, fecha, tema y autor. Destino: [marcha.uy](https://marcha.uy).

## Estado

Etapa de prueba. El OCR se probó con 6 números de muestra (1939, 1943, 1952, 1960, 1967, 1973) y con el nº 1000 completo.

Resultado en el nº 1000 (palabras válidas reconocidas en todo el número):

| Método | Palabras válidas |
|---|---|
| Acrobat Pro | ~4.600 |
| Capa ABBYY que traen los PDFs | 12.812 |
| Tesseract sin preparar la imagen | 9.025 |
| **Tesseract con binarización (elegido)** | **20.680** |

## Contenido

- `pipeline/ocr_numero.py`: corta las páginas dobles, binariza y corre Tesseract en español sobre un PDF. Saca número y fecha del nombre del archivo (`1000-Marzo-11-1960.pdf`).
- `pipeline/indice_paginas.py`: arma `data.json` con las palabras y su posición en cada página.
- `pipeline/procesar_coleccion.py`: corre los dos anteriores sobre toda la colección (carpetas por rollo de microfilm). Se puede cortar y retomar.
- `sitio-prueba/index.html`: visor de prueba de un número con buscador y resaltado sobre el escaneo.

Lectura con IA (modelo mixto Claude Haiku + Claude Sonnet) y catálogo:

- `pipeline/leer_ia.py`: lee cada página con Claude Haiku en una sola pasada (artículos con título, firma, sección, tipo, temas y texto en orden de lectura; número de página impreso; dibujos firmados y humor gráfico con su ubicación; autoevaluación de la lectura). Las páginas dudosas vuelven a leerse con Claude Sonnet, sin pasar un tope de gasto. Une los artículos que siguen en otra página. Las páginas que el filtro de derechos de autor bloquea quedan con el texto de Tesseract. Se puede cortar y retomar.
- `pipeline/simular_escalado.py`: repite sin llamar a la API la regla que decide qué páginas pasan a Sonnet, y estima el costo de la colección completa.
- `pipeline/reunir.py`: vuelve a unir los artículos que siguen en otra página, sin llamar a la API.
- `pipeline/armar_catalogo.py`: arma el catálogo SQLite (`marcha.sqlite`), fuente de verdad del archivo, con búsqueda de texto completo sin tildes. Esquema en `pipeline/catalogo_esquema.md`.
- `pipeline/autores_alias.json`: seudónimos y variantes de firma de autores y dibujantes (por ejemplo, "J. E. Suárez" = Peloduro). Se completa y revisa a mano.
- `pipeline/tapas_grandes.py`: tapas de los 1676 números en buena resolución, del escaneo original.
- `pipeline/armar_sitio_prueba.py` y `sitio-prueba-3/index.html`: sitio de prueba local con buscador, visor con zoom (OpenSeadragon), autores y dibujantes, archivo gráfico y tapas.

Los PDFs y las imágenes no se guardan en este repositorio (pesan ~86 GB); irán a un almacenamiento aparte.

## Requisitos

```
sudo apt-get install poppler-utils tesseract-ocr tesseract-ocr-spa
pip install Pillow opencv-python-headless
```

En macOS: `brew install poppler tesseract tesseract-lang`.

## Uso

```
python3 pipeline/ocr_numero.py "1000-Marzo-11-1960.pdf" salida/1000
python3 pipeline/indice_paginas.py salida/1000
cp sitio-prueba/index.html salida/1000/ && cd salida/1000 && python3 -m http.server
```

Toda la colección (en macOS, `caffeinate` evita que la máquina se duerma):

```
caffeinate -i python3 pipeline/procesar_coleccion.py "MARCHA ONLINE/rollos" "MARCHA ONLINE/salida"
tail -f "MARCHA ONLINE/salida/avance.log"
```

## Prueba de lectura con IA (28/10/2026)

Tres números (nº 30 de 1940, nº 748 de 1955, nº 1477 de 1970; 66 páginas).

| | Por página (precio normal) |
|---|---|
| Claude Haiku | US$ 0,0036 |
| Claude Sonnet | US$ 0,065 |

Con la regla de escalado actual pasan a Sonnet el 18 % de las páginas (las que tienen texto en diagonal o girado leído a medias, a las que les falta mucho texto frente al OCR, o que Haiku no pudo leer). Estimado para las 42.302 páginas: unos US$ 650 a precio normal, unos US$ 325 con la API de lotes.

Limitaciones conocidas: el filtro de Anthropic no transcribe textos conocidos de autores con derechos vigentes (por ejemplo, un ensayo de Cortázar en el nº 1477); esas páginas quedan con el escaneo y el texto de Tesseract, buscable. Las uniones de artículos sin marca "pasa a pág." quedan señaladas para revisar.

Uso (la clave de API se lee de `~/.marcha-clave-anthropic`, fuera del repositorio):

```
python3 pipeline/leer_ia.py rollos/.../30-Enero-12-1940.pdf --salida prueba-ia --tesseract salida --tope 2
python3 pipeline/simular_escalado.py prueba-ia salida
python3 pipeline/armar_catalogo.py salida prueba-ia prueba-ia/marcha.sqlite
python3 pipeline/tapas_grandes.py rollos prueba-ia/sitio/tapas
python3 pipeline/armar_sitio_prueba.py rollos salida prueba-ia prueba-ia/sitio
cp sitio-prueba-3/index.html prueba-ia/sitio/ && python3 -m http.server 8766 --directory prueba-ia/sitio
```

Requiere Python 3.10 o más y `pip install anthropic pydantic Pillow opencv-python-headless numpy`.

## Colección (inventario del 26/10/2026)

1676 PDFs, 89 GB, en 34 carpetas de rollo. Números 1 a 1674 (julio 1939 a 1974), falta el 1242. Casos especiales: `280-misterio-Mayo-4-1945.pdf`, `Chasque1-Agosto-28-1968.pdf` y `Chasque2-Setiembre-04-1968.pdf`.
