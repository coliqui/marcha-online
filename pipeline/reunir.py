"""Vuelve a unir los artículos que siguen en otra página, sin llamar a la API.

Uso: python3 reunir.py CARPETA_IA

Lee la versión final de cada página (pNN.json) de cada número y reescribe articulos.json.
"""
import glob, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from leer_ia import unir_continuaciones

for carpeta in sorted(glob.glob(os.path.join(sys.argv[1], "*", ""))):
    archivo = os.path.join(carpeta, "articulos.json")
    if not os.path.exists(archivo):
        continue
    datos = json.load(open(archivo))
    paginas = [(int(os.path.basename(f)[1:3]), json.load(open(f)))
               for f in sorted(glob.glob(os.path.join(carpeta, "p[0-9][0-9].json")))]
    datos["articulos"] = unir_continuaciones(paginas)
    json.dump(datos, open(archivo, "w"), ensure_ascii=False, indent=1)
    unidos = [a for a in datos["articulos"] if len(a["paginas"]) > 1]
    print(f"{datos['id']}: {len(datos['articulos'])} artículos, {len(unidos)} unidos entre páginas")
    for a in unidos:
        print(f"   págs. {a['paginas']}: {a['titulo'][:60]}" + (" (sin marca)" if a.get("union_sin_marca") else ""))
