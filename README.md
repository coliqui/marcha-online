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

## Colección (inventario del 26/10/2026)

1676 PDFs, 89 GB, en 34 carpetas de rollo. Números 1 a 1674 (julio 1939 a 1974), falta el 1242. Casos especiales: `280-misterio-Mayo-4-1945.pdf`, `Chasque1-Agosto-28-1968.pdf` y `Chasque2-Setiembre-04-1968.pdf`.
