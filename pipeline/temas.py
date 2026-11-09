"""Unifica y agrupa los temas que la IA puso a cada nota. Gratis (no usa la API).

Uso: python3 temas.py CARPETA_LECTURA [CARPETA_SITIO]

1. Unifica: mayúsculas, tildes, signos y plurales («Cine»/«cine», «libro»/«libros») se juntan solos; los sinónimos
   y variantes de pipeline/temas_alias.json (editable) también. Cada grupo toma como nombre la forma más usada (o
   la clave de la tabla de sinónimos).
2. Agrupa: los ~40 temas más frecuentes son los temas generales; cada uno de los demás (con 5 notas o más) va como
   subtema del general que lo contiene en su nombre («economía uruguaya» → «economía») o, si no, del general con el
   que más aparece junto en las mismas notas (si es al menos en un cuarto de ellas).
Escribe CARPETA_LECTURA/temas.json (jerarquía y cantidades) y temas-mapa.json (forma escrita → tema unificado);
si se da CARPETA_SITIO, copia los dos a datos/ para el sitio de prueba.
"""
import glob, json, os, re, shutil, sys, unicodedata
from collections import Counter, defaultdict

AQUI = os.path.dirname(os.path.abspath(__file__))
GENERALES = 40


def clave(t):
    t = unicodedata.normalize("NFD", t or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9ñ ]", " ", t)).strip()


def singular(k):
    palabras = []
    for w in k.split():
        if len(w) > 4 and w.endswith("es") and not w.endswith(("ses", "ies")):
            w = w[:-2]
        elif len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "is", "us")):
            w = w[:-1]
        palabras.append(w)
    return " ".join(palabras)


def armar(lectura):
    alias = json.load(open(os.path.join(AQUI, "temas_alias.json")))
    no_generales = {clave(x) for x in alias.get("_no_generales", [])}
    sub_forzado = {clave(k): v for k, v in alias.get("_subtemas", {}).items()}
    forzados = {clave(x) for x in alias.get("_generales", [])}
    sinonimo = {}  # clave singular de la variante -> nombre del tema
    for nombre, variantes in alias.items():
        if nombre.startswith("_"):
            continue
        for v in [nombre] + variantes:
            sinonimo[singular(clave(v))] = nombre
    escritas, notas = Counter(), []  # forma escrita -> veces; temas por nota
    for f in glob.glob(os.path.join(lectura, "*", "articulos.json")):
        for a in json.load(open(f))["articulos"]:
            if a.get("temas"):
                notas.append(a["temas"]); escritas.update(a["temas"])
    # grupos por clave singular; nombre = el de la tabla de sinónimos o la forma escrita más usada
    grupo = {}
    formas = defaultdict(Counter)
    for t, n in escritas.items():
        k = singular(clave(t))
        if not k:
            continue
        grupo[t] = sinonimo.get(k, k)
        formas[grupo[t]][t] += n
    nombre = {g: (g if g in alias else f.most_common(1)[0][0].strip()) for g, f in formas.items()}
    mapa = {t: nombre[g] for t, g in grupo.items()}
    # cantidades y co-apariciones por tema unificado
    cuenta, junto = Counter(), defaultdict(Counter)
    for ts in notas:
        u = sorted({mapa[t] for t in ts if t in mapa})
        cuenta.update(u)
        for x in u:
            for y in u:
                if x != y:
                    junto[x][y] += 1
    ordenados = [t for t, _ in cuenta.most_common() if clave(t) not in no_generales and clave(t) not in sub_forzado]
    # generales: los más frecuentes, salvo los que son parte de uno ya elegido (su nombre lo contiene, o aparecen
    # junto a él en al menos la mitad de sus notas: «cultura uruguaya», «Frente Amplio» van dentro de otro)
    generales = []
    for t in ordenados:
        if len(generales) >= GENERALES and clave(t) not in forzados:
            continue
        kt = set(clave(t).split())
        if any(set(clave(g).split()) <= kt for g in generales) or any(junto[t][g] >= 0.5 * cuenta[t] for g in generales):
            continue
        generales.append(t)
    gset = set(generales)
    hijos, sueltos = defaultdict(list), []
    for t, n in cuenta.most_common():
        if t in gset or n < 5:
            continue
        kt = set(clave(t).split())
        forzado = sub_forzado.get(clave(t))
        if forzado and forzado in gset:
            hijos[forzado].append((t, n)); continue
        padre = next((g for g in generales if set(clave(g).split()) <= kt), None)  # «economía uruguaya» → «economía»
        if not padre:
            candidatos = [(junto[t][g] / n, g) for g in generales if junto[t][g]]
            if candidatos:
                r, g = max(candidatos)
                padre = g if r >= 0.25 else None
        (hijos[padre].append((t, n)) if padre else sueltos.append((t, n)))
    jerarquia = {"generales": [{"tema": g, "notas": cuenta[g],
                                "subtemas": [{"tema": t, "notas": n} for t, n in hijos[g]]} for g in generales],
                 "otros": [{"tema": t, "notas": n} for t, n in sueltos],
                 "total_temas_escritos": len(escritas), "total_temas_unificados": len(cuenta)}
    json.dump(jerarquia, open(os.path.join(lectura, "temas.json"), "w"), ensure_ascii=False, indent=1)
    json.dump(mapa, open(os.path.join(lectura, "temas-mapa.json"), "w"), ensure_ascii=False)
    return jerarquia, formas, nombre


