"""Autores y dibujantes del sitio final, desde el catálogo (marcha.sqlite). Gratis, sin IA.

Limpieza por reglas: las firmas compartidas se separan en personas; cada forma va a su nombre canónico con la tabla
de seudónimos (autores_alias.json, ya volcada al catálogo); fuera lo que no es un autor («viene de pág.», «[ilegible]»,
nombres rotos o larguísimos); instituciones y grupos van en una lista aparte; las siglas solo quedan si la tabla las
une a una persona. Escribe dudosos-autores.json con lo que convendría revisar (para un lote opcional con Sonnet).
"""
import json, os, re, sqlite3, sys, unicodedata
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pipeline"))
from unificar_autores import separar, plano, NO_PERSONA, PILA  # noqa: E402

PILA = PILA | set("ines susana beatriz silvia graciela mercedes dolores esther ester julia sara laura lucia cecilia clara alicia "
                  "amalia margarita josefina juana luisa victoria adela celia irma nelly blanca delia elsa olga raquel "
                  "felipe jaime vicente andres nicolas gabriel diego felix ignacio tomas emilio ruben armando".split())

BASURA = re.compile(r"(?i)\bviene\b|\bpasa a\b|\bp[aá]gs?\.?\s*\d|contin[uú]a|\bsigue en\b|\d|ilegible|\[|\]|\.\.\.|…|derechos reservados|^\W*$")
PARTICULAS = {"de", "del", "da", "do", "dos", "das", "la", "las", "los", "van", "von", "y", "di", "le", "du"}
NO_ID = re.compile(r"(?i)no identificad|desconocid|sin firma|an[oó]nim|^firma$|^autor$")
TITULOS = re.compile(r"^(dr|dra|prof|ing|arq|sr|sra|don|doña|gral|cnel|cap|tte|agr|esc|cr|lic|mtro)\.?\s+", re.I)


def es_sigla(n):
    w = re.sub(r"[.\-]", " ", n).split()
    return bool(w) and all(len(x) <= 2 or x.isupper() and len(x) <= 4 for x in w)


def slug(t):
    t = unicodedata.normalize("NFD", t).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:80] or "sin-nombre"


MINUSC = PARTICULAS | {"e", "h"}


def lindo(n):
    """«JUAN CARLOS ONETTI» -> «Juan Carlos Onetti»; «Lola NOBLIA DE PLAZA» -> «Lola Noblia de Plaza»."""
    if not re.search(r"[A-ZÁÉÍÓÚÑ]{3}", n):
        return n
    todo = n.upper() == n
    out = []
    for k, w in enumerate(n.split()):
        lw = w.lower()
        if w.upper() != w or re.fullmatch(r"([A-ZÁÉÍÓÚÑ]\.)+|[IVX]+|[A-ZÁÉÍÓÚÑ]{1,2}\.?", w):
            out.append(w)
        elif k and lw in MINUSC and (todo or True):
            out.append(lw)
        else:
            out.append("-".join(x[:1].upper() + x[1:].lower() for x in w.split("-")))
    return " ".join(out)


def apellido_nombre(n):
    """«Emir Rodríguez Monegal» -> («rodriguez monegal emir», «Rodríguez Monegal, Emir»). Seudónimos de una palabra, tal cual."""
    base = TITULOS.sub("", n.strip())
    w0 = base.split()
    if "(" in base or any(x[:1].islower() and plano(x) not in PARTICULAS for x in w0[1:]) or \
            w0 and plano(w0[0]) in {"uno", "dos", "tres", "cuatro", "cinco", "algunos", "algunas", "otro", "otra", "un", "una"}:  # «Peloduro (Julio E. Suárez)»: un seudónimo con su nombre real va por el seudónimo
        return plano(base), n
    w = base.split()
    if len(w) < 2:
        return plano(base), n
    k = len(w) - 1  # el apellido empieza en la última palabra...
    if plano(w[k - 1]) in PARTICULAS:  # «Carlos Real de Azúa», «Luis de la Torre»: la partícula y lo anterior
        while k > 1 and plano(w[k - 1]) in PARTICULAS:
            k -= 1
        if k > 1 and plano(w[k - 1]) not in PILA and len(w[k - 1].strip(".")) > 1:
            k -= 1
    elif len(w) >= 3 and plano(w[-2]) not in PILA and len(w[-2].strip(".")) > 1:
        k = len(w) - 2  # ...o en la penúltima si no es un nombre de pila (dos apellidos)
    ap, pila = " ".join(w[k:]), " ".join(w[:k])
    lead = []
    while len(w[k:]) > 1 and plano(w[k]) in PARTICULAS and plano(w[k]) != "y":  # «Juan de Lara» -> «Lara, Juan de»
        lead.append(w[k]); k += 1
    if lead:
        ap, pila = " ".join(w[k:]), " ".join(w[:k - len(lead)] + lead)
    clave = plano(" ".join(x for x in w[k:] if plano(x) not in PARTICULAS) + " " + pila)
    return clave, f"{ap}, {pila}"


