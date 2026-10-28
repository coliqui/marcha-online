"""Arma el sitio de prueba local a partir de la lectura con IA y del OCR.

Uso: python3 armar_sitio_prueba.py CARPETA_ROLLOS CARPETA_OCR CARPETA_IA CARPETA_SITIO

- tapas/: tapa de cada número (si tapas_grandes.py no las dejó antes, se usa la imagen web).
- paginas/<id>/: imagen web (pNN.jpg) y en alta resolución (pNN-hd.jpg) de cada página.
- ilustraciones/: dibujos firmados y humor gráfico, recortados del escaneo original.
- datos/: numeros.json (toda la colección), <id>.json (páginas, palabras, artículos)
  y alias.json (seudónimos y variantes de firma de autores y dibujantes).
"""
import glob, json, os, shutil, subprocess, sys, tempfile

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from leer_ia import ARRIBA, ABAJO, paginas_de

AQUI = os.path.dirname(os.path.abspath(__file__))
ANCHO_TAPA = 480
LADO_HD = 3000  # lado mayor de la imagen para el visor con zoom
LADO_ILUSTRACION = 1600
MARGEN = 0.015  # aire alrededor de cada recorte, en fracción de la página
HUMOR = {"caricatura", "chiste", "historieta"}


def va_al_archivo_grafico(il):
    """Dibujos firmados de cualquier tipo, y el humor gráfico aunque no tenga firma."""
    return bool(il.get("firma")) or il.get("tipo") in HUMOR


rollos, ocr, ia, sitio = sys.argv[1:5]
for d in ("tapas", "paginas", "ilustraciones", "datos"):
    os.makedirs(os.path.join(sitio, d), exist_ok=True)
shutil.copy(os.path.join(AQUI, "autores_alias.json"), os.path.join(sitio, "datos", "alias.json"))

# Tapas de toda la colección
numeros = []
for f in sorted(glob.glob(os.path.join(ocr, "*", "numero.json"))):
    meta = json.load(open(f))
    tapa = os.path.join(sitio, "tapas", f"{meta['id']}.jpg")
    if not os.path.exists(tapa):
        im = Image.open(os.path.join(os.path.dirname(f), "p01.jpg"))
        im.thumbnail((ANCHO_TAPA, ANCHO_TAPA * 3), Image.LANCZOS)
        im.convert("L").save(tapa, quality=78, optimize=True, progressive=True)
    numeros.append({"id": meta["id"], "numero": meta["numero"], "fecha": meta["fecha"], "paginas": meta["paginas"],
                    "ia": os.path.exists(os.path.join(ia, meta["id"], "articulos.json")),
                    "grande": os.path.exists(os.path.join(sitio, "tapas", "grande", f"{meta['id']}.jpg"))})
numeros.sort(key=lambda m: (m["fecha"], m["id"]))
json.dump(numeros, open(os.path.join(sitio, "datos", "numeros.json"), "w"), ensure_ascii=False, separators=(",", ":"))
print(f"{len(numeros)} tapas")

# Números leídos con IA
for art in sorted(glob.glob(os.path.join(ia, "*", "articulos.json"))):
    carpeta_ia = os.path.dirname(art)
    nid = os.path.basename(carpeta_ia)
    meta = json.load(open(os.path.join(ocr, nid, "numero.json")))
    palabras = {p["n"]: p for p in json.load(open(os.path.join(ocr, nid, "data.json")))}
    destino = os.path.join(sitio, "paginas", nid)
    os.makedirs(destino, exist_ok=True)

    lecturas, paginas = {}, []
    for n in range(1, meta["paginas"] + 1):
        shutil.copy(os.path.join(ocr, nid, f"p{n:02d}.jpg"), destino)
        p = lecturas[n] = json.load(open(os.path.join(carpeta_ia, f"p{n:02d}.json")))
        paginas.append({"n": n, "img": f"paginas/{nid}/p{n:02d}.jpg", "hd": f"paginas/{nid}/p{n:02d}-hd.jpg",
                        "ar": palabras[n]["ar"], "w": palabras[n]["w"], "impresa": p.get("pagina_impresa"),
                        "modelo": p["modelo"], "legibilidad": p["legibilidad"], "notas": p.get("notas"),
                        "bloqueada": p.get("bloqueada", False), "ilustraciones": []})

    # Imagen en alta resolución y recortes de ilustraciones, desde el escaneo original
    total_ilus = 0
    pdf = os.path.join(rollos, meta["archivo"])
    with tempfile.TemporaryDirectory() as tmp:
        for n, im in enumerate(paginas_de(pdf, tmp), 1):
            hd = im.copy()
            hd.thumbnail((LADO_HD, LADO_HD), Image.LANCZOS)
            hd.save(os.path.join(destino, f"p{n:02d}-hd.jpg"), quality=75, optimize=True, progressive=True)
            w, h = im.size
            for k, il in enumerate(lecturas[n]["ilustraciones"], 1):
                total_ilus += 1
                if not va_al_archivo_grafico(il):
                    continue
                a, b = ARRIBA if il["imagen"] == "arriba" else ABAJO
                x0, y0, x1, y1 = (max(0, min(1000, v)) / 1000 for v in il["caja"])
                if x1 <= x0 or y1 <= y0:
                    continue
                fy = lambda v: a + v * (b - a)  # de milésimas de la franja a fracción de la página
                caja = (max(0, x0 - MARGEN), max(0, fy(y0) - MARGEN), min(1, x1 + MARGEN), min(1, fy(y1) + MARGEN))
                recorte = im.crop((int(caja[0] * w), int(caja[1] * h), int(caja[2] * w), int(caja[3] * h)))
                recorte.thumbnail((LADO_ILUSTRACION, LADO_ILUSTRACION), Image.LANCZOS)
                nombre = f"ilustraciones/{nid}-p{n:02d}-{k}.jpg"
                recorte.save(os.path.join(sitio, nombre), quality=82, optimize=True, progressive=True)
                paginas[n - 1]["ilustraciones"].append({**il, "img": nombre, "pagina_caja": [round(v * 1000) for v in caja]})

    articulos = json.load(open(art))["articulos"]
    for x in articulos:
        for clave in ("empieza_cortado", "termina_cortado"):
            x.pop(clave, None)
    # Páginas sin lectura con IA (bloqueadas por derechos de autor): el texto de Tesseract, buscable
    for n, p in lecturas.items():
        if p["modelo"] == "tesseract":
            articulos.append({"titulo": f"Texto de la página {n} (OCR automático, sin revisar)", "autor": None,
                              "seccion": None, "tipo": "ocr", "temas": [], "texto": p.get("texto_tesseract", ""),
                              "viene_de_pagina": None, "continua_en_pagina": None, "paginas": [n],
                              "bloqueada": p.get("bloqueada", False)})
    json.dump({"id": nid, "numero": meta["numero"], "fecha": meta["fecha"], "rollo": meta.get("rollo"),
               "paginas": paginas, "articulos": articulos},
              open(os.path.join(sitio, "datos", f"{nid}.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    print(f"{nid}: {len(paginas)} páginas, {len(articulos)} artículos, ilustraciones {total_ilus} detectadas -> "
          f"{sum(len(p['ilustraciones']) for p in paginas)} en el archivo gráfico")