def secciones(lectura):
    """Secciones fijas (secciones_alias.json): qué notas tiene cada una, por el campo «sección» o por el título."""
    alias = json.load(open(os.path.join(AQUI, "secciones_alias.json")))
    reglas = []  # (forma, es_prefijo, nombre)
    for nombre, formas in alias.items():
        if not nombre.startswith("_"):
            reglas += [(clave(f.rstrip("*")), f.endswith("*"), nombre) for f in formas]
    def de(t):
        k = clave(t)
        for f, pref, nombre in reglas:
            if k == f or (pref and k.startswith(f)):
                return nombre
        return None
    notas, autores, anios, forma = defaultdict(list), defaultdict(Counter), defaultdict(list), {}
    for f in glob.glob(os.path.join(lectura, "*", "articulos.json")):
        nid = os.path.basename(os.path.dirname(f))
        fecha = json.load(open(os.path.join(os.path.dirname(f), "..", "..", "salida", nid, "numero.json"))).get("fecha", "") \
            if os.path.exists(os.path.join(os.path.dirname(f), "..", "..", "salida", nid, "numero.json")) else ""
        for i, a in enumerate(json.load(open(f))["articulos"]):
            if a.get("tipo") == "aviso":
                continue
            s = de(a.get("seccion") or "") or de(a.get("titulo") or "")
            if s:
                notas[s].append([nid, i])
                if a.get("autor"):
                    autores[s][clave(a["autor"])] += 1
                    forma.setdefault(clave(a["autor"]), Counter())[a["autor"].strip()] += 1
                if fecha:
                    anios[s].append(fecha[:4])
    lista = [{"seccion": s, "notas": len(v), "numeros": len({x[0] for x in v}),
              "desde": min(anios[s]) if anios[s] else None, "hasta": max(anios[s]) if anios[s] else None,
              "autores": [[forma[a].most_common(1)[0][0], n] for a, n in autores[s].most_common(4)]} for s, v in notas.items()]
    lista.sort(key=lambda x: -x["notas"])
    return lista, dict(notas)


if __name__ == "__main__":
    lectura = sys.argv[1]
    j, formas, nombre = armar(lectura)
    print(f"{j['total_temas_escritos']} formas escritas → {j['total_temas_unificados']} temas unificados; "
          f"{len(j['generales'])} generales con {sum(len(g['subtemas']) for g in j['generales'])} subtemas; "
          f"{len(j['otros'])} temas con 5+ notas sin agrupar")
    lista, notas = secciones(lectura)
    j["secciones"] = lista
    json.dump(j, open(os.path.join(lectura, "temas.json"), "w"), ensure_ascii=False, indent=1)
    json.dump(notas, open(os.path.join(lectura, "secciones-notas.json"), "w"))
    print(f"{len(lista)} secciones fijas; {sum(x['notas'] for x in lista)} notas en ellas")
    if len(sys.argv) > 2:
        for f in ("temas.json", "temas-mapa.json", "secciones-notas.json"):
            shutil.copy(os.path.join(lectura, f), os.path.join(sys.argv[2], "datos", f))
