"""Arma el catálogo SQLite del archivo a partir del OCR y de la lectura con IA.

Uso: python3 armar_catalogo.py CARPETA_OCR CARPETA_IA ARCHIVO.sqlite

El catálogo es la fuente de verdad del sitio: números, páginas, artículos, autores con sus
seudónimos y obras gráficas, más índices de búsqueda de texto completo que ignoran tildes y
mayúsculas. Se puede regenerar entero en cualquier momento; las correcciones hechas a mano
(seudónimos, por ahora) viven en autores_alias.json.
Esquema explicado en catalogo_esquema.md.
"""
import glob, json, os, re, sqlite3, sys, unicodedata

AQUI = os.path.dirname(os.path.abspath(__file__))
HUMOR = {"caricatura", "chiste", "historieta"}

ESQUEMA = """
CREATE TABLE numeros (
  id TEXT PRIMARY KEY,            -- identificador del número, como en el nombre del PDF ("1477", "Chasque1")
  numero INTEGER,                 -- número impreso, si es numérico
  fecha TEXT NOT NULL,            -- AAAA-MM-DD
  rollo TEXT,                     -- rollo de microfilm ("Rollo 71")
  archivo TEXT,                   -- PDF original, relativo a la carpeta de rollos
  paginas INTEGER NOT NULL,
  leido_ia INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE paginas (
  numero_id TEXT NOT NULL REFERENCES numeros(id),
  n INTEGER NOT NULL,             -- orden en el escaneo (1..paginas)
  impresa INTEGER,                -- número de página impreso, si se ve
  imagen TEXT,                    -- imagen web, relativa al sitio
  imagen_hd TEXT,                 -- imagen en alta resolución, para el zoom
  lectura TEXT,                   -- haiku | sonnet | tesseract (sin IA)
  legibilidad TEXT,
  partes_sin_leer INTEGER,
  bloqueada INTEGER NOT NULL DEFAULT 0,  -- la IA no la transcribió por derechos de autor
  notas TEXT,
  texto_ocr TEXT,                 -- texto de Tesseract, sin corregir
  PRIMARY KEY (numero_id, n)
);
CREATE TABLE autores (
  id INTEGER PRIMARY KEY,
  nombre TEXT NOT NULL UNIQUE,    -- nombre canónico ("Peloduro (Julio E. Suárez)")
  revisado INTEGER NOT NULL DEFAULT 0   -- 1 si viene de la tabla de seudónimos revisada a mano
);
CREATE TABLE seudonimos (
  forma TEXT NOT NULL,            -- como aparece firmado ("J. E. SUAREZ")
  clave TEXT PRIMARY KEY,         -- forma normalizada: sin tildes, mayúsculas, puntuación ni años
  autor_id INTEGER NOT NULL REFERENCES autores(id)
);
CREATE TABLE articulos (
  id INTEGER PRIMARY KEY,
  numero_id TEXT NOT NULL REFERENCES numeros(id),
  titulo TEXT NOT NULL,
  firma TEXT,                     -- tal como está impresa
  autor_id INTEGER REFERENCES autores(id),
  seccion TEXT,
  tipo TEXT,                      -- editorial, nota, crítica, cuento, aviso, … ; "ocr" = página sin lectura IA
  temas TEXT,                     -- lista JSON
  texto TEXT,
  union_sin_marca INTEGER NOT NULL DEFAULT 0  -- partes unidas sin marca "pasa a pág.", a revisar
);
CREATE TABLE articulo_paginas (
  articulo_id INTEGER NOT NULL REFERENCES articulos(id),
  numero_id TEXT NOT NULL,
  n INTEGER NOT NULL,
  orden INTEGER NOT NULL,
  PRIMARY KEY (articulo_id, orden),
  FOREIGN KEY (numero_id, n) REFERENCES paginas(numero_id, n)
);
CREATE TABLE obras_graficas (
  id INTEGER PRIMARY KEY,
  numero_id TEXT NOT NULL,
  n INTEGER NOT NULL,
  tipo TEXT,                      -- dibujo, caricatura, chiste, historieta, grabado, viñeta
  descripcion TEXT,
  firma TEXT,                     -- firma del dibujante, tal como está
  autor_id INTEGER REFERENCES autores(id),
  retratado TEXT,                 -- a quién muestra, si se sabe
  caja TEXT,                      -- [x0, y0, x1, y1] en milésimas de la página
  imagen TEXT,                    -- recorte, relativo al sitio
  FOREIGN KEY (numero_id, n) REFERENCES paginas(numero_id, n)
);
CREATE TABLE referencias (
  numero_id TEXT NOT NULL REFERENCES numeros(id),   -- dónde aparece la cita
  pagina INTEGER,
  titulo_articulo TEXT,
  tipo TEXT,                      -- numero | anterior | fecha (por ahora); titulo | autor más adelante
  cita TEXT,                      -- la frase tal como está
  contexto TEXT,
  destino_id TEXT REFERENCES numeros(id),   -- el número citado; NULL si todavía no se resolvió
  resuelta INTEGER NOT NULL
);
CREATE INDEX articulos_autor ON articulos(autor_id);
CREATE INDEX obras_autor ON obras_graficas(autor_id);
CREATE INDEX articulo_paginas_pagina ON articulo_paginas(numero_id, n);

-- Búsqueda de texto completo, sin tildes ni mayúsculas ("cortazar" encuentra "Cortázar")
CREATE VIRTUAL TABLE buscar_articulos USING fts5(titulo, firma, autor, seccion, temas, texto,
  tokenize = 'unicode61 remove_diacritics 2');
CREATE VIRTUAL TABLE buscar_obras USING fts5(descripcion, firma, autor, retratado,
  tokenize = 'unicode61 remove_diacritics 2');
CREATE VIRTUAL TABLE buscar_paginas USING fts5(texto_ocr,
  tokenize = 'unicode61 remove_diacritics 2');
"""


