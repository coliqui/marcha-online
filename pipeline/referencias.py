"""Detecta y resuelve las referencias de un artículo a otros números de Marcha (sin usar la API).

Uso: python3 referencias.py CARPETA_OCR CARPETA_LECTURA [ARCHIVO_SALIDA]

Busca en el texto de cada artículo frases como "en nuestro número anterior", "MARCHA Nº 1079",
"(Viene del número 1078)", "publicamos el 12 de mayo" o "dijimos la semana pasada", y las resuelve
contra la lista completa de números (fecha y número de cada uno):
  - número explícito -> ese número;
  - "número anterior", "semana pasada" -> el número anterior al del artículo;
  - fecha -> el número de esa fecha (o el último publicado en los 7 días anteriores).
Las que no se pueden resolver quedan guardadas igual ("resuelta": false), para resolverlas más
adelante, cuando haya más números leídos. Las referencias por título o autor quedan pendientes.
Deja la lista en CARPETA_LECTURA/referencias.json (o en ARCHIVO_SALIDA).
"""
import glob, json, os, re, sys
from datetime import date, timedelta

MESES = {m: i for i, m in enumerate(["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                                      "setiembre", "octubre", "noviembre", "diciembre"], 1)}
MESES["septiembre"] = 9
MES = "|".join(MESES)

PATRONES = [
    # tipo, expresión; los grupos con nombre dicen qué se extrae
    ("numero", re.compile(r"\b(?:MARCHA|Marcha)\s*,?\s*(?:N\s*[º°o.]+|n[uú]m(?:ero|\.)?)\s*(?P<num>\d{1,2}\.?\d{0,3})\b")),
    ("numero", re.compile(r"\b(?:[Vv]iene|[Cc]ontinuaci[oó]n)\s+del?\s+(?:n[uú]mero|N\s*[º°o.]+)\s*(?P<num>\d{1,2}\.?\d{0,3})\b")),
    ("numero", re.compile(r"\b(?:n[uú]mero|N\s*[º°o.]+)\s*(?P<num>\d{2,4})\s+de\s+(?:MARCHA|Marcha)\b")),
    ("anterior", re.compile(r"\b(?:en|de|desde)\s+(?:nuestr[oa]|el|la|est[ea])\s+(?:[uú]ltim[oa]\s+)?(?:n[uú]mero|edici[oó]n)\s+anterior\b", re.I)),
    ("anterior", re.compile(r"\b(?:publicamos|dijimos|señalamos|informamos|comentamos|anunciamos|dec[ií]amos|escribimos|"
                            r"adelantamos|transcribimos|reprodujimos|anotamos|planteamos|preguntamos)\b[^.;\n]{0,40}\b"
                            r"(?:la\s+semana\s+(?:pasada|anterior)|el\s+viernes\s+(?:pasado|anterior))", re.I)),
    ("anterior", re.compile(r"\b(?:la\s+semana\s+(?:pasada|anterior))\b[^.;\n]{0,15}\b(?:publicamos|dijimos|señalamos|"
                            r"informamos|comentamos|anunciamos|escribimos|adelantamos)\b", re.I)),
    ("fecha", re.compile(r"\b(?:en\s+(?:nuestra\s+edici[oó]n|nuestro\s+n[uú]mero|MARCHA|Marcha)\s+del?|publicamos\s+el|"
                         r"(?:publicad[oa]|aparecid[oa])\s+en\s+(?:MARCHA|Marcha|nuestro\s+semanario)\s+el)\s+(?P<dia>\d{1,2})\s+de\s+(?P<mes>" + MES + r")(?:\s+(?:de|del)\s+(?P<anio>\d{4}))?",
                         re.I)),
]


def numeros_coleccion(ocr):
    numeros = []
    for f in glob.glob(os.path.join(ocr, "*", "numero.json")):
        m = json.load(open(f))
        numeros.append({"id": m["id"], "numero": m["numero"], "fecha": m["fecha"]})
    return sorted(numeros, key=lambda m: (m["fecha"], m["id"]))


def resolver(tipo, m, origen, numeros, por_numero):
    """Devuelve el número de destino (dict) o None."""
    if tipo == "numero":
        n = int(m.group("num").replace(".", ""))
        return por_numero.get(n) if n != origen["numero"] else None
    if tipo == "anterior":
        i = next(i for i, x in enumerate(numeros) if x["id"] == origen["id"])
        return numeros[i - 1] if i > 0 else None
    if tipo == "fecha":
        anio = int(m.group("anio")) if m.group("anio") else int(origen["fecha"][:4])
        try:
            buscada = date(anio, MESES[m.group("mes").lower()], int(m.group("dia")))
        except ValueError:
            return None
        if not m.group("anio") and buscada.isoformat() > origen["fecha"]:  # "el 20 de diciembre", citado en enero
            buscada = buscada.replace(year=anio - 1)
        candidatos = [x for x in numeros if buscada - timedelta(days=7) < date.fromisoformat(x["fecha"]) <= buscada]
        return candidatos[-1] if candidatos and candidatos[-1]["id"] != origen["id"] else None


def main(ocr, lectura, salida=None):
    numeros = numeros_coleccion(ocr)
    por_numero = {m["numero"]: m for m in numeros if m["numero"] is not None}
    origenes = {m["id"]: m for m in numeros}
    refs = []
    for f in sorted(glob.glob(os.path.join(lectura, "*", "articulos.json"))):
        d = json.load(open(f))
        origen = origenes[d["id"]]
        for i, a in enumerate(d["articulos"]):
            vistas = set()
            for tipo, patron in PATRONES:
                for m in patron.finditer(a["texto"]):
                    if m.start() in vistas:
                        continue
                    vistas.add(m.start())
                    destino = resolver(tipo, m, origen, numeros, por_numero)
                    a0, b0 = max(0, m.start() - 70), min(len(a["texto"]), m.end() + 70)
                    refs.append({"origen": origen["id"], "fecha_origen": origen["fecha"], "pagina": a["paginas"][0],
                                 "articulo": i, "titulo": a["titulo"], "tipo": tipo, "cita": m.group(0),
                                 "contexto": " ".join(a["texto"][a0:b0].split()),
                                 "destino": destino["id"] if destino else None,
                                 "fecha_destino": destino["fecha"] if destino else None,
                                 "leido": bool(destino) and os.path.exists(os.path.join(lectura, destino["id"], "articulos.json")),
                                 "resuelta": destino is not None})
    salida = salida or os.path.join(lectura, "referencias.json")
    json.dump(refs, open(salida, "w"), ensure_ascii=False, indent=1)
    resueltas = sum(r["resuelta"] for r in refs)
    print(f"{len(refs)} referencias, {resueltas} resueltas, {len(refs) - resueltas} pendientes; "
          f"{sum(r['leido'] for r in refs)} apuntan a números ya leídos con IA")
    for tipo in ("numero", "anterior", "fecha"):
        print(f"  {tipo}: {sum(r['tipo'] == tipo for r in refs)}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
