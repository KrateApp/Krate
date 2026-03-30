# KRATE — Contexto para Claude Code
> Este archivo es la memoria del proyecto. Léelo completo antes de tocar cualquier archivo.
> Actualizado: marzo 2026 · v0.4 · Fase 3 completa / Fase 4 iniciando

---

## Qué es Krate

Krate es una herramienta de organización de librería para DJs que usa IA para clasificar tracks en playlists según **vibe** — no según etiquetas de género ni metadata. El DJ describe cómo se siente un track en lenguaje natural (escrito o por voz), y la IA lo compara contra descripciones de vibe de cada playlist para encontrar dónde pertenece.

**El problema que resuelve:** Las etiquetas de género describen qué es una canción. Krate detecta cómo se siente. Un track puede estar etiquetado como "techno" y aun así pertenecer a 5 playlists completamente distintas según su energía, atmósfera y propósito en un set.

**El diferenciador clave:** Ninguna herramienta existente (Djoid, Cyanite, Lexicon, Rekordcloud) enruta tracks contra descripciones de vibe definidas por el usuario en su propio idioma con razonamiento visible. Krate sí.

---

## El Usuario

- **DJ:** MASMASMAS (Grey) — DJ en México, alias principal
- **Serie CPT:** Sub-serie de playlists con mayor flexibilidad de género
- **Librería:** 345 tracks, 34 playlists (25 activas, 7 ocultas, 1 inbox)
- **Perfil técnico:** Self-taught developer, aprendiendo Python construyendo Krate
- **Usuarios beta:** 2-5 amigos DJs, no técnicos, en México
- **Idioma:** Mix español/inglés. UI y docs en español.

---

## Arquitectura del Sistema

### Componentes

```
SERVIDOR REMOTO (Railway)
├── app.py              — Backend Flask, todas las rutas API
├── krate.py            — Motor de IA (match_vibe), prompts de Anthropic
├── templates/
│   └── index.html      — Frontend completo (HTML/CSS/JS vanilla, sin frameworks)
├── playlist_vibes.json — Vibes de playlists + estado (ocultas, orden, inbox)
├── requirements.txt    — flask, anthropic, mutagen, python-dotenv, gunicorn, flask-cors
└── Rekordbox.xml       — Subido por el usuario en cada sesión (NO en git)

COMPUTADORA DEL USUARIO (local)
├── krate_audio.py      — Mini servidor Flask en localhost:5001
│                         Sirve archivos de audio e imágenes de portada
│                         Resuelve que Railway no puede acceder al disco local
└── krate_audio.bat     — Launcher para Windows: instala deps y corre krate_audio.py
```

### Por qué esta arquitectura

**El problema del audio:** Una app web en la nube no puede acceder a archivos en el disco del usuario. La música del DJ vive en su computadora (o memoria externa). La solución es un servidor de audio local (`krate_audio.py`) que corre en la computadora del usuario y sirve los archivos a `localhost:5001`. El frontend hace requests a `localhost:5001` para audio/portadas, y a Railway para todo lo demás.

**El problema del XML:** Rekordbox exporta la librería como XML. Krate lee ese XML para obtener tracks y playlists. En producción, el usuario sube su XML manualmente al inicio de cada sesión mediante un botón en la UI. El XML no se persiste en Railway entre sesiones — cada sesión es fresca.

### Flujo de datos completo

```
1. Usuario exporta Rekordbox.xml desde Rekordbox
2. Abre krate_audio.bat → levanta servidor local en localhost:5001
3. Abre krate-production.up.railway.app en el navegador
4. Sube su Rekordbox.xml mediante el botón "Cambiar XML"
5. Railway parsea el XML en memoria (app.py)
6. Usuario selecciona track → frontend hace request a localhost:5001/audio/{id}
7. Usuario describe el vibe → app.py llama a krate.py → krate.py llama a Anthropic API
8. Claude devuelve sugerencias con razonamiento → UI las muestra
9. Usuario confirma asignación → se guarda en session_assignments (memoria, no DB)
10. Al terminar: usuario exporta Rekordbox_krate.xml
11. Reimporta en Rekordbox: Preferences → Advanced → Database → Imported Library
12. BUG CONOCIDO de Rekordbox: hay que importar playlists Y tracks por separado
```

---

