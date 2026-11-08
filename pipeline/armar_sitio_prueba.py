"""Arma el sitio de prueba local a partir de la lectura con IA y del OCR.

Uso: python3 armar_sitio_prueba.py CARPETA_ROLLOS CARPETA_OCR CARPETA_IA CARPETA_SITIO

- tapas/: tapa de cada número (si tapas_grandes.py no las dejó antes, se usa la imagen web).
- paginas/<id>/: imagen web (pNN.jpg) y en alta resolución (pNN-hd.jpg) de cada página.
- ilustraciones/: dibujos firmados y humor gráfico, recortados del escaneo original.
- datos/: numeros.json (toda la colección), <id>.json (páginas y artículos), <id>-palabras.json
  (posición de cada palabra del OCR, que el sitio carga solo al abrir ese número)
  y alias.json (seudónimos y variantes de firma de autores y dibujantes).
"""
import unicodedata, re
import glob, json, os, shutil, subprocess, sys, tempfile

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from leer_ia import ARRIBA, ABAJO, paginas_de, pliegos

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
# los datos se arman en una carpeta aparte y recién al final reemplazan a los anteriores: si el armado se corta,
# el sitio sigue con la última versión completa
DATOS = os.path.join(sitio, "datos.nuevo")
shutil.rmtree(DATOS, ignore_errors=True)
os.makedirs(DATOS, exist_ok=True)
for d in ("tapas", "paginas", "ilustraciones", "datos"):
    os.makedirs(os.path.join(sitio, d), exist_ok=True)
shutil.copy(os.path.join(AQUI, "autores_alias.json"), os.path.join(DATOS, "alias.json"))

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
json.dump(numeros, open(os.path.join(DATOS, "numeros.json"), "w"), ensure_ascii=False, separators=(",", ":"))
print(f"{len(numeros)} tapas")

def caja_en_pagina(bloque, margen=0.0):
    """Caja [x0, y0, x1, y1] en milésimas de una de las dos franjas -> en milésimas de la página entera."""
    a, b = ARRIBA if bloque["imagen"] == "arriba" else ABAJO
    x0, y0, x1, y1 = (max(0, min(1000, v)) / 1000 for v in bloque["caja"])
    return [round(max(0, x0 - margen) * 1000), round(max(0, a + y0 * (b - a) - margen) * 1000),
            round(min(1, x1 + margen) * 1000), round(min(1, a + y1 * (b - a) + margen) * 1000)]


revisar = []  # casos para revisar a mano, con el lugar de la página donde mirar


def ubicar_articulos(articulos, palabras):
    """Zona aproximada de cada artículo en cada una de sus páginas, en milésimas [x0, y0, x1, y1]: se buscan sus
    palabras propias (las que no tiene ningún otro artículo de esa página) entre las palabras de Tesseract y se
    toma el rectángulo que las encierra (sin los puntos sueltos). Sirve para ordenar la columna de artículos como
    se lee la página y para marcar el artículo sobre el escaneo."""
    norm = lambda t: [w for w in re.findall(r"[a-zñ]{4,}", unicodedata.normalize("NFKD", t.lower()).encode("ascii", "ignore").decode().replace("~", "")) ]
    por_pagina = {}
    for i, a in enumerate(articulos):
        for n in a["paginas"]:
            por_pagina.setdefault(n, []).append(i)
    for n, idx in por_pagina.items():
        ws = palabras.get(n, {}).get("w", [])
        if not ws:
            continue
        conjuntos = {i: set(norm(articulos[i]["titulo"] + " " + articulos[i]["texto"])) for i in idx}
        puntos = {i: [] for i in idx}
        for w in ws:
            t = norm(w[0])
            if len(t) != 1:
                continue
            duenos = [i for i in idx if t[0] in conjuntos[i]]
            if len(duenos) == 1:
                puntos[duenos[0]].append(w)
        for i, ps in puntos.items():
            if len(ps) < 6:
                continue
            xs = sorted(p[1] for p in ps); ys = sorted(p[2] for p in ps)
            xe = sorted(p[1] + p[3] for p in ps); ye = sorted(p[2] + p[4] for p in ps)
            q = lambda v, f: v[min(len(v) - 1, int(f * len(v)))]
            articulos[i].setdefault("zonas", {})[str(n)] = [q(xs, .04), q(ys, .04), q(xe, .96), q(ye, .96)]


