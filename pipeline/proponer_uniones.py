"""Propone a qué artículo pertenece cada pedazo suelto: un texto que empieza a mitad de frase y no se unió a nada
(casi siempre la continuación de una nota de otra página, con la volanta o el intertítulo tomados como título).
No cambia nada: deja las propuestas en CARPETA_LECTURA/uniones-propuestas.json para revisarlas en el sitio.

Uso: python3 proponer_uniones.py CARPETA_LECTURA

Señales, para cada candidata (una nota del mismo número que termina cortada en una página igual o anterior):
- palabra cortada: la candidata termina en "situa-" (o "situa") y el pedazo empieza con "ción", y "situación"
  existe en el número;
- la candidata dice "pasa a la pág. N" (o continua_en_pagina) y el pedazo está en la pág. N;
- el título del pedazo repite palabras del título de la candidata (las volantas de continuación: "DUELOS...");
- la candidata termina sin punto (a mitad de frase);
- cercanía: el mismo pliego o el siguiente.
Segura: palabra cortada que arma una palabra, o "pasa a" a esa página, o título repetido, y ninguna otra candidata
con la misma fuerza. Dudosa: hay candidatas pero ninguna clara. Sin candidata: ninguna nota termina cortada.
"""
import glob, json, os, re, sys, unicodedata

sin = lambda t: unicodedata.normalize("NFD", t or "").encode("ascii", "ignore").decode().lower()
pal = lambda t: re.findall(r"[a-zñ]+", sin(t))
VACIAS = set("para como sobre entre desde hasta donde cuando porque pero esta este estos estas todo todos todas "
             "una unos unas que del los las por con sin ante tras".split())


def proponer(d, ocr_vocab):
    arts = d["articulos"]
    enf = {p["n"]: p.get("enfrentada") for p in d["paginas"]}
    from collections import Counter
    veces = Counter(ocr_vocab)
    for a in arts:
        veces.update(pal(a["texto"]))
    vocab = set(veces)
    palabra = lambda w: veces[w] >= 4  # una palabra de verdad (los pedazos de palabras cortadas aparecen poco)
    salida = []
    for i, f in enumerate(arts):
        t = f["texto"].strip()
        if len(f["paginas"]) != 1 or f["tipo"] in ("poema", "aviso", "ocr") or not re.match(r"^[a-záéíóúñ]", t):
            continue
        n = f["paginas"][0]
        primera = (pal(t[:40]) or [""])[0]
        candidatas = []
        for j, c in enumerate(arts):
            if j == i or c["tipo"] in ("aviso", "ocr") or max(c["paginas"]) > n and enf.get(n) not in c["paginas"]:
                continue
            fin = c["texto"].rstrip()
            if not fin:
                continue
            ult = (re.findall(r"[^\W\d_]+-?$", fin) or [""])[0]
            señales, puntos = [], 0
            corte = ult.rstrip("-")
            if not re.fullmatch(r"[a-zñ]{1,}", sin(corte)) or len(sin(corte)) != len(corte):
                corte = ""  # "1.º", símbolos: no es una palabra cortada
            if corte and primera and (sin(corte) + primera) in vocab and sin(corte) + primera not in VACIAS and \
                    (ult.endswith("-") or (sin(corte) not in vocab or primera not in vocab)):
                señales.append(f"palabra cortada «{corte}-{primera}»"); puntos += 3
            pasa = re.findall(r"pasa a (?:la )?p[aá]g(?:ina|\.)?\s*(\d+)", sin(fin[-80:]))
            tc0 = {w for w in pal(c["titulo"]) if len(w) >= 5 and w not in VACIAS}
            ya_sigue = any(o is not c and o is not f and n in o["paginas"] and
                           len(tc0 & {w for w in pal(o["titulo"]) if len(w) >= 5}) >= min(2, len(tc0) or 9) for o in arts)
            if not ya_sigue and (c.get("continua_en_pagina") == n or (pasa and int(pasa[-1]) in (n, f.get("pagina_impresa")))):
                señales.append("dice «pasa a» esta página"); puntos += 3
            tf = {w for w in pal(f["titulo"]) if len(w) >= 5 and w not in VACIAS}
            tc = {w for w in pal(c["titulo"]) if len(w) >= 5 and w not in VACIAS}
            if tf and len(tf & tc) >= max(1, min(2, len(tf))):
                señales.append("el título repite el de la nota"); puntos += 3
            fuerte = any(x.startswith(("palabra", "dice")) for x in señales)
            if 1 in c["paginas"] and not any(x.startswith("dice") for x in señales):
                continue  # una llamada de tapa sin "pasa a pág." no es el comienzo de la nota
            if primera and not palabra(primera) and not any(x.startswith("palabra") for x in señales):
                puntos -= 2; señales.append("el pedazo empieza con media palabra que no completa el final de la nota")
            if not re.search(r"[.!?»”\")]\s*$", fin) or fin.endswith("-"):
                señales.append("la nota termina a mitad de frase"); puntos += 1
            else:
                continue  # una nota que termina con punto no sigue en otro lado (salvo "pasa a", ya contado)
            if n in c["paginas"] or enf.get(n) in c["paginas"]:
                puntos += 1; señales.append("en el mismo pliego")
            elif any(abs(n - k) <= 3 for k in c["paginas"]):
                puntos += 0.5
            candidatas.append((puntos, j, señales))
        candidatas.sort(key=lambda x: -x[0])
        if not candidatas:
            clase = "sin_candidata"
        else:
            p0 = candidatas[0][0]; p1 = candidatas[1][0] if len(candidatas) > 1 else 0
            clave = any(x.startswith(("palabra", "el título")) for x in candidatas[0][2])  # "pasa a" solo no alcanza
            clase = "segura" if p0 >= 4 and p0 - p1 >= 2 and clave else "dudosa"
        salida.append({"pedazo": i, "pagina": n, "clase": clase,
                       "candidatas": [{"articulo": j, "puntos": p, "senales": s} for p, j, s in candidatas[:3]]})
    return salida


if __name__ == "__main__":
    lectura = sys.argv[1]
    ocr = os.path.join(os.path.dirname(lectura.rstrip("/")), "salida")
    todo = {}
    for f in sorted(glob.glob(os.path.join(lectura, "sitio", "datos", "*.json"))):
        if not re.match(r"^\d+\.json$", os.path.basename(f)):
            continue
        d = json.load(open(f))
        vocab = []
        for t in glob.glob(os.path.join(ocr, d["id"], "p*.txt")):
            vocab += pal(open(t, encoding="utf-8").read())
        todo[d["id"]] = proponer(d, vocab)
    json.dump(todo, open(os.path.join(lectura, "uniones-propuestas.json"), "w"), ensure_ascii=False, indent=1)
    from collections import Counter
    c = Counter(x["clase"] for v in todo.values() for x in v)
    print(dict(c), "total", sum(c.values()))
