"""Repite offline la regla de escalado a Sonnet sobre lecturas de Haiku ya guardadas (sin llamar a la API).

Uso: python3 simular_escalado.py CARPETA_IA CARPETA_OCR [paginas_coleccion]
"""
import glob, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from leer_ia import MODELOS, dudas

ia, ocr = sys.argv[1], sys.argv[2]
total_coleccion = int(sys.argv[3]) if len(sys.argv) > 3 else 42302
precio = lambda m, e, s: (e * MODELOS[m][1] + s * MODELOS[m][2]) / 1e6

paginas, escaladas, gasto_h = 0, [], 0.0
for f in sorted(glob.glob(os.path.join(ia, "*", "p*.haiku.json")), key=lambda x: (int(x.split(os.sep)[-2]), x)):
    nid, n = f.split(os.sep)[-2], int(re.search(r"p(\d+)\.haiku", f).group(1))
    h = json.load(open(f))
    paginas += 1
    gasto_h += precio("haiku", h.get("tokens_entrada", 12000), h.get("tokens_salida", 0))
    t = os.path.join(ocr, nid, f"p{n:02d}.txt")
    motivo = dudas(h, open(t, encoding="utf-8").read() if os.path.exists(t) else "")
    if motivo:
        escaladas.append((nid, n, motivo[1]))

# costo medio por página de Sonnet, medido en las lecturas de Sonnet que haya
son = [json.load(open(f)) for f in glob.glob(os.path.join(ia, "*", "p*.sonnet.json"))]
son = [s for s in son if not s.get("bloqueada")]
por_pag_s = sum(precio("sonnet", s["tokens_entrada"], s["tokens_salida"]) for s in son) / max(len(son), 1)
por_pag_h = gasto_h / paginas
tasa = len(escaladas) / paginas
for nid, n, m in escaladas:
    print(f"  nº {nid} pág. {n}: {m}")
print(f"{len(escaladas)} de {paginas} páginas a Sonnet ({tasa:.0%})")
normal = total_coleccion * (por_pag_h + tasa * por_pag_s)
print(f"Costo por página: Haiku US$ {por_pag_h:.4f}, Sonnet US$ {por_pag_s:.4f}")
print(f"Colección completa ({total_coleccion} págs.): US$ {normal:.0f} a precio normal, US$ {normal / 2:.0f} por lotes")
