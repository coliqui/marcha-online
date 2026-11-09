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
import argparse, base64, glob, io, json, os, re, subprocess, sys, tempfile, time, unicodedata
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
                "poema", "humor", "aviso", "pie de foto", "destacado", "otro"]


class Referencia(BaseModel):
    texto_cita: str  # la frase tal como está ("en nuestro número anterior", "ver MARCHA Nº 1079")
    numero: Optional[str]
    fecha: Optional[str]  # AAAA-MM-DD si se puede saber
    titulo: Optional[str]
    autor: Optional[str]


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
    referencias: List[Referencia] = []  # menciones a otros números o artículos de Marcha
    # modo pliego: el artículo cruza a la página enfrentada (sigue en ella, viene de ella, o las dos cosas)
    cruza_pliego: Literal["no", "sigue_en_la_enfrentada", "viene_de_la_enfrentada", "ambas"] = "no"
    # tipo "destacado": cita en recuadro o letra grande que repite una frase de otro artículo (su título va acá)
    destacado_de: Optional[str] = None
    # bajada o copete: texto corto en otra letra junto al título (o un segundo titular del mismo artículo)
    bajada: Optional[str] = None


class Ilustracion(BaseModel):
    tipo: Literal["dibujo", "caricatura", "chiste", "historieta", "grabado", "viñeta"]
    descripcion: str
    firma: Optional[str]  # solo la firma del dibujante, tal como está en el dibujo
    retratado: Optional[str]  # a quién muestra, si se sabe; nunca va en firma
    imagen: Literal["arriba", "abajo"]
    caja: List[int]  # x0, y0, x1, y1 en milésimas de esa imagen


class BloqueGirado(BaseModel):
    imagen: Literal["arriba", "abajo"]
    caja: List[int]  # x0, y0, x1, y1 en milésimas de esa imagen


class Bloque(BaseModel):
    texto: str
    va_despues_de: Optional[str]  # últimas palabras del texto de la página tras las que va el bloque


INSTRUCCIONES_BLOQUE = """Esta imagen es un bloque de texto de una página del semanario Marcha (Montevideo, {fecha}) que estaba impreso en diagonal; lo recortamos del escaneo y lo giramos para que se lea derecho. Puede incluir pedazos de otras cosas de la página alrededor: ignoralos y transcribí solo el bloque de texto que quedó derecho.

- Transcribí palabra por palabra, en orden. Uní las palabras cortadas con guion al final del renglón y los renglones de un mismo párrafo; separá los párrafos con una línea en blanco.
- Respetá la ortografía de época tal como está impresa.
- Si una palabra o un tramo no se puede leer, escribí [ilegible] en su lugar. Nunca resumas, completes, unas frases ni inventes texto.
- Si el bloque empieza o termina con una palabra cortada, dejala tal cual (por ejemplo "tra el casco" o "mayor par-").
- Si en la imagen no hay un bloque de texto corrido de un artículo (es una sombra del escaneo, una mancha, un dibujo, la marca del microfilm o el texto de un aviso), devolvé texto vacío.

Este es el texto ya leído del resto de la página. En va_despues_de copiá, exactamente como aparecen ahí, las últimas cinco a ocho palabras tras las cuales va el bloque, según la continuidad del texto; si el bloque va al comienzo de un artículo o no lo podés saber, null.
<pagina>
{texto}
</pagina>"""


class Pagina(BaseModel):
    pagina_impresa: Optional[int]
    legibilidad: Literal["buena", "regular", "mala"]
    partes_sin_leer: bool
    notas: Optional[str]
    articulos: List[Articulo]
    ilustraciones: List[Ilustracion]
    bloques_girados: List[BloqueGirado]


class PaginaCompleta(Pagina):
    transcripcion_completa: bool
    omisiones: Optional[str]


# Agregados a las instrucciones (desde la etapa 2): contexto verdadero del trabajo y declaración de completitud.
CONTEXTO = """Contexto: esta transcripción es parte de la digitalización del archivo completo del semanario Marcha (1939-1974) que realizan los herederos de su fundador, Carlos Quijano, para ofrecerlo en consulta pública y gratuita. El objetivo es el texto íntegro de cada página, tal como fue impreso: es un archivo, no un resumen. La respuesta puede ser muy larga (una página de Marcha suele tener entre 1.500 y 3.000 palabras); transcribila entera.

"""
COMPLETITUD = """
- transcripcion_completa: true si transcribiste entero el texto legible de todos los artículos de la página (notas, editoriales, críticas, cartas, cuentos, poemas, columnas). No cuentan como omisión: los avisos publicitarios, los encabezados y adornos, los crucigramas o tableros, ni la zona repetida donde se superponen las dos imágenes. false solo si abreviaste, resumiste o dejaste afuera texto de algún artículo; en ese caso, en omisiones decí exactamente qué quedó afuera (qué artículo, qué columna o qué renglones) y por qué. "Mala lectura", "calidad del OCR" o "texto denso" no son razones: el texto se lee de la imagen, no del OCR (que es solo una ayuda y suele tener errores); si la imagen se lee, se transcribe. Nunca pongas [ilegible] en un texto que se puede leer: [ilegible] es solo para lo que no se ve.
"""


