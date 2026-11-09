"""Achica la lista de Revisión: marca como «resuelto automáticamente» lo que se puede verificar con evidencia
objetiva (con la razón, y se puede reabrir desde el sitio) y le da al resto una prioridad según cuánto afecta al
lector, con una frase de qué mirar. Corre después de armar_sitio_prueba.py y reescribe datos/revisar.json.

Uso: python3 priorizar_revision.py CARPETA_OCR CARPETA_LECTURA CARPETA_SITIO
"""
import json, os, re, sys

ocr_dir, lectura, sitio = sys.argv[1:4]
arch = os.path.join(sitio, "datos", "revisar.json")
casos = [c for c in json.load(open(arch)) if not c.get("carta_suelta")]  # se recalculan abajo (sin duplicar)
correcciones = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "correcciones.json")))["correcciones"]

palabras = lambda t: len(re.findall(r"\w{2,}", t or ""))
# palabras «de verdad» del OCR: 3+ letras con vocal (descarta la basura de manchas y bordes)
reales = lambda t: len([w for w in re.findall(r"[^\W\d_]{3,}", t or "") if re.search("[aeiouáéíóú]", w, re.I)])
_cache = {}


def pagina(nid, n):
    if (nid, n) not in _cache:
        f = os.path.join(lectura, nid, f"p{n:02d}.json")
        p = json.load(open(f)) if os.path.exists(f) else {}
        o = os.path.join(ocr_dir, nid, f"p{n:02d}.txt")
        ocr = open(o, encoding="utf-8").read() if os.path.exists(o) else ""
        ia = sum(palabras(a["texto"]) for a in p.get("articulos", []))
        _cache[(nid, n)] = (p, ia, reales(ocr))
    return _cache[(nid, n)]


_datos = {}


def articulo(nid, i):
    if nid not in _datos:
        _datos[nid] = json.load(open(os.path.join(sitio, "datos", f"{nid}.json")))["articulos"]
    return _datos[nid][i] if i is not None and i < len(_datos[nid]) else None


def entre_comillas(texto):
    return re.findall(r"«([^»]{1,80})»", texto or "")


