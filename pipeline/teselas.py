"""Teselas de zoom profundo (Deep Zoom, .dzi) de cada página, para el visor del sitio final. Gratis, reanudable.

Uso: python3 teselas.py CARPETA_DATOS [--procesos 6]

Corta las páginas como el resto del archivo; si salida-recorte/<nº>/recorte.json ya tiene el doblez real de un
pliego, usa ese corte. Escribe CARPETA_DATOS/teselas.noindex/<nº>/pNN.dzi y pNN_files/<nivel>/<col>_<fila>.jpg
(254 px con 1 de solapamiento, JPEG 80), y una marca «hecho» por número. El log dice tamaño y ritmo.
"""
import glob, json, math, os, subprocess, sys, tempfile, time
from multiprocessing import Pool
from PIL import Image

TAM, SOLAPA, CALIDAD = 254, 1, 80


def dzi(im, base):
    w, h = im.size
    niveles = math.ceil(math.log2(max(w, h))) + 1
    for nivel in range(niveles - 1, -1, -1):
        esc = 2 ** (niveles - 1 - nivel)
        iw, ih = max(1, math.ceil(w / esc)), max(1, math.ceil(h / esc))
        cap = im if esc == 1 else im.resize((iw, ih), Image.LANCZOS)
        d = f"{base}_files/{nivel}"
        os.makedirs(d, exist_ok=True)
        for c in range(math.ceil(iw / TAM)):
            for f in range(math.ceil(ih / TAM)):
                x0, y0 = max(0, c * TAM - SOLAPA), max(0, f * TAM - SOLAPA)
                x1, y1 = min(iw, (c + 1) * TAM + SOLAPA), min(ih, (f + 1) * TAM + SOLAPA)
                cap.crop((x0, y0, x1, y1)).save(f"{d}/{c}_{f}.jpg", quality=CALIDAD)
    open(base + ".dzi", "w").write(
        f'<?xml version="1.0" encoding="UTF-8"?><Image xmlns="http://schemas.microsoft.com/deepzoom/2008" '
        f'Format="jpg" Overlap="{SOLAPA}" TileSize="{TAM}"><Size Width="{w}" Height="{h}"/></Image>')


def numero(args):
    x, datos = args
    destino = os.path.join(datos, "teselas.noindex", x["id"])
    if os.path.exists(os.path.join(destino, "hecho")):
        return x["id"], None
    os.makedirs(destino, exist_ok=True)
    rc = os.path.join(datos, "salida-recorte", x["id"], "recorte.json")
    cortes = json.load(open(rc)) if os.path.exists(rc) else {}
    n = 1
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdfimages", "-png", os.path.join(datos, "rollos", x["archivo"]), f"{tmp}/i"], check=True)
        for f in sorted(glob.glob(f"{tmp}/i-*.png")):
            im = Image.open(f).convert("L")
            w, h = im.size
            if w < h:
                paginas = [im]
            else:
                xc = cortes.get(str(n), {}).get("x", w // 2)
                paginas = [im.crop((0, 0, xc, h)), im.crop((xc, 0, w, h))]
            for p in paginas:
                dzi(p, os.path.join(destino, f"p{n:02d}"))
                n += 1
    open(os.path.join(destino, "hecho"), "w").close()
    return x["id"], n - 1


def du(d):
    return int(subprocess.run(["du", "-sk", d], capture_output=True, text=True).stdout.split()[0]) / 1e6


if __name__ == "__main__":
    datos = sys.argv[1]
    procesos = int(sys.argv[sys.argv.index("--procesos") + 1]) if "--procesos" in sys.argv else 6
    os.nice(10)
    nums = json.load(open(os.path.join(datos, "lectura-ia", "etapa.json")))["numeros"]
    t0, hechos = time.time(), 0
    with Pool(procesos) as pool:
        for nid, pags in pool.imap_unordered(numero, [(x, datos) for x in nums]):
            hechos += 1
            if pags is not None and hechos % 10 == 0:
                gb = du(os.path.join(datos, "teselas.noindex"))
                print(f"{time.strftime('%H:%M')} {hechos}/{len(nums)} números; {gb:.1f} GB; "
                      f"{(time.time() - t0) / 60:.0f} min", flush=True)
    print(f"listo: {du(os.path.join(datos, 'teselas.noindex')):.1f} GB", flush=True)
