"""Juez de uniones: Sonnet decide, solo con texto, si dos pedazos son la misma nota (y en qué orden).

Uso:
  python3 juez_uniones.py enviar CARPETA_LECTURA --tope DOLARES   (manda lotes por prioridad hasta el tope)
  python3 juez_uniones.py recoger CARPETA_LECTURA                  (recoge los lotes terminados)

Prioridad: 1) notas partidas fuertes, 2) uniones dudosas, 3) notas partidas débiles. Los resultados quedan en
CARPETA_LECTURA/juez/resultados.json ({custom_id: juicio}); los lotes en juez/lotes.json. No cambia nada del archivo.
"""
import json, os, re, sys, unicodedata
from typing import Literal
from pydantic import BaseModel
import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from leer_ia import cliente, MODELOS

COSTO_POR_CASO = 0.0028  # Sonnet, en lote (medido en la prueba: US$ 0,0026)
POR_LOTE = 500


class Juicio(BaseModel):
    misma_nota: Literal["si", "no", "dudoso"]
    orden: Literal["A_luego_B", "B_luego_A", "no_aplica"]
    confianza: float
    razon: str


ESQUEMA = anthropic.transform_schema(Juicio)


def palabras(t, n, final):
    w = t.split()
    return " ".join(w[-n:] if final else w[:n])


def texto_pedido(a, b):
    marcas = lambda t: "; ".join(re.findall(r"(?i)\((?:pasa|viene|sigue|contin)[^)]{0,40}\)", t)) or "ninguna"
    return f"""Sos editor de un archivo del semanario uruguayo Marcha. La lectura automática de un número puede haber partido una sola nota en dos textos. Decidí si los dos textos A y B son partes de la MISMA nota (una continúa a la otra) o son notas distintas. Fijate en si las frases empalman (una palabra cortada «com-» + «sión», una frase que sigue), el tema, el autor, las marcas «Pasa a / Viene de». Si no hay pruebas claras, respondé «dudoso». Es mucho peor unir dos notas distintas que dejar separadas dos partes de la misma.

A: título «{a['titulo']}», firma {a.get('autor') or 'ninguna'}, páginas {a['paginas']}, marcas: {marcas(a['texto'])}
Comienzo de A: «{palabras(a['texto'], 60, False)}»
Final de A: «{palabras(a['texto'], 80, True)}»

B: título «{b['titulo']}», firma {b.get('autor') or 'ninguna'}, páginas {b['paginas']}, marcas: {marcas(b['texto'])}
Comienzo de B: «{palabras(b['texto'], 80, False)}»
Final de B: «{palabras(b['texto'], 60, True)}»"""


def casos(L):
    """[(grupo, nid, i_a, i_b)] por prioridad, sin repetir pares."""
    sin = lambda t: unicodedata.normalize("NFD", t or "").encode("ascii", "ignore").decode().lower()
    fuertes, debiles, dudosas, vistos = [], [], [], set()
    p = json.load(open(os.path.join(L, "partidas.json")))
    for nid, lista in p.items():
        arts = json.load(open(os.path.join(L, nid, "articulos.json")))["articulos"]
        for x in lista:
            if max(x["primero"], x["segundo"]) >= len(arts):
                continue
            a, b = arts[x["primero"]], arts[x["segundo"]]
            fuerte = any(s.startswith("título partido") for s in x["senales"]) and (
                any(s.startswith("mismos nombres") for s in x["senales"]) or (a.get("autor") and sin(a["autor"]) == sin(b.get("autor"))))
            (fuertes if fuerte else debiles).append(("fuerte" if fuerte else "debil", nid, x["primero"], x["segundo"]))
    u = json.load(open(os.path.join(L, "uniones-propuestas.json")))
    for nid, lista in u.items():
        for x in lista:
            if x["clase"] == "dudosa":
                dudosas.append(("dudosa", nid, x["candidatas"][0]["articulo"], x["pedazo"]))
    salida = []
    for g, nid, a, b in fuertes + dudosas + debiles:
        k = (nid, min(a, b), max(a, b))
        if k not in vistos:
            vistos.add(k)
            salida.append((g, nid, a, b))
    return salida