def evaluar(c):
    """-> (razón de resuelto automático o None, prioridad 0-100, qué mirar)"""
    g, nid, n = c["grupo"], c["id"], c["n"]
    p, ia, ocr = pagina(nid, n)
    cobertura = ia / ocr if ocr else 1
    a = articulo(nid, c.get("art"))
    if g == "bloqueadas":
        return "La IA no la transcribe por derechos de autor; el escaneo y el texto de Tesseract ya son buscables. No hay nada que corregir.", 0, ""
    if g == "corregidos":
        return "Corrección hecha a mano: ya está aplicada.", 0, ""
    if g == "falsas_diagonales":
        return "El ángulo medido en el escaneo confirma que el texto está derecho (menos de 15° o más de 75°).", 0, ""
    if g == "relecturas_descartadas" and not p.get("incompleta"):
        return f"La lectura que quedó está completa: tiene el {min(100, round(100 * cobertura))}% de las palabras de Tesseract.", 0, ""
    if g == "incompletas":
        if n == 1:
            return "Es la tapa: solo tiene titulares y llamadas; los textos siguen adentro.", 0, ""
        if cobertura >= 0.85:
            return f"La lectura tiene el {min(100, round(100 * cobertura))}% de las palabras de Tesseract: no falta texto.", 0, ""
        falta = max(0, ocr - ia)
        return None, min(35, 10 + falta // 60), f"Faltan unas {falta} palabras frente a Tesseract; se relee sola en la etapa 3. ¿Está bien dejarla para esa relectura?"
    if g == "repetidos":
        if cobertura >= 0.8:
            return f"Sin lo repetido queda el {min(100, round(100 * cobertura))}% de las palabras de Tesseract: no se perdió texto.", 0, ""
        return None, 60, "Se sacó texto que la IA copió dos veces. ¿Está entero el texto que quedó?"
    if g == "uniones_aplicadas":
        motivos = [x.get("motivo", "") for x in correcciones if x.get("automatica") and x["numero"] == nid
                   and a and x["titulo_contiene"][:30].lower() in a["titulo"].lower()]
        if any("palabra cortada" in m for m in motivos):
            return "La unión empalma una palabra cortada (por ejemplo «porcenta-» + «je»): es la continuación.", 0, ""
        return None, 25, "Se unió un pedazo suelto a esta nota porque repite su título. ¿Es la misma nota?"
    if g in ("partida", "partida_fuerte", "union_dudosa"):
        v = veredicto(c)
        if v and v["misma_nota"] == "no" and v["confianza"] >= 0.8:
            return f"El juez (Sonnet) dice que son notas distintas: {v['razon'][:160]}", 0, ""
        if v:
            c["desc"] += f" Juez: {v['misma_nota']} ({v['confianza']:.0%}): {v['razon'][:160]}"
    if g in ("partida", "partida_fuerte"):
        return None, 53 if c.get("partida_fuerte") else 18, c["mirar_partida"]
    if g == "autor_dudoso":
        return None, 15, c["mirar_autor"]
    if c.get("manual"):
        return None, c.get("prioridad", 60), c["mirar"]
    if g == "estructura" and c.get("carta_suelta"):
        return None, 52, f"¿«{entre_comillas(c['desc'])[0]}» es la continuación o el final de otra carta del pliego? Si es así, decime de cuál (en «No, dejar como está» podés escribirlo)."
    if g == "estructura":
        if "empieza a mitad de frase" in c["desc"]:
            return "Es el mismo caso que figura en «Uniones propuestas» (no se cuenta dos veces).", 0, ""
        if "casi igual" in c["desc"]:
            t = entre_comillas(c["desc"]) + ["", ""]
            return None, 55, f"¿Sacar «{t[0]}» como artículo aparte? Repite parte de «{t[1]}»: es una cita destacada de esa nota o un texto que la IA leyó dos veces."
        t = entre_comillas(c["desc"]) + ["", ""]
        destino = f"a «{t[1]}»" if "es parte de" in c["desc"] else "a la nota de esa firma"
        return None, 50, f"¿Pasar «{t[0]}» a autor (es una firma, no un título) y unir su texto {destino}?"
    if g == "union_dudosa":
        largo = palabras(a["texto"]) if a else 0
        t = entre_comillas(c["desc"]) + ["", "", ""]
        return None, min(20, 5 + largo // 200), f"¿Unir el pedazo suelto «{t[0]}» ({largo} palabras) a la nota «{t[2]}»?"
    if g == "uniones_sin_marca":
        largo = palabras(a["texto"]) if a else 0
        return None, min(48, 10 + largo // 100), f"¿Dejar unidas las partes de «{a['titulo'][:60] if a else ''}» (se unieron sin «pasa a pág.»)?"
    if g == "diagonales":
        return None, 30, "El texto en diagonal se leyó enderezado. ¿Está completo y en su lugar?"
    if g == "bloques_sin_ubicar":
        return None, 40, "Un bloque en diagonal quedó al comienzo del artículo: ver dónde va."
    return None, 20, ""


# «Carta de X» con un título que no está impreso y un texto que empieza a mitad de frase o es solo un cierre:
# suele ser la continuación o el final de otra carta del pliego (caso del nº 1129, señalado por Coli)
import glob, unicodedata
sin = lambda t: unicodedata.normalize("NFD", t or "").encode("ascii", "ignore").decode().lower()
fechas = {c["id"]: c["fecha"] for c in casos}
for f in sorted(glob.glob(os.path.join(sitio, "datos", "*.json"))):
    if not re.match(r"^\d+\.json$", os.path.basename(f)):
        continue
    d = json.load(open(f))
    for i, a in enumerate(d["articulos"]):
        if not re.match(r"(?i)^carta (de|del) ", a["titulo"]):
            continue
        n, t = a["paginas"][0], a["texto"].strip()
        o = os.path.join(ocr_dir, d["id"], f"p{n:02d}.txt")
        impreso = os.path.exists(o) and sin(a["titulo"])[:14] in re.sub(r"\s+", " ", sin(open(o, encoding="utf-8").read()))
        if not impreso and (re.match(r"^[a-záéíóúñ….]", t) or len(t.split()) < 80):
            casos.append({"grupo": "estructura", "id": d["id"], "fecha": d["fecha"], "n": n, "art": i, "zona": None,
                          "desc": f"«{a['titulo'][:60]}» no es un título impreso y su texto empieza «{t[:40]}…».",
                          "carta_suelta": True})

# notas partidas en varias dentro del mismo pliego (detectar_partidas.py)
casos = [c for c in casos if c.get("grupo") not in ("partida", "partida_fuerte")]
_part = os.path.join(lectura, "partidas.json")
for nid, lista in (json.load(open(_part)) if os.path.exists(_part) else {}).items():
    arts = json.load(open(os.path.join(lectura, nid, "articulos.json")))["articulos"]
    fecha = json.load(open(os.path.join(sitio, "datos", f"{nid}.json")))["fecha"] if os.path.exists(os.path.join(sitio, "datos", f"{nid}.json")) else ""
    for x in lista:
        if x["primero"] >= len(arts) or x["segundo"] >= len(arts):
            continue
        a, b = arts[x["primero"]], arts[x["segundo"]]
        fuerte = any(z.startswith("título partido") for z in x["senales"]) and (
            any(z.startswith("mismos nombres") for z in x["senales"]) or (a.get("autor") and a.get("autor") == b.get("autor")))
        casos.append({"grupo": "partida_fuerte" if fuerte else "partida", "id": nid, "fecha": fecha, "n": x["pagina"], "art": x["primero"], "art2": x["segundo"], "zona": None,
                      "desc": f"«{a['titulo'][:50]}» (pág. {', '.join(map(str, a['paginas']))}) y «{b['titulo'][:50]}» "
                              f"(pág. {', '.join(map(str, b['paginas']))}). Señales: {'; '.join(x['senales'])}.",
                      "partida_fuerte": bool(fuerte),
                      "mirar_partida": f"¿«{a['titulo'][:45]}» y «{b['titulo'][:45]}» son una sola nota?"})

# autores que podrían ser la misma persona (unificar_autores.py): para revisar, no se unieron
casos = [c for c in casos if c.get("grupo") != "autor_dudoso"]
_aut = os.path.join(lectura, "autores-dudosos.json")
for d in (json.load(open(_aut)) if os.path.exists(_aut) else []):
    ej = d.get("ejemplo") or {}
    if not ej:
        continue
    fecha = json.load(open(os.path.join(sitio, "datos", f"{ej['id']}.json")))["fecha"] if os.path.exists(os.path.join(sitio, "datos", f"{ej['id']}.json")) else ""
    casos.append({"grupo": "autor_dudoso", "id": ej["id"], "fecha": fecha, "n": ej["n"], "art": ej["art"], "zona": None,
                  "desc": f"«{d['forma']}» ({d['tipo']}); posibles: {', '.join('«' + x + '»' for x in d['candidatos'][:5])}.",
                  "mirar_autor": f"¿«{d['forma']}» es la misma persona que «{d['candidatos'][0]}»?"})

# casos puestos a mano (lectura-ia/casos-manuales.json): van siempre a «Para Coli»
_man = os.path.join(lectura, "casos-manuales.json")
casos = [c for c in casos if not c.get("manual")]
for m in (json.load(open(_man)) if os.path.exists(_man) else []):
    casos.append({**m, "manual": True, "art": m.get("art"), "zona": None})

# veredictos del juez de uniones (juez_uniones.py), identificados por título y comienzo de cada texto
import unicodedata as _ud
_sin = lambda t: " ".join(_ud.normalize("NFD", t or "").encode("ascii", "ignore").decode().lower().split())
_vf = os.path.join(lectura, "juez", "veredictos.json")
VEREDICTO = {}
for v in (json.load(open(_vf)) if os.path.exists(_vf) else []):
    VEREDICTO[(v["nid"], frozenset((v["a"], v["b"])))] = v
_cache_arts = {}
def veredicto(c):
    i, j = c.get("art"), c.get("art2")
    if i is None or j is None:
        return None
    arts = _cache_arts.setdefault(c["id"], json.load(open(os.path.join(lectura, c["id"], "articulos.json")))["articulos"])
    if max(i, j) >= len(arts):
        return None
    d = lambda x: _sin(x["titulo"])[:40] + "|" + _sin(x["texto"].strip())[:30]
    return VEREDICTO.get((c["id"], frozenset((d(arts[i]), d(arts[j])))))

auto = 0
for c in casos:
    razon, prioridad, mirar = evaluar(c)
    c["auto"], c["prioridad"], c["mirar"] = razon, prioridad, mirar
    auto += bool(razon)
json.dump(casos, open(arch, "w"), ensure_ascii=False, separators=(",", ":"))
quedan = [c for c in casos if not c["auto"]]
print(f"{len(casos)} casos: {auto} resueltos automáticamente, {len(quedan)} quedan "
      f"({sum(c['prioridad'] >= 50 for c in quedan)} con prioridad alta)")
