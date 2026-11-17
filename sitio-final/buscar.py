"""Índice de búsqueda del sitio final con Pagefind (un documento por nota). Gratis, sin IA.

Uso: python3 buscar.py CARPETA_DATOS SALIDA_PAGEFIND  (normalmente SALIDA = CARPETA_DATOS/sitio-final-cache/pagefind) [--desde 1960 --hasta 1969] [--base /]

Cada nota: título, autor y texto; filtros de autor, tema (los ~40 temas generales), sección y año; metadatos para el
resultado (nº, fecha, página, enlace al visor). Las páginas sin transcripción de la IA (bloqueadas por derechos o
sin lectura) entran con el texto de Tesseract de la página, para que se encuentren, sin mostrarse enteras.
"""
import html, json, os, shutil, sqlite3, subprocess, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autores as AU  # noqa: E402

BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".venv-ia", "lib", "python3.12", "site-packages",
                   "pagefind_bin_extended", "pagefind_extended")  # el binario que trae el paquete pagefind
MESES = "enero febrero marzo abril mayo junio julio agosto setiembre octubre noviembre diciembre".split()


def fecha_larga(f):
    a, m, d = f.split("-")
    return f"{int(d)} de {MESES[int(m) - 1]} de {a}"


def temas_generales(lectura):
    """forma escrita del tema -> tema general (o el tema unificado si no tiene general)."""
    mapa = json.load(open(os.path.join(lectura, "temas-mapa.json")))
    j = json.load(open(os.path.join(lectura, "temas.json")))
    padre = {}
    for g in j["generales"]:
        padre[g["tema"]] = g["tema"]
        for s in g["subtemas"]:
            padre[s["tema"]] = g["tema"]
    return {forma: padre.get(u) for forma, u in mapa.items()}


def indexar(datos, salida, desde=None, hasta=None, base="/"):
    """Escribe un HTML chico por nota en una carpeta temporal y corre el binario de Pagefind sobre ella."""
    lectura = os.path.join(datos, "lectura-ia")
    db = sqlite3.connect(os.path.join(lectura, "marcha.sqlite"))
    general = temas_generales(lectura)
    por_articulo = {}
    AU.armar(os.path.join(lectura, "marcha.sqlite"), por_articulo=por_articulo)  # los mismos nombres que la página Autores
    filtro = ""
    if desde:
        filtro = f" and n.fecha >= '{int(desde)}-01-01' and n.fecha <= '{int(hasta)}-12-31'"
    t0, cuenta = time.time(), 0
    tmp = tempfile.mkdtemp(prefix="pagefind-docs-", dir=os.path.dirname(salida.rstrip("/")))
    e = lambda t: html.escape(str(t or ""), quote=True)

    def doc(nombre, enlace, titulo, cuerpo, meta, filtros):
        nonlocal cuenta
        ms = "".join(f'<span data-pagefind-meta="{k}">{e(v)}</span>' for k, v in meta.items() if v)
        fs = "".join(f'<span data-pagefind-filter="{k}">{e(v)}</span>' for k, vs in filtros.items() for v in vs)
        d = os.path.join(tmp, nombre[:3])
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, nombre + ".html"), "w", encoding="utf-8").write(
            f'<!doctype html><html lang="es"><head><meta charset="utf-8"><title>{e(titulo)}</title></head><body>'
            f'<main data-pagefind-body><h1 data-pagefind-meta="title">{e(titulo)}</h1>{ms}<div hidden>{fs}</div>'
            f'<span hidden data-pagefind-meta="enlace">{e(enlace)}</span><p>{e(cuerpo)}</p></main></body></html>')
        cuenta += 1

    try:
        sql = f"""select r.id, r.numero_id, r.titulo, r.firma, r.seccion, r.tipo, r.temas, r.texto, n.fecha,
                         (select min(p.n) from articulo_paginas p where p.articulo_id = r.id)
                  from articulos r join numeros n on n.id = r.numero_id
                  where r.tipo not in ('aviso', 'ocr') and length(coalesce(r.texto, '')) > 40 {filtro}"""
        for aid, nid, titulo, firma, seccion, tipo, temas, texto, fecha, pag in db.execute(sql):
            pag = pag or 1
            filtros = {"año": [fecha[:4]]}
            if por_articulo.get(aid):
                filtros["autor"] = por_articulo[aid]
            ts = sorted({general.get(t) for t in json.loads(temas or "[]")} - {None})
            if ts:
                filtros["tema"] = ts
            if seccion:
                filtros["sección"] = [seccion]
            doc(f"a{aid}", f"{base}numero/{nid}/#p{pag}-a{aid}", titulo or "(sin título)", f"{firma or ''}. {texto}",
                {"autor": firma or "", "fecha": fecha_larga(fecha), "numero": nid, "pagina": pag, "tipo": tipo or ""}, filtros)
        # páginas sin lectura de la IA: el texto de Tesseract, por página
        sql = f"""select p.numero_id, p.n, p.texto_ocr, n.fecha from paginas p join numeros n on n.id = p.numero_id
                  where (p.bloqueada = 1 or p.lectura = 'tesseract') and length(coalesce(p.texto_ocr, '')) > 80 {filtro}"""
        for nid, n, texto, fecha in db.execute(sql):
            doc(f"o{nid}-{n}", f"{base}numero/{nid}/#p{n}", f"Página {n} (texto sin corregir)", texto,
                {"fecha": fecha_larga(fecha), "numero": nid, "pagina": n, "tipo": "ocr"}, {"año": [fecha[:4]]})
        t1 = time.time()
        nueva = salida.rstrip("/") + ".nuevo"
        shutil.rmtree(nueva, ignore_errors=True)
        subprocess.run([BIN, "--site", tmp, "--output-path", nueva, "--force-language", "es", "--quiet"], check=True)
        if os.path.exists(salida):
            shutil.rmtree(salida)
        os.rename(nueva, salida)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return cuenta, t1 - t0, time.time() - t1


if __name__ == "__main__":
    a = sys.argv
    desde = a[a.index("--desde") + 1] if "--desde" in a else None
    hasta = a[a.index("--hasta") + 1] if "--hasta" in a else None
    base = a[a.index("--base") + 1] if "--base" in a else "/"
    n, s1, s2 = indexar(a[1], a[2], desde, hasta, base)
    print(f"{n} documentos: preparados en {s1 / 60:.1f} min, indexados en {s2 / 60:.1f} min")