## Stack Tecnológico

| Capa | Tecnología | Notas |
|------|-----------|-------|
| Backend | Python 3 + Flask | app.py |
| IA | Anthropic SDK (claude-opus-4-5) | krate.py, lazy import en primer match |
| XML | xml.etree.ElementTree | Parseo y escritura de Rekordbox XML |
| Frontend | HTML/CSS/JS vanilla | Un solo archivo: templates/index.html |
| Audio local | Flask + flask-cors | krate_audio.py en localhost:5001 |
| Portadas | mutagen (ID3) | Extrae APIC frames de MP3 |
| Voz | Web Speech API | Bilingual toggle ES/EN |
| Waveform | Web Audio API | Canvas, decodificación en browser |
| Deploy | Railway | gunicorn app:app --bind 0.0.0.0:5000 |
| Repo | GitHub privado (KrateApp/Krate) | |
| Landing | Netlify (krateapp.netlify.app) | Waitlist, sin backend |

---

## Variables de Entorno

| Variable | Dónde | Para qué |
|----------|-------|----------|
| `ANTHROPIC_API_KEY` | Railway Variables + .env local | Autenticación con Anthropic API |
| `PORT` | Railway (fijo: 5000) | Puerto donde gunicorn escucha |

**IMPORTANTE:** El archivo `.env` nunca va a GitHub. Está en `.gitignore`.
La key de Anthropic la paga Grey — los usuarios beta no necesitan su propia key.

---

## Archivos que NUNCA van a GitHub

```
.env
Rekordbox.xml
Rekordbox_krate.xml
playlist_vibes.json
krate.bat
krate_dashboard.bat
__pycache__/
*.pyc
```

`playlist_vibes.json` y `Rekordbox.xml` son datos personales del DJ — cada usuario tiene los suyos.

---

## Features Implementados (Fase 3 completa)

### Core
- [x] Parseo de Rekordbox XML (tracks + playlists)
- [x] Matching con IA — Modo Revisión (top 3 sugerencias) y Modo Auto (asignación directa)
- [x] Prompts bilingües — responde en el idioma de la descripción
- [x] Razonamiento visible — la IA explica por qué sugiere cada playlist
- [x] Sugerencia de nueva playlist cuando nada encaja
- [x] Sesión activa — asignaciones en memoria durante la sesión
- [x] Exportación a Rekordbox_krate.xml

### UI/UX
- [x] Dashboard Flask single-file (templates/index.html)
- [x] Sistema de inbox — cualquier playlist puede ser la bandeja de entrada
- [x] Gestión de playlists — ocultar/restaurar, reordenar (drag), editar vibes inline
- [x] Vista de detalle de playlist — tracks con selector de columnas drag-and-drop
- [x] Diálogo de acción — describir+coincidir O mover manualmente desde cualquier vista
- [x] Entrada por voz con toggle ES/EN (Web Speech API)
- [x] Reproductor de audio con waveform canvas, seek, portadas
- [x] Columnas configurables con persistencia en localStorage
- [x] Track deletion con confirmación
- [x] Toast notifications
- [x] Panel de sesión con lista de asignaciones + playlists modificadas

### Producción
- [x] Deployado en Railway
- [x] krate_audio.py — servidor de audio local para producción
- [x] krate_audio.bat — launcher para usuarios Windows
- [x] Upload de XML desde el browser

---

## Lo que Falta (Pendiente)

### Fase 3 — Último pendiente
- [ ] Pulido de UI — animaciones específicas (ver krate_animaciones.md)
  - Place track: slide-out + flash en inbox
  - Waveform: fade-in al cargar
  - Nav transitions: crossfade 120ms entre paneles
  - Sidebar dots: scale pulse al asignar
  - Matching state: puntos animados "BUSCANDO · · ·"

### Fase 4 — Prueba real (CURRENT)
- [ ] Onboarding de 2-5 DJs beta
- [ ] Documentar bugs y casos borde en uso real
- [ ] Ajuste fino de prompts según feedback
- [ ] Evaluar si el flujo de XML upload es claro para usuarios no técnicos

### Fase 5 — Escala (PENDING)
- [ ] Auth (Clerk o Supabase Auth)
- [ ] DB para tracking de usuarios y matches (Supabase)
- [ ] Pagos y trial (Stripe)
- [ ] Landing page de conversión (la actual es waitlist)

