"""Limpieza fina de los nombres dudosos de Autores con Sonnet (solo texto, Batch API, con tope de gasto).

Uso (desde sitio-final/, con ../.venv-ia/bin/python):
  autores_sonnet.py estimar  CARPETA_DATOS                 cuenta tokens de entrada (gratis) y estima el costo
  autores_sonnet.py piloto   CARPETA_DATOS --n 3           unos pocos pedidos sin lote, para medir la salida real
  autores_sonnet.py enviar   CARPETA_DATOS --tope USD      manda el lote si el costo estimado entra en el tope
  autores_sonnet.py recoger  CARPETA_DATOS                 baja resultados y anota el costo real en gasto.json
  autores_sonnet.py aplicar  CARPETA_DATOS                 une solo lo seguro (deshacible), lo dudoso va a Revisión

Casos (de autores.armar): siglas frecuentes sin dueño (con candidatos de iniciales compatibles y años que se cruzan) y
firmas raras (1–2 textos) con el mismo apellido que otra persona. Varios casos por pedido para abaratar.
Lo que se une va a autores_alias.json con la marca "_sonnet_sitio" (lista de [forma, nombre]) para poder deshacerlo.
"""
import json, os, sys
from collections import Counter
from typing import List, Literal, Optional
from pydantic import BaseModel
import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
sys.path.insert(0, os.path.join(AQUI, "..", "pipeline"))
import autores as AU  # noqa: E402
from leer_ia import cliente  # noqa: E402

MODELO, P_ENT, P_SAL = "claude-sonnet-5-5", 2.00, 10.00  # US$ por millón, sin descuento; el lote paga la mitad
POR_PEDIDO = 12


class Caso(BaseModel):
    n: int
    decision: Literal["misma", "distinta", "dudoso"]
    con_quien: Optional[str]
    confianza: float
    razon: str


class Respuesta(BaseModel):
    casos: List[Caso]


ESQUEMA = anthropic.transform_schema(Respuesta)
INSTR = ("Sos editor del archivo del semanario uruguayo Marcha (Montevideo, 1939–1974). Las firmas de las notas tienen "
         "variantes: iniciales, apellido solo, abreviaturas y errores de lectura (OCR). Para cada caso numerado, decidí si la "
         "firma es la MISMA persona que exactamente uno de sus candidatos. Respondé «misma» solo si es claro (mismo nombre con "
         "un error de lectura o una inicial, años y secciones compatibles); «distinta» si son personas diferentes; «dudoso» si "
         "no alcanza. Unir dos personas distintas es mucho peor que dejar separadas dos formas de la misma: ante la duda, "
         "«dudoso». Una sigla solo es de alguien si sus iniciales coinciden y escribía lo mismo en los mismos años. En con_quien "
         "copiá el candidato exacto cuando respondas «misma». razon: una frase corta.")


def ctx(x):
    an = sorted({t[0][:4] for t in x["textos"]} | {o[0][:4] for o in x["obras"]})
    sec = Counter(t[4] for t in x["textos"] if t[4]).most_common(3)
    tits = [t[3][:50] for t in x["textos"][:3]]
    return (f"{len(x['textos'])} textos, {len(x['obras'])} dibujos, {an[0] + '–' + an[-1] if an else '?'}; "
            f"secciones: {', '.join(s for s, _ in sec) or '—'}; títulos: {' | '.join(tits) or '—'}")


def iniciales(nombre):
    return "".join(w[0] for w in nombre.replace(".", " ").split() if w[:1].isupper())


