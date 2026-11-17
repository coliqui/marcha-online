"""Arma el sitio público de Marcha (estático, sin framework). Gratis: no usa la API.

Uso: python3 armar.py CARPETA_DATOS [--base /]

Lee (sin modificar) lo que ya armó el sitio de prueba en CARPETA_DATOS/lectura-ia/sitio/ (numeros.json, datos de
cada número, referencias.json, tapas/) y las teselas de zoom de CARPETA_DATOS/teselas.noindex/. Escribe el sitio en
CARPETA_DATOS/sitio-final-salida/: lo arma en una carpeta temporal y la cambia de una vez (atómico, se puede repetir).
Las miniaturas webp se guardan aparte (sitio-final-cache/), para no rehacerlas cada vez; las teselas y las miniaturas
entran al sitio como enlaces simbólicos (en el despliegue se copian o se suben a un almacenamiento aparte).

BASE_URL: prefijo de todos los enlaces internos («/» en la Mac; otro si el sitio vive en una subcarpeta).
"""
import html, json, os, re, shutil, sys, tempfile
from concurrent.futures import ProcessPoolExecutor
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autores as AU  # noqa: E402

AQUI = os.path.dirname(os.path.abspath(__file__))
MESES = "enero febrero marzo abril mayo junio julio agosto setiembre octubre noviembre diciembre".split()
BASE = "/"
OSD = '<script src="https://cdnjs.cloudflare.com/ajax/libs/openseadragon/4.1.0/openseadragon.min.js" defer></script>'


def esc(t):
    return html.escape(str(t or ""), quote=True)


def fecha_larga(f):
    a, m, d = f.split("-")
    return f"{int(d)} de {MESES[int(m) - 1]} de {a}"


def u(ruta=""):
    return BASE + ruta


