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