def casos(datos):
    gente, _, _, dud = AU.armar(os.path.join(datos, "lectura-ia", "marcha.sqlite"))
    por_nombre = {x["nombre"]: x for x in gente}
    sig_cand = [x for x in gente if len(x["textos"]) >= 5 and not AU.es_sigla(x["nombre"])]
    salida = []
    # las siglas no están en «gente»: su contexto se arma con los datos del dudoso
    for d in dud:
        if "candidatos" in d:
            x = por_nombre.get(d["forma"])
            if not x:
                continue
            cands = [c for c in d["candidatos"] if c in por_nombre][:5]
            salida.append({"forma": d["forma"], "ctx": ctx(x), "cands": [(c, ctx(por_nombre[c])) for c in cands]})
        else:
            ini = iniciales(d["forma"]).upper()
            if len(ini) < 2:
                continue  # una letra sola no se puede atribuir
            cands = [c for c in sig_cand if iniciales(c["nombre"]).upper() in (ini, ini[:1] + ini[-1:]) or
                     iniciales(c["nombre"]).upper().startswith(ini) and len(iniciales(c["nombre"])) == len(ini) + 1]
            cands.sort(key=lambda c: -len(c["textos"]))
            if not cands:
                continue
            salida.append({"forma": d["forma"], "ctx": f"sigla, {d['textos']} textos; títulos: {' | '.join(d.get('ejemplos', []))}",
                           "cands": [(c["nombre"], ctx(c)) for c in cands[:6]]})
    return salida


def pedidos(cs):
    out = []
    for k in range(0, len(cs), POR_PEDIDO):
        grupo = cs[k:k + POR_PEDIDO]
        texto = INSTR + "\n\n" + "\n\n".join(
            f"Caso {k + i}: firma «{c['forma']}» ({c['ctx']})\nCandidatos:\n" + "\n".join(f"- «{n}»: {t}" for n, t in c["cands"])
            for i, c in enumerate(grupo))
        out.append((f"aut__{k}", dict(model=MODELO, max_tokens=6000, output_config={"effort": "low", "format": {"type": "json_schema", "schema": ESQUEMA}},
                                      messages=[{"role": "user", "content": texto}])))
    return out


def carpeta(datos):
    d = os.path.join(datos, "lectura-ia", "juez")
    os.makedirs(d, exist_ok=True)
    return d


