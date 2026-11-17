// Comunes a todas las páginas: menú del celular, fecha del pie, botón «arriba», selector de años.
const MESES = "enero febrero marzo abril mayo junio julio agosto setiembre octubre noviembre diciembre".split(" ");
function hoyMontevideo() {
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-CA", { timeZone: "America/Montevideo", year: "numeric", month: "2-digit", day: "2-digit" })
    .formatToParts(new Date()).map(x => [x.type, x.value]));
  return { a: +p.year, m: +p.month, d: +p.day };
}
function fechaLarga(f) { const [a, m, d] = f.split("-").map(Number); return `${d} de ${MESES[m - 1]} de ${a}`; }
const esc = t => String(t ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

document.addEventListener("DOMContentLoaded", () => {
  const b = document.querySelector(".menu-btn"), menu = document.getElementById("menu");
  if (b) b.onclick = () => { const ab = menu.classList.toggle("abierto"); b.setAttribute("aria-expanded", ab); };
  const h = hoyMontevideo();
  document.querySelectorAll("[data-hoy]").forEach(e => e.textContent = `Hoy es ${h.d} de ${MESES[h.m - 1]} de ${h.a}.`);
  const arriba = document.querySelector(".arriba");
  if (arriba) {
    const ver = () => arriba.hidden = window.scrollY < 600;
    window.addEventListener("scroll", ver, { passive: true }); ver();
    arriba.onclick = e => { e.preventDefault(); window.scrollTo({ top: 0, behavior: "smooth" }); };
  }
  document.querySelectorAll(".sel-anio").forEach(s => s.onchange = () => { if (s.value) location.href = `${window.BASE}numeros/${s.value}/`; });
});
