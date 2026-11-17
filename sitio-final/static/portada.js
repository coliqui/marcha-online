// Portada: la Marcha publicada en este mismo día y mes (hora de Montevideo); si ese día no hubo, la más cercana.
document.addEventListener("DOMContentLoaded", async () => {
  const B = window.BASE, h = hoyMontevideo();
  let fechas = {};
  try { fechas = await (await fetch(`${B}datos/fechas.json`)).json(); } catch { return; }
  const clave = (m, d) => `${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
  let lista = [], dia = new Date(Date.UTC(2000, h.m - 1, h.d));
  for (const k of [0, 1, -1, 2, -2]) {  // como mucho a dos días
    const x = new Date(dia.getTime() + k * 864e5);
    lista = fechas[clave(x.getUTCMonth() + 1, x.getUTCDate())] || [];
    if (lista.length) { dia = x; break; }
  }
  if (!lista.length) return;
  const exacto = dia.getUTCMonth() + 1 === h.m && dia.getUTCDate() === h.d;
  document.getElementById("portada-tit").textContent = exacto
    ? `Un ${h.d} de ${MESES[h.m - 1]} en Marcha` : `Cerca del ${h.d} de ${MESES[h.m - 1]} en Marcha`;
  const mostrar = n => {
    const m = lista[n], url = `${B}numero/${m.id}/`;
    const img = document.getElementById("portada-img");
    img.src = `${B}img/tapas-g/${m.id}.webp`; img.alt = `Tapa de Marcha nº ${m.id}, ${fechaLarga(m.fecha)}`;
    document.getElementById("portada-link").href = url;
    document.getElementById("portada-leer").href = url;
    document.getElementById("portada-dato").innerHTML = `<b>Nº ${esc(m.id)}</b> · ${fechaLarga(m.fecha)}`;
    document.querySelectorAll("#portada-otras button").forEach((b, i) => b.setAttribute("aria-pressed", i === n));
  };
  if (lista.length > 1) {
    const otras = document.getElementById("portada-otras");
    otras.innerHTML = lista.map(m => `<button type="button" aria-pressed="false"><img src="${B}img/tapas/${m.id}.webp" alt="" loading="lazy">${m.fecha.slice(0, 4)}</button>`).join("");
    otras.querySelectorAll("button").forEach((b, i) => b.onclick = () => mostrar(i));
  }
  mostrar(Math.floor(Math.random() * lista.length));
});
