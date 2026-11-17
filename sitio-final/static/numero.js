// Lector de un número: visor con zoom (OpenSeadragon sobre las teselas) y las notas de la página, en orden de lectura.
document.addEventListener("DOMContentLoaded", async () => {
  const L = document.getElementById("lector");
  if (!L) return;
  const d = await (await fetch(L.dataset.datos)).json();
  const total = d.paginas.length, SEP = 0.02;
  const sel = document.getElementById("selpag"), chk = document.getElementById("vdoble");
  sel.innerHTML = d.paginas.map(p => `<option value="${p.n}">pág. ${p.impresa && p.impresa !== p.n ? `${p.n} (impresa ${p.impresa})` : p.n}</option>`).join("");
  const guardado = (() => { try { return localStorage.getItem("vista-doble"); } catch { return null; } })();
  chk.checked = guardado === null ? window.matchMedia("(min-width: 900px)").matches : guardado === "1";
  let visor = null, actual = 0;

  const paginaHash = () => { const m = location.hash.match(/p(\d+)/); return Math.min(total, Math.max(1, m ? +m[1] : 1)); };
  const vistas = n => {  // páginas que se ven juntas, de izquierda a derecha
    const p = d.paginas[n - 1], m = chk.checked ? p.enfrentada : null;
    return m ? [Math.min(n, m), Math.max(n, m)] : [n];
  };

  // Orden de lectura: los artículos anchos cortan la página en franjas; dentro de cada franja, columna por columna.
  function ordenLectura(arts, numeros) {
    const salida = [];
    numeros.forEach(pag => {
      const deAca = arts.filter(a => a.paginas.find(k => numeros.includes(k)) === pag);
      const conZona = deAca.filter(a => a.zonas && a.zonas[pag]).map(a => ({ a, z: a.zonas[pag] }));
      const sinZona = deAca.filter(a => !(a.zonas && a.zonas[pag]));
      const anchos = conZona.filter(x => x.z[2] - x.z[0] >= 550).sort((p, q) => p.z[1] - q.z[1]);
      const angostos = conZona.filter(x => x.z[2] - x.z[0] < 550);
      const cortes = [-1, ...anchos.map(x => x.z[1])];
      cortes.forEach((desde, k) => {
        if (k > 0) salida.push(anchos[k - 1].a);
        const hasta = k + 1 < cortes.length ? cortes[k + 1] : Infinity;
        const franja = angostos.filter(x => x.z[1] > desde && x.z[1] <= hasta).sort((p, q) => p.z[0] - q.z[0]);
        const columnas = [];
        franja.forEach(x => { const c = columnas.find(c => Math.abs(c.x - x.z[0]) < 70); c ? c.items.push(x) : columnas.push({ x: x.z[0], items: [x] }); });
        columnas.forEach(c => c.items.sort((p, q) => p.z[1] - q.z[1]).forEach(x => salida.push(x.a)));
      });
      salida.push(...sinZona);
    });
    return salida;
  }
  // un párrafo de un solo renglón corto, sin punto final y que no es el último, se dibuja como intertítulo
  const esIntertitulo = (x, k, n) => k < n - 1 && !x.includes("\n") && x.trim().length <= 70 && !/[.,;:—\-?!»")]$/.test(x.trim()) && /\p{Lu}/u.test(x.trim()[0] || "") && !/\s(a|al|de|del|el|en|la|las|lo|los|para|por|que|se|un|una|y|o|con|sin)$/i.test(x.trim());
  const parrafosSimples = t => {
    const ps = t.split(/\n\s*\n/).filter(x => x.trim());
    return ps.map((x, k) => k > 0 && esIntertitulo(x, k, ps.length) ? `<h4 class="intertitulo">${esc(x.trim())}</h4>` : `<p>${esc(x).replace(/\n/g, "<br>")}</p>`).join("");
  };
  // el texto con tablas (renglones que empiezan con «|», como las transcribe la IA) dibujadas como tablas
  function parrafos(texto) {
    const lineas = (texto || "").split("\n"), partes = [];
    let i = 0;
    while (i < lineas.length) {
      if (/^\s*\|/.test(lineas[i])) {
        const filas = [];
        while (i < lineas.length && /^\s*\|/.test(lineas[i])) filas.push(lineas[i++]);
        const celdas = f => f.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(c => c.trim());
        const separador = f => /^[\s|:-]+$/.test(f) && f.includes("-");
        const cuerpo = filas.filter(f => !separador(f)).map(celdas);
        const conEnc = filas.length > 1 && separador(filas[1]);
        const celda = (c, tag) => `<${tag}${tag === "td" && /^[\d.,%" ()$-]+$/.test(c) && /\d/.test(c) ? ' class="num"' : ""}>${esc(c)}</${tag}>`;
        partes.push(`<div class="tabla"><table>${cuerpo.map((f, k) => `<tr>${f.map(c => celda(c, conEnc && k === 0 ? "th" : "td")).join("")}</tr>`).join("")}</table></div>`);
      } else {
        const bloque = [];
        while (i < lineas.length && !/^\s*\|/.test(lineas[i])) bloque.push(lineas[i++]);
        partes.push(parrafosSimples(bloque.join("\n")));
      }
    }
    return partes.join("");
  }
  const enlace = id => `${window.BASE}numero/${encodeURIComponent(id)}/`;

  function notas(nums) {
    const arts = ordenLectura(d.articulos.filter(a => a.paginas.some(k => nums.includes(k)) && a.tipo !== "aviso"), nums);
    const avisos = d.articulos.filter(a => a.paginas.some(k => nums.includes(k)) && a.tipo === "aviso");
    document.getElementById("notas-tit").textContent = nums.length > 1 ? `Notas de las págs. ${nums.join(" y ")}` : `Notas de la pág. ${nums[0]}`;
    const item = a => `<details class="nota"><summary><b>${esc(a.titulo)}</b>
      <span class="meta">${[a.autor, a.seccion, a.tipo && a.tipo !== "nota" ? a.tipo : ""].filter(Boolean).map(esc).join(" · ")}${a.paginas.length > 1 ? ` · págs. ${a.paginas.join(", ")}` : ""}</span></summary>
      <div class="texto">${a.bajada ? `<p class="bajada">${esc(a.bajada)}</p>` : ""}${parrafos(a.texto)}
      ${(a.recuadros || []).map(r => `<aside class="recuadro"><span class="meta">Recuadro${r.autor ? " · " + esc(r.autor) : ""}</span>${parrafos(r.texto)}</aside>`).join("")}
      ${(a.destacados || []).length ? `<div class="destacados"><span class="meta">Citas destacadas en la página</span>${a.destacados.map(r => `<blockquote>${esc(r.texto)}</blockquote>`).join("")}</div>` : ""}
      ${a.citas ? `<p class="cita"><b>Cita:</b> ${a.citas.map(c => `<a href="${enlace(c.id)}">Nº ${esc(c.id)}${c.fecha ? ` (${fechaLarga(c.fecha)})` : ""}</a>`).join(" · ")}</p>` : ""}</div></details>`;
    document.getElementById("notas").innerHTML = (arts.length ? arts.map(item).join("") : `<p class="muted">Sin notas transcriptas en esta página.</p>`) +
      (avisos.length ? `<details class="nota"><summary><b>Avisos (${avisos.length})</b></summary><div class="texto">${avisos.map(a => `<div class="aviso"><p><b>${esc(a.titulo)}</b></p>${parrafos(a.texto)}</div>`).join("")}</div></details>` : "");
  }

  // Trackpad como en el sitio de prueba: dos dedos desplazan la página, pellizcar hace zoom (llega como rueda con ctrlKey,
  // o como gesturestart/gesturechange en Safari). En pantallas táctiles queda el pellizco y el arrastre propios de OpenSeadragon.
  function gestos(v) {
    v.addHandler("canvas-key", e => { if (/^[rRfF]$/.test(e.originalEvent.key)) { e.preventDefaultAction = true; e.preventDefault = true; } });
    v.addHandler("canvas-scroll", e => {
      const ev = e.originalEvent;
      e.preventDefaultAction = true; e.preventDefault = true; ev.preventDefault();
      const fino = Math.abs(ev.deltaX) + Math.abs(ev.deltaY) < 40;  // trackpad: pasos chicos, al instante; ratón: animado
      if (ev.ctrlKey || ev.metaKey) v.viewport.zoomBy(Math.exp(-ev.deltaY * 0.025), v.viewport.pointFromPixel(e.position, true), fino);
      else v.viewport.panBy(v.viewport.deltaPointsFromPixels(new OpenSeadragon.Point(ev.deltaX * 2, ev.deltaY * 2)), fino);
      v.viewport.applyConstraints();
    });
    if (window.matchMedia("(pointer: fine)").matches) {
      const el = v.element; let escala = 1;
      const punto = ev => { const r = el.getBoundingClientRect(); return v.viewport.pointFromPixel(new OpenSeadragon.Point(ev.clientX - r.left, ev.clientY - r.top), true); };
      el.addEventListener("gesturestart", ev => { ev.preventDefault(); escala = 1; });
      el.addEventListener("gesturechange", ev => { ev.preventDefault(); v.viewport.zoomBy(ev.scale / escala, punto(ev), true); escala = ev.scale; v.viewport.applyConstraints(); });
      el.addEventListener("gestureend", ev => ev.preventDefault());
    }
    return v;
  }

  function mostrar(n) {
    const nums = vistas(n);
    sel.value = n; actual = n;
    const fuentes = nums.map((k, i) => ({ tileSource: `${L.dataset.teselas}p${String(k).padStart(2, "0")}.dzi`, x: i * (1 + SEP), y: 0, width: 1 }));
    if (!visor) {
      visor = OpenSeadragon({ element: document.getElementById("visor"), prefixUrl: "https://cdnjs.cloudflare.com/ajax/libs/openseadragon/4.1.0/images/",
        tileSources: fuentes, showNavigator: false, visibilityRatio: 0.5, minZoomImageRatio: 0.8, maxZoomPixelRatio: 2.5,
        gestureSettingsTouch: { pinchRotate: false }, gestureSettingsMouse: { clickToZoom: false, dblClickToZoom: true, scrollToZoom: false },
        zoomPerScroll: 1.4, animationTime: 0.6, springStiffness: 9 });
      gestos(visor); document.getElementById("visor").osd = visor;  // para depurar desde la consola
    } else {
      visor.world.removeAll();
      fuentes.forEach(f => visor.addTiledImage(f));
    }
    notas(nums);
  }
  const ir = n => { n = Math.min(total, Math.max(1, n)); if (`#p${n}` !== location.hash) history.replaceState(null, "", `#p${n}`); mostrar(n); };
  sel.onchange = () => ir(+sel.value);
  L.querySelectorAll("[data-pag]").forEach(b => b.onclick = () => {
    const ns = vistas(actual); ir(+b.dataset.pag > 0 ? Math.max(...ns) + 1 : Math.min(...ns) - 1);
  });
  chk.onchange = () => { try { localStorage.setItem("vista-doble", chk.checked ? "1" : "0"); } catch {} mostrar(actual); };
  window.addEventListener("hashchange", () => mostrar(paginaHash()));
  document.addEventListener("keydown", e => { if (e.target.tagName === "SELECT" || e.target.tagName === "INPUT") return;
    if (e.key === "ArrowRight") L.querySelector('[data-pag="1"]').click(); if (e.key === "ArrowLeft") L.querySelector('[data-pag="-1"]').click(); });
  if (d.citado_en && d.citado_en.length) document.getElementById("citado").innerHTML = `<p class="citado"><b>Citado en:</b> ${d.citado_en.map(c =>
    `<a href="${enlace(c.id)}#p${c.pagina || 1}">Nº ${esc(c.id)}${c.titulo ? ` · «${esc(c.titulo.slice(0, 50))}»` : ""}</a>`).join(" · ")}</p>`;
  mostrar(paginaHash());
});