def pagina(titulo, cuerpo, actual="", extra_head="", scripts=()):
    menu = [("numeros/", "Números"), ("autores/", "Autores"), ("buscar/", "Buscar"), ("acerca/", "Acerca del proyecto")]
    nav = "".join(f'<a href="{u(r)}"{" aria-current=\"page\"" if r == actual else ""}>{n}</a>' for r, n in menu)
    js = "".join(f'<script src="{u("static/" + s)}" defer></script>' for s in ("app.js",) + tuple(scripts))
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(titulo)}</title><meta name="description" content="Archivo digital del semanario Marcha (Montevideo, 1939–1974).">
<link rel="stylesheet" href="{u("static/estilo.css")}">{extra_head}
<script>window.BASE={json.dumps(BASE)};</script>{js}</head>
<body><header class="cab"><div class="cab-in"><a class="marca" href="{u()}">MARCHA<span>archivo 1939–1974</span></a>
<button class="menu-btn" aria-label="Menú" aria-expanded="false" aria-controls="menu"><span></span><span></span><span></span></button>
<nav id="menu" class="menu">{nav}</nav></div></header>
<main class="cont">{cuerpo}</main>
<footer class="pie"><div class="pie-in">
<p class="hoy" data-hoy></p>
<p>Marcha © sus autores y herederos. Se permite citar mencionando la fuente.</p>
<p>El texto de las notas fue transcripto automáticamente (reconocimiento de texto e inteligencia artificial) a partir del escaneo de los microfilms y puede contener errores. Ante cualquier duda, vale la imagen de la página.</p>
<p class="pie-links"><span title="Próximamente">Cómo citar</span> · <span title="Próximamente">Avisanos de un error</span> · <span title="Próximamente">Apoyá el archivo</span></p>
</div></footer>
<a href="#" class="arriba" aria-label="Volver arriba" hidden>↑</a>
</body></html>"""


def miniaturas(args):
    """Tapa -> webp chica (grilla) y grande (portada), si no existen."""
    src, chica, grande = args
    try:
        if os.path.exists(chica) and os.path.exists(grande):
            return
        im = Image.open(src).convert("RGB")
        for dest, ancho, q in ((chica, 320, 62), (grande, 900, 70)):
            if not os.path.exists(dest):
                c = im.copy()
                c.thumbnail((ancho, ancho * 2), Image.LANCZOS)
                c.save(dest, "WEBP", quality=q, method=5)
    except Exception as e:
        print("  miniatura fallida", src, e)


def tam_dzi(f):
    t = open(f).read()
    return int(re.search(r'Width="(\d+)"', t).group(1)), int(re.search(r'Height="(\d+)"', t).group(1))


def armar(datos):
    sitio = os.path.join(datos, "lectura-ia", "sitio")
    teselas = os.path.join(datos, "teselas.noindex")
    cache = os.path.join(datos, "sitio-final-cache")
    destino = os.path.join(datos, "sitio-final-salida")
    nums = json.load(open(os.path.join(sitio, "datos", "numeros.json")))
    nums = [m for m in nums if os.path.exists(os.path.join(sitio, "datos", f"{m['id']}.json"))]
    nums.sort(key=lambda m: (m["fecha"], m.get("numero") or 0))
    refs = json.load(open(os.path.join(sitio, "datos", "referencias.json")))

    # miniaturas
    for d in ("tapas", "tapas-g"):
        os.makedirs(os.path.join(cache, d), exist_ok=True)
    trabajos = [(os.path.join(sitio, "tapas", f"{m['id']}.jpg"), os.path.join(cache, "tapas", f"{m['id']}.webp"),
                 os.path.join(cache, "tapas-g", f"{m['id']}.webp")) for m in nums]
    with ProcessPoolExecutor(6) as ex:
        list(ex.map(miniaturas, [t for t in trabajos if os.path.exists(t[0])], chunksize=20))

    tmp = tempfile.mkdtemp(prefix="sitio-final-", dir=datos)
    try:
        shutil.copytree(os.path.join(AQUI, "static"), os.path.join(tmp, "static"))
        os.symlink(teselas, os.path.join(tmp, "teselas"))
        os.symlink(cache, os.path.join(tmp, "img"))
        os.makedirs(os.path.join(tmp, "datos", "numeros"))
        pf = os.path.join(cache, "pagefind")
        if os.path.exists(pf):
            os.symlink(os.path.realpath(pf), os.path.join(tmp, "pagefind"))

        def escribir(ruta, contenido):
            f = os.path.join(tmp, ruta)
            os.makedirs(os.path.dirname(f), exist_ok=True)
            open(f, "w", encoding="utf-8").write(contenido)

        # fechas.json: MM-DD -> números de ese día
        fechas = {}
        for m in nums:
            fechas.setdefault(m["fecha"][5:], []).append({"id": m["id"], "fecha": m["fecha"]})
        escribir("datos/fechas.json", json.dumps(fechas, ensure_ascii=False, separators=(",", ":")))

        # cada número: páginas (con el tamaño de sus teselas) y notas, en un JSON que se carga al abrirlo
        por_anio = {}
        for k, m in enumerate(nums):
            d = json.load(open(os.path.join(sitio, "datos", f"{m['id']}.json")))
            paginas = []
            for p in d["paginas"]:
                f = os.path.join(teselas, m["id"], f"p{p['n']:02d}.dzi")
                w, h = tam_dzi(f) if os.path.exists(f) else (0, 0)
                paginas.append({"n": p["n"], "impresa": p.get("impresa"), "enfrentada": p.get("enfrentada"), "w": w, "h": h})
            arts = []
            for i, a in enumerate(d["articulos"]):
                citas = [{"id": r["destino"], "fecha": r.get("fecha_destino")} for r in refs
                         if r["origen"] == m["id"] and r["articulo"] == i and r.get("resuelta")]
                arts.append({k2: v for k2, v in {"titulo": a["titulo"], "autor": a.get("autor"), "seccion": a.get("seccion"),
                             "tipo": a.get("tipo"), "bajada": a.get("bajada"), "texto": a.get("texto"), "paginas": a["paginas"],
                             "zonas": a.get("zonas"), "citas": citas or None,
                             "recuadros": [{"texto": r.get("texto"), "autor": r.get("autor")} for r in a.get("recuadros") or []] or None,
                             "destacados": [{"texto": r.get("texto")} for r in a.get("destacados") or []] or None}.items() if v})
            citado = [{"id": r["origen"], "fecha": r.get("fecha_origen"), "pagina": r.get("pagina"), "titulo": r.get("titulo")}
                      for r in refs if r.get("destino") == m["id"] and r.get("resuelta")]
            escribir(f"datos/numeros/{m['id']}.json", json.dumps({"id": m["id"], "fecha": m["fecha"], "paginas": paginas,
                     "articulos": arts, "citado_en": citado}, ensure_ascii=False, separators=(",", ":")))
            ant, sig = (nums[k - 1] if k else None), (nums[k + 1] if k + 1 < len(nums) else None)
            anio = m["fecha"][:4]
            por_anio.setdefault(anio, []).append(m)
            flechas = (f'<a class="btn" href="{u("numero/" + ant["id"] + "/")}" aria-label="Número anterior">← Nº {esc(ant["id"])}</a>' if ant else "<span></span>") + \
                      (f'<a class="btn" href="{u("numero/" + sig["id"] + "/")}" aria-label="Número siguiente">Nº {esc(sig["id"])} →</a>' if sig else "<span></span>")
            cuerpo = f"""<div class="num-cab"><a href="{u("numeros/" + anio + "/")}" class="volver">← Volver a los números de {anio}</a>
