# KRATE — Spec de Animaciones
**v0.4 · Fase 3**

Este documento describe todas las animaciones de Krate. Cada entrada incluye el trigger, los elementos afectados, los valores CSS/JS recomendados y notas de implementación.

---

## Principios generales

- **Duración base:** 150–200ms para feedback inmediato, 300ms para transiciones de panel
- **Easing por defecto:** `cubic-bezier(0.16, 1, 0.3, 1)` — entra rápido, sale suave (tipo "spring")
- **Nada decorativo:** cada animación comunica un estado o confirma una acción
- **Respeta `prefers-reduced-motion`:** todas las animaciones deben estar dentro de un `@media (prefers-reduced-motion: no-preference)` o chequearse con JS

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}
```

---

## 01 — Resultados del match

**Trigger:** El usuario presiona el botón "Coincidir"  
**Archivo relevante:** `index.html` → función `renderResults()` y `doMatch()`

### Estado de carga (mientras la IA procesa)

El área de resultados muestra un estado pulsante mientras espera la respuesta.

```css
/* Añadir al elemento .matching-state */
@keyframes krate-pulse {
  0%, 100% { opacity: 0.4; }
  50%       { opacity: 1; }
}

.matching-state {
  animation: krate-pulse 1.2s ease-in-out infinite;
}
```

### Entrada de los result-cards (cascada)

Cada card entra con un pequeño delay entre sí — la card #01 primero, luego #02, luego #03.

```css
@keyframes krate-slide-up {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.result-card {
  opacity: 0;
  animation: krate-slide-up 220ms cubic-bezier(0.16, 1, 0.3, 1) forwards;
}

/* En renderResults(), añadir delay inline a cada card */
/* card 0 → animation-delay: 0ms   */
/* card 1 → animation-delay: 60ms  */
/* card 2 → animation-delay: 120ms */
```

**En JS (dentro de `renderResults()`):**

```js
// Después de insertar el HTML en el DOM:
document.querySelectorAll('.result-card').forEach((card, i) => {
  card.style.animationDelay = `${i * 60}ms`;
});
```

### Highlight del resultado #01

La card mejor puntuada tiene un borde rojo que aparece con un ligero "draw-in" desde la izquierda.

```css
@keyframes krate-border-draw {
  from { box-shadow: inset 2px 0 0 #FF3D1A; }
  to   { box-shadow: inset 0 0 0 1px #FF3D1A; }
}

.result-card.best {
  animation: krate-slide-up 220ms cubic-bezier(0.16, 1, 0.3, 1) forwards,
             krate-border-draw 300ms cubic-bezier(0.16, 1, 0.3, 1) forwards;
}
```

### Al presionar "Colocar aquí"

La card sale con fade + slide hacia la derecha.

```css
@keyframes krate-slide-out {
  to {
    opacity: 0;
    transform: translateX(16px);
  }
}

.result-card.done {
  animation: krate-slide-out 180ms cubic-bezier(0.4, 0, 1, 1) forwards;
  pointer-events: none;
}
```

---

## 02 — Selección de track en el inbox

**Trigger:** El usuario hace click en una fila del inbox  
**Archivo relevante:** `index.html` → función `selectInboxTrack()`

### Activación de la fila seleccionada

La fila activa muestra su borde rojo izquierdo deslizándose hacia adentro.

```css
.track-row {
  border-left: 2px solid transparent;
  transition: border-color 150ms ease,
              background 150ms ease;
}

.track-row.active {
  border-left-color: #FF3D1A;
  background: #100D0B;
}
```

### Aparición del chip de track seleccionado

El texto "— Artista · Track" en el label PISTAS aparece con fade.

```css
.selected-chip {
  display: inline-block;
  opacity: 0;
  transition: opacity 200ms ease;
}

.selected-chip.visible {
  opacity: 1;
}
```

**En JS (dentro de `selectInboxTrack()`):**

```js
const chip = document.getElementById('selected-chip');
chip.textContent = '— ' + t.artist + ' · ' + t.name;
chip.classList.remove('visible');
// Forzar reflow para reiniciar la transición
void chip.offsetWidth;
chip.classList.add('visible');
```

### Reveal del textarea de descripción

Cuando se selecciona el primer track y el textarea estaba vacío, hace un sutil fade-in.

```css
.sort-input-wrap {
  transition: opacity 200ms ease;
}

.sort-input-wrap.ready {
  opacity: 1;
}
```

---

## 03 — Colocar un track

**Trigger:** El usuario presiona "Colocar aquí" en una sugerencia  
**Archivo relevante:** `index.html` → función `placeTrack()`

### El track desaparece del inbox

La fila del track colocado se vuelve 30% de opacidad (ya existe en el código) pero con una transición suave.

```css
/* Reemplazar el style="opacity:0.3" inline por una clase animada */
.track-row.placed {
  transition: opacity 300ms ease;
  opacity: 0.3;
  pointer-events: none;
}
```

### Actualización del contador

El número de tracks restantes hace un pequeño "flip" al cambiar.

```css
@keyframes krate-count-flip {
  0%   { transform: translateY(0);     opacity: 1; }
  40%  { transform: translateY(-6px);  opacity: 0; }
  60%  { transform: translateY(6px);   opacity: 0; }
  100% { transform: translateY(0);     opacity: 1; }
}

#inbox-count-label {
  display: inline-block;
}

#inbox-count-label.updating {
  animation: krate-count-flip 280ms cubic-bezier(0.16, 1, 0.3, 1);
}
```

**En JS (dentro de `renderInboxTracks()` cuando cambia el contador):**

```js
const countEl = document.getElementById('inbox-count-label');
countEl.classList.remove('updating');
void countEl.offsetWidth; // reflow
countEl.classList.add('updating');
countEl.addEventListener('animationend', () => {
  countEl.classList.remove('updating');
}, { once: true });
```

### Toast de confirmación

El toast ya existe — solo necesita una animación de entrada y salida más fluida.

```css
/* Reemplazar la transición actual del .toast */
.toast {
  transition: opacity 200ms ease,
              transform 200ms cubic-bezier(0.16, 1, 0.3, 1);
  transform: translateX(-50%) translateY(12px);
}

.toast.show {
  opacity: 1;
  transform: translateX(-50%) translateY(0);
}
```

---

## 04 — Navegación entre paneles

**Trigger:** El usuario hace click en un ítem del sidebar (Inbox, Playlists, Sesión)  
**Archivo relevante:** `index.html` → función `showPanel()`

### Crossfade entre paneles

Los paneles entran con fade y un micro-movimiento vertical.

```css
.panel {
  opacity: 0;
  /* Quitar display:none — usar visibility + opacity para poder animar */
  visibility: hidden;
  position: absolute;
  inset: 0;
  transition: opacity 200ms ease,
              transform 200ms cubic-bezier(0.16, 1, 0.3, 1),
              visibility 200ms;
  transform: translateY(6px);
  overflow-y: auto;
}

.panel.visible {
  opacity: 1;
  visibility: visible;
  position: relative;
  transform: translateY(0);
}
```

> **Nota:** Si el cambio a `position: absolute/relative` rompe el layout, mantener `display:none/flex` y simplemente añadir la animación solo en la entrada con una clase `entering`:

```js
// Alternativa más segura — añadir en showPanel():
const panel = document.getElementById('panel-' + id);
panel.classList.add('visible', 'entering');
panel.addEventListener('animationend', () => {
  panel.classList.remove('entering');
}, { once: true });
```

```css
@keyframes krate-panel-in {
  from { opacity: 0; transform: translateY(8px); }
  to   { opacity: 1; transform: translateY(0); }
}

.panel.entering {
  animation: krate-panel-in 220ms cubic-bezier(0.16, 1, 0.3, 1) forwards;
}
```

### Indicador activo del sidebar deslizante

La línea roja izquierda del nav-item activo se desliza entre ítems en lugar de aparecer/desaparecer.

```css
.nav-item {
  transition: color 150ms ease,
              background 150ms ease,
              border-left-color 150ms ease;
}
```

---

## 05 — Player bar

**Trigger A:** El usuario selecciona un track  
**Trigger B:** El audio termina de cargar  
**Archivo relevante:** `index.html` → funciones `loadTrackInPlayer()`, `drawWaveform()`, `startPlayback()`

### Slide up al cargar un track

Si el player está "vacío" (sin track previo), la barra entera sube suavemente desde abajo.

```css
@keyframes krate-player-in {
  from { transform: translateY(100%); opacity: 0; }
  to   { transform: translateY(0);    opacity: 1; }
}

.player-bar.has-track {
  animation: krate-player-in 300ms cubic-bezier(0.16, 1, 0.3, 1) forwards;
}
```

**En JS (dentro de `loadTrackInPlayer()`):**

```js
const bar = document.querySelector('.player-bar');
if (!bar.classList.contains('has-track')) {
  bar.classList.add('has-track');
}
```

### Pulse del botón play mientras carga

Mientras el audio está cargando, el botón de play pulsa sutilmente.

```css
@keyframes krate-play-loading {
  0%, 100% { opacity: 0.3; transform: scale(0.95); }
  50%       { opacity: 0.7; transform: scale(1.02); }
}

#player-play-btn:disabled {
  animation: krate-play-loading 1s ease-in-out infinite;
}

