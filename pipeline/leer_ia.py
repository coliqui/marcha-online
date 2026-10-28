"""Lectura con IA en una sola pasada: modelo mixto Haiku + Sonnet.

Uso: python3 leer_ia.py ENTRADA.pdf [ENTRADA2.pdf ...] --salida CARPETA --tesseract CARPETA_OCR [--tope 2.0]

1. Haiku lee todas las páginas y devuelve, por página: número impreso, artículos
   (título, autor, sección, tipo, temas, texto, continuaciones), ilustraciones con
   su ubicación, y una autoevaluación de la lectura.
2. Se marcan como difíciles las páginas que Haiku declara poco legibles o con partes
   sin leer, y aquellas cuyo texto es bastante más corto que el de Tesseract.
3. Sonnet vuelve a leer solo esas páginas, de la más dudosa a la menos, sin pasar
   el tope de gasto (en dólares, contando todo lo gastado en la corrida).
4. Se unen los artículos que siguen en otra página (con marca "pasa a" o sin ella).

Las páginas que el filtro de Anthropic bloquea quedan con el texto de Tesseract.
Salida por número: CARPETA/<id>/pNN.json (versión final de cada página),
pNN.haiku.json / pNN.sonnet.json (lecturas crudas), articulos.json y costo.json.
"""
import argparse, base64, glob, io, json, os, re, subprocess, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import List, Literal, Optional

import anthropic
from PIL import Image
from pydantic import BaseModel, ValidationError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ocr_numero import metadatos

MODELOS = {
    # id del modelo, precio por millón de tokens (entrada, salida), sin descuento por lotes
    "haiku": ("claude-haiku-5-5", 0.10, 0.50),
    "sonnet": ("claude-sonnet-5-5", 2.00, 10.00),
}
LADO_MAXIMO = 1568
ARRIBA, ABAJO = (0.0, 0.55), (0.45, 1.0)  # franjas de la página que van en cada imagen
TIPOS = Literal["editorial", "nota", "entrevista", "crítica", "columna", "carta", "cuento",
                "poema", "humor", "aviso", "pie de foto", "otro"]


class Articulo(BaseModel):
    titulo: str
    autor: Optional[str]
    seccion: Optional[str]
    tipo: TIPOS
    temas: List[str]
    texto: str
    viene_de_pagina: Optional[int]
    continua_en_pagina: Optional[int]
    empieza_cortado: bool
    termina_cortado: bool


class Ilustracion(BaseModel):
    tipo: Literal["dibujo", "caricatura", "chiste", "historieta", "grabado", "viñeta"]
    descripcion: str
    firma: Optional[str]  # solo la firma del dibujante, tal como está en el dibujo
    retratado: Optional[str]  # a quién muestra, si se sabe; nunca va en firma
    imagen: Literal["arriba", "abajo"]
    caja: List[int]  # x0, y0, x1, y1 en milésimas de esa imagen


class Pagina(BaseModel):
    pagina_impresa: Optional[int]
    legibilidad: Literal["buena", "regular", "mala"]
    partes_sin_leer: bool
    notas: Optional[str]
    articulos: List[Articulo]
    ilustraciones: List[Ilustracion]