<h1>Marcha nº {esc(m["id"])}</h1><p class="sub">{fecha_larga(m["fecha"])} · {len(paginas)} páginas</p>
<div class="flechas">{flechas}</div></div>
<div id="lector" data-id="{esc(m["id"])}" data-teselas="{u("teselas/" + m["id"] + "/")}" data-datos="{u("datos/numeros/" + m["id"] + ".json")}">
<div class="recorrer"><button class="btn" data-pag="-1" aria-label="Página anterior">‹</button>
<label>Recorrer: <select id="selpag" aria-label="Ir a la página"></select></label>
<button class="btn" data-pag="1" aria-label="Página siguiente">›</button>
<label class="doble"><input type="checkbox" id="vdoble"> doble página</label></div>
<div class="lector-cuerpo"><div id="visor" class="visor" role="img" aria-label="Páginas del nº {esc(m["id"])}"></div>
<aside class="notas"><h2 id="notas-tit">Notas de esta página</h2><div id="notas"><p class="muted">Cargando…</p></div>
<div id="citado"></div></aside></div></div>"""
            escribir(f"numero/{m['id']}/index.html", pagina(f"Marcha nº {m['id']} · {fecha_larga(m['fecha'])}", cuerpo, "numeros/",
                     extra_head=OSD, scripts=("numero.js",)))

        anios = sorted(por_anio)
        # una página por año: barra fija con los años y las flechas, y la grilla de tapas
        for j, a in enumerate(anios):
            opciones = "".join(f'<option value="{x}"{" selected" if x == a else ""}>{x}</option>' for x in anios)
            prev = f'<a class="btn" href="{u("numeros/" + anios[j - 1] + "/")}">← {anios[j - 1]}</a>' if j else '<span class="btn off">←</span>'
            nxt = f'<a class="btn" href="{u("numeros/" + anios[j + 1] + "/")}">{anios[j + 1]} →</a>' if j + 1 < len(anios) else '<span class="btn off">→</span>'
            grilla = "".join(f'<a class="tapa" href="{u("numero/" + m["id"] + "/")}"><img src="{u("img/tapas/" + m["id"] + ".webp")}" '
                             f'alt="Tapa del nº {esc(m["id"])}" loading="lazy" width="320" height="450"><b>Nº {esc(m["id"])}</b>'
                             f'<span>{fecha_larga(m["fecha"])[:-8]}</span></a>' for m in por_anio[a])
            cuerpo = f"""<div class="barra-anios"><a class="btn todos" href="{u("numeros/")}">Todos</a>{prev}
<select class="sel-anio" aria-label="Elegir año">{opciones}</select>{nxt}</div>
<h1>{a} <span class="muted">· {len(por_anio[a])} números</span></h1><div class="grilla">{grilla}</div>"""
            escribir(f"numeros/{a}/index.html", pagina(f"Marcha en {a}", cuerpo, "numeros/"))

        # /numeros/: los años agrupados por década, cada uno con su primera tapa
        decadas = {}
        for a in anios:
            decadas.setdefault(a[:3] + "0", []).append(a)
        bloques = "".join(f'<h2>Década de {d}</h2><div class="grilla anios">' + "".join(
            f'<a class="tapa" href="{u("numeros/" + a + "/")}"><img src="{u("img/tapas/" + por_anio[a][0]["id"] + ".webp")}" alt="" loading="lazy" width="320" height="450">'
            f'<b>{a}</b><span>{len(por_anio[a])} números</span></a>' for a in ans) + "</div>" for d, ans in decadas.items())
        cuerpo = f"""<h1>Los {len(nums):,} números</h1><p class="muted">Del nº 1 (23 de junio de 1939) al último, clausurado en 1974. Elegí un año.</p>
