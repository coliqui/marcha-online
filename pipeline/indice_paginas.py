"""Arma data.json para el visor de prueba a partir de la salida de ocr_numero.py.

Uso: python3 indice_paginas.py CARPETA_DEL_NUMERO

Por cada página guarda las palabras reconocidas con su caja en milésimas del
ancho y alto de la imagen, para poder marcarlas sobre el escaneo.
"""
import csv, glob, json, os, sys
from PIL import Image

carpeta = sys.argv[1]
paginas = []
for tsv in sorted(glob.glob(f"{carpeta}/p*.tsv")):
    n = os.path.basename(tsv)[1:3]
    iw, ih = Image.open(f"{carpeta}/p{n}.png").size
    palabras, lineas, actual = [], 0, None
    for r in csv.DictReader(open(tsv, encoding="utf-8"), delimiter="\t", quoting=csv.QUOTE_NONE):
        if r["level"] != "5" or not r["text"].strip() or float(r["conf"]) < 0:
            continue
        clave = (r["block_num"], r["par_num"], r["line_num"])
        if clave != actual:
            lineas, actual = lineas + 1, clave
        x, y, w, h = (int(r[k]) for k in ("left", "top", "width", "height"))
        palabras.append([r["text"].strip(), round(x * 1000 / iw), round(y * 1000 / ih),
                         round(w * 1000 / iw), round(h * 1000 / ih), lineas])
    paginas.append({"n": int(n), "img": f"p{n}.jpg", "ar": round(ih / iw, 4), "w": palabras})

json.dump(paginas, open(f"{carpeta}/data.json", "w"), ensure_ascii=False, separators=(",", ":"))
print(f"{len(paginas)} páginas, {sum(len(p['w']) for p in paginas)} palabras")
