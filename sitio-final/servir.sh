#!/bin/zsh
# Sirve el sitio final SOLO en esta Mac: http://127.0.0.1:8771 (Coli cerró el acceso por Wi-Fi el 17/11/2026).
# Si se cae, lo vuelve a levantar a los 5 segundos.
D="$HOME/Desktop/MARCHA ONLINE"
while true; do
  /opt/homebrew/bin/python3.12 -m http.server 8771 --bind 127.0.0.1 --directory "$D/sitio-final-salida" >> "$D/sitio-final.log" 2>&1
  echo "$(date) el servidor se detuvo; lo vuelvo a levantar" >> "$D/sitio-final.log"
  sleep 5
done
