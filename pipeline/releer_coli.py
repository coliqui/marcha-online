"""Relee con Sonnet (sin lote) páginas puntuales que Coli marcó, con su nota como indicación para esa página.

Uso: python3 releer_coli.py CARPETA_DATOS --tope DOLARES NUMERO:PAGINA:"indicación" [...]

Cada página va como siempre (modo pliego: la enfrentada como contexto; reglas vigentes; texto de Tesseract como ayuda)
más la indicación de Coli. La lectura vieja de Sonnet, si había, se guarda en <id>/_antes-coli/. La nueva solo se usa
si está completa y no es más corta que la versión actual (si no, queda como «sonnet-trunca» y sigue la anterior).
Después se rearma la versión final del número (con correcciones.json). Registra el costo en gasto.json.
"""
import json, os, shutil, sys

import anthropic

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from leer_ia import MODELOS, PaginaCompleta, cliente, contenido_pagina, palabras  # noqa: E402
from lotes_ia import Numero, cargar, finalizar_numero, incompleta  # noqa: E402


def main():
    datos = sys.argv[1]
    tope = float(sys.argv[sys.argv.index("--tope") + 1])
    casos = [a.split(":", 2) for a in sys.argv[2:] if a.count(":") >= 2]
    rollos, ocr, L = (os.path.join(datos, x) for x in ("rollos", "salida", "lectura-ia"))
    info = {x["id"]: x for x in cargar(os.path.join(L, "etapa.json"), {"numeros": []})["numeros"]}
    _, p_ent, p_sal = MODELOS["sonnet"]
    cli, gasto, rehacer, informe = cliente(), 0.0, {}, []
    for nid, n, indicacion in casos:
        n = int(n)
        if gasto + 0.15 > tope:
            informe.append(f"nº {nid} p. {n}: no se leyó (tope de US$ {tope})"); continue
        num = rehacer.get(nid) or Numero(info[nid], rollos, ocr, L)
        rehacer[nid] = num
        m = num.pliegos.get(n)
        im = num.imagenes()
        contenido = contenido_pagina(im[n - 1], num.meta, n, num.total, num.ocr[n], completa=True,
                                     enfrentada=(im[m - 1], m) if m else None, giro=num.giro(n))
        contenido.append({"type": "text", "text": "Indicación de la persona que revisa el archivo para esta página "
                          f"(tenela en cuenta, sin dejar de transcribir todo lo demás): {indicacion}"})
        try:
            r = cli.messages.parse(model=MODELOS["sonnet"][0], max_tokens=16000, output_config={"effort": "low"},
                                   messages=[{"role": "user", "content": contenido}], output_format=PaginaCompleta)
        except anthropic.BadRequestError as e:  # el filtro de contenido (derechos de autor) bloqueó la salida
            informe.append(f"nº {nid} p. {n}: bloqueada por el filtro de contenido; queda la lectura anterior ({str(e)[:80]})")
            continue
        usd = (r.usage.input_tokens * p_ent + r.usage.output_tokens * p_sal) / 1e6
        gasto += usd
        if r.stop_reason in ("refusal", "max_tokens") or r.parsed_output is None:
            informe.append(f"nº {nid} p. {n}: sin lectura ({r.stop_reason}), US$ {usd:.3f}"); continue
        nueva = {**r.parsed_output.model_dump(), "modelo": "sonnet", "tokens_entrada": r.usage.input_tokens,
                 "tokens_salida": r.usage.output_tokens, "indicacion_coli": indicacion}
        actual = cargar(f"{num.destino}/p{n:02d}.json", {})
        w_new = sum(palabras(a["texto"]) for a in nueva["articulos"])
        w_old = sum(palabras(a["texto"]) for a in actual.get("articulos", []))
        vieja = f"{num.destino}/p{n:02d}.sonnet.json"
        if os.path.exists(vieja):
            os.makedirs(f"{num.destino}/_antes-coli", exist_ok=True)
            shutil.copy(vieja, f"{num.destino}/_antes-coli/p{n:02d}.sonnet.json")
        completa = not incompleta(nueva, num.ocr[n])
        if completa and w_new >= 0.9 * w_old:
            json.dump(nueva, open(vieja, "w"), ensure_ascii=False, indent=1)
            estado = "aceptada"
        else:
            json.dump(nueva, open(f"{num.destino}/p{n:02d}.sonnet-trunca.json", "w"), ensure_ascii=False, indent=1)
            estado = "descartada (" + ("incompleta" if not completa else "más corta que la actual") + ")"
        informe.append(f"nº {nid} p. {n}: {estado}; palabras {w_old} → {w_new}; completa={nueva.get('transcripcion_completa')}; "
                       f"omisiones: {(nueva.get('omisiones') or '—')[:160]}; US$ {usd:.3f}")
    for num in rehacer.values():
        finalizar_numero(num)
    g = json.load(open(os.path.join(L, "gasto.json")))
    g["usd"] += gasto
    g["detalle"].append({"concepto": "relectura de notas de Coli (17/11/2026), sin lote", "modelo": "sonnet", "usd": round(gasto, 4)})
    json.dump(g, open(os.path.join(L, "gasto.json"), "w"), ensure_ascii=False, indent=1)
    print("\n".join(informe))
    print(f"costo total US$ {gasto:.4f}")


if __name__ == "__main__":
    main()
