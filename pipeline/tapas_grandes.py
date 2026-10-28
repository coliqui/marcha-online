"""Tapas en buena resolución, sacadas del escaneo original de cada número.

Uso: python3 tapas_grandes.py CARPETA_ROLLOS CARPETA_TAPAS [trabajos]

Deja CARPETA_TAPAS/<id>.jpg (480 px de ancho, para la grilla) y
CARPETA_TAPAS/grande/<id>.jpg (1600 px de ancho, para verla entera).
Si la primera imagen del PDF es una página doble, la tapa es la mitad derecha.
"""
import glob, os, subprocess, sys, tempfile
from concurrent.futures import ProcessPoolExecutor

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ocr_numero import metadatos

CHICA, GRANDE = 480, 1600


def tapa(args):
    pdf, carpeta = args
    nid = metadatos(pdf)["id"]
    chica, grande = os.path.join(carpeta, f"{nid}.jpg"), os.path.join(carpeta, "grande", f"{nid}.jpg")
    if os.path.exists(chica) and os.path.exists(grande):
        return nid
    with tempfile.TemporaryDirectory() as tmp:
        # -all deja cada imagen en su formato original (JPEG casi siempre), sin recomprimir
        subprocess.run(["pdfimages", "-all", "-f", "1", "-l", "1", pdf, f"{tmp}/i"], check=True)
        archivos = glob.glob(f"{tmp}/i-*")
        im = Image.open(max(archivos, key=os.path.getsize)).convert("L")
    w, h = im.size
    if w > h:
        im = im.crop((w // 2, 0, w, h))
    for destino, ancho in ((grande, GRANDE), (chica, CHICA)):
        copia = im.copy()
        copia.thumbnail((ancho, ancho * 3), Image.LANCZOS)
        copia.save(destino, quality=80, optimize=True, progressive=True)
    return nid


if __name__ == "__main__":
    rollos, carpeta = sys.argv[1], sys.argv[2]
    trabajos = int(sys.argv[3]) if len(sys.argv) > 3 else os.cpu_count()
    os.makedirs(os.path.join(carpeta, "grande"), exist_ok=True)
    pdfs = sorted(glob.glob(os.path.join(rollos, "**", "*.pdf"), recursive=True))
    with ProcessPoolExecutor(trabajos) as ex:
        for i, _ in enumerate(ex.map(tapa, [(p, carpeta) for p in pdfs], chunksize=8), 1):
            if i % 200 == 0:
                print(f"{i}/{len(pdfs)}", flush=True)
    print(f"{len(pdfs)} tapas")
