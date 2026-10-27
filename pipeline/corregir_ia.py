"""Segunda pasada: lectura de las páginas con un modelo de visión de Claude.

Uso: python3 corregir_ia.py MODELO ENTRADA.pdf [ENTRADA2.pdf ...] --salida CARPETA [--tesseract CARPETA_OCR]

MODELO es `haiku` o `sonnet`. Para cada página el modelo recibe la imagen (en dos
mitades, para que la letra chica se lea bien) y, si existe, el texto de Tesseract
como ayuda. Devuelve los artículos de la página en orden de lectura, con título,
autor, sección, temas y las marcas "Pasa a pág." / "Viene de pág.". Después se
unen las partes de un mismo artículo que siguen en otra página del número.

La clave de API se lee de ~/.marcha-clave-anthropic (o de ANTHROPIC_API_KEY).

Salida por número: CARPETA/<id>/<modelo>/pNN.json, articulos.json y costo.json.
"""
import argparse, base64, glob, io, json, os, subprocess, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

import anthropic
from PIL import Image
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ocr_numero import metadatos

MODELOS = {
    # id del modelo, precio por millón de tokens (entrada, salida), sin descuento por lotes
    "haiku": ("claude-haiku-5-5", 0.10, 0.50),
    "sonnet": ("claude-sonnet-5-5", 2.00, 10.00),
}
LADO_MAXIMO = 1568  # cada mitad de página se manda a este tamaño como máximo


class Articulo(BaseModel):
    titulo: str
    autor: Optional[str]
    seccion: Optional[str]
    temas: List[str]
    texto: str
    viene_de_pagina: Optional[int]
    continua_en_pagina: Optional[int]


class Pagina(BaseModel):
    articulos: List[Articulo]
    notas: Optional[str]


INSTRUCCIONES = """Sos un transcriptor experto en prensa uruguaya del siglo XX. Te paso una página del semanario Marcha (Montevideo), número {numero}, del {fecha}, página {pagina} de {total}. La imagen viene en dos partes: la mitad de arriba y la mitad de abajo de la misma página, que se superponen un poco.

Transcribí todos los artículos de la página en orden de lectura, siguiendo las columnas como lo haría una persona:
- Uní las palabras cortadas con guion al final del renglón ("re-\\nnuncia" -> "renuncia") y los renglones de un mismo párrafo. Separá los párrafos con una línea en blanco.
- Respetá la ortografía y el estilo originales. No resumas ni modernices.
- Si una palabra no se puede leer, escribí [ilegible] en su lugar. No inventes texto.
- No transcribas dos veces lo que aparece en la zona donde se superponen las dos mitades.
- Si el artículo dice "(Pasa a la pág. X)", "(Continúa en la pág. X)" o similar, poné X en continua_en_pagina. Si dice "(Viene de la pág. Y)", poné Y en viene_de_pagina.
- Incluí avisos publicitarios breves solo si tienen texto propio; en ese caso usá la sección "Aviso".
- autor: la firma, si la hay. seccion: el nombre de la sección o columna si aparece (por ejemplo "Cartas de los lectores"); si no, null.
- temas: dos o tres temas generales en español (por ejemplo "política argentina", "teatro", "economía").
- notas: cualquier problema de lectura de la página (manchas, partes cortadas), o null.

Como ayuda, este es el texto que sacó un OCR automático de la misma página. Tiene muchos errores y mezcla columnas; usalo solo para confirmar palabras dudosas:
<ocr>
{ocr}
</ocr>"""


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


