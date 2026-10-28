"""Lectura con IA de muchos números a la vez, con la API de lotes (mitad de precio).

Uso:
  python3 lotes_ia.py elegir CARPETA_ROLLOS CARPETA_LECTURA CANTIDAD
      Elige CANTIDAD números repartidos parejo entre 1939 y 1974 (no seguidos), para que
      aparezcan pronto los distintos estilos de impresión. Los agrega a CARPETA_LECTURA/etapa.json.
  python3 lotes_ia.py seguir CARPETA_ROLLOS CARPETA_OCR CARPETA_LECTURA [--tope DOLARES]
      Avanza lo que se pueda y termina: manda los lotes que falten, recoge los que ya
      terminaron, decide qué páginas pasan a Sonnet y arma la versión final de cada número.
      Se puede correr las veces que haga falta; nunca repite una página ya leída.
  python3 lotes_ia.py estado CARPETA_LECTURA

Todo el estado queda en CARPETA_LECTURA: etapa.json (números elegidos), lotes.json (lotes
mandados y su estado), gasto.json (gasto acumulado, que no puede pasar el tope) y, por número,
las mismas lecturas que deja leer_ia.py (pNN.haiku.json, pNN.sonnet.json, pNN.json, articulos.json).
"""
import argparse, glob, json, os, sys, tempfile, time
from datetime import datetime

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from leer_ia import (INSTRUCCIONES_BLOQUE, MODELOS, Bloque, Pagina, cliente, contenido_pagina, dudas, enderezar,
                     finalizar, imagen_b64, insertar_bloque, palabras, paginas_de)
from ocr_numero import metadatos

ESQUEMA = anthropic.transform_schema(Pagina)
ESQUEMA_BLOQUE = anthropic.transform_schema(Bloque)
PAGINAS_POR_LOTE = 80   # cada página pesa ~2 MB en el pedido (imágenes en base64); un lote admite hasta 256 MB
COSTO_ESTIMADO = {"haiku": 0.0020, "sonnet": 0.040, "bloque": 0.025}  # por pedido, con descuento de lotes y margen


def cargar(ruta, vacio):
    return json.load(open(ruta)) if os.path.exists(ruta) else vacio


def guardar(ruta, datos):
    tmp = ruta + ".tmp"
    json.dump(datos, open(tmp, "w"), ensure_ascii=False, indent=1)
    os.replace(tmp, ruta)  # nunca queda un archivo a medio escribir si la Mac se duerme


def elegir(rollos, lectura, cantidad):
    os.makedirs(lectura, exist_ok=True)
    etapa = cargar(os.path.join(lectura, "etapa.json"), {"numeros": []})
    ya = {x["id"] for x in etapa["numeros"]}
    pdfs = []
    for pdf in glob.glob(os.path.join(rollos, "**", "*.pdf"), recursive=True):
        m = metadatos(pdf)
        if m["id"] not in ya:
            pdfs.append((m["fecha"], m["id"], os.path.relpath(pdf, rollos)))
    pdfs.sort()
    paso = len(pdfs) / cantidad
    elegidos = [pdfs[int(i * paso + paso / 2)] for i in range(cantidad)]
    etapa["numeros"] += [{"id": i, "fecha": f, "archivo": a} for f, i, a in elegidos]
    guardar(os.path.join(lectura, "etapa.json"), etapa)
    print(f"{len(elegidos)} números agregados ({elegidos[0][0]} a {elegidos[-1][0]}); total {len(etapa['numeros'])}")


class Numero:
    """Un número del semanario con sus imágenes de página (se extraen del PDF solo si hacen falta)."""

    def __init__(self, info, rollos, ocr, lectura):
        self.id, self.pdf = info["id"], os.path.join(rollos, info["archivo"])
        self.meta = {**metadatos(self.pdf), "rollo": os.path.dirname(info["archivo"]), "archivo": info["archivo"]}
        self.destino = os.path.join(lectura, self.id)
        os.makedirs(self.destino, exist_ok=True)
        self.total = json.load(open(os.path.join(ocr, self.id, "numero.json")))["paginas"]
        self.ocr = {}
        for n in range(1, self.total + 1):
            f = os.path.join(ocr, self.id, f"p{n:02d}.txt")
            self.ocr[n] = open(f, encoding="utf-8").read() if os.path.exists(f) else ""
        self._imagenes = None

    def imagenes(self):
        if self._imagenes is None:
            with tempfile.TemporaryDirectory() as tmp:
                self._imagenes = list(paginas_de(self.pdf, tmp))
            if len(self._imagenes) != self.total:
                raise RuntimeError(f"nº {self.id}: el PDF tiene {len(self._imagenes)} páginas y el OCR {self.total}")
        return self._imagenes

    def lectura(self, n, modelo):
        f = f"{self.destino}/p{n:02d}.{modelo}.json"
        return json.load(open(f)) if os.path.exists(f) else None


