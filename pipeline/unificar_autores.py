"""Unifica las variantes de un mismo autor (gratis, sin IA) y deja las dudosas para revisar.

Uso: python3 unificar_autores.py CARPETA_LECTURA [--aplicar]

1. Separa las firmas de varias personas («Hugo R. Alfaro y H. Alsina Thevenet», «…, H. Alsina, Thevenet»).
2. Normaliza cada nombre: sin «Por», «Escribe», títulos («Dr.»), mayúsculas, tildes ni puntos.
3. Une con seguridad: la misma persona escrita distinto (mayúsculas, tildes, puntos, «H.» = «Homero» cuando no hay
   otro con ese apellido e inicial, el apellido solo cuando hay una única persona con ese apellido) y los errores de
   lectura de una letra en un apellido largo (Thevenet / Thevenel) de una forma rara hacia una frecuente.
4. Lo dudoso (otra inicial con el mismo apellido, el apellido solo con varias personas posibles) no se une: va a
   autores-dudosos.json, para el grupo de Revisión «Posible mismo autor».
Con --aplicar agrega los grupos a pipeline/autores_alias.json (marcados en «_automaticos», para poder deshacerlos);
los seudónimos ya cargados se respetan.
"""
import glob, json, os, re, sys, unicodedata
from collections import Counter, defaultdict

AQUI = os.path.dirname(os.path.abspath(__file__))
PREFIJOS = re.compile(r"^(por|escribe[n]?|de|del|texto de|nota de|dr|dra|prof|ing|arq|sr|sra|don|dona|gral|cnel)\b\.?\s*", re.I)
NO_PERSONA = re.compile(r"(?i)ilegible|redacci[oó]n|marcha|^(un|una|otro|otra|unos|unas|el|la|los|las|grupo|varios|varias)\b|lector|"
                        r"amig[oa]|suscript|vecin|ciudadan|corresponsal|editorialista|comentarista|enviad|observador|an[oó]nimo|direcci[oó]n|"
                        r"comit[eé]|asociaci[oó]n|federaci[oó]n|sindicato|partido|movimiento|uni[oó]n|centro|consejo|junta|comisi[oó]n|"
                        r"directorio|agencia|banco|compa[ñn][ií]a|\bs\.? ?a\.?$|ltda|\bc[ií]a\b|\d|^ap$|^upi$|^afp$|^efe$")


def plano(t):
    return re.sub(r"\s+", " ", re.sub(r"[^a-zñ ]", " ", unicodedata.normalize("NFD", t or "").encode("ascii", "ignore").decode().lower())).strip()


CONOCIDOS = set()  # nombres que aparecen como firma de una sola persona (para no unir «Alfaro, Calvero»)


def separar(firma):
    """Una firma -> lista de nombres de personas."""
    partes = re.split(r"\s+y\s+|\s*;\s*|\s*/\s*|\s+e\s+(?=[A-ZÁÉÍÓÚ])|\s+[a-z]\.\s+(?=[A-ZÁÉÍÓÚ])", firma.strip())
    partes = [x for p in partes for x in re.split(r"\s+-\s+", p)]  # «ALFARO - CHIFFLET»; «Abadie - Aicardi» se vuelve a juntar abajo
    salida = []
    for p in partes:
        trozos = [x.strip() for x in p.split(",") if x.strip()]
        for t in trozos:
            if len(t.split()) > 5:
                continue  # varias personas pegadas que no se pueden separar con seguridad
            # «H. Alsina, Thevenet»: un trozo de una sola palabra sin iniciales es el resto del apellido anterior
            if salida and len(t.split()) == 1 and not re.search(r"\.", t) and len(salida[-1].split()) >= 2 \
                    and plano(salida[-1] + " " + t) in CONOCIDOS:
                salida[-1] += " " + t
            else:
                salida.append(t)
    return [s for s in salida if len(plano(s)) >= 3]


def partes_nombre(n):
    """-> (apellido, inicial, nombre completo de pila o '') en minúsculas sin tildes."""
    n = PREFIJOS.sub("", n.strip())
    w = plano(n).split()
    if not w:
        return None
    if len(w) == 1:
        return (w[0], "", "")
    iniciales = [x for x in w[:-1] if len(x) == 1]
    pila = [x for x in w[:-1] if len(x) > 1]
    # apellido: las últimas palabras que no son nombres de pila conocidos ni iniciales
    ap = [w[-1]]
    if len(w) >= 3 and len(w[-2]) > 1 and w[-2] not in PILA:
        ap = w[-2:]
    resto = [x for x in w[:len(w) - len(ap)]]
    inicial = resto[0][0] if resto else ""
    nombre = " ".join(x for x in resto if len(x) > 1)
    return (" ".join(ap), inicial, nombre)


PILA = set("maria jose luis carlos juan angel pedro alberto manuel jorge julio hugo mario raul oscar carmen ana elena rosa "
           "francisco antonio miguel enrique eduardo ernesto ricardo roberto alfredo arturo hector daniel fernando sergio pablo "
           "jesus ramon rafael marta teresa isabel homero emir angel guillermo gregorio washington".split())


def distancia1(a, b):
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    if len(a) > len(b):
        a, b = b, a
    return any(b[:i] + b[i + 1:] == a for i in range(len(b)))