def leer_pagina(cli, modelo, im, meta, n, total, ocr):
    w, h = im.size
    arriba, abajo = im.crop((0, 0, w, int(h * 0.55))), im.crop((0, int(h * 0.45), w, h))
    contenido = [
        {"type": "text", "text": "Mitad de arriba:"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": imagen_b64(arriba)}},
        {"type": "text", "text": "Mitad de abajo:"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": imagen_b64(abajo)}},
        {"type": "text", "text": INSTRUCCIONES.format(numero=meta["id"], fecha=meta["fecha"], pagina=n,
                                                      total=total, ocr=ocr or "(no hay)")},
    ]
    r = cli.messages.parse(
        model=MODELOS[modelo][0],
        max_tokens=16000,
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": contenido}],
        output_format=Pagina,
    )
    if r.stop_reason in ("refusal", "max_tokens") or r.parsed_output is None:
        raise RuntimeError(f"página {n}: el modelo terminó con stop_reason={r.stop_reason}")
    return r.parsed_output, r.usage.input_tokens, r.usage.output_tokens


def unir_continuaciones(paginas):
    """Une cada artículo que "pasa a la pág. X" con el que en X "viene de" su página."""
    lista = [dict(a, paginas=[n]) for n, p in paginas for a in p["articulos"]]
    usados = set()
    for a in lista:
        destino = a.get("continua_en_pagina")
        if not destino:
            continue
        candidatos = [b for b in lista if id(b) not in usados and b["paginas"] == [destino]
                      and b.get("viene_de_pagina") == a["paginas"][0]]
        if not candidatos:
            continue
        # entre varios, el de título más parecido
        palabras = set(a["titulo"].lower().split())
        b = max(candidatos, key=lambda c: len(palabras & set(c["titulo"].lower().split())))
        a["texto"] += "\n\n" + b["texto"]
        a["paginas"].append(destino)
        a["continua_en_pagina"] = b.get("continua_en_pagina")
        usados.add(id(b))
    return [a for a in lista if id(a) not in usados]


def procesar(cli, modelo, pdf, salida, carpeta_ocr):
    meta = metadatos(pdf)
    destino = os.path.join(salida, meta["id"], modelo)
    os.makedirs(destino, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        imagenes = list(paginas_de(pdf, tmp))
    total = len(imagenes)

    def una(args):
        n, im = args
        archivo = f"{destino}/p{n:02d}.json"
        if os.path.exists(archivo):
            return json.load(open(archivo))
        ocr_txt = os.path.join(carpeta_ocr or "", meta["id"], f"p{n:02d}.txt")
        ocr = open(ocr_txt, encoding="utf-8").read() if carpeta_ocr and os.path.exists(ocr_txt) else ""
        t0 = time.time()
        pagina, ent, sal = leer_pagina(cli, modelo, im, meta, n, total, ocr)
        datos = {**pagina.model_dump(), "tokens_entrada": ent, "tokens_salida": sal,
                 "segundos": round(time.time() - t0)}
        json.dump(datos, open(archivo, "w"), ensure_ascii=False, indent=1)
        print(f"  {meta['id']} {modelo} pág. {n}/{total}: {len(pagina.articulos)} artículos, "
              f"{ent}+{sal} tokens", flush=True)
        return datos

    with ThreadPoolExecutor(4) as ex:
        resultados = list(ex.map(una, enumerate(imagenes, 1)))

    articulos = unir_continuaciones(list(enumerate(resultados, 1)))
    json.dump({**meta, "modelo": MODELOS[modelo][0], "articulos": articulos},
              open(f"{destino}/articulos.json", "w"), ensure_ascii=False, indent=1)
    ent = sum(r["tokens_entrada"] for r in resultados)
    sal = sum(r["tokens_salida"] for r in resultados)
    _, p_ent, p_sal = MODELOS[modelo]
    costo = (ent * p_ent + sal * p_sal) / 1e6
    resumen = {"paginas": total, "tokens_entrada": ent, "tokens_salida": sal,
               "costo_usd": round(costo, 4), "costo_por_pagina_usd": round(costo / total, 5),
               "costo_por_pagina_por_lotes_usd": round(costo / total / 2, 5)}
    json.dump(resumen, open(f"{destino}/costo.json", "w"), indent=1)
    print(f"{meta['id']} {modelo}: {len(articulos)} artículos, US$ {costo:.2f}", flush=True)
    return resumen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("modelo", choices=MODELOS)
    ap.add_argument("pdfs", nargs="+")
    ap.add_argument("--salida", required=True)
    ap.add_argument("--tesseract", help="carpeta de salida de procesar_coleccion.py")
    a = ap.parse_args()
    cli = cliente()
    for pdf in a.pdfs:
        try:
            procesar(cli, a.modelo, pdf, a.salida, a.tesseract)
        except anthropic.AuthenticationError:
            sys.exit("La clave de API no es válida.")
        except anthropic.PermissionDeniedError as e:
            sys.exit(f"La cuenta no tiene permiso o no tiene crédito: {e.message}")
        except anthropic.BadRequestError as e:
            if "credit" in str(e.message).lower():
                sys.exit(f"Se terminó el crédito de la cuenta: {e.message}")
            print(f"Error en {pdf}: {e.message}", file=sys.stderr)
        except (anthropic.APIStatusError, anthropic.APIConnectionError, RuntimeError) as e:
            print(f"Error en {pdf}: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
