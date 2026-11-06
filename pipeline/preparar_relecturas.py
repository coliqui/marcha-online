"""Prepara la relectura de páginas ya leídas con las reglas nuevas (etapa 3): páginas incompletas, con tablas y con
carteleras. Mueve sus lecturas a <id>/_etapa2/ (no se borra nada: elegir_lectura las sigue usando como respaldo y
se queda con la mejor) y borra la versión final de la página, para que lotes_ia.py la vuelva a leer.
No se tocan: páginas bloqueadas, páginas que ya leyó Sonnet, ni páginas con correcciones a mano o uniones aplicadas.

Uso: python3 preparar_relecturas.py CARPETA_OCR CARPETA_LECTURA [--aplicar]
(sin --aplicar solo cuenta)
"""
import glob, json, os, re, shutil, sys

ocr_dir, lectura = sys.argv[1:3]
aplicar = "--aplicar" in sys.argv
cor = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "correcciones.json")))["correcciones"]
protegidas = set()
for c in cor:
    protegidas.add((c["numero"], c["pagina"]))
    for clave in ("unir", "destacados", "recuadros"):
        for x in c.get(clave, []):
            protegidas.add((c["numero"], x["pagina"]))
    if c.get("partir"):
        protegidas.add((c["numero"], c["partir"]["a"]["pagina"]))
        protegidas.update((c["numero"], k) for k in c["partir"].get("paginas", []))
YA = {("059", 6), ("1305", 5), ("982", 12), ("1162", 5)}  # prueba de tablas, ya releídas
CARTELERA = re.compile(r"(?i)cartelera|cines? de estreno|gu[ií]a (cinematogr|de espect)|espect[aá]culos|estrenos")


def con_tablas(ocr):
    lineas = [l for l in ocr.split("\n") if l.strip()]
    return sum(1 for l in lineas if len(re.findall(r"\d[\d.,]*", l)) >= 2 and len(l) < 80) >= 8


motivos = {}
for d in sorted(glob.glob(os.path.join(lectura, "*", "articulos.json"))):
    nid = os.path.basename(os.path.dirname(d))
    carp = os.path.dirname(d)
    arts = json.load(open(d))["articulos"]
    cartel = {n for a in arts if CARTELERA.search((a["titulo"] or "") + " " + (a.get("seccion") or "")) for n in a["paginas"]}
    for f in sorted(glob.glob(os.path.join(carp, "p[0-9][0-9].json"))):
        n = int(os.path.basename(f)[1:3])
        p = json.load(open(f))
        o = os.path.join(ocr_dir, nid, f"p{n:02d}.txt")
        ocr = open(o, encoding="utf-8").read() if os.path.exists(o) else ""
        m = []
        if p.get("incompleta") and n != 1:
            m.append("incompleta")
        if con_tablas(ocr):
            m.append("tablas")
        if n in cartel:
            m.append("cartelera")
        if not m or p.get("bloqueada") or p.get("modelo") in ("sonnet", "tesseract") or (nid, n) in protegidas or (nid, n) in YA \
                or os.path.exists(os.path.join(carp, f"p{n:02d}.sonnet.json")):
            continue
        motivos[(nid, n)] = m

# Sonnet: las páginas con pedazos sueltos (proponer_uniones.py), salvo las protegidas o ya leídas por Sonnet
sonnet = set()
prop = os.path.join(lectura, "uniones-propuestas.json")
for nid, lista in (json.load(open(prop)) if os.path.exists(prop) else {}).items():
    for x in lista:
        k = (nid, x["pagina"]); carp = os.path.join(lectura, nid)
        f = os.path.join(carp, f"p{k[1]:02d}.json")
        if k in protegidas or not os.path.exists(f) or os.path.exists(os.path.join(carp, f"p{k[1]:02d}.sonnet.json")):
            continue
        p = json.load(open(f))
        if p.get("bloqueada") or p.get("modelo") == "tesseract":
            continue
        sonnet.add(k)
for k in sonnet:
    motivos.pop(k, None)  # la relee Sonnet: no hace falta Haiku de nuevo
print(f"{len(sonnet)} páginas para Sonnet (pedazos sueltos / estructura)")

from collections import Counter
c = Counter(x for m in motivos.values() for x in m)
print(f"{len(motivos)} páginas para releer: {dict(c)}; protegidas por correcciones: {len(protegidas)}")
if aplicar:
    registro = []
    for (nid, n), m in sorted(motivos.items()):
        carp = os.path.join(lectura, nid)
        os.makedirs(os.path.join(carp, "_etapa2"), exist_ok=True)
        for f in glob.glob(os.path.join(carp, f"p{n:02d}.*json")) + [os.path.join(carp, f"p{n:02d}.json")]:
            nombre = os.path.basename(f)
            if not os.path.exists(f) or nombre.startswith(f"p{n:02d}.bloques"):
                continue  # los bloques en diagonal ya leídos se reusan
            destino = os.path.join(carp, "_etapa2", nombre)
            if not os.path.exists(destino):
                shutil.copy2(f, destino)
            os.remove(f)
        registro.append({"id": nid, "n": n, "motivos": m})
    for nid, n in sorted(sonnet):  # se conserva la lectura de Haiku; solo se aparta la versión final
        carp = os.path.join(lectura, nid); f = os.path.join(carp, f"p{n:02d}.json")
        os.makedirs(os.path.join(carp, "_etapa2"), exist_ok=True)
        if os.path.exists(f):
            if not os.path.exists(os.path.join(carp, "_etapa2", f"p{n:02d}.json")):
                shutil.copy2(f, os.path.join(carp, "_etapa2", f"p{n:02d}.json"))
            os.remove(f)
        registro.append({"id": nid, "n": n, "motivos": ["sonnet"]})
    json.dump([{"id": i, "n": n} for i, n in sorted(sonnet)], open(os.path.join(lectura, "forzar-sonnet.json"), "w"))
    json.dump(registro, open(os.path.join(lectura, "relecturas-etapa3.json"), "w"), ensure_ascii=False, indent=1)
    print("movidas a _etapa2; lista en relecturas-etapa3.json")
