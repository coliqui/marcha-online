"""OCR de un número de Marcha.

Uso: python3 ocr_numero.py ENTRADA.pdf SALIDA_DIR

El nombre del PDF sigue el formato `numero-Mes-dia-año.pdf` (ej. 1000-Marzo-11-1960.pdf).
Para cada número:
  1. extrae las imágenes escaneadas (pdfimages, sin recomprimir);
  2. corta las páginas dobles (más anchas que altas) en dos páginas sueltas;
  3. reduce a ~300 ppi y binariza con Otsu (blanco y negro puro);
  4. corre Tesseract en español y guarda el texto (.txt) y las cajas de cada palabra (.tsv);
  5. guarda una imagen web (JPEG de 1300 px de ancho) de cada página.

Requiere: poppler-utils, tesseract-ocr, tesseract-ocr-spa, Pillow, opencv-python-headless.
"""
import json, os, re, subprocess, sys, tempfile, glob
from concurrent.futures import ProcessPoolExecutor
import cv2
import numpy as np
from PIL import Image

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
ESCALA_OCR = 0.6      # escaneos a 500 ppi -> 300 ppi, el tamaño que mejor lee Tesseract
ANCHO_WEB = 1300


def metadatos(nombre):
    m = re.match(r"(\d+)-([A-Za-zé]+)-(\d{1,2})-(\d{4})", os.path.basename(nombre))
    if not m:
        raise ValueError(f"nombre de archivo inesperado: {nombre}")
    num, mes, dia, anio = m.groups()
    mes_n = MESES.index(mes.lower().replace("setiembre", "septiembre")) + 1
    return {"numero": int(num), "fecha": f"{anio}-{mes_n:02d}-{int(dia):02d}"}


def ocr_pagina(args):
    png, base = args
    subprocess.run(["tesseract", png, base, "-l", "spa", "--psm", "3", "txt", "tsv"],
                   check=True, capture_output=True, env={**os.environ, "OMP_THREAD_LIMIT": "1"})
    return base


def procesar(pdf, salida, trabajos=os.cpu_count()):
    meta = metadatos(pdf)
    os.makedirs(salida, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdfimages", "-png", pdf, f"{tmp}/i"], check=True)
        tareas, n = [], 1
        for f in sorted(glob.glob(f"{tmp}/i-*.png")):
            im = Image.open(f).convert("L")
            w, h = im.size
            partes = [im] if w < h else [im.crop((0, 0, w // 2, h)), im.crop((w // 2, 0, w, h))]
            for p in partes:
                base = f"{salida}/p{n:02d}"
                web = p.resize((ANCHO_WEB, round(p.height * ANCHO_WEB / p.width)), Image.LANCZOS)
                web.save(base + ".jpg", quality=72, optimize=True, progressive=True)
                chica = p.resize((round(p.width * ESCALA_OCR), round(p.height * ESCALA_OCR)), Image.LANCZOS)
                _, bn = cv2.threshold(np.array(chica), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                cv2.imwrite(base + ".png", bn)
                tareas.append((base + ".png", base))
                n += 1
        with ProcessPoolExecutor(trabajos) as ex:
            list(ex.map(ocr_pagina, tareas))
    meta["paginas"] = n - 1
    json.dump(meta, open(f"{salida}/numero.json", "w"), ensure_ascii=False, indent=1)
    return meta


if __name__ == "__main__":
    print(procesar(sys.argv[1], sys.argv[2]))
