"""Juez de autores: Sonnet decide si una firma dudosa es la misma persona que alguno de sus candidatos.

Uso:
  python3 juez_autores.py enviar CARPETA_LECTURA --tope DOLARES
  python3 juez_autores.py recoger CARPETA_LECTURA
  python3 juez_autores.py aplicar CARPETA_LECTURA        (agrega a autores_alias.json lo de seguridad alta)

Los casos salen de autores-dudosos.json (unificar_autores.py). A cada nombre se le da contexto: años, secciones,
temas y cantidad de textos. Solo texto, en lote.
"""
import json, os, sys
from collections import Counter, defaultdict
from typing import Literal, Optional
from pydantic import BaseModel
import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from leer_ia import cliente, MODELOS
from unificar_autores import separar, plano

COSTO = 0.0025


class Juicio(BaseModel):
    misma_persona: Literal["si", "no", "dudoso"]
    con_quien: Optional[str]
    confianza: float
    razon: str


ESQUEMA = anthropic.transform_schema(Juicio)


def contexto(L):
    info = defaultdict(lambda: {"textos": 0, "anios": [], "secciones": Counter(), "temas": Counter()})
    for f in os.listdir(L):
        p = os.path.join(L, f, "articulos.json")
        if not os.path.exists(p):
            continue
        num = os.path.join(os.path.dirname(L.rstrip("/")), "salida", f, "numero.json")
        anio = json.load(open(num)).get("fecha", "")[:4] if os.path.exists(num) else ""
        for a in json.load(open(p))["articulos"]:
            if not a.get("autor"):
                continue
            for x in separar(a["autor"]):
                d = info[x.strip()]
                d["textos"] += 1
                if anio:
                    d["anios"].append(anio)
                if a.get("seccion"):
                    d["secciones"][a["seccion"]] += 1
                d["temas"].update(a.get("temas", [])[:3])
    return info


def describir(nombre, info):
    d = info.get(nombre)
    if not d:
        return f"«{nombre}»: sin datos"
    an = sorted(d["anios"])
    return (f"«{nombre}»: {d['textos']} textos, {an[0] if an else '?'}–{an[-1] if an else '?'}; secciones: "
            f"{', '.join(s for s, _ in d['secciones'].most_common(3)) or '—'}; temas: {', '.join(t for t, _ in d['temas'].most_common(5)) or '—'}")


def enviar(L, tope):
    dud = json.load(open(os.path.join(L, "autores-dudosos.json")))
    info = contexto(L)
    gasto = json.load(open(os.path.join(L, "gasto.json")))["usd"]
    os.makedirs(os.path.join(L, "juez"), exist_ok=True)
    pedidos = []
    for k, d in enumerate(dud):
        if gasto + (len(pedidos) + 1) * COSTO > tope:
            break
        texto = (f"Sos editor del archivo del semanario uruguayo Marcha (1939-1974). Las firmas de las notas tienen variantes y "
                 f"errores de lectura. Decidí si la firma «{d['forma']}» es la MISMA PERSONA que alguno de estos candidatos "
                 f"(errores de lectura de una inicial, apellido solo, abreviatura). Si son personas distintas (otro nombre, otra "
                 f"época, otros temas) respondé «no»; si no hay pruebas claras, «dudoso». Es mucho peor unir dos personas distintas "
                 f"que dejar separadas dos formas de la misma. En con_quien poné el candidato exacto, si respondés «si».\n\n"
                 f"Firma: {describir(d['forma'], info)}\nCandidatos:\n" + "\n".join("- " + describir(c, info) for c in d["candidatos"][:6]))
        pedidos.append(Request(custom_id=f"autor__{k}", params=MessageCreateParamsNonStreaming(
            model=MODELOS["sonnet"][0], max_tokens=800, messages=[{"role": "user", "content": texto}],
            output_config={"format": {"type": "json_schema", "schema": ESQUEMA}})))
    lote = cliente().messages.batches.create(requests=pedidos)
    json.dump({"id": lote.id, "casos": len(pedidos), "estado": "enviado"}, open(os.path.join(L, "juez", "autores-lote.json"), "w"))
    print(f"lote {lote.id}: {len(pedidos)} de {len(dud)} casos")


def recoger(L):
    lote = json.load(open(os.path.join(L, "juez", "autores-lote.json")))
    cli = cliente(); b = cli.messages.batches.retrieve(lote["id"])
    if b.processing_status != "ended":
        print("en proceso", b.request_counts.succeeded); return False
    _, pe, ps = MODELOS["sonnet"]; usd = 0; res = {}
    for r in cli.messages.batches.results(lote["id"]):
        if r.result.type != "succeeded":
            continue
        m = r.result.message; usd += (m.usage.input_tokens * pe + m.usage.output_tokens * ps) / 1e6 / 2
        try:
            res[r.custom_id] = Juicio.model_validate_json(next(c.text for c in m.content if c.type == "text")).model_dump()
        except Exception:
            pass
    g = json.load(open(os.path.join(L, "gasto.json"))); g["usd"] += usd
    g["detalle"].append({"lote": lote["id"], "modelo": "juez-autores-sonnet", "usd": round(usd, 4)})
    json.dump(g, open(os.path.join(L, "gasto.json"), "w"), ensure_ascii=False, indent=1)
    json.dump(res, open(os.path.join(L, "juez", "autores-resultados.json"), "w"), ensure_ascii=False)
    lote["estado"] = "recogido"; json.dump(lote, open(os.path.join(L, "juez", "autores-lote.json"), "w"))
    print(f"recogido: {len(res)} juicios, US$ {usd:.2f}"); return True


def aplicar(L):
    dud = json.load(open(os.path.join(L, "autores-dudosos.json")))
    res = json.load(open(os.path.join(L, "juez", "autores-resultados.json")))
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "autores_alias.json"); alias = json.load(open(p))
    def canon_de(nombre):  # el grupo de autores_alias donde ya está ese nombre (o el nombre mismo)
        for k, vs in alias.items():
            if not k.startswith("_") and plano(nombre) in {plano(k)} | {plano(v) for v in vs}:
                return k
        return None
    unidos = 0; resto = []
    for k, d in enumerate(dud):
        j = res.get(f"autor__{k}")
        if not j:
            resto.append(d); continue
        d["juez"] = j
        if j["misma_persona"] == "si" and j["confianza"] >= 0.85 and j.get("con_quien") in d["candidatos"]:
            c = canon_de(j["con_quien"]) or j["con_quien"]
            alias.setdefault(c, [])
            if d["forma"] not in alias[c]:
                alias[c].append(d["forma"])
            alias["_automaticos"] = sorted(set(alias.get("_automaticos", [])) | {c})
            unidos += 1
        elif j["misma_persona"] == "no" and j["confianza"] >= 0.85:
            continue  # personas distintas: sale de la revisión
        else:
            resto.append(d)
    json.dump(alias, open(p, "w"), ensure_ascii=False, indent=1)
    json.dump(resto, open(os.path.join(L, "autores-dudosos.json"), "w"), ensure_ascii=False, indent=1)
    print(f"unidos {unidos}; quedan dudosos {len(resto)}")


if __name__ == "__main__":
    a, L = sys.argv[1], sys.argv[2]
    if a == "enviar":
        enviar(L, float(sys.argv[sys.argv.index("--tope") + 1]))
    elif a == "recoger":
        recoger(L)
    else:
        aplicar(L)