INSTRUCCIONES = """Sos un transcriptor experto en prensa uruguaya del siglo XX. Te paso una página del semanario Marcha (Montevideo), número {numero}, del {fecha}; es la imagen {pagina} de {total} del escaneo. La página viene en dos imágenes: "arriba" (el 55 % superior) y "abajo" (el 55 % inferior); se superponen un poco.

Transcribí todos los textos de la página en orden de lectura, siguiendo las columnas como lo haría una persona:
- Uní las palabras cortadas con guion al final del renglón ("re-\\nnuncia" -> "renuncia") y los renglones de un mismo párrafo. Separá los párrafos con una línea en blanco.
- Respetá la ortografía de época tal como está impresa ("fué", "dió", "Constantín"). No corrijas, no resumas, no modernices.
- Incluí también los bloques en diagonal, en recuadros o en tipografía rara. Un bloque en diagonal o girado casi nunca es un texto aparte: suele ser el comienzo o un tramo de un artículo de la misma página y se lee en el lugar que le corresponde dentro de ese artículo.
- Decidí el orden de lectura por la continuidad del texto, no por la posición en la página: una palabra cortada con guion al final de un bloque ("mayor par-") sigue al comienzo de otro ("te de los…"), y una frase o un diálogo que queda abierto sigue donde tiene sentido. Antes de terminar, revisá cada paso de un bloque a otro.
- Si un mismo artículo ocupa varios bloques de la página, devolvelo como un solo artículo, con los bloques ya en orden de lectura. Nunca lo partas en varios artículos con el mismo título.
- Un artículo puede ocupar dos páginas enfrentadas. En ese caso poné el mismo título en las dos partes.
- Si una palabra no se puede leer, escribí [ilegible]. No inventes texto.
- No transcribas dos veces lo que aparece en la zona donde se superponen las dos imágenes.

Para cada artículo:
- autor: la firma tal como está impresa (nombre, iniciales o seudónimo), o null.
- seccion: el nombre de la sección o columna si aparece; si no, null.
- tipo: el que mejor corresponda. Los avisos publicitarios van como "aviso", con su texto principal.
- temas: dos o tres temas generales en español ("política uruguaya", "cine", "economía").
- continua_en_pagina / viene_de_pagina: el número que dicen las marcas impresas como "(Pasa a la pág. X)" o "(Viene de la pág. Y)".
- empieza_cortado: true si el texto arranca a mitad de frase o es claramente la continuación de algo de otra página sin marca. termina_cortado: true si queda cortado al final de la página sin marca de continuación.

Para la página:
- pagina_impresa: el número de página impreso en la página (en el pie o el encabezado), o null si no se ve.
- ilustraciones: solo dos clases de imágenes:
  1. los dibujos firmados de cualquier tipo (dibujo, grabado, caricatura, viñeta), y
  2. el humor gráfico (caricaturas, chistes, historietas), aunque no tenga firma.
  No incluyas fotos, mapas, avisos publicitarios ilustrados, ni dibujos sin firma que solo acompañan un artículo, ni filetes o adornos.
  Para cada una: descripción en una frase; firma: el nombre o seudónimo del dibujante tal como está escrito dentro o al pie del dibujo (por ejemplo "PELODURO", "J. E. Suárez", "Seoane"), o null si no tiene. Cuidado: el nombre de la persona dibujada o el epígrafe NO es la firma; eso va en retratado (a quién muestra el dibujo, si se sabe; si no, null). Decí en qué imagen está ("arriba" o "abajo") y su caja [x0, y0, x1, y1] en milésimas del ancho y alto de esa imagen. Si aparece en las dos imágenes, ponela una sola vez, en la que se vea entera.
- legibilidad: "buena", "regular" o "mala". partes_sin_leer: true si dejaste algún bloque de texto sin transcribir completo, incluidos los textos en diagonal, girados o en recuadros que no pudiste leer enteros.
- notas: problemas de lectura, o null. Mencioná siempre si hubo texto en diagonal, girado o en recuadros que te costó leer o que no leíste completo.

Como ayuda, este es el texto que sacó un OCR automático de la misma página. Tiene muchos errores y mezcla columnas; usalo solo para confirmar palabras dudosas:
<ocr>
{ocr}
</ocr>"""

PALABRA = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{3,}")
# Señales en las notas de Haiku, frase por frase. Cuenta el texto (no la imagen) en diagonal o girado
# que Haiku admite haber leído a medias, porque suele saltearlo; los recuadros, crucigramas, tableros y
# textos de margen no cuentan.
TEXTO_GIRADO = re.compile(r"(?:texto|bloque|cuerpo|frases?|l[ií]neas?|columnas?)\b[^.;]{0,40}?(?:en diagonal|girad|rotad|inclinad)"
                          r"|(?:en diagonal|girad|rotad|inclinad)[^.;]{0,25}?\b(?:texto|bloque|cuerpo)", re.I)
LECTURA_PARCIAL = re.compile(r"parcial|difícil|dificil|aproximad|no (?:se )?(?:pudo|puede|pude) leer|sin transcribir"
                             r"|no (?:se )?transcrib|omit|salte", re.I)