def enviar(L, tope):
    os.makedirs(os.path.join(L, "juez"), exist_ok=True)
    lotes = json.load(open(os.path.join(L, "juez", "lotes.json"))) if os.path.exists(os.path.join(L, "juez", "lotes.json")) else []
    hechos = {c for l in lotes for c in l["pedidos"]}
    gasto = json.load(open(os.path.join(L, "gasto.json")))
    comprometido = gasto["usd"] + sum(l["casos"] * COSTO_POR_CASO for l in lotes if l["estado"] != "recogido")
    pendientes = [c for c in casos(L) if f"juez__{c[0]}__{c[1]}__{c[2]}__{c[3]}" not in hechos]
    cli = cliente(); cache = {}
    for i in range(0, len(pendientes), POR_LOTE):
        parte = pendientes[i:i + POR_LOTE]
        if comprometido + len(parte) * COSTO_POR_CASO > tope:
            entra = int((tope - comprometido) / COSTO_POR_CASO)
            parte = parte[:max(0, entra)]
            if len(parte) < 20:
                print(f"tope: quedan {len(pendientes) - i} casos sin mandar ({pendientes[i][0]} en adelante)")
                break
        pedidos = []
        for g, nid, a, b in parte:
            arts = cache.setdefault(nid, json.load(open(os.path.join(L, nid, "articulos.json")))["articulos"])
            pedidos.append(Request(custom_id=f"juez__{g}__{nid}__{a}__{b}", params=MessageCreateParamsNonStreaming(
                model=MODELOS["sonnet"][0], max_tokens=1200, messages=[{"role": "user", "content": texto_pedido(arts[a], arts[b])}],
                output_config={"format": {"type": "json_schema", "schema": ESQUEMA}})))
        try:
            lote = cli.messages.batches.create(requests=pedidos)
        except (anthropic.BadRequestError, anthropic.PermissionDeniedError) as e:
            print(f"La API rechazó el lote ({e.message}); se frena acá."); break
        lotes.append({"id": lote.id, "casos": len(pedidos), "estado": "enviado", "pedidos": [p["custom_id"] for p in pedidos],
                      "grupos": sorted({p["custom_id"].split("__")[1] for p in pedidos})})
        json.dump(lotes, open(os.path.join(L, "juez", "lotes.json"), "w"))
        comprometido += len(pedidos) * COSTO_POR_CASO
        print(f"lote {lote.id}: {len(pedidos)} casos ({', '.join(lotes[-1]['grupos'])}); comprometido US$ {comprometido:.2f}")
        if len(parte) < POR_LOTE and i + POR_LOTE < len(pendientes):
            print(f"tope: quedan {len(pendientes) - i - len(parte)} casos sin mandar"); break


def recoger(L):
    lotes = json.load(open(os.path.join(L, "juez", "lotes.json")))
    res_f = os.path.join(L, "juez", "resultados.json")
    res = json.load(open(res_f)) if os.path.exists(res_f) else {}
    gasto = json.load(open(os.path.join(L, "gasto.json")))
    cli = cliente(); _, pe, ps = MODELOS["sonnet"]
    for l in lotes:
        if l["estado"] == "recogido":
            continue
        b = cli.messages.batches.retrieve(l["id"])
        if b.processing_status != "ended":
            print(f"{l['id']}: {b.processing_status} ({b.request_counts.succeeded} listos)"); continue
        usd = 0
        for r in cli.messages.batches.results(l["id"]):
            if r.result.type != "succeeded":
                continue
            m = r.result.message
            usd += (m.usage.input_tokens * pe + m.usage.output_tokens * ps) / 1e6 / 2
            try:
                res[r.custom_id] = Juicio.model_validate_json(next(c.text for c in m.content if c.type == "text")).model_dump()
            except Exception:
                pass
        gasto["usd"] += usd; gasto["detalle"].append({"lote": l["id"], "modelo": "juez-sonnet", "usd": round(usd, 4)})
        l.update(estado="recogido", usd=round(usd, 4))
        print(f"{l['id']} recogido: US$ {usd:.2f}")
    json.dump(res, open(res_f, "w"), ensure_ascii=False)
    json.dump(lotes, open(os.path.join(L, "juez", "lotes.json"), "w"))
    json.dump(gasto, open(os.path.join(L, "gasto.json"), "w"), ensure_ascii=False, indent=1)
    print(len(res), "juicios guardados; gastado en total US$", round(gasto["usd"], 2))


if __name__ == "__main__":
    accion, L = sys.argv[1], sys.argv[2]
    if accion == "enviar":
        enviar(L, float(sys.argv[sys.argv.index("--tope") + 1]))
    else:
        recoger(L)