<div class="barra-anios"><select class="sel-anio" aria-label="Elegir año"><option value="">Ir al año…</option>{"".join(f'<option value="{a}">{a}</option>' for a in anios)}</select></div>{bloques}""".replace(",", ".", 1)
        escribir("numeros/index.html", pagina("Los números de Marcha", cuerpo, "numeros/"))

        # portada: la Marcha de este día (elegida en el navegador, con la fecha de Montevideo)
        cuerpo = f"""<section class="hoy-portada" id="portada"><h1 id="portada-tit">Marcha, un día como hoy</h1>
<div class="portada-cuerpo"><a id="portada-link" class="portada-grande" href="{u("numeros/")}"><img id="portada-img" alt="" width="900" height="1270"></a>
<div class="portada-info"><p id="portada-dato" class="sub"></p><p><a id="portada-leer" class="btn grande" href="{u("numeros/")}">Leer este número</a></p>
<div id="portada-otras" class="otras"></div>
<p class="muted presenta">Marcha fue el semanario que Carlos Quijano fundó y dirigió en Montevideo de 1939 a 1974. Este archivo reúne los {len(nums):,} números, página por página, con el texto de cada nota.</p>
<p><a href="{u("numeros/")}">Ver todos los números →</a></p></div></div></section>""".replace(f"{len(nums):,}", f"{len(nums):,}".replace(",", "."))
        escribir("index.html", pagina("Marcha · archivo del semanario (1939–1974)", cuerpo, "", scripts=("portada.js",)))

        prueba = '<p>Mientras tanto, en la Mac, se puede usar la versión de prueba: <a href="http://localhost:8767/">localhost:8767</a>.</p>'
        # autores y dibujantes: una lista (datos/autores.json, la dibuja autores.js) y una página por persona
        gente, colectivas, descartes, _ = AU.armar(os.path.join(datos, "lectura-ia", "marcha.sqlite"))
        lista = [[x["mostrar"], x["slug"], len(x["textos"]), len(x["obras"]), x["clave"]] + ([1] if c else [])
                 for c, xs in ((0, gente), (1, colectivas)) for x in xs]
        escribir("datos/autores.json", json.dumps(lista, ensure_ascii=False, separators=(",", ":")))
        letras = "".join(f'<button type="button" data-l="{l}">{l}</button>' for l in "ABCDEFGHIJKLMNÑOPQRSTUVWXYZ")
        cuerpo = f"""<h1>Autores y dibujantes</h1>
<p class="muted">{f"{len(gente):,}".replace(",", ".")} personas y seudónimos que firmaron textos o dibujos en Marcha, por apellido. Las firmas de una misma persona
(iniciales, seudónimos, errores de lectura) se agrupan con una tabla que se sigue revisando; las siglas que no se sabe de quién son no se listan.</p>
<div class="aut-barra"><input type="search" id="aut-filtro" placeholder="Filtrar por nombre…" aria-label="Filtrar autores por nombre" autocomplete="off">
<div class="aut-orden" role="group" aria-label="Orden"><button type="button" class="btn" data-orden="alfa" aria-pressed="true">A–Z</button><button type="button" class="btn" data-orden="cant" aria-pressed="false">Por cantidad</button></div>
<label class="doble"><input type="checkbox" id="aut-colectivas"> incluir firmas colectivas ({f"{len(colectivas):,}".replace(",", ".")})</label></div>
<nav class="az" aria-label="Índice alfabético">{letras}</nav>
<p id="aut-info" class="muted"></p><ul id="aut-lista" class="aut-lista"><li class="muted">Cargando…</li></ul>
<p><button type="button" class="btn" id="aut-mas" hidden>Mostrar más</button></p>"""
        escribir("autores/index.html", pagina("Autores y dibujantes · Marcha", cuerpo, "autores/", scripts=("autores.js",)))
        fechas_n = {m["id"]: m["fecha"] for m in nums}
        for x in gente + colectivas:
            def item(nid, pag, tit, extra):
                f = fechas_n.get(nid, "")
                return (f'<li><a href="{u("numero/" + nid + "/")}#p{pag}">{esc(tit) or "(sin título)"}</a>'
                        f'<span class="muted">Nº {esc(nid)}{" · " + fecha_larga(f) if f else ""} · pág. {pag}{" · " + esc(extra) if extra else ""}</span></li>')
            textos = "".join(item(nid, pag, tit, sec) for _, nid, pag, tit, sec in x["textos"])
            obras = "".join(item(nid, n, desc[:120] or tipo, tipo if desc else "") for _, nid, n, tipo, desc in x["obras"])
            nt, no = len(x["textos"]), len(x["obras"])
            cuenta = " y ".join(t for t in (f"{nt} {'texto' if nt == 1 else 'textos'}" if nt else "", f"{no} {'dibujo' if no == 1 else 'dibujos'}" if no else "") if t)
            vistas, formas = {AU.plano(x["nombre"])}, []
            for f in sorted(x["firmas"], key=lambda f: (f.isupper(), f)):
                if AU.plano(AU.TITULOS.sub("", f)) not in vistas:
                    vistas.add(AU.plano(AU.TITULOS.sub("", f))); formas.append(f)
            otras = f" · también firma como {', '.join(esc(f) for f in formas[:12])}" if formas else ""
            periodo = f" · {x['desde']}{'–' + x['hasta'] if x['hasta'] != x['desde'] else ''}" if x["desde"] else ""
            cuerpo = (f'<p><a href="{u("autores/")}">← Autores y dibujantes</a></p><h1>{esc(AU.lindo(x["nombre"]))}</h1>'
                      f'<p class="muted">{cuenta}{periodo}{otras}</p>'
                      + (f'<h2>Textos</h2><ul class="aut-textos">{textos}</ul>' if textos else "")
                      + (f'<h2>Dibujos</h2><ul class="aut-textos">{obras}</ul>' if obras else ""))
            escribir(f"autores/{x['slug']}/index.html", pagina(f"{AU.lindo(x['nombre'])} · Marcha", cuerpo, "autores/"))
        cuerpo = """<h1>Buscar</h1>