NO_CUENTA = re.compile(r"crucigrama|palabras cruzadas|ajedrez|tablero|no aplica|margen|\bsin texto|no hay texto", re.I)
RESUMIDO = re.compile(r"resum", re.I)
FRASES = re.compile(r"[^.;]+")


class Caja:
    """Cuenta el gasto de toda la corrida y frena antes de pasar el tope."""

    def __init__(self, tope):
        self.tope, self.gastado, self.lock = tope, 0.0, Lock()

    def sumar(self, modelo, ent, sal):
        _, p_ent, p_sal = MODELOS[modelo]
        with self.lock:
            self.gastado += (ent * p_ent + sal * p_sal) / 1e6

    def alcanza(self, estimado):
        with self.lock:
            return self.gastado + estimado <= self.tope


def cliente():
    ruta = os.path.expanduser("~/.marcha-clave-anthropic")
    if os.path.exists(ruta):
        return anthropic.Anthropic(api_key=open(ruta).read().strip(), max_retries=4)
    return anthropic.Anthropic(max_retries=4)


def imagen_b64(im):
    im = im.copy()
    im.thumbnail((LADO_MAXIMO, LADO_MAXIMO), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return base64.standard_b64encode(buf.getvalue()).decode()


def paginas_de(pdf, tmp):
    """Imágenes de cada página suelta, cortando las dobles como ocr_numero.py."""
    subprocess.run(["pdfimages", "-png", pdf, f"{tmp}/i"], check=True)
    for f in sorted(glob.glob(f"{tmp}/i-*.png")):
        im = Image.open(f).convert("L")
        w, h = im.size
        if w < h:
            yield im
        else:
            yield im.crop((0, 0, w // 2, h))
            yield im.crop((w // 2, 0, w, h))


def leer(cli, modelo, im, meta, n, total, ocr):
    w, h = im.size
    franjas = [im.crop((0, int(h * a), w, int(h * b))) for a, b in (ARRIBA, ABAJO)]
    contenido = [
        {"type": "text", "text": "Imagen \"arriba\":"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": imagen_b64(franjas[0])}},
        {"type": "text", "text": "Imagen \"abajo\":"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": imagen_b64(franjas[1])}},
        {"type": "text", "text": INSTRUCCIONES.format(numero=meta["id"], fecha=meta["fecha"], pagina=n,
                                                      total=total, ocr=ocr or "(no hay)")},
    ]
    t0 = time.time()
    r = cli.messages.parse(
        model=MODELOS[modelo][0],
        max_tokens=16000,
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": contenido}],
        output_format=Pagina,
    )
    if r.stop_reason in ("refusal", "max_tokens") or r.parsed_output is None:
        raise RuntimeError(f"stop_reason={r.stop_reason}")
    return {**r.parsed_output.model_dump(), "modelo": modelo, "tokens_entrada": r.usage.input_tokens,
            "tokens_salida": r.usage.output_tokens, "segundos": round(time.time() - t0)}


def palabras(texto):
    return len(PALABRA.findall(texto))


def dudas(pag, ocr):
    """Por qué conviene que Sonnet vuelva a leer la página, o None si la lectura de Haiku alcanza.

    Devuelve (prioridad, motivo); a mayor prioridad, antes pasa a Sonnet si el tope no alcanza para todas.
    """
    if pag is None:
        return 5, "Haiku no pudo leerla"
    if pag.get("bloqueada"):
        return None  # Sonnet también la bloquearía
    notas = pag.get("notas") or ""
    p_ia = sum(palabras(a["texto"]) for a in pag["articulos"])
    p_ocr = palabras(ocr)
    pag["palabras_ia"], pag["palabras_ocr"] = p_ia, p_ocr
    if p_ocr > 150 and p_ia < 0.6 * p_ocr:
        return 4, f"falta texto: {p_ia} palabras contra {p_ocr} del OCR"
    frases = FRASES.findall(notas)
    if any(RESUMIDO.search(f) and "aviso" not in f.lower() for f in frases):
        return 4, "Haiku resumió en vez de transcribir"
    if any(TEXTO_GIRADO.search(f) and not NO_CUENTA.search(f) and (pag["partes_sin_leer"] or LECTURA_PARCIAL.search(f))
           for f in frases):
        return 3, "texto en diagonal o girado leído a medias"
    if pag["legibilidad"] == "mala" and pag["partes_sin_leer"]:
        return 2, "página muy poco legible con partes sin leer"
    return None


CORRECCIONES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "correcciones.json")


def aplicar_correcciones(nid, articulos):
    """Correcciones hechas a mano (correcciones.json): sobreviven a cada nueva lectura o unión.

    Cada corrección identifica un artículo por número y primera página, y reemplaza los campos indicados.
    """
    if not os.path.exists(CORRECCIONES):
        return articulos
    for c in json.load(open(CORRECCIONES))["correcciones"]:
        if c["numero"] != nid:
            continue
        for a in articulos:
            if a["paginas"][0] == c["pagina"] and (not c.get("tipo") or a.get("tipo") == c["tipo"]):
                a.update(c["cambios"])
                a["corregido_a_mano"] = True
    return articulos


PROPIOS = {"cuento", "poema", "aviso", "humor", "pie de foto", "carta"}  # tipos que solo siguen en uno igual


def compatibles(a, b):
    """Dos partes pueden ser el mismo texto: del mismo tipo, o ambas de tipo periodístico general."""
    return a.get("tipo") == b.get("tipo") or not ({a.get("tipo"), b.get("tipo")} & PROPIOS)


def juntar_partes(articulos):
    """Une las partes de un mismo artículo que la IA devolvió por separado en una página (mismo título).

    La parte que sigue en otra página (cortada al final o con "pasa a") va última; la que viene de otra
    página va primera; el resto conserva el orden en que las devolvió la IA.
    """
    grupos = {}
    for a in articulos:
        grupos.setdefault(" ".join(a["titulo"].lower().split()), []).append(a)
    juntos = []
    for partes in grupos.values():
        partes.sort(key=lambda a: (not (a.get("viene_de_pagina")),
                                   bool(a.get("termina_cortado") or a.get("continua_en_pagina")
                                        or a["texto"].rstrip().endswith("-"))))
        a = partes[0]
        for b in partes[1:]:
            a["texto"] = a["texto"].rstrip() + "\n\n" + b["texto"].lstrip()
            for clave in ("autor", "seccion"):
                a[clave] = a.get(clave) or b.get(clave)
            a["termina_cortado"], a["continua_en_pagina"] = b.get("termina_cortado"), b.get("continua_en_pagina")
        juntos.append(a)
    return juntos


def unir_continuaciones(paginas):
    """Une los artículos que siguen en otra página del número.

    Primero las marcas impresas ("pasa a la pág. X" / "viene de la pág. Y", con X e Y
    en numeración impresa). Después, sin marca, un artículo que termina cortado en una
    página (según la IA, o porque su texto termina en guion) con el único de la página
    siguiente que empieza cortado; si no hay ninguno así, con el único de la página
    siguiente que es del mismo tipo (cuento con cuento, crítica con crítica…).
    """
    impresa = {n: p.get("pagina_impresa") or n for n, p in paginas}
    lista = []
    for n, p in paginas:
        lista += juntar_partes(dict(a, paginas=[n]) for a in p["articulos"])
    for a in lista:
        if a["texto"].rstrip().endswith(("-", "—")):
            a["termina_cortado"] = True
    usados = set()

    def pegar(a, b):
        # el título y la firma suelen estar en una sola de las partes
        if b.get("autor") and not a.get("autor"):
            a["titulo"], a["autor"] = b["titulo"], b["autor"]
        fin, inicio = a["texto"].rstrip(), b["texto"].lstrip()
        if fin.endswith("-") and inicio[:1].islower():
            a["texto"] = fin[:-1] + inicio  # palabra cortada entre páginas: "cons-" + "tantemente"
        else:
            a["texto"] = fin + "\n\n" + inicio
        a["paginas"] += b["paginas"]
        a["continua_en_pagina"] = b.get("continua_en_pagina")
        a["termina_cortado"] = b.get("termina_cortado", False)
        usados.add(id(b))

    for a in lista:
        destino = a.get("continua_en_pagina")
        if not destino or id(a) in usados:
            continue
        origen = impresa[a["paginas"][-1]]
        candidatos = [b for b in lista if id(b) not in usados and b is not a
                      and impresa[b["paginas"][0]] == destino and b.get("viene_de_pagina") == origen]
        if candidatos:
            palabras_a = set(a["titulo"].lower().split())
            pegar(a, max(candidatos, key=lambda c: len(palabras_a & set(c["titulo"].lower().split()))))

    for a in lista:
        if id(a) in usados or not a.get("termina_cortado") or a.get("continua_en_pagina"):
            continue
        sig = a["paginas"][-1] + 1
        libres = [b for b in lista if id(b) not in usados and b is not a and b["paginas"][0] == sig
                  and b.get("viene_de_pagina") in (None, impresa[a["paginas"][-1]])]
        # primero el que dice venir justo de esta página, después el que empieza cortado
        candidatos = [b for b in libres if b.get("viene_de_pagina") and compatibles(a, b)]
        if not candidatos:
            candidatos = [b for b in libres if b.get("empieza_cortado") and compatibles(a, b)]
        if not candidatos and a.get("tipo") in PROPIOS - {"aviso", "pie de foto"}:
            candidatos = [b for b in libres if b.get("tipo") == a.get("tipo")]
        if len(candidatos) == 1:
            pegar(a, candidatos[0])
            a["union_sin_marca"] = True
    return [a for a in lista if id(a) not in usados]


def procesar(cli, pdf, salida, carpeta_ocr, caja):
    meta = metadatos(pdf)
    destino = os.path.join(salida, meta["id"])
    os.makedirs(destino, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        imagenes = list(paginas_de(pdf, tmp))
    total = len(imagenes)
    ocr = {}
    for n in range(1, total + 1):
        f = os.path.join(carpeta_ocr, meta["id"], f"p{n:02d}.txt")
        ocr[n] = open(f, encoding="utf-8").read() if os.path.exists(f) else ""

    def pasada(modelo, n, estimado):
        archivo = f"{destino}/p{n:02d}.{modelo}.json"
        if os.path.exists(archivo):
            datos = json.load(open(archivo))
            # lo leído en una corrida anterior también cuenta para el tope
            caja.sumar(modelo, datos.get("tokens_entrada", 12000), datos.get("tokens_salida", 0))
            return datos
        if not caja.alcanza(estimado):
            return None
        try:
            datos = leer(cli, modelo, imagenes[n - 1], meta, n, total, ocr[n])
            caja.sumar(modelo, datos["tokens_entrada"], datos["tokens_salida"])
        except anthropic.BadRequestError as e:
            if "content filtering" not in str(e.message):
                print(f"  {meta['id']} {modelo} pág. {n}: error {e.message}", flush=True)
                return None
            caja.sumar(modelo, 12000, 0)  # lo que se mandó se cobra igual
            datos = {"bloqueada": True, "modelo": modelo, "error": str(e.message)}
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
            raise
        except (RuntimeError, ValidationError, anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            print(f"  {meta['id']} {modelo} pág. {n}: error {e}", flush=True)
            return None
        json.dump(datos, open(archivo, "w"), ensure_ascii=False, indent=1)
        print(f"  {meta['id']} {modelo} pág. {n}/{total}: "
              + ("BLOQUEADA" if datos.get("bloqueada") else
                 f"{len(datos['articulos'])} artículos, {len(datos['ilustraciones'])} ilustraciones, "
                 f"legibilidad {datos['legibilidad']}") + f" (gastado US$ {caja.gastado:.2f})", flush=True)
        return datos

    with ThreadPoolExecutor(4) as ex:
        haiku = dict(zip(range(1, total + 1), ex.map(lambda n: pasada("haiku", n, 0.01), range(1, total + 1))))
    return meta, destino, total, ocr, haiku, pasada


def finalizar(meta, destino, total, ocr, lecturas):
    paginas = []
    for n in range(1, total + 1):
        pag = lecturas[n]
        if pag is None or pag.get("bloqueada"):
            pag = {"pagina_impresa": None, "legibilidad": "mala", "partes_sin_leer": True,
                   "notas": "Sin lectura con IA (bloqueada por el filtro de contenido o sin presupuesto); "
                            "se usa el texto de Tesseract.",
                   "articulos": [], "ilustraciones": [], "modelo": "tesseract", "texto_tesseract": ocr[n],
                   "bloqueada": bool(pag and pag.get("bloqueada"))}
        json.dump(pag, open(f"{destino}/p{n:02d}.json", "w"), ensure_ascii=False, indent=1)
        paginas.append((n, pag))
    articulos = aplicar_correcciones(meta["id"], unir_continuaciones(paginas))
    json.dump({**meta, "articulos": articulos}, open(f"{destino}/articulos.json", "w"), ensure_ascii=False, indent=1)
    return paginas, articulos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdfs", nargs="+")
    ap.add_argument("--salida", required=True)
    ap.add_argument("--tesseract", required=True, help="carpeta de salida de procesar_coleccion.py")
    ap.add_argument("--tope", type=float, default=2.0, help="gasto máximo de la corrida, en dólares")
    a = ap.parse_args()
    cli, caja = cliente(), Caja(a.tope)

    # 1. Haiku en todas las páginas de todos los números
    numeros = []
    for pdf in a.pdfs:
        try:
            numeros.append(procesar(cli, pdf, a.salida, a.tesseract, caja))
        except anthropic.AuthenticationError:
            sys.exit("La clave de API no es válida.")
        except anthropic.PermissionDeniedError as e:
            sys.exit(f"La cuenta no tiene permiso o no tiene crédito: {e.message}")
    gasto_haiku = caja.gastado

    # 2. Sonnet en las páginas más dudosas, mientras alcance el tope
    cola = []
    for i, (meta, destino, total, ocr, haiku, pasada) in enumerate(numeros):
        for n, h in haiku.items():
            motivo = dudas(h, ocr[n])
            if motivo:
                cola.append((motivo[0], i, n))
    cola.sort(reverse=True)
    escaladas = []
    for d, i, n in cola:
        _, _, _, _, _, pasada = numeros[i]
        if not caja.alcanza(0.12):
            print(f"Tope de US$ {a.tope:.2f} alcanzado: quedan {len(cola) - len(escaladas)} páginas dudosas sin Sonnet.")
            break
        s = pasada("sonnet", n, 0.12)
        if s is not None:
            escaladas.append((i, n, d, s))

    # 3. Versión final de cada página y unión de artículos
    resumen = {"tope_usd": a.tope, "gasto_haiku_usd": round(gasto_haiku, 4), "numeros": []}
    for i, (meta, destino, total, ocr, haiku, _) in enumerate(numeros):
        lecturas = dict(haiku)
        for n in lecturas:
            # la lectura de Sonnet, si la hay (de esta corrida o de una anterior), reemplaza a la de Haiku
            archivo = f"{destino}/p{n:02d}.sonnet.json"
            if os.path.exists(archivo) and not (s := json.load(open(archivo))).get("bloqueada"):
                lecturas[n] = s
        paginas, articulos = finalizar(meta, destino, total, ocr, lecturas)
        resumen["numeros"].append({
            "id": meta["id"], "fecha": meta["fecha"], "paginas": total,
            "a_sonnet": sorted(n for j, n, d, s in escaladas if j == i),
            "bloqueadas": sorted(n for n, p in paginas if p.get("bloqueada")),
            "articulos": len(articulos),
            "unidos_entre_paginas": sum(1 for x in articulos if len(x["paginas"]) > 1),
            "ilustraciones": sum(len(p["ilustraciones"]) for _, p in paginas)})
    resumen["paginas_dudosas"] = len(cola)
    resumen["gasto_total_usd"] = round(caja.gastado, 4)
    json.dump(resumen, open(os.path.join(a.salida, "costo.json"), "w"), ensure_ascii=False, indent=1)
    print(json.dumps(resumen, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
