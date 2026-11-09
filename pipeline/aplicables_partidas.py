"""De las posibles notas partidas (partidas.json, subgrupo fuerte: mismo título o título partido + mismo autor o
mismos nombres), elige las que se pueden unir con certeza y en qué orden. Gratis. Escribe partidas-aplicables.json.

Orden y continuidad:
- «Pasa a la pág. N» en una que apunta a la página de la otra, o «(Viene de la pág. N)» al comienzo de la otra, o
  una de las dos en la tapa: la de la tapa (o la que «pasa a») va primero. En Marcha las notas de tapa siguen en la
  pág. 4, que en el escaneo está a la izquierda.
- Si no: la primera termina a mitad de frase (sin punto ni firma) y la segunda empieza en minúscula.
Se descartan: dos partes firmadas (dos comunicados), una con firma al pie seguida de otra, o un punto final
seguido de un comienzo en minúscula (la segunda sigue a otro texto).
Uso: python3 aplicables_partidas.py CARPETA_LECTURA
"""
import json, os, re, sys, unicodedata
L = sys.argv[1]
sin = lambda t: unicodedata.normalize("NFD", t or "").encode("ascii", "ignore").decode().lower()
num = lambda t: re.findall(r"\b([ivxlc]+|\d+)\b", sin(t))
firma = lambda t: re.search(r"\n\s*[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑa-záéíóúñ.,\- ]{3,60}\s*$", t.rstrip()) or re.search(r"(?i)secretari|saluda|atentamente", t.rstrip()[-80:])
p = json.load(open(os.path.join(L, "partidas.json"))); salida = {}; total = 0
for nid, lista in p.items():
    datos = json.load(open(os.path.join(L, "sitio", "datos", f"{nid}.json")))
    impresa = {pg["n"]: pg.get("impresa") for pg in datos["paginas"]}
    arts = json.load(open(os.path.join(L, nid, "articulos.json")))["articulos"]
    usados = set()
    for x in lista:
        a, b = arts[x["primero"]], arts[x["segundo"]]
        fuerte = any(s.startswith("título partido") for s in x["senales"]) and (
            any(s.startswith("mismos nombres") for s in x["senales"]) or (a.get("autor") and sin(a["autor"]) == sin(b.get("autor"))))
        if not fuerte or num(a["titulo"]) != num(b["titulo"]) or "carta" in (a["tipo"], b["tipo"]):
            continue
        if x["primero"] in usados or x["segundo"] in usados:
            continue  # una parte en dos pares: dudoso
        pag = lambda u: {impresa.get(n) for n in u["paginas"]} | set(u["paginas"])
        def destino(u):  # «Pasa a la pág. N» al final
            m = re.findall(r"(?i)pasa a (?:la )?p[aá]g(?:ina|\.)?\s*(\d+)", u["texto"][-120:])
            return int(m[-1]) if m else None
        def origen(v):  # «(Viene de la pág. N)» al comienzo
            m = re.match(r"(?i)^\W*viene de (?:la )?p[aá]g(?:ina|\.)?\s*(\d+)", v["texto"].strip())
            return int(m.group(1)) if m else None
        def consistente(u, v):  # u sigue en v: las marcas que haya apuntan una a la otra y ninguna contradice
            d, o = destino(u), origen(v)
            if d is None and o is None:
                return False
            if o is None and not re.match(r"^(\.\.\.|…)?\s*[a-záéíóúñ]", v["texto"].strip()):
                return False  # solo «pasa a», y el otro empieza una frase nueva: puede ser otra nota (nº 711)
            return (d is None or d in pag(v)) and (o is None or o in pag(u))
        orden = None
        if consistente(a, b) and not consistente(b, a):
            orden = (a, b)
        elif consistente(b, a) and not consistente(a, b):
            orden = (b, a)
        else:
            fa, ib = a["texto"].rstrip(), b["texto"].strip()
            if re.search(r"[a-záéíóúñ]-$", fa) and re.match(r"^[a-záéíóúñ]", ib) and destino(b) is None and origen(b) is None:
                orden = (a, b)  # palabra cortada con guion: «com-» + «sión»
        if not orden:
            continue
        p1, p2 = orden
        if firma(p1["texto"]) and firma(p2["texto"]) and p1.get("autor") and p2.get("autor"):
            continue  # dos partes firmadas: dos comunicados
        salida.setdefault(nid, []).append({"primero": arts.index(p1), "segundo": arts.index(p2), "senales": x["senales"]})
        usados |= {x["primero"], x["segundo"]}; total += 1
json.dump(salida, open(os.path.join(L, "partidas-aplicables.json"), "w"), ensure_ascii=False, indent=1)
print(total, "uniones aplicables en", len(salida), "números")
