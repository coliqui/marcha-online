"""Lectura con IA de muchos números a la vez, con la API de lotes (mitad de precio).

Uso:
  python3 lotes_ia.py elegir CARPETA_ROLLOS CARPETA_LECTURA CANTIDAD
      Elige CANTIDAD números repartidos parejo entre 1939 y 1974 (no seguidos), para que
      aparezcan pronto los distintos estilos de impresión. Los agrega a CARPETA_LECTURA/etapa.json.
  python3 lotes_ia.py seguir CARPETA_ROLLOS CARPETA_OCR CARPETA_LECTURA [--tope DOLARES]
      Avanza lo que se pueda y termina: manda los lotes que falten, recoge los que ya
      terminaron, decide los pasos siguientes de cada página y arma la versión final de cada
      número. Se puede correr las veces que haga falta; nunca repite una página ya leída.

Pasos por página (etapa 2): Haiku la lee entera, con la página enfrentada como contexto y
enderezada si está impresa de costado; si le falta texto, Haiku la relee en dos mitades
("mitad"); si es muy poco legible o Haiku falló, Sonnet ("sonnet"); los bloques de texto en
diagonal confirmados se leen enderezados ("bloque").
  python3 lotes_ia.py estado CARPETA_LECTURA

Todo el estado queda en CARPETA_LECTURA: etapa.json (números elegidos), lotes.json (lotes
mandados y su estado), gasto.json (gasto acumulado, que no puede pasar el tope) y, por número,
las mismas lecturas que deja leer_ia.py (pNN.haiku.json, pNN.sonnet.json, pNN.json, articulos.json).
"""
import argparse, glob, json, os, re, sys, tempfile, threading, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from leer_ia import (INSTRUCCIONES_BLOQUE, MODELOS, Bloque, PaginaCompleta, cliente, confirmar_bloques,
                     contenido_pagina, enderezar, finalizar, imagen_b64, insertar_bloque, orientacion, palabras,
                     paginas_de, pliegos, siguiente_paso, unir_mitades, quitar_repetidos)
from ocr_numero import metadatos

ESQUEMA = anthropic.transform_schema(PaginaCompleta)  # desde la etapa 2: declara si la transcripción está completa
ESQUEMA_BLOQUE = anthropic.transform_schema(Bloque)
PEDIDOS_POR_LOTE = 60   # cada pedido pesa ~3 MB (tres imágenes en base64); un lote admite hasta 256 MB
# por página, con descuento de lotes y algo de margen ("mitad" son dos pedidos; "bloque", uno por bloque)
COSTO_ESTIMADO = {"haiku": 0.0030, "mitad": 0.0060, "sonnet": 0.040, "bloque": 0.025}


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
        self._candado = threading.RLock()  # las imágenes se pueden preparar en otro hilo (precarga del lote siguiente)
        self.pliegos = pliegos(self.pdf)  # página -> página enfrentada en el escaneo de a dos

    def imagenes(self):
        with self._candado:
            return self._imagenes_()

    def _imagenes_(self):
        if self._imagenes is None:
            with tempfile.TemporaryDirectory() as tmp:
                self._imagenes = list(paginas_de(self.pdf, tmp))
            if len(self._imagenes) != self.total:
                raise RuntimeError(f"nº {self.id}: el PDF tiene {len(self._imagenes)} páginas y el OCR {self.total}")
        return self._imagenes

    def lectura(self, n, modelo):
        f = f"{self.destino}/p{n:02d}.{modelo}.json"
        return json.load(open(f)) if os.path.exists(f) else None

    def giro(self, n):
        """Grados para enderezar la página si está impresa de costado (Tesseract); se mide una sola vez."""
        with self._candado:
            return self._giro_(n)

    def _giro_(self, n):
        archivo = f"{self.destino}/giros.json"
        giros = cargar(archivo, {})
        if str(n) not in giros:
            # todas las páginas del número de una vez, en paralelo (Tesseract tarda un segundo por página)
            faltan = [k for k in range(1, self.total + 1) if str(k) not in giros]
            imgs = self.imagenes()  # extraer las páginas antes de repartir el trabajo (los hilos no piden el candado)
            with ThreadPoolExecutor(10) as ex:
                for k, g in zip(faltan, ex.map(lambda k: orientacion(imgs[k - 1]), faltan)):
                    giros[str(k)] = g
            guardar(archivo, giros)
        return giros[str(n)]