### Desktop (Parallel track, baja urgencia)
- [ ] pywebview prototype → elimina necesidad de krate_audio.py
- [ ] PyInstaller .exe
- [ ] pyrekordbox — lectura directa de DB de Rekordbox (elimina XML)

---

## Decisiones de Diseño Importantes

**Por qué no hay puntuación de confianza:**
Se eliminó porque sería arbitrario — la IA no tiene métrica objetiva al comparar lenguaje natural con lenguaje natural. El razonamiento escrito ya comunica calidad del match mejor que cualquier número.

**Por qué no Electron:**
El stack Flask + HTML/CSS/JS funciona dentro de pywebview sin modificaciones, produciendo un ejecutable de ~15-30 MB vs ~200 MB de Electron.

**Por qué app web y no desktop primero:**
Los DJs trabajan en múltiples máquinas. La web app es accesible desde cualquier lugar sin instalación. El desktop solo se vuelve relevante si Krate monitorea la librería en tiempo real.

**Por qué el servidor de audio local:**
Railway no puede acceder a archivos en el disco del usuario. Subirlos al servidor es impractical (5-10 GB por librería). El servidor local en localhost:5001 resuelve esto con ~50 líneas de Python y doble-click.

**Por qué flask-cors en krate_audio.py:**
El browser bloquea requests desde `https://krate-production.up.railway.app` hacia `http://localhost:5001` por CORS. flask-cors resuelve esto explícitamente.

---

## Precios y Negocio (Para contexto, no para código)

- **Free tier:** 20 matches/mes permanente
- **Trial:** 14 días acceso completo, requiere tarjeta
- **Pro:** ~169 MXN/mes
- **Créditos:** ~85 MXN por 50 matches
- **Beta actual:** Grey paga los créditos de Anthropic para sus amigos DJs
- **Competidores:** Djoid, Lexicon, MIXO, Rekordcloud, Sort Your Music

---

## Cómo Trabajar en Este Proyecto

### Lo que Claude Code debe hacer
- Leer este archivo primero siempre
- Editar archivos existentes con cambios quirúrgicos — no rewrites completos
- Preguntar una cosa antes de construir si algo es ambiguo
- Asumir que `playlist_vibes.json` y `Rekordbox.xml` no existen en el servidor
- Mantener el estilo visual existente — colores del sistema de diseño en index.html

### Lo que Claude Code NO debe hacer
- Reescribir index.html completo para un cambio pequeño
- Agregar frameworks JS (React, Vue, etc.) — el proyecto es vanilla JS by design
- Tocar krate.py sin entender que es el motor de IA con prompts cuidadosamente calibrados
- Hardcodear rutas de archivo absolutas
- Agregar autenticación o DB por su cuenta — eso es Fase 5 con decisiones pendientes

### Sistema de diseño (colores principales)
```css
--bg:        #0A0908   /* fondo principal */
--bg-dark:   #070706   /* sidebar, player */
--border:    #1A1914   /* bordes */
--text:      #C8C4B8   /* texto principal */
--text-dim:  #6A6858   /* texto secundario */
--accent:    #FF3D1A   /* rojo Krate — acciones, activo */
--text-warm: #9A9880   /* texto terciario */
```

---

## Archivos Clave y Su Propósito

| Archivo | Propósito |
|---------|-----------|
| `app.py` | Backend Flask. Todas las rutas `/api/*`. Lee XML, sirve datos, gestiona sesión en memoria |
| `krate.py` | Motor de IA. `match_vibe()` construye el prompt y llama a Anthropic. No importar en startup |
| `templates/index.html` | Todo el frontend. CSS + JS inline. Un solo archivo |
| `playlist_vibes.json` | Vibes + metadata (`_inbox`, `_ignored`, `_order`). NO en git |
| `krate_audio.py` | Servidor local de audio/portadas. Corre en localhost:5001 en la máquina del usuario |
| `krate_audio.bat` | Launcher Windows para krate_audio.py. Instala deps automáticamente |
| `requirements.txt` | Dependencias Python para Railway |
| `.gitignore` | Excluye archivos sensibles y personales |

---

*Krate comenzó en marzo de 2026. Nunca ha habido un momento más barato para construir.*
