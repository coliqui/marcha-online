// Lista de autores y dibujantes: por apellido con índice A–Z, o por cantidad; filtro por nombre.
document.addEventListener("DOMContentLoaded", async () => {
  const ul = document.getElementById("aut-lista");
  if (!ul) return;
  const datos = await (await fetch(`${window.BASE}datos/autores.json`)).json();
  // [mostrar, slug, textos, dibujos, clave, colectiva?]
  const filtro = document.getElementById("aut-filtro"), mas = document.getElementById("aut-mas"), info = document.getElementById("aut-info");
  const col = document.getElementById("aut-colectivas"), PASO = 300;
  const plano = t => t.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const inicial = x => { const c = x[0].replace(/^[^\p{L}\d]+/u, "")[0] || "#"; return /ñ/i.test(c) ? "Ñ" : plano(c).toUpperCase(); };
  datos.forEach(x => { x.p = plano(x[0]); x.l = inicial(x); x.n = x[2] + x[3]; });
  const porCant = [...datos].sort((a, b) => b.n - a.n || a[4].localeCompare(b[4]));
  let orden = (() => { try { return localStorage.getItem("aut-orden") || "alfa"; } catch { return "alfa"; } })();
  let letra = (location.hash.match(/^#([A-ZÑ])$/) || [])[1] || "A", visibles = PASO;
  const cuenta = x => [x[2] ? `${x[2]} ${x[2] === 1 ? "texto" : "textos"}` : "", x[3] ? `${x[3]} ${x[3] === 1 ? "dibujo" : "dibujos"}` : ""].filter(Boolean).join(" · ");
  function dibujar() {
    const q = plano(filtro.value.trim());
    let xs = (orden === "cant" ? porCant : datos).filter(x => col.checked || !x[5]);
    if (q) xs = xs.filter(x => q.split(/\s+/).every(w => x.p.includes(w)));
    else if (orden === "alfa") xs = xs.filter(x => x.l === letra);
    document.querySelectorAll(".az button").forEach(b => b.setAttribute("aria-pressed", !q && orden === "alfa" && b.dataset.l === letra));
    document.querySelector(".az").hidden = orden !== "alfa";
    document.querySelectorAll("[data-orden]").forEach(b => b.setAttribute("aria-pressed", b.dataset.orden === orden));
    info.textContent = q ? `${xs.length.toLocaleString("es")} coincidencias` : orden === "alfa" ? `${letra}: ${xs.length.toLocaleString("es")} nombres` : `${xs.length.toLocaleString("es")} nombres, de más a menos textos`;
    ul.innerHTML = xs.slice(0, visibles).map(x => `<li><a href="${window.BASE}autores/${x[1]}/">${esc(x[0])}</a><span class="muted">${cuenta(x)}</span></li>`).join("") || `<li class="muted">Ningún nombre coincide.</li>`;
    mas.hidden = xs.length <= visibles;
  }
  document.querySelectorAll(".az button").forEach(b => b.onclick = () => { letra = b.dataset.l; filtro.value = ""; visibles = PASO; history.replaceState(null, "", "#" + letra); dibujar(); });
  document.querySelectorAll("[data-orden]").forEach(b => b.onclick = () => { orden = b.dataset.orden; visibles = PASO; try { localStorage.setItem("aut-orden", orden); } catch {} dibujar(); });
  filtro.oninput = () => { visibles = PASO; dibujar(); };
  col.onchange = () => { visibles = PASO; dibujar(); };
  mas.onclick = () => { visibles += PASO; dibujar(); };
  dibujar();
});