def armar(catalogo, destino_dudosos=None):
    db = sqlite3.connect(catalogo)
    canon = {}  # forma escrita (plana) -> nombre canónico
    revisados = set()
    for forma, nombre, rev in db.execute("select s.forma, a.nombre, a.revisado from seudonimos s join autores a on a.id = s.autor_id"):
        canon[plano(forma)] = nombre
        if rev:
            revisados.add(nombre)
    for nombre, rev in db.execute("select nombre, revisado from autores"):
        canon.setdefault(plano(nombre), nombre)
        if rev:
            revisados.add(nombre)
    personas = defaultdict(lambda: {"textos": [], "obras": [], "firmas": set()})
    descartes = defaultdict(int)
    def limpiar(f):
        f = re.sub(r"(?i)^(por|texto de|dibujo de|foto de|fotos? de)\s+(?=\S)", "", f.strip(" .,;:-—")).strip()
        f = re.sub(r"\s*\([^)]*[\"“«”»][^)]*\)\s*$", "", f)  # «JUAN CARLOS ONETTI ("PERIQUITO EL AGUADOR")»
        f = re.sub(r"\s*\((?=[a-záéíóú])[^)]*\)\s*$", "", f)  # «Víctor Rozen (secretario general)»
        f = re.sub(r"^(lr|doctor|dra?|prof|cnel|gral)\.?\s+", "", TITULOS.sub("", f), flags=re.I)
        return f.strip(" .,;:-—")

    def a_persona(forma):
        f = limpiar(forma)
        if not f or len(f) > 45 or BASURA.search(f) or NO_ID.search(f) or not (f[0].isupper() or f[0] in "«\"“'("):
            descartes["no es autor"] += 1
            return None
        nombre = canon.get(plano(f)) or canon.get(plano(forma)) or f
        return limpiar(nombre) or f

    sql = """select r.id, r.numero_id, r.titulo, coalesce(r.firma, a.nombre), r.seccion, r.tipo, n.fecha,
                    (select min(p.n) from articulo_paginas p where p.articulo_id = r.id)
             from articulos r join numeros n on n.id = r.numero_id left join autores a on a.id = r.autor_id
             where coalesce(r.firma, a.nombre) is not null and r.tipo != 'aviso'"""
    for _, nid, titulo, firma, seccion, tipo, fecha, pag in db.execute(sql):
        for parte in separar(firma) or [firma]:
            nombre = a_persona(parte)
            if nombre:
                p = personas[nombre]
                p["textos"].append((fecha, nid, pag or 1, titulo, seccion or ""))
                p["firmas"].add(parte.strip())
    for nid, n, tipo, desc, firma, fecha, nombre in db.execute(
            """select o.numero_id, o.n, o.tipo, o.descripcion, o.firma, nu.fecha, a.nombre from obras_graficas o
               join numeros nu on nu.id = o.numero_id left join autores a on a.id = o.autor_id
               where coalesce(a.nombre, o.firma) is not null"""):
        nombre = a_persona(nombre or firma)
        if nombre:
            personas[nombre]["obras"].append((fecha, nid, n, tipo or "", desc or ""))
            if firma:
                personas[nombre]["firmas"].add(firma.strip())
    juntas = {}
    for nombre in sorted(personas, key=lambda n: (n.isupper(), -len(personas[n]["textos"]) - len(personas[n]["obras"]))):
        k = plano(nombre)
        if k in juntas and juntas[k] != nombre:
            dst, src = personas[juntas[k]], personas.pop(nombre)
            dst["textos"] += src["textos"]; dst["obras"] += src["obras"]; dst["firmas"] |= src["firmas"] | {nombre}
        else:
            juntas[k] = nombre
    gente, instituciones, dudosos, slugs = [], [], [], set()
    for nombre, p in personas.items():
        if es_sigla(nombre) and nombre not in revisados:
            descartes["sigla sin dueño conocido"] += 1
            if len(p["textos"]) >= 3:
                dudosos.append({"forma": nombre, "textos": len(p["textos"]), "ejemplos": [t[3][:60] for t in p["textos"][:3]]})
            continue
        clave, mostrar = apellido_nombre(lindo(nombre))
        s = slug(mostrar)
        while s in slugs:
            s += "-2"
        slugs.add(s)
        anios = sorted({t[0][:4] for t in p["textos"]} | {o[0][:4] for o in p["obras"]})
        x = {"nombre": nombre, "mostrar": mostrar, "clave": clave, "slug": s, "textos": sorted(p["textos"]),
             "obras": sorted(p["obras"]), "firmas": sorted(p["firmas"] - {nombre}), "desde": anios[0] if anios else "",
             "hasta": anios[-1] if anios else ""}
        if NO_PERSONA.search(re.sub(r"(?i)\bde sa$", "", nombre)) and nombre not in revisados:
            x["mostrar"], x["clave"] = nombre, plano(nombre)
            instituciones.append(x)
        else:
            gente.append(x)
            if len(p["textos"]) + len(p["obras"]) <= 2 and not (nombre in revisados):
                pass
    gente.sort(key=lambda x: x["clave"])
    instituciones.sort(key=lambda x: x["clave"])
    # dudosos para un lote opcional: siglas frecuentes sin dueño y apellidos que se repiten con otra inicial
    por_ap = defaultdict(list)
    for x in gente:
        por_ap[x["clave"].split()[0] if x["clave"] else ""].append(x)
    for ap, xs in por_ap.items():
        if len(xs) > 1 and len(ap) > 3:
            raros = [x for x in xs if len(x["textos"]) + len(x["obras"]) <= 2]
            if raros and len(xs) - len(raros) >= 1:
                for r in raros:
                    dudosos.append({"forma": r["nombre"], "textos": len(r["textos"]) + len(r["obras"]),
                                    "candidatos": [x["nombre"] for x in xs if x is not r][:6],
                                    "ejemplos": [t[3][:60] for t in r["textos"][:3]]})
    if destino_dudosos:
        json.dump(dudosos, open(destino_dudosos, "w"), ensure_ascii=False, indent=1)
    return gente, instituciones, dict(descartes), dudosos