def clave(firma):
    """"J. E. SUÁREZ" -> "j e suarez"; "Camnitzer 1969" -> "camnitzer"."""
    s = unicodedata.normalize("NFD", firma or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    return " ".join(t for t in re.split(r"[^a-z0-9ñ]+", s) if t and not t.isdigit())


def main(ocr, ia, destino):
    if os.path.exists(destino):
        os.remove(destino)
    db = sqlite3.connect(destino)
    db.executescript(ESQUEMA)

    # Autores de la tabla de seudónimos revisada a mano
    def autor(nombre, revisado=0):
        db.execute("INSERT OR IGNORE INTO autores (nombre, revisado) VALUES (?, ?)", (nombre, revisado))
        return db.execute("SELECT id FROM autores WHERE nombre = ?", (nombre,)).fetchone()[0]

    def seudonimo(forma, autor_id):
        db.execute("INSERT OR IGNORE INTO seudonimos (forma, clave, autor_id) VALUES (?, ?, ?)",
                   (forma, clave(forma), autor_id))

    for canon, formas in json.load(open(os.path.join(AQUI, "autores_alias.json"))).items():
        if canon.startswith("_"):
            continue
        aid = autor(canon, 1)
        for f in [canon] + formas:
            seudonimo(f, aid)

    def autor_de(firma):
        """Firma -> autor; una firma nueva crea un autor con ese nombre, sin revisar."""
        if not firma or "ilegible" in firma.lower() or not clave(firma):
            return None
        fila = db.execute("SELECT autor_id FROM seudonimos WHERE clave = ?", (clave(firma),)).fetchone()
        if fila:
            return fila[0]
        aid = autor(firma)
        seudonimo(firma, aid)
        return aid

    nombre_autor = lambda aid: db.execute("SELECT nombre FROM autores WHERE id = ?", (aid,)).fetchone()[0] if aid else ""

    for f in sorted(glob.glob(os.path.join(ocr, "*", "numero.json"))):
        m = json.load(open(f))
        con_ia = os.path.exists(os.path.join(ia, m["id"], "articulos.json"))
        db.execute("INSERT INTO numeros VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (m["id"], m["numero"], m["fecha"], m.get("rollo"), m.get("archivo"), m["paginas"], int(con_ia)))
        for n in range(1, m["paginas"] + 1):
            txt = os.path.join(ocr, m["id"], f"p{n:02d}.txt")
            texto_ocr = open(txt, encoding="utf-8").read() if os.path.exists(txt) else ""
            p = {}
            if con_ia:
                p = json.load(open(os.path.join(ia, m["id"], f"p{n:02d}.json")))
            db.execute("INSERT INTO paginas VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (m["id"], n, p.get("pagina_impresa"), f"paginas/{m['id']}/p{n:02d}.jpg",
                        f"paginas/{m['id']}/p{n:02d}-hd.jpg", p.get("modelo", "tesseract"), p.get("legibilidad"),
                        p.get("partes_sin_leer"), int(p.get("bloqueada", False)), p.get("notas"), texto_ocr))
            db.execute("INSERT INTO buscar_paginas (rowid, texto_ocr) VALUES "
                       "((SELECT rowid FROM paginas WHERE numero_id = ? AND n = ?), ?)", (m["id"], n, texto_ocr))
            # Archivo gráfico: dibujos firmados y humor gráfico
            for k, il in enumerate(p.get("ilustraciones", []), 1):
                if not (il.get("firma") or il.get("tipo") in HUMOR):
                    continue
                aid = autor_de(il.get("firma"))
                cur = db.execute("INSERT INTO obras_graficas (numero_id, n, tipo, descripcion, firma, autor_id, retratado, "
                                 "caja, imagen) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                 (m["id"], n, il["tipo"], il["descripcion"], il.get("firma"), aid, il.get("retratado"),
                                  json.dumps(il["caja"]), f"ilustraciones/{m['id']}-p{n:02d}-{k}.jpg"))
                db.execute("INSERT INTO buscar_obras (rowid, descripcion, firma, autor, retratado) VALUES (?, ?, ?, ?, ?)",
                           (cur.lastrowid, il["descripcion"], il.get("firma"), nombre_autor(aid), il.get("retratado")))
            # Páginas sin lectura con IA: su texto de OCR entra como un artículo más, marcado "ocr"
            if con_ia and p.get("modelo") == "tesseract":
                guardar_articulo(db, m["id"], {"titulo": f"Texto de la página {n} (OCR automático, sin revisar)",
                                               "tipo": "ocr", "temas": [], "texto": texto_ocr, "paginas": [n]},
                                 None, "")

        if con_ia:
            for a in json.load(open(os.path.join(ia, m["id"], "articulos.json")))["articulos"]:
                aid = autor_de(a.get("autor"))
                guardar_articulo(db, m["id"], a, aid, nombre_autor(aid))

    refs = os.path.join(ia, "referencias.json")
    for r in (json.load(open(refs)) if os.path.exists(refs) else []):
        db.execute("INSERT INTO referencias VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (r["origen"], r["pagina"], r["titulo"], r["tipo"],
                   r["cita"], r["contexto"], r["destino"], int(r["resuelta"])))
    db.commit()
    for tabla in ("numeros", "paginas", "articulos", "autores", "seudonimos", "obras_graficas", "referencias"):
        print(f"{tabla}: {db.execute(f'SELECT count(*) FROM {tabla}').fetchone()[0]}")
    db.execute("VACUUM")
    db.close()


def guardar_articulo(db, nid, a, aid, nombre):
    cur = db.execute("INSERT INTO articulos (numero_id, titulo, firma, autor_id, seccion, tipo, temas, texto, union_sin_marca) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (nid, a["titulo"], a.get("autor"), aid, a.get("seccion"), a.get("tipo"),
                      json.dumps(a.get("temas", []), ensure_ascii=False), a["texto"], int(bool(a.get("union_sin_marca")))))
    for orden, n in enumerate(a["paginas"], 1):
        db.execute("INSERT INTO articulo_paginas VALUES (?, ?, ?, ?)", (cur.lastrowid, nid, n, orden))
    db.execute("INSERT INTO buscar_articulos (rowid, titulo, firma, autor, seccion, temas, texto) VALUES (?, ?, ?, ?, ?, ?, ?)",
               (cur.lastrowid, a["titulo"], a.get("autor"), nombre, a.get("seccion"), " ".join(a.get("temas", [])), a["texto"]))


if __name__ == "__main__":
    main(*sys.argv[1:4])