#player-play-btn:not(:disabled) {
  animation: none;
  transition: transform 100ms ease, color 100ms ease;
}

#player-play-btn:not(:disabled):active {
  transform: scale(0.92);
}
```

### Waveform draw-in

La forma de onda se revela de izquierda a derecha usando un clip-path animado.

```css
#waveform-canvas {
  clip-path: inset(0 100% 0 0);
  transition: clip-path 600ms cubic-bezier(0.16, 1, 0.3, 1);
}

#waveform-canvas.drawn {
  clip-path: inset(0 0% 0 0);
}
```

**En JS — al final de `drawWaveform()`:**

```js
function drawWaveform(buffer) {
  // ... código existente de dibujo ...

  const canvas = document.getElementById('waveform-canvas');
  canvas.classList.remove('drawn');
  void canvas.offsetWidth; // reflow para reiniciar
  requestAnimationFrame(() => canvas.classList.add('drawn'));
}
```

### Transición de metadata (nombre + artista)

Cuando cambia el track en el player, el nombre y artista hacen un crossfade.

```css
.player-meta-name,
.player-meta-artist {
  transition: opacity 200ms ease;
}

.player-meta-name.changing,
.player-meta-artist.changing {
  opacity: 0;
}
```

**En JS (al inicio de `loadTrackInPlayer()`, antes de cambiar el texto):**

```js
const nameEl  = document.getElementById('player-meta-name');
const artistEl = document.getElementById('player-meta-artist');

