"""Recorte por el doblez real: gratis, sin IA. Reanudable.

Uso: python3 recorte_pliego.py CARPETA_DATOS [--procesos 6]

Las imágenes dobles del escaneo (más anchas que altas) se partían por la mitad exacta, y a veces el corte cae sobre una
columna de texto. Acá se busca el doblez real (la franja vertical sin texto más cercana al centro: poca variación
horizontal de tono, sea margen blanco o sombra lisa) y, si se aparta del centro, se vuelve a cortar y a pasar
Tesseract a esas dos páginas.

No toca nada de lo existente: escribe en CARPETA_DATOS/salida-recorte/<nº>/
  recorte.json   {pagina_izquierda: {"x": corte nuevo, "mitad": corte viejo, "ancho": w}, ...}
  pNN.txt/.tsv   OCR nuevo de las páginas recortadas de nuevo
  hecho          marca de número terminado (para reanudar)
Al final escribe salida-recorte/informe.json: páginas cuyo OCR nuevo trae bastante texto que antes caía del otro
lado del corte (candidatas a releer con IA, con el costo estimado).
"""
import glob, json, os, subprocess, sys, tempfile, time
from multiprocessing import Pool
import numpy as np
from PIL import Image

DESVIO_MINIMO = 0.006  # fracción del ancho: menos que esto, el corte viejo estaba bien
COSTO_RELECTURA = 0.04  # US$ por página con Sonnet en lote (medido)


def doblez(im):
    """x del doblez en una imagen doble (gris): la columna de píxeles más tranquila a lo ALTO de toda la página
    (un hueco entre columnas se interrumpe con títulos o fotos que lo cruzan; el doblez no)."""
    a = np.asarray(im, dtype=np.int16)
    h, w = a.shape
    a = a[int(h * 0.04):int(h * 0.96)]
    d = np.abs(np.diff(a, axis=1))
    franjas = np.array_split(d, 40, axis=0)
    e = np.stack([f.mean(axis=0) for f in franjas])  # 40 x w
    k = max(5, w // 250)
    e = np.apply_along_axis(lambda r: np.convolve(r, np.ones(k) / k, mode="same"), 1, e)
    peor = np.percentile(e, 95, axis=0)  # lo más «escrito» que tiene cada columna a lo alto
    lo, hi = int(w * 0.44), int(w * 0.56)
    zona = peor[lo:hi]
    cerca = np.abs(np.arange(lo, hi) - w / 2) / w  # leve preferencia por el centro
    x = int(lo + np.argmin(zona * (1 + 2 * cerca)))
    m = w // 2
    # prudente: solo se mueve si en la mitad exacta hay texto y en el doblez nuevo claramente no
    return x if peor[m - k:m + k].max() > 2.5 * peor[x] else m


def tesseract(im, base):
    with tempfile.TemporaryDirectory() as t:
        png = os.path.join(t, "p.png")
        im.save(png)
        subprocess.run(["tesseract", png, base, "-l", "spa", "--psm", "3", "txt", "tsv"],
                       check=True, capture_output=True, env={**os.environ, "OMP_THREAD_LIMIT": "1"})


def numero(args):
    x, datos = args
    destino = os.path.join(datos, "salida-recorte", x["id"])
    if os.path.exists(os.path.join(destino, "hecho")):
        return x["id"], None
    os.makedirs(destino, exist_ok=True)
    pdf = os.path.join(datos, "rollos", x["archivo"])
    info, n = {}, 1
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdfimages", "-png", pdf, f"{tmp}/i"], check=True)
        for f in sorted(glob.glob(f"{tmp}/i-*.png")):
            im = Image.open(f).convert("L")
            w, h = im.size
            if w < h:
                n += 1
                continue
            xc = doblez(im)
            info[n] = {"x": xc, "mitad": w // 2, "ancho": w}
            if abs(xc - w // 2) > DESVIO_MINIMO * w:
                tesseract(im.crop((0, 0, xc, h)), os.path.join(destino, f"p{n:02d}"))
                tesseract(im.crop((xc, 0, w, h)), os.path.join(destino, f"p{n + 1:02d}"))
            n += 2
    json.dump(info, open(os.path.join(destino, "recorte.json"), "w"))
    open(os.path.join(destino, "hecho"), "w").close()
    return x["id"], sum(1 for v in info.values() if abs(v["x"] - v["mitad"]) > DESVIO_MINIMO * v["ancho"])


def palabras(f):
    if not os.path.exists(f):
        return 0
    return len(open(f, encoding="utf-8", errors="ignore").read().split())


def informe(datos):
    """Páginas donde el OCR nuevo tiene bastante más texto que el viejo (lo que el corte viejo le sacaba)."""
    sal = []
    for d in sorted(glob.glob(os.path.join(datos, "salida-recorte", "*", "recorte.json"))):
        nid = os.path.basename(os.path.dirname(d))
        for f in glob.glob(os.path.join(os.path.dirname(d), "p*.txt")):
            p = os.path.basename(f)[:-4]
            nuevo, viejo = palabras(f), palabras(os.path.join(datos, "salida", nid, p + ".txt"))
            if nuevo - viejo >= 25:
                sal.append({"id": nid, "pagina": int(p[1:]), "palabras_viejo": viejo, "palabras_nuevo": nuevo})
    json.dump({"paginas": sal, "cantidad": len(sal), "costo_relectura_usd": round(len(sal) * COSTO_RELECTURA, 2)},
              open(os.path.join(datos, "salida-recorte", "informe.json"), "w"), ensure_ascii=False, indent=1)
    return len(sal)


if __name__ == "__main__":
    datos = sys.argv[1]
    procesos = int(sys.argv[sys.argv.index("--procesos") + 1]) if "--procesos" in sys.argv else 6
    os.nice(10)
    nums = json.load(open(os.path.join(datos, "lectura-ia", "etapa.json")))["numeros"]
    t0, hechos, movidos = time.time(), 0, 0
    with Pool(procesos) as pool:
        for nid, m in pool.imap_unordered(numero, [(x, datos) for x in nums]):
            hechos += 1
            movidos += m or 0
            if m is not None:
                print(f"{time.strftime('%H:%M')} nº {nid}: {m} pliegos recortados de nuevo "
                      f"({hechos}/{len(nums)}, {(time.time() - t0) / 60:.0f} min)", flush=True)
    print(f"listo: {movidos} pliegos recortados de nuevo; {informe(datos)} páginas para releer", flush=True)
