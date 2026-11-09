"""Detecta notas que la lectura partió en varias dentro del mismo pliego (caso del nº 1260 pp. 22-23, «El infierno
tan temido»). Gratis: solo compara los artículos ya leídos. No cambia nada; deja la lista en
CARPETA_LECTURA/partidas.json para la categoría de revisión «Nota partida en varias».

Uso: python3 detectar_partidas.py CARPETA_LECTURA

Para cada par de textos del mismo pliego (misma página o la enfrentada), suma señales:
- el título de uno no está impreso (inventado por la IA: «sin título», «(continuación)», «[…]») o es un pedazo del
  título del otro («El infierno» / «… tan temido»: el título cruza el pliego);
- el segundo empieza a mitad de frase (minúscula, «…», «y», «que»);
- el primero termina sin punto final ni firma;
- no tienen firmas distintas (uno sin firma, o la misma);
- comparten nombres propios poco comunes (el mismo tema).
Con 3 puntos o más, el par queda como posible nota partida.
"""
import glob, json, os, re, sys, unicodedata
from collections import Counter

sin = lambda t: unicodedata.normalize("NFD", t or "").encode("ascii", "ignore").decode().lower()
INVENTADO = re.compile(r"(?i)sin t[ií]tulo|continuaci[oó]n|^\[|\]$|cierre de carta|p[aá]ginas? \d+(-\d+)?\)")
PROPIOS = set("cuadernos carta nota pagina".split())
TIPOS = {"nota", "otro", "columna", "entrevista", "editorial", "crítica", "carta", "cuento"}


def nombres(t):
    return {w for w in re.findall(r"\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{3,}\b", t or "")}


def palabras_titulo(t):
    return [w for w in re.findall(r"[a-z]{3,}", sin(t)) if w not in ("los", "las", "del", "una", "por", "que", "con")]


def detectar(d, comunes):
    arts = d["articulos"]
    enf = {p["n"]: p.get("enfrentada") for p in d["paginas"]}
    pliego = lambda a: {k for n in a["paginas"] for k in (n, enf.get(n)) if k}
    salida = []
    for i, a in enumerate(arts):
        if a["tipo"] not in TIPOS:
            continue
        for j, b in enumerate(arts):
            if j <= i or b["tipo"] not in TIPOS or not (pliego(a) & set(b["paginas"])):
                continue
            # orden: el que empieza antes (página) es el primero
            p, s = (a, b) if (min(a["paginas"]), i) <= (min(b["paginas"]), j) else (b, a)
            senales = []
            tp, ts = palabras_titulo(p["titulo"]), palabras_titulo(s["titulo"])
            if INVENTADO.search(s["titulo"]) or INVENTADO.search(p["titulo"]):
                senales.append("título que no está impreso")
            elif (tp and ts and (" ".join(ts) in " ".join(tp) or " ".join(tp) in " ".join(ts))) or \
                    (tp and ts and len(set(tp) & set(ts)) >= max(1, min(len(tp), len(ts)) - 0) and set(tp) != set(ts)):
                senales.append("título partido o repetido")
            ini = s["texto"].strip()
            if re.match(r"^([a-záéíóúñ…]|\.\.\.|y |que |pero )", ini):
                senales.append("empieza a mitad de frase")
            fin = p["texto"].rstrip()
            firma_al_pie = re.search(r"\n\s*[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑa-záéíóúñ.\- ]{3,40}\s*$", fin)
            if fin and not re.search(r"[.!?»”\")]\s*$", fin) and not firma_al_pie:
                senales.append("el primero termina sin punto ni firma")
            fa, fb = sin(p.get("autor")), sin(s.get("autor"))
            if not fa or not fb or fa == fb or fa in fb or fb in fa:
                senales.append("no tienen firmas distintas")
            else:
                continue  # dos firmas distintas: son dos notas
            raros = {w for w in nombres(p["texto"]) & nombres(s["texto"]) if comunes[w] <= 3}
            if len(raros) >= 2:
                senales.append(f"mismos nombres ({', '.join(sorted(raros)[:3])})")
            puntos = sum(2 if x.startswith(("título", "empieza")) else 1 for x in senales)
            if puntos >= 4 and any(x.startswith(("título", "empieza")) for x in senales):
                salida.append({"primero": arts.index(p), "segundo": arts.index(s), "pagina": min(p["paginas"]),
                               "puntos": puntos, "senales": senales})
    return salida


if __name__ == "__main__":
    lectura = sys.argv[1]
    todo = {}
    for f in sorted(glob.glob(os.path.join(lectura, "sitio", "datos", "*.json"))):
        if not re.match(r"^\d+\.json$", os.path.basename(f)):
            continue
        d = json.load(open(f))
        d["articulos"] = json.load(open(os.path.join(lectura, d["id"], "articulos.json")))["articulos"]
        comunes = Counter(w for a in d["articulos"] for w in nombres(a["texto"]))
        r = detectar(d, comunes)
        if r:
            todo[d["id"]] = r
    json.dump(todo, open(os.path.join(lectura, "partidas.json"), "w"), ensure_ascii=False, indent=1)
    print(sum(len(v) for v in todo.values()), "posibles notas partidas en", len(todo), "números")
