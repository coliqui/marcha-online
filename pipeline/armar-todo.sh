#!/bin/zsh
# Arma el sitio de prueba y el catálogo con todos los números leídos (corre solo, independiente de la sesión).
eval "$(/opt/homebrew/bin/brew shellenv)"
D="$HOME/Desktop/MARCHA ONLINE"; cd "$HOME/marcha-online"
cp sitio-prueba-3/index.html "$D/lectura-ia/sitio/"
.venv-ia/bin/python pipeline/referencias.py "$D/salida" "$D/lectura-ia" && \
.venv-ia/bin/python pipeline/armar_sitio_prueba.py "$D/rollos" "$D/salida" "$D/lectura-ia" "$D/lectura-ia/sitio" && \
.venv-ia/bin/python pipeline/proponer_uniones.py "$D/lectura-ia" && \
.venv-ia/bin/python pipeline/armar_sitio_prueba.py "$D/rollos" "$D/salida" "$D/lectura-ia" "$D/lectura-ia/sitio" > /dev/null && \
.venv-ia/bin/python pipeline/temas.py "$D/lectura-ia" "$D/lectura-ia/sitio" && \
.venv-ia/bin/python pipeline/detectar_partidas.py "$D/lectura-ia" && \
.venv-ia/bin/python pipeline/priorizar_revision.py "$D/salida" "$D/lectura-ia" "$D/lectura-ia/sitio" && \
.venv-ia/bin/python pipeline/armar_catalogo.py "$D/salida" "$D/lectura-ia" "$D/lectura-ia/marcha.sqlite" && \
.venv-ia/bin/python pipeline/notas_aplicadas.py "$D/lectura-ia" && \
.venv-ia/bin/python sitio-final/armar.py "$D" && \
.venv-ia/bin/python sitio-final/buscar.py "$D" "$D/sitio-final-cache/pagefind-completo" && \
echo "=== ARMADO TERMINADO $(date)"