INSTRUCCIONES = """Sos un transcriptor experto en prensa uruguaya del siglo XX. Te paso una página del semanario Marcha (Montevideo), número {numero}, del {fecha}; es la imagen {pagina} de {total} del escaneo. La página viene en dos imágenes: "arriba" (el 55 % superior) y "abajo" (el 55 % inferior); se superponen un poco.

Transcribí todos los textos de la página en orden de lectura, siguiendo las columnas como lo haría una persona:
- Transcribí todo, aunque la página sea larga o densa. Leé siempre de la imagen: el texto OCR es solo una ayuda y en las páginas gastadas viene lleno de errores, aunque la imagen se lea bien.
- Las listas de nombres (el staff o cuerpo de redacción, nóminas, censos, listas de firmantes) van completas, nombre por nombre: son parte del archivo. Las críticas de libros, cine y teatro, las cartas de lectores, las novedades editoriales, las carteleras y los recuadros son parte del archivo y van completos, como cualquier artículo.
- En las carteleras y guías de cine o teatro, lo más importante es, de cada película u obra, el título, el director y el reparto (y la sala, si figura): transcribilos siempre, cada película u obra en su propio párrafo, como en el original. Las reseñas también, si se pueden leer, pero son secundarias.
- Las tablas (cifras, estadísticas, resultados, horarios, cuadros comparativos) transcribilas como tabla Markdown dentro del texto del artículo, celda por celda y con todas sus filas, incluidas las de totales: una línea por fila, las celdas separadas por "|", la fila de encabezados seguida de una línea "| --- | --- |". Respetá las cifras tal como están impresas; si una celda no se puede leer, poné [ilegible]. El texto que rodea a la tabla va antes y después, en párrafos aparte.
- En la tapa, cada titular o llamada es un ítem aparte (no mezcles una llamada con la nota principal).
- La firma ("Escribe X", "Por X", o un nombre arriba o al pie) va en autor: nunca es el título de un artículo aparte, aunque esté en letra grande en medio de la página. Las volantas, intertítulos y subtítulos que aparecen entre las columnas de un artículo tampoco empiezan otro: quedan dentro de su texto, como párrafo aparte, y el artículo sigue. Un subtítulo en negrita o en mayúsculas dentro de una nota (por ejemplo «Bombas...», «Situación del dólar en 1964») es parte de esa nota, aunque esté en otra columna.
- En las encuestas, las preguntas de la encuesta van dentro del texto de la nota (no son artículos aparte); cada respuesta firmada puede ser un ítem con su autor. Un recuadro con título de sección que contiene dos notas distintas son dos artículos, cada uno con su título.
- La bajada o copete (un texto corto en negrita o en otra letra junto al título, que resume la nota) y los titulares secundarios de un mismo artículo van en el campo bajada de ese artículo: no son un artículo aparte. Si las columnas que rodean un titular son la continuación del mismo artículo, es todo un solo artículo.
- Las citas destacadas (una frase del mismo artículo repetida en un recuadro o en letra grande) van como un ítem de tipo "destacado", con el título del artículo del que salen en destacado_de. Un recuadro con texto propio (una nota del autor o de la redacción) es un artículo o recuadro normal.
- Uní las palabras cortadas con guion al final del renglón ("re-\\nnuncia" -> "renuncia") y los renglones de un mismo párrafo. Separá los párrafos con una línea en blanco.
- Respetá la ortografía de época tal como está impresa ("fué", "dió", "Constantín"). No corrijas, no resumas, no modernices.
- Incluí también los bloques en diagonal, en recuadros o en tipografía rara. Un bloque en diagonal o girado casi nunca es un texto aparte: suele ser el comienzo o un tramo de un artículo de la misma página y se lee en el lugar que le corresponde dentro de ese artículo.
- Decidí el orden de lectura por la continuidad del texto, no por la posición en la página: una palabra cortada con guion al final de un bloque ("mayor par-") sigue al comienzo de otro ("te de los…"), y una frase o un diálogo que queda abierto sigue donde tiene sentido. Antes de terminar, revisá cada paso de un bloque a otro.
- Si un mismo artículo ocupa varios bloques de la página, devolvelo como un solo artículo, con los bloques ya en orden de lectura. Nunca lo partas en varios artículos con el mismo título.
- Un artículo puede ocupar dos páginas enfrentadas, a veces con el título repartido entre las dos (una parte en cada página). En ese caso usá el título completo en las dos partes.
- Si una palabra o un tramo no se puede leer, escribí [ilegible] en su lugar. Nunca resumas, completes, unas frases ni inventes texto: un hueco marcado es mejor que un texto que parece verdadero.
- No transcribas dos veces lo que aparece en la zona donde se superponen las dos imágenes.

Para cada artículo:
- autor: la firma tal como está impresa (nombre, iniciales o seudónimo), o null. La firma puede estar al pie, arriba del título o en el encabezado ("Por …"): buscala en los tres lugares.
- seccion: el nombre de la sección o columna si aparece; si no, null.
- tipo: el que mejor corresponda. Los avisos publicitarios van como "aviso", con su texto principal.
- temas: dos o tres temas generales en español ("política uruguaya", "cine", "economía").
- continua_en_pagina / viene_de_pagina: el número que dicen las marcas impresas como "(Pasa a la pág. X)" o "(Viene de la pág. Y)".
- referencias: las menciones a otros números o artículos de Marcha ("en nuestro número anterior", "ver MARCHA Nº 1079", "como publicamos el 12 de mayo", "(Viene del número 1078)", "el artículo de Julio Castro sobre…"), con el número, la fecha (AAAA-MM-DD), el título y el autor citados si se pueden saber; si no hay, una lista vacía.
- empieza_cortado: true si el texto arranca a mitad de frase o es claramente la continuación de algo de otra página sin marca. termina_cortado: true si queda cortado al final de la página sin marca de continuación.

Para la página:
- pagina_impresa: el número de página impreso en la página (en el pie o el encabezado), o null si no se ve.
- ilustraciones: solo dos clases de imágenes:
  1. los dibujos firmados de cualquier tipo (dibujo, grabado, caricatura, viñeta) — una tira de varios recuadros es una sola historieta —, y
  2. el humor gráfico (caricaturas, chistes, historietas), aunque no tenga firma.
  No incluyas fotos, mapas, avisos publicitarios ilustrados, ni dibujos sin firma que solo acompañan un artículo, ni filetes o adornos.
  Para cada una: descripción en una frase; firma: el nombre o seudónimo del dibujante tal como está escrito dentro o al pie del dibujo (por ejemplo "PELODURO", "J. E. Suárez", "Seoane"), o null si no tiene. Cuidado: el nombre de la persona dibujada o el epígrafe NO es la firma; eso va en retratado (a quién muestra el dibujo, si se sabe; si no, null). Decí en qué imagen está ("arriba" o "abajo") y su caja [x0, y0, x1, y1] en milésimas del ancho y alto de esa imagen. Si aparece en las dos imágenes, ponela una sola vez, en la que se vea entera.
- bloques_girados: solo los bloques de texto corrido (varios renglones de un artículo) impresos inclinados en diagonal, con la imagen en que está ("arriba" o "abajo") y su caja [x0, y0, x1, y1] en milésimas de esa imagen. No cuentan los recuadros, los avisos, los títulos, las firmas, los epígrafes ni el texto dentro de dibujos o caricaturas, aunque estén torcidos. Casi todas las páginas no tienen ninguno: en ese caso, una lista vacía.
- legibilidad: "buena", "regular" o "mala". partes_sin_leer: true si dejaste algún bloque de texto sin transcribir completo, incluidos los textos en diagonal, girados o en recuadros que no pudiste leer enteros.
- notas: problemas de lectura, o null. Mencioná siempre si hubo texto en diagonal, girado o en recuadros que te costó leer o que no leíste completo.

{enderezados}Como ayuda, este es el texto que sacó un OCR automático de la misma página. Tiene muchos errores y mezcla columnas; usalo solo para confirmar palabras dudosas:
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


def pliegos(pdf):
    """Página -> página enfrentada, según cómo está escaneado el PDF: cada imagen doble (más ancha que alta)
    es un pliego con la página izquierda y la derecha del diario abierto."""
    salida = subprocess.run(["pdfimages", "-list", pdf], capture_output=True, text=True, check=True).stdout
    enfrentadas, n = {}, 1
    for linea in salida.splitlines()[2:]:
        c = linea.split()
        if len(c) > 4 and c[2] == "image":
            if int(c[3]) > int(c[4]):
                enfrentadas[n], enfrentadas[n + 1] = n + 1, n
                n += 2
            else:
                n += 1
    return enfrentadas


PLIEGO = """
Esta página forma un pliego con la imagen {m}, que en el diario abierto está a su {lado}. Te la paso al final ("Página enfrentada") solo como contexto: no la transcribas. Muchos artículos de Marcha cruzan el pliego:
- Si un artículo de esta página sigue en la página enfrentada, en cruza_pliego poné "sigue_en_la_enfrentada"; si viene de ella, "viene_de_la_enfrentada"; si las dos cosas, "ambas"; si no, "no".
- Si el título está repartido entre las dos páginas (una parte en cada una), poné en esta página el título completo.
"""


MITAD = """
Esta vez transcribí SOLO la mitad {cual} de la página: la imagen "{imagen}". La otra mitad la transcribe otro pedido; la página entera va al final solo como contexto. {regla} En transcripcion_completa y omisiones hablá solo de tu mitad: que la otra mitad la transcriba otro pedido no es una omisión.
- Si un artículo sigue en la otra mitad, marcá termina_cortado (arriba) o empieza_cortado (abajo) y usá el mismo título que tiene en la página.
"""
REGLA_MITAD = {"superior": "Terminá donde termina la imagen \"arriba\".",
               "inferior": "La imagen \"abajo\" se superpone un poco con la de arriba: empezá por el primer renglón que no "
                           "esté entero en la mitad superior, sin repetir lo de arriba. No vuelvas a copiar párrafos de "
                           "la mitad superior aunque sean del mismo artículo: cada renglón impreso se transcribe una sola "
                           "vez, en el pedido de la mitad donde está."}


def contenido_pagina(im, meta, n, total, ocr, enderezados=(), completa=False, enfrentada=None, mitad=None, giros=(0, 0),
                     giro=0):
    """El mensaje que se manda por cada página: sus dos mitades (o solo una, si la página se lee en dos pedidos),
    los bloques enderezados, la página enfrentada como contexto y las instrucciones. giros: cuánto girar cada
    mitad (grados) para que el texto quede derecho; giro: cuánto girar la página entera (impresa de costado)."""
    if giro:
        im = im.rotate(-giro, expand=True)
    w, h = im.size
    franjas = [im.crop((0, int(h * a), w, int(h * b))).rotate(-g, expand=True) if g else im.crop((0, int(h * a), w, int(h * b)))
               for (a, b), g in zip((ARRIBA, ABAJO), giros)]
    contenido = []
    for nombre, franja, cual in (("arriba", franjas[0], "superior"), ("abajo", franjas[1], "inferior")):
        if mitad in (None, cual):
            contenido += [{"type": "text", "text": f"Imagen \"{nombre}\":"},
                          {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": imagen_b64(franja)}}]
    for k, b in enumerate(enderezados, 1):
        contenido += [{"type": "text", "text": f"Bloque enderezado {k}:"},
                      {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": imagen_b64(b)}}]
    aviso = ("Las imágenes \"Bloque enderezado\" son los bloques de texto en diagonal o girados de esta misma página, "
             "recortados del escaneo original y girados para que se lean derechos. Transcribí esos bloques a partir "
             "de ellas, palabra por palabra, y ubicá su texto en el lugar que le corresponde dentro del artículo, "
             "según la continuidad con los bloques de antes y de después.\n\n") if enderezados else ""
    texto = INSTRUCCIONES.format(numero=meta["id"], fecha=meta["fecha"], pagina=n, total=total,
                                 ocr=ocr or "(no hay)", enderezados=aviso)
    if completa:
        corte = texto.index("\nComo ayuda, este es el texto")
        texto = CONTEXTO + texto[:corte] + COMPLETITUD + texto[corte:]
    if enfrentada:
        im_e, m = enfrentada
        corte = texto.index("\nComo ayuda, este es el texto")
        texto = texto[:corte] + PLIEGO.format(m=m, lado="izquierda" if m < n else "derecha") + texto[corte:]
    if mitad:
        corte = texto.index("\nComo ayuda, este es el texto")
        texto = texto[:corte] + MITAD.format(cual=mitad, imagen="arriba" if mitad == "superior" else "abajo",
                                             regla=REGLA_MITAD[mitad]) + texto[corte:]
    contenido.append({"type": "text", "text": texto})
    if mitad:
        contenido += [{"type": "text", "text": "Página entera, solo como contexto:"},
                      {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": imagen_b64(im)}}]
    if enfrentada:
        contenido += [{"type": "text", "text": f"Página enfrentada (imagen {enfrentada[1]}), solo como contexto:"},
                      {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": imagen_b64(enfrentada[0])}}]
    return contenido


def leer(cli, modelo, im, meta, n, total, ocr, enderezados=(), esfuerzo="low", completa=False, enfrentada=None,
         mitad=None, giros=(0, 0), giro=0):
    contenido = contenido_pagina(im, meta, n, total, ocr, enderezados, completa, enfrentada, mitad, giros, giro)
    t0 = time.time()
    r = cli.messages.parse(
        model=MODELOS[modelo][0],
        max_tokens=16000,
        output_config={"effort": esfuerzo},
        messages=[{"role": "user", "content": contenido}],
        output_format=PaginaCompleta if completa else Pagina,
    )
    if r.stop_reason in ("refusal", "max_tokens") or r.parsed_output is None:
        raise RuntimeError(f"stop_reason={r.stop_reason}")
    return {**r.parsed_output.model_dump(), "modelo": modelo, "tokens_entrada": r.usage.input_tokens,
            "tokens_salida": r.usage.output_tokens, "segundos": round(time.time() - t0)}


def leer_bloque(cli, modelo, recorte, meta, texto_pagina):
    contenido = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": imagen_b64(recorte)}},
                 {"type": "text", "text": INSTRUCCIONES_BLOQUE.format(fecha=meta["fecha"], texto=texto_pagina)}]
    r = cli.messages.parse(model=MODELOS[modelo][0], max_tokens=8000, output_config={"effort": "low"},
                           messages=[{"role": "user", "content": contenido}], output_format=Bloque)
    if r.stop_reason in ("refusal", "max_tokens") or r.parsed_output is None:
        raise RuntimeError(f"stop_reason={r.stop_reason}")
    return r.parsed_output.model_dump(), r.usage.input_tokens, r.usage.output_tokens


def _palabras_norm(texto):
    return [m.group(0).lower() for m in re.finditer(r"\w+", unicodedata.normalize("NFC", texto))]


def ya_transcripto(articulos, bloque, umbral=0.6):
    """El bloque ya estaba en la lectura de la página (la IA a veces lo transcribe igual): se comparan
    secuencias de 4 palabras, sin tildes de puntuación ni mayúsculas."""
    pb = _palabras_norm(bloque["texto"])
    if len(pb) < 8:
        return False
    texto = _palabras_norm(" ".join(a["texto"] for a in articulos))
    grupos = {tuple(texto[i:i + 4]) for i in range(len(texto) - 3)}
    propios = [tuple(pb[i:i + 4]) for i in range(len(pb) - 3)]
    return sum(g in grupos for g in propios) / len(propios) >= umbral


def insertar_bloque(articulos, bloque):
    """Pone el texto del bloque enderezado en su lugar dentro del artículo. Busca las últimas palabras que
    indicó la IA (sin puntuación ni mayúsculas; la última puede estar cortada). Si el bloque ya estaba
    transcripto, no lo repite. Si no encuentra dónde va, lo agrega al comienzo del artículo más largo de la
    página y lo marca para revisar. Devuelve "ubicado", "ya_estaba" o "sin_ubicar"."""
    if len(bloque["texto"].split()) < 5:
        return "sin_texto"
    if ya_transcripto(articulos, bloque):
        return "ya_estaba"
    ancla = _palabras_norm(bloque.get("va_despues_de") or "")[-5:]
    if len(ancla) >= 2:
        for a in articulos:
            tokens = list(re.finditer(r"\w+", a["texto"]))
            bajas = [t.group(0).lower() for t in tokens]
            for i in range(len(bajas) - len(ancla) + 1):
                if bajas[i:i + len(ancla) - 1] == ancla[:-1] and bajas[i + len(ancla) - 1].startswith(ancla[-1]):
                    fin = tokens[i + len(ancla) - 1].end()
                    # se avanza hasta el final de la palabra y de su puntuación
                    while fin < len(a["texto"]) and not a["texto"][fin].isspace():
                        fin += 1
                    a["texto"] = a["texto"][:fin] + "\n\n" + bloque["texto"].strip() + "\n\n" + a["texto"][fin:].lstrip()
                    return "ubicado"
    if articulos:
        a = max(articulos, key=lambda x: len(x["texto"]))
        a["texto"] = bloque["texto"].strip() + "\n\n" + a["texto"]
        a["bloque_sin_ubicar"] = True
    return "sin_ubicar"


def enderezar(im, bloque, margen=0.25):
    """Recorta un bloque girado de la página entera (resolución original) y lo gira hasta que los
    renglones queden horizontales.

    El margen es amplio porque las cajas que da el modelo son aproximadas. Como el bloque ya fue señalado
    como girado, no se prueban los ángulos cercanos a 0° ni a 90°: así no gana el texto derecho que suele
    quedar dentro del recorte (columnas vecinas, títulos), ni esas columnas puestas de costado.
    """
    import cv2, numpy as np
    a, b = ARRIBA if bloque["imagen"] == "arriba" else ABAJO
    x0, y0, x1, y1 = (max(0, min(1000, v)) / 1000 for v in bloque["caja"])
    w, h = im.size
    caja = (max(0, x0 - margen) * w, max(0, a + y0 * (b - a) - margen) * h,
            min(1, x1 + margen) * w, min(1, a + y1 * (b - a) + margen) * h)
    recorte = im.crop(tuple(int(v) for v in caja))
    chico = recorte.copy()
    chico.thumbnail((900, 900))
    _, tinta = cv2.threshold(np.array(chico), 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    cw, ch = tinta.shape[1], tinta.shape[0]

    # 1. Las letras de cada palabra se juntan en una mancha alargada en el sentido del renglón; cada
    #    mancha vota por su ángulo con su largo. Gana el ángulo inclinado con más largo total.
    manchas = cv2.dilate(tinta, np.ones((3, 3), np.uint8), iterations=1)
    votos = np.zeros(181)
    for c in cv2.findContours(manchas, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]:
        puntos = cv2.boxPoints(cv2.minAreaRect(c))
        lados = [puntos[1] - puntos[0], puntos[2] - puntos[1]]
        largo, corto = sorted(lados, key=lambda v: -np.hypot(*v))
        if np.hypot(*largo) < 25 or np.hypot(*largo) < 2.5 * max(np.hypot(*corto), 1):
            continue
        angulo = np.degrees(np.arctan2(largo[1], largo[0]))  # y hacia abajo: renglón que baja = positivo
        angulo = (angulo + 90) % 180 - 90
        if 6 <= abs(angulo) <= 84:
            votos[int(round(angulo)) + 90] += np.hypot(*largo)
    if not votos.any():
        return recorte, 0.0
    votos = np.convolve(votos, np.ones(5), mode="same")
    candidato = int(np.argmax(votos)) - 90

    # 2. Ajuste fino cerca de ese ángulo: donde las sumas por fila de la tinta varían más.
    def nitidez(angulo):
        m = cv2.getRotationMatrix2D((cw / 2, ch / 2), angulo, 1)
        return float(np.var(cv2.warpAffine(tinta, m, (cw, ch)).sum(axis=1)))

    mejor = max((candidato + d / 4 for d in range(-12, 13)), key=nitidez)
    return recorte.rotate(mejor, resample=Image.BICUBIC, expand=True, fillcolor=255), mejor


DIAGONAL = (15, 75)  # grados: fuera de este rango el bloque no está de verdad inclinado


def orientacion(im):
    """Cuántos grados hay que girar una página para que el texto quede derecho, según Tesseract: 90 o 270 si está
    impresa de costado, 0 si está derecha o si no está seguro. No se usa 180 porque da falsas alarmas."""
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        chica = im.copy()
        chica.thumbnail((2000, 2000))
        chica.save(f.name)
        salida = subprocess.run(["tesseract", f.name, "stdout", "--psm", "0"], capture_output=True, text=True).stdout
    giro = re.search(r"Rotate: (\d+)", salida)
    confianza = re.search(r"Orientation confidence: ([\d.]+)", salida)
    if giro and confianza and float(confianza.group(1)) >= 2 and int(giro.group(1)) in (90, 270):
        return int(giro.group(1))
    return 0


def confirmar_bloques(pag, im):
    """Mide el ángulo real de cada bloque girado que informó la IA y guarda en la página solo los que
    están de verdad en diagonal ("bloques_confirmados"); la IA suele marcar recuadros y avisos derechos."""
    confirmados = []
    for bloque in pag.get("bloques_girados") or []:
        _, angulo = enderezar(im, bloque)
        if DIAGONAL[0] <= abs(angulo) <= DIAGONAL[1]:
            confirmados.append({**bloque, "angulo": angulo})
    pag["bloques_confirmados"] = confirmados
    return confirmados


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
    if pag.get("transcripcion_completa") is False and p_ia < 0.75 * p_ocr:
        return 4, f"Haiku declaró la transcripción incompleta: {(pag.get('omisiones') or '')[:80]}"
    if pag.get("bloques_confirmados"):
        return 3, f"{len(pag['bloques_confirmados'])} bloque(s) de texto en diagonal para enderezar"
    if any(TEXTO_GIRADO.search(f) and not NO_CUENTA.search(f) and (pag["partes_sin_leer"] or LECTURA_PARCIAL.search(f))
           for f in frases):
        return 3, "texto en diagonal o girado leído a medias"
    if pag["legibilidad"] == "mala" and pag["partes_sin_leer"]:
        return 2, "página muy poco legible con partes sin leer"
    return None


CORRECCIONES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "correcciones.json")


# Comentarios que la IA mete a veces dentro de la transcripción (no están impresos): se sacan al armar la versión
# final. Son patrones muy específicos; las «NOTA:» impresas en Marcha y los [ilegible] no se tocan.
COMENTARIOS_IA = [
    re.compile(r"\[(?!ilegible\]|cortado\])[^\]\n]{0,160}?(contin[uú]a|sigue en|transcrib|columna|p[aá]gina enfrentada|orden de lectura)[^\]\n]{0,160}\]", re.I),
    re.compile(r"\((?:nota|n\. de la t\.?)[^)\n]{0,200}?(transcrib|columna|orden de lectura|imagen|escaneo|digitaliz)[^)\n]{0,200}\)", re.I),
    re.compile(r"(?:^|(?<=[.!?\s]))Estas notas son parte del mismo art[ií]culo\.?", re.I),
    re.compile(r"\(?(?:El texto |La nota )?[Cc]ontin[uú]a en la p[aá]gina enfrentada\.?\)?"),
]


def sin_comentarios_ia(texto):
    """-> (texto limpio, cuántos comentarios se sacaron)"""
    n = 0
    for p in COMENTARIOS_IA:
        texto, k = p.subn("", texto)
        n += k
    if n:
        texto = re.sub(r"[ \t]+\n", "\n", re.sub(r"\n{3,}", "\n\n", texto)).strip()
    return texto, n


def adjuntar_destacados(articulos):
    """Las citas destacadas (tipo "destacado") pasan a ser parte del artículo del que salen: se busca por el título
    que dio la IA (destacado_de) entre los artículos de la misma página o la de al lado. Si no se encuentra, queda
    como ítem aparte, de tipo "destacado"."""
    parecido = lambda t: " ".join(re.findall(r"[a-z0-9]+", unicodedata.normalize("NFD", t or "").encode("ascii", "ignore").decode().lower()))
    sacar = []
    for d in articulos:
        if d.get("tipo") != "destacado" or not d.get("destacado_de"):
            continue
        cerca = lambda a: any(abs(n - m) <= 1 for n in a["paginas"] for m in d["paginas"])
        de = parecido(d["destacado_de"])
        a = next((a for a in articulos if a is not d and a.get("tipo") != "destacado" and cerca(a)
                  and (parecido(a["titulo"]) == de or (len(de) > 8 and de in parecido(a["titulo"]))
                       or (len(de) > 15 and parecido(a["titulo"])[:15] == de[:15]))), None)
        if a:
            a.setdefault("destacados", []).append({"texto": d["texto"], "autor": d.get("autor"), "nota": None})
            sacar.append(d)
    return [a for a in articulos if a not in sacar] + nuevas_separadas


def aplicar_correcciones(nid, articulos):
    """Correcciones hechas a mano (correcciones.json): sobreviven a cada nueva lectura o unión.

    Cada corrección identifica un artículo por número y primera página (y, si hace falta, tipo o parte del
    título: "titulo_contiene", sin importar tildes ni mayúsculas), reemplaza los campos de
    "cambios" y aplica los "reemplazos" de texto ([texto leído, texto correcto]) en orden.
    Además puede juntar en ese artículo otras piezas que la IA separó por error, identificadas igual (página,
    tipo, titulo_contiene): "unir" (su texto sigue al del artículo, en ese orden; "antes" es un intertítulo que
    va entre medio), "destacados" (citas destacadas en recuadro: quedan como cita del artículo, no como artículo
    aparte) y "recuadros" (recuadros propios del artículo, como una nota del autor).
    """
    if not os.path.exists(CORRECCIONES):
        return articulos
    parecido = lambda t: unicodedata.normalize("NFD", t or "").encode("ascii", "ignore").decode().lower()
    es = lambda a, c: (a["paginas"][0] == c["pagina"] and (not c.get("tipo") or a.get("tipo") == c["tipo"])
                       and (not c.get("titulo_contiene") or parecido(c["titulo_contiene"]) in parecido(a["titulo"]))
                       and (not c.get("empieza") or " ".join(parecido(a["texto"]).split()).startswith(" ".join(parecido(c["empieza"]).split()))))
    sacar, nuevas_separadas = [], []
    for c in json.load(open(CORRECCIONES))["correcciones"]:
        if c["numero"] != nid:
            continue
        for a in articulos:
            if a in sacar or not es(a, c):
                continue
            if c.get("separar_desde"):  # una unión equivocada: desde ese texto, es otra nota (se agrega aparte)
                sep = c["separar_desde"]
                corte = a["texto"].find(sep["desde"])
                if corte <= 0:
                    print(f"  corrección sin aplicar en nº {nid} pág. {c['pagina']}: no encontré {sep['desde']!r}")
                else:
                    nueva = {**{k: None for k in ("autor", "seccion", "viene_de_pagina", "continua_en_pagina")},
                             "titulo": sep["titulo"], "tipo": sep.get("tipo", a.get("tipo")), "temas": sep.get("temas", []),
                             "autor": sep.get("autor"), "texto": a["texto"][corte:].strip(), "paginas": sep["paginas"],
                             "separada_por_juez": True}
                    a["texto"] = a["texto"][:corte].rstrip()
                    a["paginas"] = [n for n in a["paginas"] if n not in sep["paginas"]] or a["paginas"][:1]
                    nuevas_separadas.append(nueva)
            if c.get("cortar_desde"):  # un tramo pegado por error (copiado de otra nota que ya lo tiene): se saca
                corte = a["texto"].find(c["cortar_desde"])
                if corte < 0:
                    print(f"  corrección sin aplicar en nº {nid} pág. {c['pagina']}: no encontré {c['cortar_desde']!r}")
                else:
                    a["texto"] = a["texto"][:corte].rstrip()
            if c.get("partir"):  # un tramo del texto pegado por error a este artículo pasa a otro
                corte = a["texto"].find(c["partir"]["desde"])
                b = next((x for x in articulos if x is not a and es(x, c["partir"]["a"])), None)
                if corte < 0 or b is None:
                    print(f"  corrección sin aplicar en nº {nid} pág. {c['pagina']}: no pude partir {c['partir']}")
                else:
                    tramo, a["texto"] = a["texto"][corte:], a["texto"][:corte].rstrip()
                    k = b["texto"].find(c["partir"]["despues_de"]) if c["partir"].get("despues_de") else -1
                    if k >= 0:  # el tramo va en medio del otro artículo, tras ese texto
                        k += len(c["partir"]["despues_de"])
                        resto = b["texto"][k:]
                        if resto.startswith(".") and tramo.lstrip()[:1].islower():
                            resto = resto[1:]
                        b["texto"] = b["texto"][:k] + " " + tramo.strip() + "\n\n" + resto.lstrip()
                    else:
                        b["texto"] = b["texto"].rstrip() + "\n\n" + tramo
                    b["paginas"] = sorted(set(b["paginas"]) | set(c["partir"].get("paginas", [])))
                    a["paginas"] = sorted(set(a["paginas"]) - set(c["partir"].get("quitar_paginas", [])))
                    b["corregido_a_mano"] = True
            for pieza in c.get("quitar", []):  # texto leído dos veces: la copia suelta se saca (el original queda)
                b = next((x for x in articulos if x is not a and x not in sacar and es(x, pieza)), None)
                if b is None:
                    print(f"  corrección sin aplicar en nº {nid} pág. {c['pagina']}: no encontré {pieza}")
                else:
                    sacar.append(b)
            for clave in ("unir", "destacados", "recuadros"):
                for pieza in c.get(clave, []):
                    b = next((x for x in articulos if x is not a and x not in sacar and es(x, pieza)), None)
                    if b is None:
                        print(f"  corrección sin aplicar en nº {nid} pág. {c['pagina']}: no encontré {pieza}")
                        continue
                    sacar.append(b)
                    if clave == "unir" and pieza.get("despues_de"):  # el pedazo va en medio, tras ese texto
                        k = a["texto"].find(pieza["despues_de"])
                        if k < 0:
                            print(f"  corrección sin aplicar en nº {nid}: no encontré {pieza['despues_de']!r}")
                            sacar.remove(b)
                            continue
                        k += len(pieza["despues_de"])
                        resto = a["texto"][k:]
                        if resto.startswith(".") and b["texto"].lstrip()[:1].islower():
                            resto = resto[1:]  # la IA había cerrado la frase que en realidad sigue
                        a["texto"] = a["texto"][:k] + " " + b["texto"].strip() + "\n\n" + resto.lstrip()
                        a["paginas"] = sorted(set(a["paginas"]) | set(b["paginas"]))
                    elif clave == "unir":
                        fin = a["texto"].rstrip()
                        if fin.endswith("-") and not pieza.get("antes"):  # palabra cortada: "porcenta-" + "je"
                            corte = re.findall(r"[^\W\d_]+-$", fin)
                            sigue = b["texto"].lstrip()
                            if corte and parecido(sigue[:len(corte[0]) - 1]) == parecido(corte[0][:-1]):
                                fin = fin[:-len(corte[0])]  # el pedazo repite la palabra entera ("só-" + "sólo")
                                a["texto"] = fin + sigue
                            else:
                                a["texto"] = fin[:-1] + sigue
                        else:
                            a["texto"] = fin + "\n\n" + (pieza["antes"] + "\n\n" if pieza.get("antes") else "") + b["texto"].lstrip()
                        a["paginas"] = sorted(set(a["paginas"]) | set(b["paginas"]))
                        a["continua_en_pagina"] = b.get("continua_en_pagina") or a.get("continua_en_pagina")
                        a["temas"] = list(dict.fromkeys(a.get("temas", []) + b.get("temas", [])))
                    else:
                        a.setdefault(clave, []).append({"texto": b["texto"], "autor": b.get("autor"), "nota": pieza.get("nota")})
            a.update(c.get("cambios", {}))
            for viejo, nuevo in c.get("reemplazos", []):
                if viejo in a["texto"]:
                    a["texto"] = a["texto"].replace(viejo, nuevo, 1)
                else:
                    print(f"  corrección sin aplicar en nº {nid} pág. {c['pagina']}: no encontré {viejo!r}")
            if c.get("automatica") and c.get("unir"):  # unión automática: se puede deshacer borrando la entrada
                a.setdefault("uniones_automaticas", []).append(c["unir"][0]["pagina"])
            elif c.get("automatica"):
                a["separada_por_juez"] = True
            else:
                a["corregido_a_mano"] = True
            break
    return [a for a in articulos if a not in sacar] + nuevas_separadas

PROPIOS = {"cuento", "poema", "aviso", "humor", "pie de foto", "carta"}  # tipos que solo siguen en uno igual


def compatibles(a, b):
    """Dos partes pueden ser el mismo texto: del mismo tipo, o ambas de tipo periodístico general."""
    return a.get("tipo") == b.get("tipo") or not ({a.get("tipo"), b.get("tipo")} & PROPIOS)


def sin_repetir(fin, inicio, maximo=80):
    """Saca del comienzo de `inicio` el tramo que repite el final de `fin` (la franja donde se superponen las dos
    mitades de la página). Devuelve `inicio` recortado."""
    pf, pi = fin.split(), inicio.split()
    for k in range(min(maximo, len(pf), len(pi)), 4, -1):
        if [w.strip(".,;:") for w in pf[-k:]] == [w.strip(".,;:") for w in pi[:k]]:
            return " ".join(pi[k:])
    return inicio


TRAMO_REPETIDO = 12  # palabras seguidas idénticas


def quitar_repetidos(articulos, ocr=""):
    """Saca los tramos de 12 o más palabras que la IA copió dos veces en una misma página (pasa sobre todo al
    leer en dos mitades: la de abajo vuelve a copiar párrafos de la de arriba). Queda la primera aparición. Un
    tramo que el OCR de Tesseract también tiene dos veces está impreso dos veces y no se toca.
    Cambia los textos en el lugar y devuelve cuántas palabras sacó."""
    norm = lambda w: re.sub(r"\W", "", w.lower())
    palabras_ocr = [x for x in map(norm, (ocr or "").split()) if x]
    veces_ocr = {}
    for k in range(len(palabras_ocr) - TRAMO_REPETIDO + 1):
        g = tuple(palabras_ocr[k:k + TRAMO_REPETIDO])
        veces_ocr[g] = veces_ocr.get(g, 0) + 1
    vistos, sacadas = set(), 0
    for a in articulos:
        partes = re.split(r"(\s+)", a["texto"])
        idx = [i for i, x in enumerate(partes) if x.strip()]
        nw = [norm(partes[i]) for i in idx]
        fuera, k = set(), 0
        while k <= len(idx) - TRAMO_REPETIDO:
            g = tuple(nw[k:k + TRAMO_REPETIDO])
            if all(g) and g in vistos and veces_ocr.get(g, 0) < 2:
                j = k + TRAMO_REPETIDO
                while j < len(idx) and tuple(nw[j - TRAMO_REPETIDO + 1:j + 1]) in vistos:
                    j += 1
                fuera.update(idx[t] for t in range(k, j))
                k = j
            else:
                if all(g):
                    vistos.add(g)
                k += 1
        if fuera:
            sacadas += len(fuera)
            salida = []
            for i, x in enumerate(partes):
                if i in fuera or (not x.strip() and salida and not salida[-1].strip()):
                    continue
                salida.append(x)
            a["texto"] = "".join(salida).strip()
    return sacadas


def unir_mitades(arriba, abajo):
    """Una página leída en dos pedidos (mitad superior e inferior) -> una sola lectura."""
    clave = lambda a: " ".join(a["titulo"].lower().split())
    articulos = [dict(a) for a in arriba["articulos"]]
    for b in abajo["articulos"]:
        par = next((a for a in articulos if clave(a) == clave(b)), None)
        if par is None and b.get("empieza_cortado"):
            cortados = [a for a in articulos if a.get("termina_cortado")]
            par = cortados[-1] if len(cortados) == 1 else None
        if par is None:
            articulos.append(dict(b))
            continue
        texto = sin_repetir(par["texto"], b["texto"])
        par["texto"] = par["texto"].rstrip() + ("" if par["texto"].rstrip().endswith("-") else "\n\n") + texto
        par["termina_cortado"] = b.get("termina_cortado", False)
        par["autor"] = par.get("autor") or b.get("autor")
    completa = bool(arriba.get("transcripcion_completa")) and bool(abajo.get("transcripcion_completa"))
    return {**arriba, "articulos": articulos, "ilustraciones": arriba["ilustraciones"] + abajo["ilustraciones"],
            "bloques_girados": arriba.get("bloques_girados", []) + abajo.get("bloques_girados", []),
            "transcripcion_completa": completa,
            "omisiones": " / ".join(x for x in (arriba.get("omisiones"), abajo.get("omisiones")) if x) or None,
            "notas": " / ".join(x for x in (arriba.get("notas"), abajo.get("notas")) if x) or None,
            "partes_sin_leer": arriba.get("partes_sin_leer") or abajo.get("partes_sin_leer"),
            "tokens_entrada": arriba["tokens_entrada"] + abajo["tokens_entrada"],
            "tokens_salida": arriba["tokens_salida"] + abajo["tokens_salida"], "dividida": True}


def juntar_partes(articulos):
    """Une las partes de un mismo artículo que la IA devolvió por separado en una página (mismo título).

    La parte que sigue en otra página (cortada al final o con "pasa a") va última; la que viene de otra
    página va primera; el resto conserva el orden en que las devolvió la IA.
    """
    grupos = {}
    for k, a in enumerate(articulos):
        # las citas destacadas llevan el título de su artículo, pero no son una parte de su texto
        clave = ("destacado", k) if a.get("tipo") == "destacado" else " ".join(a["titulo"].lower().split())
        grupos.setdefault(clave, []).append(a)
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


RAZON_EXTENSION = re.compile(r"extensi[oó]n|largo|densidad|denso|abreviad|resumid|parcial", re.I)


def siguiente_paso(pag, ocr):
    """Qué hacer después de la lectura de Haiku (etapa 2 en adelante):
    "mitad"  -> releerla con Haiku en dos pedidos (mitad superior e inferior): le falta texto, casi siempre
                porque la IA abrevia las páginas largas o densas;
    "sonnet" -> releerla con Sonnet: Haiku no pudo, o la página es muy poco legible;
    None     -> la lectura alcanza."""
    if pag is None:
        return "sonnet"
    if pag.get("bloqueada"):
        return None
    p_ia = sum(palabras(a["texto"]) for a in pag["articulos"])
    p_ocr = palabras(ocr)
    if p_ocr > 150 and p_ia < 0.6 * p_ocr:
        return "mitad"
    declara = pag.get("transcripcion_completa") is False and RAZON_EXTENSION.search(pag.get("omisiones") or "")
    if p_ocr > 150 and p_ia < 0.75 * p_ocr and declara:
        return "mitad"
    if pag.get("legibilidad") == "mala" and pag.get("partes_sin_leer"):
        return "sonnet"
    return None


def unir_continuaciones(paginas):
    """Une los artículos que siguen en otra página del número.

    Primero las marcas impresas ("pasa a la pág. X" / "viene de la pág. Y", con X e Y
    en numeración impresa). Después, sin marca, un artículo que termina cortado en una
    página (según la IA, o porque su texto termina en guion) con el único de la página
    siguiente que empieza cortado; si no hay ninguno así, con el único de la página
    siguiente que es del mismo tipo (cuento con cuento, crítica con crítica…).
    """
    impresa = {n: p.get("pagina_impresa") or n for n, p in paginas}
    enfrentada = {n: p["pagina_enfrentada"] for n, p in paginas if p.get("pagina_enfrentada")}
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

    # Modo pliego: la IA vio la página enfrentada y marcó qué artículos cruzan de la izquierda a la derecha
    for a in lista:
        n = a["paginas"][-1]
        if id(a) in usados or a.get("cruza_pliego") not in ("sigue_en_la_enfrentada", "ambas") or enfrentada.get(n) != n + 1:
            continue
        libres = [b for b in lista if id(b) not in usados and b is not a and b["paginas"][0] == n + 1
                  and b.get("tipo") not in ("aviso", "pie de foto")]
        candidatos = [b for b in libres if b.get("cruza_pliego") in ("viene_de_la_enfrentada", "ambas")]
        if not candidatos:
            # la IA marcó solo un lado: vale el único de la página enfrentada que empieza cortado, o que nombra al
            # mismo autor, o que comparte palabras del título
            apellido = (a.get("autor") or "").split()[-1:] or ["\0"]
            propias = {w for w in re.findall(r"\w{5,}", a["titulo"].lower())}
            candidatos = [b for b in libres if b.get("empieza_cortado")] or \
                         [b for b in libres if apellido[0].lower() in (b["titulo"] + " " + b["texto"][:300]).lower()] or \
                         [b for b in libres if len(propias & set(re.findall(r"\w{5,}", b["titulo"].lower()))) >= 2]
            candidatos = candidatos if len(candidatos) == 1 else []
        if candidatos:
            palabras_a = set(a["titulo"].lower().split())
            pegar(a, max(candidatos, key=lambda c: len(palabras_a & set(c["titulo"].lower().split()))))
            a["union_por_pliego"] = True

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

    def haiku_previo(n):
        archivo = f"{destino}/p{n:02d}.haiku.json"
        return json.load(open(archivo)) if os.path.exists(archivo) else None

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
            enderezados = []
            if modelo == "sonnet" and haiku_previo(n):
                for bloque in confirmar_bloques(haiku_previo(n), imagenes[n - 1]):
                    recorte, angulo = enderezar(imagenes[n - 1], bloque)
                    recorte.save(f"{destino}/p{n:02d}-enderezado-{len(enderezados) + 1}.jpg", quality=85)
                    enderezados.append(recorte)
            datos = leer(cli, modelo, imagenes[n - 1], meta, n, total, ocr[n])
            caja.sumar(modelo, datos["tokens_entrada"], datos["tokens_salida"])
            previa = haiku_previo(n) if modelo == "sonnet" else None
            if previa and not previa.get("bloqueada"):
                # una relectura con mucho menos texto que la de Haiku quedó trunca: se reintenta una vez
                minimo = 0.5 * sum(palabras(a["texto"]) for a in previa["articulos"])
                if sum(palabras(a["texto"]) for a in datos["articulos"]) < minimo and caja.alcanza(estimado):
                    print(f"  {meta['id']} sonnet pág. {n}: lectura trunca, reintento", flush=True)
                    datos = leer(cli, modelo, imagenes[n - 1], meta, n, total, ocr[n])
                    caja.sumar(modelo, datos["tokens_entrada"], datos["tokens_salida"])
                if sum(palabras(a["texto"]) for a in datos["articulos"]) < minimo:
                    print(f"  {meta['id']} sonnet pág. {n}: otra vez trunca, queda la lectura de Haiku", flush=True)
                    json.dump({**datos, "trunca": True}, open(f"{destino}/p{n:02d}.sonnet-trunca.json", "w"),
                              ensure_ascii=False, indent=1)
                    return None
            # después, cada bloque girado se lee aparte, ya enderezado, y se ubica en su artículo
            for recorte in enderezados:
                texto_pagina = "\n\n".join(a["texto"] for a in datos["articulos"])
                bloque, ent, sal = leer_bloque(cli, modelo, recorte, meta, texto_pagina)
                caja.sumar(modelo, ent, sal)
                datos["tokens_entrada"] += ent
                datos["tokens_salida"] += sal
                bloque["ubicacion"] = insertar_bloque(datos["articulos"], bloque)
                datos.setdefault("bloques", []).append(bloque)
            datos["bloques_enderezados"] = len(enderezados)
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
    articulos = adjuntar_destacados(aplicar_correcciones(meta["id"], unir_continuaciones(paginas)))
    for a in articulos:
        a["texto"], quitados = sin_comentarios_ia(a["texto"])
        if quitados:
            a["comentarios_ia_quitados"] = quitados
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
