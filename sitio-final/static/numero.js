// Lector de un número: visor con zoom (OpenSeadragon sobre las teselas) y las notas de la página, en orden de lectura.
document.addEventListener("DOMContentLoaded", async () => {
  const L = document.getElementById("lector");
  if (!L) return;
  const d = await (await fetch(L.dataset.datos)).json();
  const total = d.paginas.length, SEP = 0.02;
  const sel = document.getElementById("selpag"), chk = document.getElementById("vdoble");
  // referencias fijas: en pantalla completa el visor saca el resto de la página del documento y getElementById no los encuentra
  const NOTAS = document.getElementById("notas"), NOTAS_TIT = document.getElementById("notas-tit");
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
  // «Cómo citar»: Apellido, Nombre. «Título». Marcha, nº N, Montevideo, D de mes de AAAA, p. X. Disponible en: URL
  const textoCita = a => {
    const p = Math.min(...a.paginas), url = `${window.SITIO || location.origin + "/"}numero/${encodeURIComponent(d.id)}/#p${p}`;
    const pags = a.paginas.length > 1 ? `pp. ${Math.min(...a.paginas)}–${Math.max(...a.paginas)}` : `p. ${p}`;
    return `${a.cita_autor ? a.cita_autor + ". " : ""}«${a.titulo}». Marcha, nº ${d.numero || d.id}, Montevideo, ${fechaLarga(d.fecha)}, ${pags}. Disponible en: ${url}`;
  };
  const citaHTML = a => `<div class="como-citar"><span class="meta">Cómo citar</span><p class="cita-texto">${esc(textoCita(a))}</p>
    <button type="button" class="btn chico copiar-cita">Copiar</button>
    <button type="button" class="btn chico avisar" data-titulo="${esc(a.titulo)}" data-pagina="${Math.min(...a.paginas)}">Avisanos de un error</button></div>`;
  // «Avisanos de un error»: mismo formato que lectura-ia/avisos-publico.jsonl. En prueba: sin envío real (etapa 4)
  // El mail del aviso: asunto fijo y los campos siempre en el mismo orden, «campo: valor», uno por renglón,
  // para importarlo tal cual a lectura-ia/avisos-publico.jsonl
  const CAMPOS_AVISO = ["numero", "pagina", "titulo", "categoria", "texto", "contacto", "fecha", "enlace"];
  const mailAviso = (a, destino) => ({
    para: destino, asunto: `Aviso de error · Marcha nº ${a.numero} · pág. ${a.pagina}`,
    cuerpo: CAMPOS_AVISO.map(k => `${k}: ${String(a[k] ?? "").replace(/\r?\n/g, " ⏎ ")}`).join("\n"), aviso: a });
  function avisar(pagina, titulo) {
    const m = document.createElement("div");
    m.className = "modal-aviso";
    m.innerHTML = `<form class="aviso-form" role="dialog" aria-modal="true" aria-labelledby="aviso-tit">
      <h2 id="aviso-tit">Avisanos de un error</h2>
      <p class="aviso-prueba">En prueba: por ahora el aviso no se envía, queda guardado en este navegador.</p>
      <p class="meta">Marcha nº ${esc(d.numero || d.id)} · ${fechaLarga(d.fecha)} · pág. ${pagina}${titulo ? ` · «${esc(titulo)}»` : ""}</p>
      <label>Tipo de error <select name="categoria">
        <option>texto mal leído o incompleto</option><option>autor</option><option>notas mezcladas o partidas</option><option>otro</option></select></label>
      <label>¿Qué está mal? <textarea name="texto" rows="5" required placeholder="Por ejemplo: en el tercer párrafo dice «Quíjano» y es «Quijano»."></textarea></label>
      <label>Tu mail (opcional, por si hay que consultarte) <input name="contacto" type="email" autocomplete="email"></label>
      <p class="aviso-botones"><button class="btn" type="submit">Enviar aviso</button> <button class="btn" type="button" data-cerrar>Cancelar</button></p>
      <p class="aviso-estado" aria-live="polite"></p></form>`;
    document.body.appendChild(m);
    const f = m.querySelector("form"), cerrar = () => m.remove();
    m.querySelector("[data-cerrar]").onclick = cerrar;
    m.onclick = e => { if (e.target === m) cerrar(); };
    m.addEventListener("keydown", e => { if (e.key === "Escape") cerrar(); });
    f.querySelector("textarea").focus();
    f.onsubmit = async e => {
      e.preventDefault();
      const aviso = { numero: d.id, pagina: +pagina, titulo: titulo || "", texto: f.texto.value.trim(), categoria: f.categoria.value,
                      contacto: f.contacto.value.trim(), fecha: new Date().toISOString().slice(0, 19), enlace: location.pathname + location.hash };
      if (!aviso.texto) return;
      const estado = f.querySelector(".aviso-estado");
      const cfg = window.AVISOS || {};
      if (cfg.url) {  // etapa 4: el servicio de formularios lo manda como mail a cfg.destino
        try { const r = await fetch(cfg.url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(mailAviso(aviso, cfg.destino)) });
              if (!r.ok) throw new Error(r.status); estado.textContent = "¡Gracias! Recibimos tu aviso."; setTimeout(cerrar, 1800); return; }
        catch { estado.textContent = "No se pudo enviar; quedó guardado en este navegador."; }
      } else estado.textContent = "Gracias. (En prueba: el aviso quedó guardado en este navegador, no se envió.)";
      try { const v = JSON.parse(localStorage.getItem("avisos-en-prueba") || "[]"); v.push(aviso); localStorage.setItem("avisos-en-prueba", JSON.stringify(v)); } catch {}
      f.querySelector("[type=submit]").disabled = true;
    };
  }
  document.addEventListener("click", e => {
    const b = e.target.closest(".avisar");
    if (b) avisar(b.dataset.pagina || actual, b.dataset.titulo || "");
  });
  document.addEventListener("click", async e => {
    const b = e.target.closest(".copiar-cita");
    if (!b) return;
    const t = b.parentElement.querySelector(".cita-texto").textContent;
    try { await navigator.clipboard.writeText(t); } catch {
      const r = document.createRange(); r.selectNodeContents(b.parentElement.querySelector(".cita-texto"));
      const s = getSelection(); s.removeAllRanges(); s.addRange(r); document.execCommand("copy"); s.removeAllRanges();
    }
    b.textContent = "Copiada ✓"; setTimeout(() => b.textContent = "Copiar", 2000);
  });

  function notas(nums) {
    const arts = ordenLectura(d.articulos.filter(a => a.paginas.some(k => nums.includes(k)) && a.tipo !== "aviso"), nums);
    const avisos = d.articulos.filter(a => a.paginas.some(k => nums.includes(k)) && a.tipo === "aviso");
    NOTAS_TIT.textContent = nums.length > 1 ? `Notas de las págs. ${nums.join(" y ")}` : `Notas de la pág. ${nums[0]}`;
    const item = a => `<details class="nota"><summary><b>${esc(a.titulo)}</b>
      <span class="meta">${[a.autor, a.seccion, a.tipo && a.tipo !== "nota" ? a.tipo : ""].filter(Boolean).map(esc).join(" · ")}${a.paginas.length > 1 ? ` · págs. ${a.paginas.join(", ")}` : ""}</span></summary>
      <div class="texto">${a.bajada ? `<p class="bajada">${esc(a.bajada)}</p>` : ""}${parrafos(a.texto)}
      ${(a.recuadros || []).map(r => `<aside class="recuadro"><span class="meta">Recuadro${r.autor ? " · " + esc(r.autor) : ""}</span>${parrafos(r.texto)}</aside>`).join("")}
      ${(a.destacados || []).length ? `<div class="destacados"><span class="meta">Citas destacadas en la página</span>${a.destacados.map(r => `<blockquote>${esc(r.texto)}</blockquote>`).join("")}</div>` : ""}
      ${citaHTML(a)}
      ${a.citas ? `<p class="cita"><b>Cita:</b> ${a.citas.map(c => `<a href="${enlace(c.id)}">Nº ${esc(c.id)}${c.fecha ? ` (${fechaLarga(c.fecha)})` : ""}</a>`).join(" · ")}</p>` : ""}</div></details>`;
    NOTAS.innerHTML = `<p class="aviso-pagina"><button type="button" class="btn chico avisar" data-pagina="${nums[0]}">Avisanos de un error en esta página</button></p>` + (arts.length ? arts.map(item).join("") : `<p class="muted">Sin notas transcriptas en esta página.</p>`) +
      (avisos.length ? `<details class="nota"><summary><b>Avisos (${avisos.length})</b></summary><div class="texto">${avisos.map(a => `<div class="aviso"><p><b>${esc(a.titulo)}</b></p>${parrafos(a.texto)}</div>`).join("")}</div></details>` : "");
  }

  // Trackpad como en el sitio de prueba: dos dedos desplazan la página, pellizcar hace zoom (llega como rueda con ctrlKey,
  // o como gesturestart/gesturechange en Safari). En pantallas táctiles queda el pellizco y el arrastre propios de OpenSeadragon.
  function gestos(v) {
    v.addHandler("canvas-key", e => {
      const k = e.originalEvent.key;
      if (/^[rRfF]$/.test(k)) { e.preventDefaultAction = true; e.preventDefault = true; }
      if (k === "ArrowRight" || k === "ArrowLeft") { e.preventDefaultAction = true; e.preventDefault = true; }  // las flechas pasan de página
    });
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

  // Botones del visor, en el mismo lugar de siempre (arriba a la izquierda), con íconos simples y más grandes para el dedo
  const ICONOS = {
    menos: '<path d="M5 12h14"/>', mas: '<path d="M12 5v14M5 12h14"/>',
    inicio: '<path d="M4 11.5 12 4.5l8 7"/><path d="M6.5 10v9.5h11V10"/>',
    girar: '<path d="M19 12a7 7 0 1 1-2.05-4.95"/><path d="M19 4v5h-5"/>',
    pantalla: '<path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5"/>',
    salir: '<path d="M9 4v5H4M15 4v5h5M15 20v-5h5M9 20v-5H4"/>',
  };
  function controles(v) {
    const control = document.createElement("div"), caja = document.createElement("div");  // OSD le pone estilo al de afuera
    caja.className = "visor-botones";
    control.appendChild(caja);
    const boton = (id, titulo, accion) => {
      const b = document.createElement("button");
      b.type = "button"; b.className = "vb"; b.dataset.id = id; b.title = titulo; b.setAttribute("aria-label", titulo);
      b.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONOS[id]}</svg>`;
      b.addEventListener("click", e => { e.stopPropagation(); accion(b); });
      caja.appendChild(b);
      return b;
    };
    boton("menos", "Alejar", () => { v.viewport.zoomBy(1 / 1.4); });
    boton("mas", "Acercar", () => { v.viewport.zoomBy(1.4); });
    boton("inicio", "Ver la página entera", () => { v.viewport.setRotation(0); encuadrar(); });
    boton("girar", "Girar 90°", () => { v.viewport.setRotation((v.viewport.getRotation() + 90) % 360); });
    const pc = boton("pantalla", "Pantalla completa", () => v.setFullScreen(!v.isFullPage()));
    v.addHandler("full-screen", e => {
      pc.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONOS[e.fullScreen ? "salir" : "pantalla"]}</svg>`;
      pc.title = e.fullScreen ? "Salir de pantalla completa" : "Pantalla completa"; pc.setAttribute("aria-label", pc.title);
    });
    // que tocar o arrastrar sobre los botones no mueva la imagen
    ["pointerdown", "mousedown", "touchstart", "wheel"].forEach(t => caja.addEventListener(t, e => e.stopPropagation(), { passive: true }));
    v.addControl(control, { anchor: OpenSeadragon.ControlAnchor.TOP_LEFT, autoFade: false });
  }

  function mostrar(n) {
    const nums = vistas(n);
    sel.value = n; actual = n;
    const fuentes = nums.map((k, i) => ({ tileSource: `${L.dataset.teselas}p${String(k).padStart(2, "0")}.dzi`, x: i * (1 + SEP), y: 0, width: 1 }));
    if (!visor) {
      visor = OpenSeadragon({ element: document.getElementById("visor"), prefixUrl: "https://cdnjs.cloudflare.com/ajax/libs/openseadragon/4.1.0/images/",
        tileSources: fuentes, showNavigator: false, showNavigationControl: false, visibilityRatio: 0.5, minZoomImageRatio: 0.8, maxZoomPixelRatio: 2.5,
        gestureSettingsTouch: { pinchRotate: false }, gestureSettingsMouse: { clickToZoom: false, dblClickToZoom: true, scrollToZoom: false },
        zoomPerScroll: 1.4, animationTime: 0.6, springStiffness: 9 });
      gestos(visor); document.getElementById("visor").osd = visor;  // para depurar desde la consola
      controles(visor);
      // al salir de pantalla completa: volver a mostrar la página donde quedó (notas, selector, encuadre)
      visor.addHandler("full-screen", e => { if (!e.fullScreen) setTimeout(() => sincronizar(), 50); });
    } else {
      visor.world.removeAll();
      let faltan = fuentes.length;  // cuando cargan todas las imágenes del pliego, se encuadra el pliego entero
      fuentes.forEach(f => visor.addTiledImage({ ...f, success: () => { if (--faltan === 0) encuadrar(); } }));
    }
    notas(nums);
  }
  const encuadrar = () => { if (visor && visor.world.getItemCount()) visor.viewport.fitBounds(visor.world.getHomeBounds(), true); };
  function sincronizar() {
    sel.value = actual;
    if (`#p${actual}` !== location.hash) history.replaceState(null, "", `#p${actual}`);
    notas(vistas(actual));
    encuadrar();
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
