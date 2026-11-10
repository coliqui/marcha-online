#!/bin/zsh
# Trabajo de la noche, sin IA: refinalizar (correcciones nuevas) y armar el sitio de prueba, recorte por el doblez
# real con OCR nuevo, y teselas de zoom profundo. Reanudable: volver a correrlo sigue donde quedó.
eval "$(/opt/homebrew/bin/brew shellenv)"
D="$HOME/Desktop/MARCHA ONLINE"; cd "$HOME/marcha-online"
echo "=== inicio $(date)"
nice -n 10 zsh "$D/lectura-ia/refinalizar-y-armar.sh"
echo "=== recorte $(date)"
.venv-ia/bin/python pipeline/recorte_pliego.py "$D" --procesos 6
echo "=== teselas $(date)"
.venv-ia/bin/python pipeline/teselas.py "$D" --procesos 6
echo "=== NOCHE TERMINADA $(date)"
