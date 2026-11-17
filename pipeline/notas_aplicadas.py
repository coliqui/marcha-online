"""Marca qué notas de Coli (notas-revision.jsonl) ya están aplicadas, sin tocar ese archivo.

Uso: python3 notas_aplicadas.py CARPETA_LECTURA [--marcar FECHA_NOTA "cómo se aplicó"]

Escribe CARPETA_LECTURA/notas-aplicadas.json: {fecha de la nota: {"aplicada": fecha, "como": texto, "verificada": "auto"|"a mano"}}.
- Sin opciones: verifica solas las notas que tienen una corrección en pipeline/correcciones.json cuyo motivo nombra a Coli
  en el mismo número y la misma página (si no se puede verificar, queda pendiente). Las marcas hechas a mano se conservan.
- --marcar: marca una nota a mano (cuando Claude aplica una nota que no deja corrección, por ejemplo de diseño).
El servidor del sitio de prueba (servidor_sitio.py) agrega el campo «aplicada» a cada nota al mostrarla.
"""
import json, os, re, sys
from datetime import date

AQUI = os.path.dirname(os.path.abspath(__file__))


def num(x):
    return str(int(x)) if str(x or "").isdigit() else str(x or "")


def correcciones_de_coli():
    salida = []

    def recorrer(o):
        if isinstance(o, dict):
            if "Coli" in str(o.get("motivo", "")) and o.get("numero") is not None:
                f = re.search(r"\((\d{1,2}(?:-\d{1,2})?/\d{1,2}/\d{4})", o["motivo"])
                salida.append((num(o["numero"]), o.get("pagina"), f.group(1) if f else "", o["motivo"]))
            for v in o.values():
                recorrer(v)
        elif isinstance(o, list):
            for v in o:
                recorrer(v)
    recorrer(json.load(open(os.path.join(AQUI, "correcciones.json"), encoding="utf-8")))
    return salida


def main():
    L = sys.argv[1]
    ruta = os.path.join(L, "notas-aplicadas.json")
    marcas = json.load(open(ruta, encoding="utf-8")) if os.path.exists(ruta) else {}
    if "--marcar" in sys.argv:
        i = sys.argv.index("--marcar")
        marcas[sys.argv[i + 1]] = {"aplicada": date.today().isoformat(), "como": sys.argv[i + 2], "verificada": "a mano"}
    else:
        notas = [json.loads(l) for l in open(os.path.join(L, "notas-revision.jsonl"), encoding="utf-8") if l.strip()]
        cs = correcciones_de_coli()
        for n in notas:
            if not n.get("numero") or marcas.get(n["fecha"], {}).get("verificada") == "a mano":
                continue
            p = n.get("pagina")
            hits = [c for c in cs if c[0] == num(n["numero"]) and p is not None and c[1] is not None and int(c[1]) == int(p)]
            if hits:
                marcas[n["fecha"]] = {"aplicada": hits[0][2], "como": hits[0][3][:200], "verificada": "auto"}
    json.dump(marcas, open(ruta, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    total = sum(1 for l in open(os.path.join(L, "notas-revision.jsonl"), encoding="utf-8") if l.strip())
    print(f"{len(marcas)} de {total} notas marcadas como aplicadas; pendientes {total - len(marcas)}")


if __name__ == "__main__":
    main()