<form id="busca" class="busca" role="search"><input type="search" id="q" name="q" placeholder="Buscar en el texto de las notas…" aria-label="Buscar" autocomplete="off">
<button class="btn" type="submit">Buscar</button></form>
<p class="muted ayuda">Primero aparecen las notas con la frase exacta y después las que tienen todas las palabras.</p>
<details id="avanzada" class="avanzada"><summary class="btn">Búsqueda avanzada</summary><div class="av-campos">
<label>Tema <select id="f-tema"><option value="">Todos</option></select></label>
<label>Sección <input id="f-seccion" list="l-seccion" placeholder="Todas" autocomplete="off"><datalist id="l-seccion"></datalist></label>
<label>Autor <input id="f-autor" list="l-autor" placeholder="Apellido, Nombre" autocomplete="off"><datalist id="l-autor"></datalist></label>
<span class="anios"><label>Desde <select id="f-desde"></select></label><label>Hasta <select id="f-hasta"></select></label></span>
<p><button class="btn" type="submit" form="busca">Aplicar</button> <button class="btn" type="button" id="limpiar">Limpiar filtros</button></p></div></details>
<p id="estado" class="muted" aria-live="polite"></p><ol id="resultados" class="resultados"></ol>
<p><button type="button" class="btn" id="mas" hidden>Más resultados</button></p>"""
        escribir("buscar/index.html", pagina("Buscar · Marcha", cuerpo, "buscar/", scripts=("buscar.js",)))
        escribir("acerca/index.html", pagina("Acerca del proyecto · Marcha", """<h1>Acerca del proyecto</h1>
<p><b>Borrador.</b> Marcha fue un semanario uruguayo fundado y dirigido por Carlos Quijano, publicado en Montevideo desde el 23 de junio de 1939 hasta su clausura en 1974.</p>
<p>Este archivo reúne la colección completa a partir del escaneo de los microfilms. Cada página se puede ver con zoom, y el texto de las notas fue transcripto con reconocimiento de texto e inteligencia artificial, por lo que puede contener errores.</p>
<p>Próximamente: qué fue Marcha, Carlos Quijano, cómo se hizo este archivo, bibliografía, cómo citar y cómo ayudar.</p>""", "acerca/"))

        viejo = destino + ".viejo"
        if os.path.exists(viejo):
            shutil.rmtree(viejo)
        if os.path.exists(destino):
            os.rename(destino, viejo)
        os.rename(tmp, destino)
        if os.path.exists(viejo):
            shutil.rmtree(viejo)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    print(f"sitio armado en {destino}: {len(nums)} números, {len(anios)} años")


if __name__ == "__main__":
    if "--base" in sys.argv:
        BASE = sys.argv[sys.argv.index("--base") + 1]
    armar(sys.argv[1])
