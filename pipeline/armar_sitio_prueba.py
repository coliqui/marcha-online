"""Arma el sitio de prueba local a partir de la lectura con IA y del OCR.

Uso: python3 armar_sitio_prueba.py CARPETA_ROLLOS CARPETA_OCR CARPETA_IA CARPETA_SITIO

- tapas/: tapa de cada número (si tapas_grandes.py no las dejó antes, se usa la imagen web).
- paginas/<id>/: imagen web (pNN.jpg) y en alta resolución (pNN-hd.jpg) de cada página.
- ilustraciones/: dibujos firmados y humor gráfico, recortados del escaneo original.
- datos/: numeros.json (toda la colección), <id>.json (páginas y artículos), <id>-palabras.json
  (posición de cada palabra del OCR, que el sitio carga solo al abrir ese número)
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

def caja_en_pagina(bloque, margen=0.0):
    """Caja [x0, y0, x1, y1] en milésimas de una de las dos franjas -> en milésimas de la página entera."""
    a, b = ARRIBA if bloque["imagen"] == "arriba" else ABAJO
    x0, y0, x1, y1 = (max(0, min(1000, v)) / 1000 for v in bloque["caja"])
    return [round(max(0, x0 - margen) * 1000), round(max(0, a + y0 * (b - a) - margen) * 1000),
            round(min(1, x1 + margen) * 1000), round(min(1, a + y1 * (b - a) + margen) * 1000)]


revisar = []  # casos para revisar a mano, con el lugar de la página donde mirar


def caso(grupo, meta, n, descripcion, articulo=None, zona=None):
    revisar.append({"grupo": grupo, "id": meta["id"], "fecha": meta["fecha"], "n": n,
                    "desc": descripcion, "art": articulo, "zona": zona})


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
                        "ar": palabras[n]["ar"], "impresa": p.get("pagina_impresa"),
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
    # Casos para revisar
    for n, p in lecturas.items():
        if p.get("bloqueada"):
            caso("bloqueadas", meta, n, "La IA no la transcribió por derechos de autor: queda el escaneo y el texto de Tesseract.")
        elif p.get("incompleta"):
            caso("incompletas", meta, n, "Ninguna lectura quedó completa. " + (p.get("omisiones") or p.get("notas") or "")[:200])
        for b in p.get("bloques", []):
            if b.get("ubicacion") == "sin_ubicar":
                caso("bloques_sin_ubicar", meta, n, "Bloque en diagonal enderezado que no se pudo ubicar; quedó al comienzo del "
                     "artículo. Empieza: «" + b["texto"][:90].replace("\n", " ") + "…»")
        h = json.load(open(os.path.join(carpeta_ia, f"p{n:02d}.haiku.json"))) \
            if os.path.exists(os.path.join(carpeta_ia, f"p{n:02d}.haiku.json")) else {}
        for b in h.get("bloques_confirmados", []):
            caso("diagonales", meta, n, f"Bloque en diagonal ({abs(b['angulo']):.0f}°) que se leyó enderezado: verificar que "
                 "el texto esté completo y en su lugar.", zona=caja_en_pagina(b, 0.03))
        confirmadas = [c["caja"] for c in h.get("bloques_confirmados", [])]
        falsos = [b for b in h.get("bloques_girados", []) if b["caja"] not in confirmadas]
        if falsos and "bloques_confirmados" in h:
            caso("falsas_diagonales", meta, n, f"La IA marcó {len(falsos)} bloque(s) como girados, pero el ángulo medido "
                 "dice que están derechos: se ignoraron. Verificar que no haya texto inclinado sin leer.",
                 zona=caja_en_pagina(falsos[0], 0.03))
        if os.path.exists(os.path.join(carpeta_ia, f"p{n:02d}.sonnet-trunca.json")) and not p.get("incompleta"):
            caso("relecturas_descartadas", meta, n, "Sonnet abrevió esta página y se descartó su relectura; quedó la de "
                 "Haiku. Verificar que el texto esté entero.")
    for i, a in enumerate(articulos):
        if a.get("union_sin_marca"):
            caso("uniones_sin_marca", meta, a["paginas"][0], f"«{a['titulo'][:70]}» se unió entre las págs. "
                 f"{', '.join(map(str, a['paginas']))} sin marca «pasa a pág.»: verificar que sea el mismo texto.", articulo=i)
        if a.get("corregido_a_mano"):
            caso("corregidos", meta, a["paginas"][0], f"«{a['titulo'][:70]}» tiene correcciones hechas a mano.", articulo=i)

    # las posiciones de las palabras (para marcar lo buscado) van aparte: el sitio las carga al abrir el número
    json.dump({n: palabras[n]["w"] for n in palabras}, open(os.path.join(sitio, "datos", f"{nid}-palabras.json"), "w"),
              ensure_ascii=False, separators=(",", ":"))
    json.dump({"id": nid, "numero": meta["numero"], "fecha": meta["fecha"], "rollo": meta.get("rollo"),
               "paginas": paginas, "articulos": articulos},
              open(os.path.join(sitio, "datos", f"{nid}.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    print(f"{nid}: {len(paginas)} páginas, {len(articulos)} artículos, ilustraciones {total_ilus} detectadas -> "
          f"{sum(len(p['ilustraciones']) for p in paginas)} en el archivo gráfico")

json.dump(revisar, open(os.path.join(sitio, "datos", "revisar.json"), "w"), ensure_ascii=False, separators=(",", ":"))
print(f"{len(revisar)} casos para revisar")