def main():
    accion, datos = sys.argv[1], sys.argv[2]
    J = carpeta(datos)
    gasto_p = os.path.join(datos, "lectura-ia", "gasto.json")
    if accion in ("estimar", "piloto", "enviar"):
        cs = casos(datos)
        ps = pedidos(cs)
        json.dump(cs, open(os.path.join(J, "sitio-autores-casos.json"), "w"), ensure_ascii=False, indent=1)
        cli = cliente()
        if accion == "estimar":
            muestra = ps[:: max(1, len(ps) // 8)]
            ent = sum(cli.messages.count_tokens(model=MODELO, messages=p["messages"]).input_tokens for _, p in muestra) / len(muestra)
            print(f"{len(cs)} casos en {len(ps)} pedidos; entrada media {ent:.0f} tokens por pedido")
            json.dump({"casos": len(cs), "pedidos": len(ps), "entrada_media": ent}, open(os.path.join(J, "sitio-autores-estimacion.json"), "w"))
        elif accion == "piloto":
            n = int(sys.argv[sys.argv.index("--n") + 1])
            usd, sal, ent, res = 0, [], [], {}
            for cid, p in ps[1: 1 + n]:
                m = cli.messages.create(**p)
                usd += (m.usage.input_tokens * P_ENT + m.usage.output_tokens * P_SAL) / 1e6
                ent.append(m.usage.input_tokens); sal.append(m.usage.output_tokens)
                res[cid] = next((c.text for c in m.content if c.type == "text"), "")
            g = json.load(open(gasto_p)); g["usd"] += usd
            g["detalle"].append({"concepto": f"piloto autores sitio ({n} pedidos, sin lote)", "modelo": "sonnet", "usd": round(usd, 4)})
            json.dump(g, open(gasto_p, "w"), ensure_ascii=False, indent=1)
            json.dump(res, open(os.path.join(J, "sitio-autores-piloto.json"), "w"), ensure_ascii=False, indent=1)
            e, s = sum(ent) / n, sum(sal) / n
            lote = len(ps) * (e * P_ENT + s * P_SAL) / 1e6 / 2
            print(f"piloto US$ {usd:.4f}; por pedido: entrada {e:.0f}, salida {s:.0f}; lote completo estimado US$ {lote:.2f}")
            json.dump({"pedidos": len(ps), "entrada": e, "salida": s, "lote_usd": lote, "piloto_usd": usd},
                      open(os.path.join(J, "sitio-autores-estimacion.json"), "w"))
        else:
            tope = float(sys.argv[sys.argv.index("--tope") + 1])
            est = json.load(open(os.path.join(J, "sitio-autores-estimacion.json")))
            if est["lote_usd"] * 1.15 + est.get("piloto_usd", 0) > tope:
                sys.exit(f"no se manda: estimado US$ {est['lote_usd']:.2f} (+15 %) + piloto supera el tope de US$ {tope}")
            reqs = [Request(custom_id=cid, params=MessageCreateParamsNonStreaming(**p)) for cid, p in ps]
            b = cli.messages.batches.create(requests=reqs)
            json.dump({"id": b.id, "pedidos": len(reqs), "estado": "enviado"}, open(os.path.join(J, "sitio-autores-lote.json"), "w"))
            print(f"lote {b.id}: {len(reqs)} pedidos")
    elif accion == "recoger":
        lote = json.load(open(os.path.join(J, "sitio-autores-lote.json")))
        cli = cliente(); b = cli.messages.batches.retrieve(lote["id"])
        if b.processing_status != "ended":
            print("en proceso", b.request_counts); return
        usd, res = 0, {}
        for r in cli.messages.batches.results(lote["id"]):
            if r.result.type != "succeeded":
                continue
            m = r.result.message
            usd += (m.usage.input_tokens * P_ENT + m.usage.output_tokens * P_SAL) / 1e6 / 2
            try:
                for c in Respuesta.model_validate_json(next(c.text for c in m.content if c.type == "text")).casos:
                    res[str(c.n)] = c.model_dump()
            except Exception:
                pass
        g = json.load(open(gasto_p)); g["usd"] += usd
        g["detalle"].append({"lote": lote["id"], "modelo": "sonnet-autores-sitio", "usd": round(usd, 4)})
        json.dump(g, open(gasto_p, "w"), ensure_ascii=False, indent=1)
        json.dump(res, open(os.path.join(J, "sitio-autores-resultados.json"), "w"), ensure_ascii=False, indent=1)
        lote["estado"] = "recogido"; lote["usd"] = round(usd, 4)
        json.dump(lote, open(os.path.join(J, "sitio-autores-lote.json"), "w"))
        print(f"recogido: {len(res)} decisiones, costo real US$ {usd:.4f}")
    elif accion == "aplicar":
        cs = json.load(open(os.path.join(J, "sitio-autores-casos.json")))
        res = json.load(open(os.path.join(J, "sitio-autores-resultados.json")))
        p = os.path.join(AQUI, "..", "pipeline", "autores_alias.json"); alias = json.load(open(p))
        hechas = alias.setdefault("_sonnet_sitio", [])
        unidos, revisar = 0, []
        for k, c in enumerate(cs):
            j = res.get(str(k))
            if not j:
                continue
            nombres = [n for n, _ in c["cands"]]
            if j["decision"] == "misma" and j["confianza"] >= 0.9 and j.get("con_quien") in nombres:
                dst = j["con_quien"]
                alias.setdefault(dst, [])
                if c["forma"] not in alias[dst]:
                    alias[dst].append(c["forma"]); hechas.append([c["forma"], dst]); unidos += 1
            elif j["decision"] != "distinta":
                revisar.append({"forma": c["forma"], "candidatos": nombres, "sonnet": j})
        json.dump(alias, open(p, "w"), ensure_ascii=False, indent=1)
        json.dump(revisar, open(os.path.join(datos, "lectura-ia", "revisar-autores-sitio.json"), "w"), ensure_ascii=False, indent=1)
        print(f"unidos {unidos} (deshacibles: _sonnet_sitio); a Revisión {len(revisar)}")


if __name__ == "__main__":
    main()