def pedido(numero, n, modelo):
    """El pedido de una página entera para el lote (Haiku o Sonnet)."""
    contenido = contenido_pagina(numero.imagenes()[n - 1], numero.meta, n, numero.total, numero.ocr[n])
    return Request(custom_id=f"{numero.id}__{n}__{modelo}", params=MessageCreateParamsNonStreaming(
        model=MODELOS[modelo][0], max_tokens=16000, messages=[{"role": "user", "content": contenido}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": ESQUEMA}}))


def pedidos_bloques(numero, n):
    """Un pedido por cada bloque girado de la página: el bloque enderezado y el texto que ya leyó Sonnet."""
    leida = numero.lectura(n, "sonnet")
    texto = "\n\n".join(a["texto"] for a in leida["articulos"])
    pedidos = []
    for k, bloque in enumerate(numero.lectura(n, "haiku")["bloques_girados"], 1):
        recorte, _ = enderezar(numero.imagenes()[n - 1], bloque)
        recorte.save(f"{numero.destino}/p{n:02d}-enderezado-{k}.jpg", quality=85)
        contenido = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": imagen_b64(recorte)}},
                     {"type": "text", "text": INSTRUCCIONES_BLOQUE.format(fecha=numero.meta["fecha"], texto=texto)}]
        pedidos.append(Request(custom_id=f"{numero.id}__{n}__bloque{k}", params=MessageCreateParamsNonStreaming(
            model=MODELOS["sonnet"][0], max_tokens=8000, messages=[{"role": "user", "content": contenido}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": ESQUEMA_BLOQUE}})))
    return pedidos


def costo(modelo, entrada, salida):
    _, p_ent, p_sal = MODELOS["sonnet" if modelo.startswith("bloque") else modelo]
    return (entrada * p_ent + salida * p_sal) / 1e6 / 2  # la API de lotes cobra la mitad


def seguir(rollos, ocr, lectura, tope):
    cli = cliente()
    etapa = cargar(os.path.join(lectura, "etapa.json"), {"numeros": []})
    lotes = cargar(os.path.join(lectura, "lotes.json"), [])
    gasto = cargar(os.path.join(lectura, "gasto.json"), {"usd": 0.0, "detalle": []})
    fallas = cargar(os.path.join(lectura, "fallas.json"), {})  # "id__n__modelo" -> veces que falló
    numeros = {x["id"]: Numero(x, rollos, ocr, lectura) for x in etapa["numeros"]}

    # 1. Recoger los lotes que terminaron
    for lote in lotes:
        if lote["estado"] == "recogido":
            continue
        b = cli.messages.batches.retrieve(lote["id"])
        lote["estado_api"] = b.processing_status
        if b.processing_status != "ended":
            print(f"lote {lote['id']} ({lote['modelo']}, {lote['paginas']} págs.): {b.processing_status}, "
                  f"{b.request_counts.succeeded} listas, {b.request_counts.processing} en proceso")
            continue
        gastado, problemas = 0.0, []
        for r in cli.messages.batches.results(lote["id"]):
            nid, n, modelo = r.custom_id.split("__")
            n, num = int(n), numeros[nid]
            archivo = f"{num.destino}/p{n:02d}.{modelo}.json"
            if r.result.type != "succeeded":
                error = getattr(getattr(r.result, "error", None), "error", None)
                mensaje = str(getattr(error, "message", "") or r.result.type)
                if "content filtering" in mensaje:  # derechos de autor: queda el texto de Tesseract
                    guardar(archivo, {"bloqueada": True, "modelo": modelo, "error": mensaje})
                else:
                    fallas[r.custom_id] = fallas.get(r.custom_id, 0) + 1
                problemas.append(f"{nid} pág. {n} {modelo}: {mensaje[:120]}")
                continue
            msg = r.result.message
            gastado += costo(modelo, msg.usage.input_tokens, msg.usage.output_tokens)
            texto = next((c.text for c in msg.content if c.type == "text"), "")
            if modelo.startswith("bloque"):
                try:
                    bloque = Bloque.model_validate_json(texto).model_dump()
                except Exception as e:
                    fallas[r.custom_id] = fallas.get(r.custom_id, 0) + 1
                    problemas.append(f"{nid} pág. {n} {modelo}: respuesta inválida {str(e)[:80]}")
                    continue
                leida = num.lectura(n, "sonnet")
                bloque["ubicado"] = insertar_bloque(leida["articulos"], bloque)
                leida.setdefault("bloques", []).append(bloque)
                leida["tokens_entrada"] += msg.usage.input_tokens
                leida["tokens_salida"] += msg.usage.output_tokens
                if not bloque["ubicado"]:
                    problemas.append(f"{nid} pág. {n}: bloque enderezado sin ubicar, se puso al comienzo (revisar)")
                guardar(f"{num.destino}/p{n:02d}.sonnet.json", leida)
                continue
            try:
                datos = Pagina.model_validate_json(texto).model_dump()
            except Exception as e:
                fallas[r.custom_id] = fallas.get(r.custom_id, 0) + 1
                problemas.append(f"{nid} pág. {n} {modelo}: respuesta inválida ({msg.stop_reason}) {str(e)[:80]}")
                continue
            datos.update(modelo=modelo, tokens_entrada=msg.usage.input_tokens, tokens_salida=msg.usage.output_tokens,
                         lote=lote["id"])
            if modelo == "sonnet":
                previa = num.lectura(n, "haiku") or {}
                minimo = 0.5 * sum(palabras(a["texto"]) for a in previa.get("articulos", []))
                if sum(palabras(a["texto"]) for a in datos["articulos"]) < minimo:
                    guardar(f"{num.destino}/p{n:02d}.sonnet-trunca.json", datos)
                    problemas.append(f"{nid} pág. {n} sonnet: lectura trunca, queda la de Haiku")
                    continue
            guardar(archivo, datos)
        gasto["usd"] += gastado
        gasto["detalle"].append({"lote": lote["id"], "modelo": lote["modelo"], "usd": round(gastado, 4)})
        lote.update(estado="recogido", usd=round(gastado, 4), problemas=problemas)
        guardar(os.path.join(lectura, "fallas.json"), fallas)
        guardar(os.path.join(lectura, "gasto.json"), gasto)
        guardar(os.path.join(lectura, "lotes.json"), lotes)
        print(f"lote {lote['id']} recogido: US$ {gastado:.2f}, {len(problemas)} problemas")
        for p in problemas:
            print("   ", p)

    en_curso = {(r.split("__")[0], int(r.split("__")[1]), r.split("__")[2])
                for l in lotes if l["estado"] != "recogido" for r in l["pedidos"]}

    # 2. Qué falta leer: primero Haiku en todas las páginas; después Sonnet en las dudosas
    # (una página que falló dos veces con Haiku pasa a Sonnet; dos veces con Sonnet, queda lo que haya)
    falla = lambda num, n, m: fallas.get(f"{num.id}__{n}__{m}", 0) >= 2
    faltan = {"haiku": [], "sonnet": [], "bloque": []}
    for num in numeros.values():
        for n in range(1, num.total + 1):
            h = num.lectura(n, "haiku")
            if h is None and not falla(num, n, "haiku"):
                if (num.id, n, "haiku") not in en_curso and not os.path.exists(f"{num.destino}/p{n:02d}.json"):
                    faltan["haiku"].append((num, n))
                continue
            if (num.lectura(n, "sonnet") is None and (num.id, n, "sonnet") not in en_curso and not falla(num, n, "sonnet")
                    and not os.path.exists(f"{num.destino}/p{n:02d}.sonnet-trunca.json") and dudas(h, num.ocr[n])):
                faltan["sonnet"].append((num, n))
    # Bloques girados: cuando Sonnet ya leyó la página y todavía no se leyeron aparte
    for num in numeros.values():
        for n in range(1, num.total + 1):
            h, son = num.lectura(n, "haiku"), num.lectura(n, "sonnet")
            if (h and son and not son.get("bloqueada") and h.get("bloques_girados") and "bloques" not in son
                    and not any(i == num.id and p == n and m.startswith("bloque") for i, p, m in en_curso)
                    and not falla(num, n, "bloque1")):
                faltan["bloque"].append((num, n))
    # Sonnet espera a que Haiku haya terminado con el número, para decidir con toda la información
    pendientes_haiku = {num.id for num, _ in faltan["haiku"]} | {i for i, _, m in en_curso if m == "haiku"}
    faltan["sonnet"] = [(num, n) for num, n in faltan["sonnet"] if num.id not in pendientes_haiku]

    # 3. Mandar lotes nuevos, sin pasar el tope
    comprometido = gasto["usd"] + sum(COSTO_ESTIMADO[l["modelo"]] * l["paginas"] for l in lotes if l["estado"] != "recogido")
    for modelo in ("haiku", "sonnet", "bloque"):
        cola = faltan[modelo]
        for i in range(0, len(cola), PAGINAS_POR_LOTE):
            parte = cola[i:i + PAGINAS_POR_LOTE]
            estimado = COSTO_ESTIMADO[modelo] * len(parte) * (2 if modelo == "bloque" else 1)
            if comprometido + estimado > tope:
                print(f"Tope de US$ {tope:.2f}: no mando {len(cola) - i} páginas de {modelo} "
                      f"(gastado US$ {gasto['usd']:.2f}, comprometido US$ {comprometido:.2f}).")
                break
            if modelo == "bloque":
                pedidos = [p for num, n in parte for p in pedidos_bloques(num, n)]
            else:
                pedidos = [pedido(num, n, modelo) for num, n in parte]
            try:
                b = cli.messages.batches.create(requests=pedidos)
            except (anthropic.BadRequestError, anthropic.PermissionDeniedError) as e:
                sys.exit(f"La API rechazó el lote ({e.message}). Si es por crédito, hay que cargar más antes de seguir.")
            lotes.append({"id": b.id, "modelo": modelo, "paginas": len(parte), "estado": "enviado",
                          "enviado": datetime.now().isoformat(timespec="seconds"),
                          "pedidos": [p["custom_id"] for p in pedidos]})
            guardar(os.path.join(lectura, "lotes.json"), lotes)
            comprometido += estimado
            print(f"lote {b.id} enviado: {len(parte)} páginas con {modelo} (estimado US$ {estimado:.2f})")
        for num in {num.id: num for num, _ in cola}.values():
            num._imagenes = None  # liberar memoria

    # 4. Versión final de los números que ya no esperan nada
    listos = 0
    en_curso = {(r.split("__")[0]) for l in lotes if l["estado"] != "recogido" for r in l["pedidos"]}
    for num in numeros.values():
        if num.id in en_curso or any(n.id == num.id for lista in faltan.values() for n, _ in lista):
            continue
        if os.path.exists(f"{num.destino}/articulos.json"):
            listos += 1
            continue
        lecturas = {}
        for n in range(1, num.total + 1):
            s, h = num.lectura(n, "sonnet"), num.lectura(n, "haiku")
            lecturas[n] = s if s and not s.get("bloqueada") else h
        finalizar(num.meta, num.destino, num.total, num.ocr, lecturas)
        listos += 1
    print(f"{listos} de {len(numeros)} números terminados; gastado US$ {gasto['usd']:.2f} de US$ {tope:.2f}")


def estado(lectura):
    lotes = cargar(os.path.join(lectura, "lotes.json"), [])
    gasto = cargar(os.path.join(lectura, "gasto.json"), {"usd": 0.0})
    for l in lotes:
        print(f"{l['id']}  {l['modelo']:6} {l['paginas']:4} págs.  {l['estado']:9} {l.get('estado_api', '')}"
              f"  US$ {l.get('usd', 0):.2f}")
    print(f"gastado US$ {gasto['usd']:.2f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("accion", choices=["elegir", "seguir", "estado"])
    ap.add_argument("carpetas", nargs="+")
    ap.add_argument("--tope", type=float, default=100.0)
    a = ap.parse_args()
    if a.accion == "elegir":
        elegir(a.carpetas[0], a.carpetas[1], int(a.carpetas[2]))
    elif a.accion == "seguir":
        seguir(*a.carpetas[:3], a.tope)
    else:
        estado(a.carpetas[0])
