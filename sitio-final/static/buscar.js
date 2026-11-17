// Buscador (Pagefind): frase exacta primero, después todas las palabras; filtros de tema, sección, autor y años.
document.addEventListener("DOMContentLoaded", async () => {
  const form = document.getElementById("busca");
  if (!form) return;
  const $ = id => document.getElementById(id), PASO = 15;
  const estado = $("estado"), lista = $("resultados"), mas = $("mas");
  let pf;
  try { pf = await import(`${window.BASE}pagefind/pagefind.js`); await pf.options({ excerptLength: 40 }); }
  catch { estado.textContent = "El índice de búsqueda todavía no está armado."; return; }
  pf.init();
  const filtros = await pf.filters();
  const llenar = (sel, vals) => sel.insertAdjacentHTML("beforeend", vals.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join(""));
  const orden = o => Object.keys(o || {}).sort((a, b) => a.localeCompare(b, "es"));
  const SUB = await (await fetch(`${window.BASE}datos/temas.json`)).json().catch(() => ({}));
  const cap = t => t.charAt(0).toUpperCase() + t.slice(1);
  $("f-tema").insertAdjacentHTML("beforeend", orden(filtros["tema"]).map(v => `<option value="${esc(v)}">${esc(cap(v))}</option>`).join(""));
  const subtemas = () => {  // el selector de subtema muestra los del tema elegido
    const t = $("f-tema").value, ok = new Set(Object.keys(filtros["subtema"] || {}));
    const xs = (SUB[t] || []).filter(x => ok.has(x));
    $("f-subtema").innerHTML = `<option value="">Todos los de ese tema</option>` + xs.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join("");
    $("f-subtema").disabled = !xs.length;
  };
  $("f-tema").addEventListener("change", subtemas);
  llenar($("f-seccion"), orden(filtros["sección"]));
  $("l-autor").innerHTML = orden(filtros["autor"]).map(v => `<option value="${esc(v)}">`).join("");
  const anios = orden(filtros["año"]);
  llenar($("f-desde"), anios); llenar($("f-hasta"), anios);
  $("f-desde").value = anios[0]; $("f-hasta").value = anios[anios.length - 1];

  // estado en la dirección (se puede compartir el enlace de una búsqueda)
  const P = new URLSearchParams(location.search);
  $("q").value = P.get("q") || "";
  if (P.get("tema")) $("f-tema").value = P.get("tema");
  subtemas();
  [["subtema", "f-subtema"], ["seccion", "f-seccion"], ["autor", "f-autor"], ["desde", "f-desde"], ["hasta", "f-hasta"]].forEach(([k, id]) => { if (P.get(k)) $(id).value = P.get(k); });
  if (["tema", "subtema", "seccion", "autor", "desde", "hasta"].some(k => P.get(k))) $("avanzada").open = true;

  let todos = [], visibles = 0, turno = 0, conPalabras = false;
  function armarFiltros() {
    const f = {};
    if ($("f-subtema").value) f["subtema"] = $("f-subtema").value;
    else if ($("f-tema").value) f["tema"] = $("f-tema").value;
    if ($("f-seccion").value.trim()) f["sección"] = $("f-seccion").value.trim();
    if ($("f-autor").value.trim()) f["autor"] = $("f-autor").value.trim();
    const d = $("f-desde").value, h = $("f-hasta").value;
    if (d !== anios[0] || h !== anios[anios.length - 1]) f["año"] = { any: anios.filter(a => a >= d && a <= h) };
    return f;
  }
  async function buscar(empujar) {
    const q = $("q").value.trim(), f = armarFiltros(), yo = ++turno;
    const P2 = new URLSearchParams();
    if (q) P2.set("q", q);
    [["tema", "f-tema"], ["subtema", "f-subtema"], ["seccion", "f-seccion"], ["autor", "f-autor"]].forEach(([k, id]) => $(id).value.trim() && P2.set(k, $(id).value.trim()));
    if (f["año"]) { P2.set("desde", $("f-desde").value); P2.set("hasta", $("f-hasta").value); }
    if (empujar) history.replaceState(null, "", P2.toString() ? `?${P2}` : location.pathname);
    if (!q && !Object.keys(f).length) { lista.innerHTML = ""; estado.textContent = ""; mas.hidden = true; return; }
    estado.textContent = "Buscando…"; mas.hidden = true; conPalabras = !!q;
    const palabras = q.replace(/"/g, "").trim(), soloFrase = /^".+"$/.test(q);  // entre comillas: solo la frase
    const [exacta, sueltas] = await Promise.all([
      palabras && palabras.includes(" ") ? pf.search(`"${palabras}"`, { filters: f }) : Promise.resolve({ results: [] }),
      soloFrase && palabras.includes(" ") ? Promise.resolve({ results: [] }) : pf.search(palabras || null, palabras ? { filters: f } : { filters: f, sort: { fecha: "asc" } }),
    ]);
    if (yo !== turno) return;
    const vistos = new Set(exacta.results.map(r => r.id));
    todos = [...exacta.results.map(r => ({ r, exacta: true })), ...sueltas.results.filter(r => !vistos.has(r.id)).map(r => ({ r }))];
    visibles = 0; lista.innerHTML = "";
    const n = todos.length === 1 ? "1 resultado" : `${todos.length.toLocaleString("es")} resultados`;
    estado.textContent = todos.length ? (exacta.results.length ? `${n} · ${exacta.results.length.toLocaleString("es")} con la frase exacta` : q ? n : `${n}, por fecha`) : "No se encontró nada. Probá con menos palabras o sin filtros.";
    await mostrar();
  }
  async function mostrar() {
    const tanda = todos.slice(visibles, visibles + PASO);
    visibles += tanda.length;
    const datos = await Promise.all(tanda.map(x => x.r.data()));
    lista.insertAdjacentHTML("beforeend", datos.map((d, i) => {
      const m = d.meta || {};
      return `<li><a href="${esc(m.enlace || d.url)}"><b>${esc(m.title || "(sin título)")}</b></a>
        <span class="meta">${[m.autor, m.seccion, `Nº ${m.numero}`, m.fecha, `pág. ${m.pagina}`].filter(Boolean).map(esc).join(" · ")}${tanda[i].exacta ? ' · <span class="exacta">frase exacta</span>' : ""}${m.tipo === "ocr" ? ' · <span class="muted">texto sin corregir</span>' : ""}</span>
        ${conPalabras ? `<p class="extracto">${d.excerpt}</p>` : `<p class="resumen">${esc(m.inicio || "")}</p>`}</li>`;
    }).join(""));
    mas.hidden = visibles >= todos.length;
  }
  form.onsubmit = e => { e.preventDefault(); buscar(true); };
  mas.onclick = () => mostrar();
  $("limpiar").onclick = () => { ["f-tema", "f-seccion", "f-autor"].forEach(id => $(id).value = ""); subtemas(); $("f-desde").value = anios[0]; $("f-hasta").value = anios[anios.length - 1]; buscar(true); };
  if (P.toString()) buscar(false);
});