def pedido(numero, n, modelo, mitad=None):
    """El pedido de una página para el lote (Haiku o Sonnet; entera o una mitad)."""
    m = numero.pliegos.get(n)  # modo pliego: la página enfrentada va como contexto
    contenido = contenido_pagina(numero.imagenes()[n - 1], numero.meta, n, numero.total, numero.ocr[n], completa=True,
                                 enfrentada=(numero.imagenes()[m - 1], m) if m else None, mitad=mitad,
                                 giro=numero.giro(n))
    sufijo = {None: "", "superior": "-sup", "inferior": "-inf"}[mitad]
    return Request(custom_id=f"{numero.id}__{n}__{modelo}{sufijo}", params=MessageCreateParamsNonStreaming(
        model=MODELOS[modelo][0], max_tokens=32000, messages=[{"role": "user", "content": contenido}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": ESQUEMA}}))


def pedidos_bloques(numero, n):
    """Un pedido por cada bloque en diagonal confirmado: el bloque enderezado y el texto ya leído de la página."""
    leida = elegir_lectura(numero, n, con_bloques=False) or {"articulos": []}
    texto = "\n\n".join(a["texto"] for a in leida["articulos"])
    pedidos = []
    for k, bloque in enumerate(numero.lectura(n, "haiku")["bloques_confirmados"], 1):
        recorte, _ = enderezar(numero.imagenes()[n - 1], bloque)
        recorte.save(f"{numero.destino}/p{n:02d}-enderezado-{k}.jpg", quality=85)
        contenido = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": imagen_b64(recorte)}},
                     {"type": "text", "text": INSTRUCCIONES_BLOQUE.format(fecha=numero.meta["fecha"], texto=texto)}]
        pedidos.append(Request(custom_id=f"{numero.id}__{n}__bloque{k}", params=MessageCreateParamsNonStreaming(
            model=MODELOS["sonnet"][0], max_tokens=8000, messages=[{"role": "user", "content": contenido}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": ESQUEMA_BLOQUE}})))
    return pedidos


# Solo cuenta lo que la lectura misma declara abreviado o parcial; omitir un aviso o un crucigrama no.
ABREVIADA = re.compile(r"abreviad|resumid|transcripci[oó]n (?:parcial|incompleta|no completa)|no (?:est[aá] )?complet[ao]", re.I)


def incompleta(lectura, ocr):
    """La lectura declara (en su campo o en sus notas) que dejó texto afuera, y además le falta texto frente
    al OCR (menos del 75 % de sus palabras): omitir un aviso o un adorno no la hace incompleta."""
    declara = lectura.get("transcripcion_completa") is False or bool(ABREVIADA.search(lectura.get("notas") or ""))
    return declara and sum(palabras(a["texto"]) for a in lectura["articulos"]) < 0.75 * palabras(ocr)


def elegir_lectura(num, n, con_bloques=True):
    """La mejor lectura de una página: la primera completa entre Sonnet, Haiku en dos mitades y Haiku entera; si
    ninguna está completa, la más larga, marcada "incompleta" y con el texto de Tesseract al lado para que siga
    siendo buscable. Si la versión final de una ronda anterior (carpeta _ronda1) tenía bastante más texto, queda
    esa. Después se le agregan los bloques en diagonal leídos enderezados."""
    x = _elegir(num, n)
    # versiones finales de rondas anteriores (etapa 1 en _ronda1, etapa 2 en _etapa2 si la página se releyó):
    # si alguna tenía bastante más texto, queda esa
    for carpeta in ("_ronda1", "_etapa2"):
        anterior = sin_duplicados(cargar(f"{num.destino}/{carpeta}/p{n:02d}.json", None), num.ocr[n])
        if x and anterior and not anterior.get("bloqueada") and "articulos" in anterior and not x.get("bloqueada"):
            nuevas = sum(palabras(a["texto"]) for a in x.get("articulos", []))
            viejas = sum(palabras(a["texto"]) for a in anterior["articulos"])
            if viejas >= 1.3 * nuevas and viejas - nuevas >= 100:
                x = {**anterior, "de_ronda_anterior": True}
    bloques = cargar(f"{num.destino}/p{n:02d}.bloques.json", [])
    if x and con_bloques and bloques and not x.get("bloqueada"):
        x = json.loads(json.dumps(x))  # copia: la lectura guardada no se toca
        for b in bloques:
            b["ubicacion"] = insertar_bloque(x["articulos"], b)
        x["bloques"] = bloques
    return x


def sin_duplicados(lectura, ocr):
    """Copia de la lectura sin los párrafos que la IA copió dos veces (la lectura guardada no se toca: queda de respaldo)."""
    if not lectura or lectura.get("bloqueada") or "articulos" not in lectura:
        return lectura
    x = json.loads(json.dumps(lectura))
    sacadas = quitar_repetidos(x["articulos"], ocr)
    if sacadas:
        x["repetidos_quitados"] = sacadas
    return x


def _elegir(num, n):
    lecturas = [sin_duplicados(x, num.ocr[n]) for x in
                (num.lectura(n, "sonnet"), num.lectura(n, "haiku-dividida"), num.lectura(n, "haiku")) if x]
    validas = [x for x in lecturas if not x.get("bloqueada")]
    if lecturas and not validas:
        return {"bloqueada": True}  # derechos de autor: finalizar() deja el texto de Tesseract
    for x in validas:
        if not incompleta(x, num.ocr[n]):
            return x
    # si ninguna está completa, también cuentan las lecturas de rondas anteriores (carpeta _etapa1) y las
    # relecturas descartadas por truncas: a veces una vieja tiene más texto que la nueva
    for f in (glob.glob(f"{num.destino}/_*/p{n:02d}.*.json") + glob.glob(f"{num.destino}/p{n:02d}.sonnet-trunca.json")):
        x = sin_duplicados(json.load(open(f)), num.ocr[n])
        if not x.get("bloqueada") and x.get("articulos") is not None:
            validas.append(x)
    if not validas:
        return None
    x = max(validas, key=lambda v: sum(palabras(a["texto"]) for a in v["articulos"]))
    return {**x, "incompleta": True, "texto_tesseract": num.ocr[n]}


def juntar_mitades(num, n):
    """Cuando ya llegaron las dos mitades de una página leída en dos pedidos, arma su lectura unida."""
    sup, inf = num.lectura(n, "haiku-sup"), num.lectura(n, "haiku-inf")
    if not (sup and inf) or num.lectura(n, "haiku-dividida"):
        return
    if "articulos" in sup and "articulos" in inf:
        unida = {**unir_mitades(sup, inf), "modelo": "haiku"}
    else:  # una mitad bloqueada por derechos de autor: queda la lectura de la página entera
        unida = {"bloqueada": True, "modelo": "haiku", "error": "una mitad bloqueada"}
    guardar(f"{num.destino}/p{n:02d}.haiku-dividida.json", unida)


def finalizar_numero(num):
    finalizar(num.meta, num.destino, num.total, num.ocr, {n: elegir_lectura(num, n) for n in range(1, num.total + 1)})


def costo(modelo, entrada, salida):
    _, p_ent, p_sal = MODELOS["sonnet" if modelo.startswith("bloque") else modelo.split("-")[0]]
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
                bloques = cargar(f"{num.destino}/p{n:02d}.bloques.json", [])
                bloques.append({**bloque, "pedido": modelo})
                guardar(f"{num.destino}/p{n:02d}.bloques.json", bloques)
                continue
            try:
                datos = PaginaCompleta.model_validate_json(texto).model_dump()
            except Exception as e:
                fallas[r.custom_id] = fallas.get(r.custom_id, 0) + 1
                problemas.append(f"{nid} pág. {n} {modelo}: respuesta inválida ({msg.stop_reason}) {str(e)[:80]}")
                continue
            datos.update(modelo=modelo, tokens_entrada=msg.usage.input_tokens, tokens_salida=msg.usage.output_tokens,
                         lote=lote["id"], pagina_enfrentada=num.pliegos.get(n))
            if modelo == "sonnet":
                previas = [x for x in (num.lectura(n, "haiku-dividida"), num.lectura(n, "haiku")) if x and "articulos" in x]
                minimo = 0.5 * max([sum(palabras(a["texto"]) for a in x["articulos"]) for x in previas] or [0])
                if sum(palabras(a["texto"]) for a in datos["articulos"]) < minimo:
                    guardar(f"{num.destino}/p{n:02d}.sonnet-trunca.json", datos)
                    problemas.append(f"{nid} pág. {n} sonnet: lectura trunca, queda la de Haiku")
                    continue
            guardar(archivo, datos)
            if modelo in ("haiku-sup", "haiku-inf"):
                juntar_mitades(num, n)
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
    # Bloques girados que informó Haiku: se mide su ángulo real una sola vez y se guardan los confirmados
    for num in numeros.values():
        pendientes = [n for n in range(1, num.total + 1) if (h := num.lectura(n, "haiku"))
                      and h.get("bloques_girados") and "bloques_confirmados" not in h]
        for n in pendientes:
            h = num.lectura(n, "haiku")
            confirmar_bloques(h, num.imagenes()[n - 1])
            guardar(f"{num.destino}/p{n:02d}.haiku.json", h)
        num._imagenes = None
    # (una página que falló dos veces en un paso pasa al siguiente)
    falla = lambda num, n, m: fallas.get(f"{num.id}__{n}__{m}", 0) >= 2
    curso = lambda num, n, m: any(i == num.id and p == n and x.startswith(m) for i, p, x in en_curso)
    faltan = {"haiku": [], "mitad": [], "sonnet": [], "bloque": []}
    # páginas que se releen con Sonnet aunque Haiku no lo pida (etapa 3: pedazos sueltos y problemas de estructura)
    forzar = {(x["id"], x["n"]) for x in cargar(os.path.join(lectura, "forzar-sonnet.json"), [])}
    for num in numeros.values():
        for n in range(1, num.total + 1):
            if os.path.exists(f"{num.destino}/p{n:02d}.json"):
                continue  # ya tiene su versión final
            h = num.lectura(n, "haiku")
            if h is None and not falla(num, n, "haiku"):
                if not curso(num, n, "haiku"):
                    faltan["haiku"].append((num, n))
                continue
            paso = siguiente_paso(h, num.ocr[n])
            if (num.id, n) in forzar and not h.get("bloqueada"):
                paso = "sonnet"
            if paso == "mitad" and falla(num, n, "haiku-sup") | falla(num, n, "haiku-inf"):
                paso = "sonnet"
            juntar_mitades(num, n)
            if paso == "mitad" and num.lectura(n, "haiku-dividida") is None:
                if not curso(num, n, "haiku-sup") and not curso(num, n, "haiku-inf"):
                    faltan["mitad"].append((num, n))
                continue
            if (paso == "sonnet" and num.lectura(n, "sonnet") is None and not curso(num, n, "sonnet")
                    and not falla(num, n, "sonnet") and not os.path.exists(f"{num.destino}/p{n:02d}.sonnet-trunca.json")):
                faltan["sonnet"].append((num, n))
                continue
            if (h and h.get("bloques_confirmados") and not os.path.exists(f"{num.destino}/p{n:02d}.bloques.json")
                    and not curso(num, n, "bloque") and not falla(num, n, "bloque1")
                    and not curso(num, n, "haiku") and not curso(num, n, "sonnet")):
                faltan["bloque"].append((num, n))

    # 3. Mandar lotes nuevos, sin pasar el tope
    comprometido = gasto["usd"] + sum(COSTO_ESTIMADO.get(l["modelo"], 0.04) * l["paginas"] for l in lotes
                                      if l["estado"] != "recogido")
    # mientras se arma y manda un lote, otros hilos van sacando del PDF las imágenes de los números del lote
    # siguiente (sacarlas es lo más lento); pocos hilos, para que la Mac siga usable
    precarga = ThreadPoolExecutor(3)
    def precargar(parte):
        for num in {num.id: num for num, _ in parte}.values():
            precarga.submit(lambda x=num: (x.imagenes(), x.giro(1)))
    for modelo in ("haiku", "mitad", "sonnet", "bloque"):
        cola = faltan[modelo]
        por_lote = PEDIDOS_POR_LOTE // 2 if modelo in ("mitad", "bloque") else PEDIDOS_POR_LOTE
        for i in range(0, len(cola), por_lote):
            parte = cola[i:i + por_lote]
            precargar(cola[i + por_lote:i + 2 * por_lote])
            estimado = COSTO_ESTIMADO[modelo] * len(parte) * (2 if modelo == "bloque" else 1)
            if comprometido + estimado > tope:
                print(f"Tope de US$ {tope:.2f}: no mando {len(cola) - i} páginas de {modelo} "
                      f"(gastado US$ {gasto['usd']:.2f}, comprometido US$ {comprometido:.2f}).")
                break
            if modelo == "bloque":
                pedidos = [p for num, n in parte for p in pedidos_bloques(num, n)]
            elif modelo == "mitad":
                pedidos = [pedido(num, n, "haiku", mitad) for num, n in parte for mitad in ("superior", "inferior")]
            else:
                pedidos = [pedido(num, n, modelo) for num, n in parte]
            # liberar las imágenes de los números ya preparados (el último puede seguir en el próximo lote)
            usados = list({num.id: num for num, _ in parte}.values())
            for num in usados[:-1] if i + por_lote < len(cola) else usados:
                num._imagenes = None
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
        if os.path.exists(f"{num.destino}/articulos.json") and all(
                os.path.exists(f"{num.destino}/p{n:02d}.json") for n in range(1, num.total + 1)):
            listos += 1
            continue
        finalizar_numero(num)
        listos += 1
    print(f"{listos} de {len(numeros)} números terminados; gastado US$ {gasto['usd']:.2f} de US$ {tope:.2f}")


def refinalizar(rollos, ocr, lectura):
    """Vuelve a armar la versión final de los números ya terminados (por ejemplo, tras cambiar las reglas)."""
    etapa = cargar(os.path.join(lectura, "etapa.json"), {"numeros": []})
    hechos = incompletas = 0
    for x in etapa["numeros"]:
        num = Numero(x, rollos, ocr, lectura)
        if os.path.exists(f"{num.destino}/articulos.json"):
            finalizar_numero(num)
            hechos += 1
            incompletas += sum(1 for n in range(1, num.total + 1)
                               if json.load(open(f"{num.destino}/p{n:02d}.json")).get("incompleta"))
    print(f"{hechos} números rehechos; {incompletas} páginas marcadas como incompletas (para revisar)")


def estado(lectura):
    lotes = cargar(os.path.join(lectura, "lotes.json"), [])
    gasto = cargar(os.path.join(lectura, "gasto.json"), {"usd": 0.0})
    for l in lotes:
        print(f"{l['id']}  {l['modelo']:6} {l['paginas']:4} págs.  {l['estado']:9} {l.get('estado_api', '')}"
              f"  US$ {l.get('usd', 0):.2f}")
    print(f"gastado US$ {gasto['usd']:.2f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("accion", choices=["elegir", "seguir", "estado", "refinalizar"])
    ap.add_argument("carpetas", nargs="+")
    ap.add_argument("--tope", type=float, default=100.0)
    a = ap.parse_args()
    if a.accion == "elegir":
        elegir(a.carpetas[0], a.carpetas[1], int(a.carpetas[2]))
    elif a.accion == "refinalizar":
        refinalizar(*a.carpetas[:3])
    elif a.accion == "seguir":
        seguir(*a.carpetas[:3], a.tope)
    else:
        estado(a.carpetas[0])
