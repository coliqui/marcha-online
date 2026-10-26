"""Corre el OCR sobre toda la colección.

Uso: python3 procesar_coleccion.py CARPETA_ROLLOS CARPETA_SALIDA [trabajos]

Recorre CARPETA_ROLLOS (con subcarpetas "Rollo NN"), procesa cada PDF con
ocr_numero.py y deja el resultado en CARPETA_SALIDA/<id>/. Se puede cortar y
volver a lanzar: salta los números que ya tienen numero.json y data.json.
Anota el avance en CARPETA_SALIDA/avance.log y los errores en errores.log.
"""
import glob, json, os, subprocess, sys, time, traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ocr_numero import metadatos, procesar

AQUI = os.path.dirname(os.path.abspath(__file__))


def main(rollos, salida, trabajos):
    os.makedirs(salida, exist_ok=True)
    pdfs = sorted(glob.glob(os.path.join(rollos, "**", "*.pdf"), recursive=True))
    log = open(os.path.join(salida, "avance.log"), "a")
    inicio, hechos = time.time(), 0
    for i, pdf in enumerate(pdfs, 1):
        try:
            meta = metadatos(pdf)
            destino = os.path.join(salida, meta["id"])
            if os.path.exists(os.path.join(destino, "data.json")):
                continue
            t0 = time.time()
            meta = procesar(pdf, destino, trabajos)
            rollo = os.path.basename(os.path.dirname(pdf))
            meta.update({"rollo": rollo, "archivo": os.path.relpath(pdf, rollos)})
            json.dump(meta, open(os.path.join(destino, "numero.json"), "w"), ensure_ascii=False, indent=1)
            subprocess.run([sys.executable, os.path.join(AQUI, "indice_paginas.py"), destino],
                           check=True, capture_output=True)
            hechos += 1
            resto = (time.time() - inicio) / hechos * (len(pdfs) - i)
            print(f"{i}/{len(pdfs)} {meta['id']} ({meta['fecha']}, {meta['paginas']} págs) "
                  f"en {time.time() - t0:.0f} s, faltan ~{resto / 3600:.1f} h", file=log, flush=True)
        except Exception:
            with open(os.path.join(salida, "errores.log"), "a") as err:
                print(f"== {pdf}\n{traceback.format_exc()}", file=err)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else os.cpu_count())