def dudas_de_titulos(articulos, enfrentadas):
    """Casos dudosos de estructura, para revisar a mano (no se cambia nada):
    - un texto corto que aparece casi igual dentro de otro artículo del pliego (cita destacada o texto leído dos veces);
    - un "título" que es una firma ("Escribe X", "Por X") y cuyo autor firma otro artículo del pliego;
    - un texto que empieza a mitad de frase y no se unió a nada (pedazo de otro artículo, con volanta o intertítulo
      tomado como título)."""
    from difflib import SequenceMatcher
    sin = lambda t: unicodedata.normalize("NFD", t or "").encode("ascii", "ignore").decode().lower()
    tok = lambda t: re.findall(r"[a-z0-9]{2,}", sin(t))
    pliego = lambda a: {k for n in a["paginas"] for k in (n, enfrentadas.get(n)) if k}
    salida = []
    for i, a in enumerate(articulos):
        if a["tipo"] in ("aviso", "ocr", "pie de foto", "humor"):
            continue
        ta = tok(a["texto"])
        if 6 <= len(ta) <= 90:
            for c in articulos:
                if c is a or len(tok(c["texto"])) < 3 * len(ta) or not (pliego(a) & set(c["paginas"])):
                    continue
                m = SequenceMatcher(None, ta, tok(c["texto"]), autojunk=False)
                if sum(b.size for b in m.get_matching_blocks() if b.size >= 3) >= 0.8 * len(ta):
                    salida.append((a["paginas"][0], f"«{a['titulo'][:60]}» está casi igual dentro de «{c['titulo'][:50]}»: "
                                   "¿es una cita destacada (recuadro) o un texto leído dos veces?", i))
                    break
        m = re.match(r"^(escribe[n]?|por|de nuestro (?:corresponsal|enviado)(?: especial)?)[:\s]+(.+)$", a["titulo"].strip(), re.I)
        if m and len(a["titulo"].split()) <= 8:
            ape = [w for w in tok(m.group(2)) if len(w) >= 4]
            socio = next((c for c in articulos if c is not a and c.get("autor") and pliego(a) & set(c["paginas"])
                          and any(w in tok(c["autor"]) for w in ape)), None)
            if socio or m.group(1).lower().startswith("escribe"):
                salida.append((a["paginas"][0], f"«{a['titulo'][:60]}» parece una firma, no un título"
                               + (f": ¿es parte de «{socio['titulo'][:50]}»?" if socio else "."), i))
        if len(a["paginas"]) == 1 and re.match(r"^[a-záéíóúñ]", a["texto"].strip()) and a["tipo"] != "poema":
            salida.append((a["paginas"][0], f"«{a['titulo'][:60]}» empieza a mitad de frase («{a['texto'].strip()[:40]}…»): "
                           "parece la continuación de otro artículo, y su título una volanta o intertítulo.", i))
    return salida


def caso(grupo, meta, n, descripcion, articulo=None, zona=None):
    revisar.append({"grupo": grupo, "id": meta["id"], "fecha": meta["fecha"], "n": n,
                    "desc": descripcion, "art": articulo, "zona": zona})