def unificar(lectura):
    firmas, donde = Counter(), {}
    for f in glob.glob(os.path.join(lectura, "*", "articulos.json")):
        nid = os.path.basename(os.path.dirname(f))
        for i, a in enumerate(json.load(open(f))["articulos"]):
            if a.get("autor"):
                firmas[a["autor"]] += 1
                donde.setdefault(a["autor"], {"id": nid, "n": a["paginas"][0], "art": i})
    CONOCIDOS.update(plano(f) for f in firmas if not re.search(r",|;|/|\s+y\s+", f))
    nombres = Counter()
    for f, n in firmas.items():
        for x in separar(f):
            if not NO_PERSONA.search(x):
                nombres[x.strip()] += n
    # 1. por (apellido, inicial): la misma persona escrita distinto
    grupos = defaultdict(Counter)
    for x, n in nombres.items():
        p = partes_nombre(x)
        if not p or len(p[0].replace(" ", "")) <= 2:
            continue  # solo iniciales («J. C.», «C. M. M.»): no se unen con nadie
        primer = p[2].split()[0] if p[2] else ""
        grupos[(p[0], p[1], primer)][x] += n
    # una forma con solo la inicial («H. Alsina Thevenet») va con la persona de nombre completo si hay una sola;
    # si hay varias con esa inicial y apellido («Carlos» y «Cristina» Gutiérrez), queda dudosa
    dudosos = []
    for (ap, ini, primer) in list(grupos):
        if primer or not ini:
            continue
        completos = [k for k in grupos if k[0] == ap and k[1] == ini and k[2]]
        if len(completos) == 1:
            grupos[completos[0]].update(grupos.pop((ap, ini, primer)))
        elif len(completos) > 1:
            dudosos.append({"tipo": "inicial con varias personas", "forma": grupos[(ap, ini, primer)].most_common(1)[0][0],
                            "candidatos": [grupos[k].most_common(1)[0][0] for k in completos]})
    # 2. errores de lectura de una letra en apellidos largos: la forma rara va con la frecuente
    claves = sorted(grupos, key=lambda k: -sum(grupos[k].values()))
    destino = {}
    for k in claves:
        if k in destino or len(k[0]) < 6:
            continue
        for k2 in claves:
            if k2 != k and k2 not in destino and k2[1:] == k[1:] and sum(grupos[k2].values()) <= 3 and sum(grupos[k].values()) >= 10 \
                    and distancia1(k2[0], k[0]):
                destino[k2] = k
    for k2, k in destino.items():
        grupos[k].update(grupos.pop(k2))
    # 3. apellido solo (sin inicial): a la única persona con ese apellido compuesto; si hay varias, dudoso
    por_apellido = defaultdict(list)
    for k in grupos:
        if k[1]:
            por_apellido[k[0]].append(k)
    for k in list(grupos):
        if k[1] or k not in grupos:
            continue
        otros = por_apellido.get(k[0], [])
        if len(otros) == 1 and len(k[0].split()) >= 2:
            grupos[otros[0]].update(grupos.pop(k))
        elif otros:
            dudosos.append({"tipo": "apellido solo", "forma": grupos[k].most_common(1)[0][0],
                            "candidatos": [grupos[o].most_common(1)[0][0] for o in otros][:8]})
    # 4. apellido compuesto con otra inicial y pocas firmas (J. Alsina Thevenet / H. Alsina Thevenet): dudoso
    for ap, ks in por_apellido.items():
        ks = [k for k in ks if k in grupos]
        if len(ks) > 1 and len(ap.split()) >= 2:
            cuenta = {k: sum(grupos[k].values()) for k in ks}
            mayor = max(cuenta, key=cuenta.get)
            for k, n in cuenta.items():
                if k != mayor and n <= 3 and k[1] != mayor[1]:
                    dudosos.append({"tipo": "otra inicial", "forma": grupos[k].most_common(1)[0][0],
                                    "candidatos": [grupos[mayor].most_common(1)[0][0]]})
    # nombre canónico: la forma con el nombre de pila completo más usada; si no, la más usada; en mayúsculas, a título
    unidos = {}
    for k, formas in grupos.items():
        if len(formas) < 2:
            continue
        completos = [f for f in formas if partes_nombre(f) and partes_nombre(f)[2]]
        canon = max(completos or formas, key=lambda f: (formas[f], len(f)))
        if canon.isupper():
            canon = canon.title()
        unidos[canon] = sorted(formas, key=lambda f: -formas[f])
    # un ejemplo de dónde aparece cada forma dudosa (para el link de Revisión)
    ejemplo = {}
    for f, d in donde.items():
        for x in separar(f):
            ejemplo.setdefault(x.strip(), d)
    for d in dudosos:
        d["ejemplo"] = ejemplo.get(d["forma"])
    return nombres, unidos, dudosos


if __name__ == "__main__":
    lectura = sys.argv[1]
    nombres, unidos, dudosos = unificar(lectura)
    quedan = len(nombres) - sum(len(v) - 1 for v in unidos.values())
    print(f"{len(nombres)} nombres de personas (separadas las firmas compartidas) → {quedan} después de unir; "
          f"{len(unidos)} grupos unidos; {len(dudosos)} casos dudosos")
    for c in sorted(unidos, key=lambda c: -len(unidos[c]))[:12]:
        print(f"  {c}: {unidos[c][:6]}")
    json.dump(dudosos, open(os.path.join(lectura, "autores-dudosos.json"), "w"), ensure_ascii=False, indent=1)
    if "--aplicar" in sys.argv:
        p = os.path.join(AQUI, "autores_alias.json")
        alias = json.load(open(p))
        ya = {plano(v) for k, vs in alias.items() if not k.startswith("_") for v in [k] + vs}
        auto = []
        for canon, formas in unidos.items():
            if plano(canon) in ya or any(plano(f) in ya for f in formas):
                continue  # respeta lo cargado a mano (seudónimos)
            alias[canon] = [f for f in formas if f != canon]
            auto.append(canon)
        alias["_automaticos"] = sorted(set(alias.get("_automaticos", [])) | set(auto))
        json.dump(alias, open(p, "w"), ensure_ascii=False, indent=1)
        print(f"agregados {len(auto)} grupos a autores_alias.json (lista en «_automaticos»)")