nameEl.classList.add('changing');
artistEl.classList.add('changing');

setTimeout(() => {
  nameEl.textContent  = track.name;
  artistEl.textContent = track.artist;
  nameEl.classList.remove('changing');
  artistEl.classList.remove('changing');
}, 200);
```

---

## Resumen de archivos a editar

| Archivo | Qué añadir |
|---|---|
| `index.html` `<style>` | Todos los bloques CSS de este documento |
| `index.html` `renderResults()` | Delay en cascada + animationDelay por card |
| `index.html` `selectInboxTrack()` | Chip visible class + reflow |
| `index.html` `placeTrack()` | Clase .placed en track row + contador flip |
| `index.html` `showPanel()` | Clase .entering en panel nuevo |
| `index.html` `loadTrackInPlayer()` | has-track class + metadata crossfade |
| `index.html` `drawWaveform()` | drawn class + reflow para clip-path |

---

## Orden de implementación sugerido

1. **CSS base** — añadir todos los keyframes y clases al bloque `<style>`. No rompe nada todavía.
2. **Toast** — el más fácil, ya existe. Solo cambiar la transición.
3. **Result cards** — impacto visual inmediato, bajo riesgo.
4. **Selección de track** — fila activa y chip.
5. **Colocar track** — placed class + contador.
6. **Player** — pulse de carga + waveform draw-in.
7. **Navegación** — panel fade, lo último porque puede afectar layout.