# Números leídos con IA
_prop = os.path.join(ia, "uniones-propuestas.json")
PROPUESTAS = json.load(open(_prop)) if os.path.exists(_prop) else {}
for art in sorted(glob.glob(os.path.join(ia, "*", "articulos.json"))):
    carpeta_ia = os.path.dirname(art)
    nid = os.path.basename(carpeta_ia)
    meta = json.load(open(os.path.join(ocr, nid, "numero.json")))
    palabras = {p["n"]: p for p in json.load(open(os.path.join(ocr, nid, "data.json")))}
    destino = os.path.join(sitio, "paginas", nid)
    os.makedirs(destino, exist_ok=True)

    lecturas, paginas = {}, []
    enfrentadas = pliegos(os.path.join(rollos, meta["archivo"]))  # para la vista de doble página
    for n in range(1, meta["paginas"] + 1):
        shutil.copy(os.path.join(ocr, nid, f"p{n:02d}.jpg"), destino)
        p = lecturas[n] = json.load(open(os.path.join(carpeta_ia, f"p{n:02d}.json")))
        paginas.append({"n": n, "img": f"paginas/{nid}/p{n:02d}.jpg", "hd": f"paginas/{nid}/p{n:02d}-hd.jpg",
                        "ar": palabras[n]["ar"], "impresa": p.get("pagina_impresa"), "enfrentada": enfrentadas.get(n),
                        "modelo": p["modelo"], "legibilidad": p["legibilidad"], "notas": p.get("notas"),
                        "bloqueada": p.get("bloqueada", False), "ilustraciones": []})

    # Imagen en alta resolución y recortes de ilustraciones, desde el escaneo original. Primero se calcula qué
    # hace falta; el PDF se abre solo si falta alguna imagen (las imágenes no cambian entre armados).
    total_ilus, recortes, faltan = 0, {}, False
    for n in range(1, meta["paginas"] + 1):
        faltan |= not os.path.exists(os.path.join(destino, f"p{n:02d}-hd.jpg"))
        for k, il in enumerate(lecturas[n].get("ilustraciones", []), 1):
            total_ilus += 1
            if not va_al_archivo_grafico(il):
                continue
            a, b = ARRIBA if il["imagen"] == "arriba" else ABAJO
            x0, y0, x1, y1 = (max(0, min(1000, v)) / 1000 for v in il["caja"])
            if x1 <= x0 or y1 <= y0:
                continue
            fy = lambda v: a + v * (b - a)  # de milésimas de la franja a fracción de la página
            caja = (max(0, x0 - MARGEN), max(0, fy(y0) - MARGEN), min(1, x1 + MARGEN), min(1, fy(y1) + MARGEN))
            nombre = f"ilustraciones/{nid}-p{n:02d}-{k}.jpg"
            recortes.setdefault(n, []).append((caja, nombre))
            faltan |= not os.path.exists(os.path.join(sitio, nombre))
            paginas[n - 1]["ilustraciones"].append({**il, "img": nombre, "pagina_caja": [round(v * 1000) for v in caja]})
    if faltan:
        with tempfile.TemporaryDirectory() as tmp:
            for n, im in enumerate(paginas_de(os.path.join(rollos, meta["archivo"]), tmp), 1):
                if not os.path.exists(os.path.join(destino, f"p{n:02d}-hd.jpg")):
                    hd = im.copy()
                    hd.thumbnail((LADO_HD, LADO_HD), Image.LANCZOS)
                    hd.save(os.path.join(destino, f"p{n:02d}-hd.jpg"), quality=75, optimize=True, progressive=True)
                w, h = im.size
                for caja, nombre in recortes.get(n, []):
                    if os.path.exists(os.path.join(sitio, nombre)):
                        continue
                    recorte = im.crop((int(caja[0] * w), int(caja[1] * h), int(caja[2] * w), int(caja[3] * h)))
                    recorte.thumbnail((LADO_ILUSTRACION, LADO_ILUSTRACION), Image.LANCZOS)
                    recorte.save(os.path.join(sitio, nombre), quality=82, optimize=True, progressive=True)

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
        if p.get("repetidos_quitados", 0) >= 150:
            caso("repetidos", meta, n, f"La IA había copiado dos veces parte del texto: se sacaron {p['repetidos_quitados']} "
                 "palabras repetidas (la lectura original queda guardada). Verificar que no falte nada.")
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
        if a.get("uniones_automaticas"):
            caso("uniones_aplicadas", meta, a["paginas"][0], f"«{a['titulo'][:70]}» se unió automáticamente con el pedazo "
                 f"suelto de la pág. {', '.join(map(str, a['uniones_automaticas']))} (palabra cortada o título repetido). "
                 "Si está mal, anotalo y se deshace.", articulo=i)
        if a.get("corregido_a_mano"):
            caso("corregidos", meta, a["paginas"][0], f"«{a['titulo'][:70]}» tiene correcciones hechas a mano.", articulo=i)

    ubicar_articulos(articulos, palabras)
    for n, desc, i in dudas_de_titulos(articulos, enfrentadas):
        caso("estructura", meta, n, desc, articulo=i)
    # vista previa de las uniones propuestas para los pedazos sueltos (proponer_uniones.py); no se aplica nada
    for x in PROPUESTAS.get(nid, []):
        if x["clase"] == "sin_candidata" or x["pedazo"] >= len(articulos) or \
                x["candidatas"][0]["articulo"] >= len(articulos):
            continue  # propuesta vieja (los artículos cambiaron): se recalcula con proponer_uniones.py
        f, c0 = articulos[x["pedazo"]], x["candidatas"][0]
        c = articulos[c0["articulo"]]
        caso("union_" + x["clase"], meta, x["pagina"], f"«{f['titulo'][:50]}» («{f['texto'].strip()[:35]}…») seguiría a "
             f"«{c['titulo'][:50]}» (pág. {', '.join(map(str, c['paginas']))}, termina «…{c['texto'].rstrip()[-35:]}»). "
             f"Señales: {'; '.join(c0['senales'])}.", articulo=x["pedazo"])
    # las posiciones de las palabras (para marcar lo buscado) van aparte: el sitio las carga al abrir el número
    json.dump({n: palabras[n]["w"] for n in palabras}, open(os.path.join(DATOS, f"{nid}-palabras.json"), "w"),
              ensure_ascii=False, separators=(",", ":"))
    json.dump({"id": nid, "numero": meta["numero"], "fecha": meta["fecha"], "rollo": meta.get("rollo"),
               "paginas": paginas, "articulos": articulos},
              open(os.path.join(DATOS, f"{nid}.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    print(f"{nid}: {len(paginas)} páginas, {len(articulos)} artículos, ilustraciones {total_ilus} detectadas -> "
          f"{sum(len(p['ilustraciones']) for p in paginas)} en el archivo gráfico")

json.dump(revisar, open(os.path.join(DATOS, "revisar.json"), "w"), ensure_ascii=False, separators=(",", ":"))
# referencias entre números (las detecta referencias.py)
if os.path.exists(os.path.join(ia, "referencias.json")):
    shutil.copy(os.path.join(ia, "referencias.json"), os.path.join(DATOS, "referencias.json"))
print(f"{len(revisar)} casos para revisar")

# cambio de golpe: la versión nueva de los datos reemplaza a la anterior
viejo = os.path.join(sitio, "datos.viejo")
shutil.rmtree(viejo, ignore_errors=True)
if os.path.exists(os.path.join(sitio, "datos")):
    os.rename(os.path.join(sitio, "datos"), viejo)
os.rename(DATOS, os.path.join(sitio, "datos"))
shutil.rmtree(viejo, ignore_errors=True)
print("datos del sitio reemplazados")
