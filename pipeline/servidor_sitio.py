"""Servidor local del sitio de prueba, que además guarda las notas de revisión.

Uso: python3 servidor_sitio.py CARPETA_SITIO PUERTO ARCHIVO_NOTAS

Sirve los archivos del sitio como http.server y acepta:
  POST /notas  -> agrega la nota (JSON) como una línea en ARCHIVO_NOTAS
  GET  /notas  -> devuelve todas las notas guardadas
Solo escucha en 127.0.0.1: las notas no salen de la Mac.
"""
import json, os, sys
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

CARPETA, PUERTO, NOTAS = sys.argv[1], int(sys.argv[2]), sys.argv[3]
CAMPOS = {"texto", "categoria", "regla_general", "numero", "pagina", "articulo", "titulo", "enlace", "caso"}


class Manejador(SimpleHTTPRequestHandler):
    def _json(self, codigo, datos):
        cuerpo = json.dumps(datos, ensure_ascii=False).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self):
        if self.path.split("?")[0] != "/notas":
            return super().do_GET()
        notas = []
        if os.path.exists(NOTAS):
            notas = [json.loads(l) for l in open(NOTAS, encoding="utf-8") if l.strip()]
        self._json(200, notas)

    def do_POST(self):
        if self.path != "/notas":
            return self._json(404, {"error": "no existe"})
        largo = int(self.headers.get("Content-Length", 0))
        if not 0 < largo < 100_000:
            return self._json(400, {"error": "nota vacía o demasiado larga"})
        try:
            nota = {k: v for k, v in json.loads(self.rfile.read(largo)).items() if k in CAMPOS}
        except ValueError:
            return self._json(400, {"error": "nota inválida"})
        if not str(nota.get("texto", "")).strip():
            return self._json(400, {"error": "la nota no tiene texto"})
        nota["fecha"] = datetime.now().isoformat(timespec="seconds")
        with open(NOTAS, "a", encoding="utf-8") as f:
            f.write(json.dumps(nota, ensure_ascii=False) + "\n")
        self._json(200, {"ok": True, "fecha": nota["fecha"]})

    def end_headers(self):
        # que el navegador pida siempre la versión actual de la página y los datos
        if self.path.split("?")[0] in ("/", "/index.html") or self.path.startswith("/datos/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, formato, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PUERTO), partial(Manejador, directory=CARPETA)).serve_forever()
