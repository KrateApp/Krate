"""
krate_audio.py — Servidor de audio local para Krate
Corre este archivo antes de abrir Krate en el navegador.
Solo necesitas tenerlo corriendo mientras usas Krate.
"""

import os
import xml.etree.ElementTree as ET
from urllib.parse import unquote
from flask import Flask, send_file, Response, request as flask_request
from flask_cors import CORS

app = Flask(__name__)

# Permite requests desde cualquier origen — necesario para que
# Railway pueda pedirle audio a tu computadora local
CORS(app)

# Busca el XML de Rekordbox en la misma carpeta que este archivo
# Acepta cualquier archivo que empiece con "Rekordbox" y termine en ".xml"
# Si hay varios, usa el más reciente
import glob

BASE = os.path.dirname(os.path.abspath(__file__))

def find_xml():
    candidates = glob.glob(os.path.join(BASE, "Rekordbox*.xml"))
    if not candidates:
        return os.path.join(BASE, "Rekordbox.xml")  # fallback para el aviso
    return max(candidates, key=os.path.getmtime)

XML_PATH = find_xml()


def location_to_path(location):
    """Convierte la URL de Rekordbox a una ruta de archivo real."""
    path = unquote(location)
    for prefix in ("file://localhost/", "file:///", "file://"):
        if path.startswith(prefix):
            path = path[len(prefix):]
            break
    return os.path.normpath(path)


def find_track_path(track_id):
    """Busca la ruta del archivo de audio para un TrackID dado."""
    try:
        tree = ET.parse(XML_PATH)
        root = tree.getroot()
        for t in root.find("COLLECTION"):
            if t.get("TrackID") == track_id:
                location = t.get("Location", "")
                if not location:
                    return None, None
                path = location_to_path(location)
                ext  = os.path.splitext(path)[1].lower()
                mime = {
                    ".mp3":  "audio/mpeg",
                    ".flac": "audio/flac",
                    ".wav":  "audio/wav",
                    ".aiff": "audio/aiff",
                    ".aif":  "audio/aiff",
                    ".m4a":  "audio/mp4",
                    ".ogg":  "audio/ogg",
                }.get(ext, "audio/mpeg")
                return path, mime
    except Exception:
        pass
    return None, None


@app.route("/audio/<track_id>")
def serve_audio(track_id):
    path, mime = find_track_path(track_id)
    if not path or not os.path.exists(path):
        return ("Archivo no encontrado", 404)
    return send_file(path, mimetype=mime, conditional=True)


@app.route("/art/<track_id>")
def serve_art(track_id):
    path, _ = find_track_path(track_id)
    if not path or not os.path.exists(path):
        return ("", 404)
    try:
        from mutagen.id3 import ID3, ID3NoHeaderError
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            return ("", 404)
        apic = tags.get("APIC:") or next(
            (v for k, v in tags.items() if k.startswith("APIC")), None
        )
        if apic:
            return Response(apic.data, mimetype=apic.mime)
    except Exception:
        pass
    return ("", 404)


@app.route("/audio-by-path")
def serve_audio_by_path():
    """Sirve un archivo de audio dado su location (path o URL file://) directamente.
    Usado para tracks asignados en sesión que aún no están en el XML exportado."""
    raw_path = flask_request.args.get("path", "")
    if not raw_path:
        return ("Sin ruta", 400)
    path = location_to_path(raw_path)
    if not path or not os.path.exists(path):
        return ("Archivo no encontrado", 404)
    ext  = os.path.splitext(path)[1].lower()
    mime = {
        ".mp3":  "audio/mpeg",
        ".flac": "audio/flac",
        ".wav":  "audio/wav",
        ".aiff": "audio/aiff",
        ".aif":  "audio/aiff",
        ".m4a":  "audio/mp4",
        ".ogg":  "audio/ogg",
    }.get(ext, "audio/mpeg")
    return send_file(path, mimetype=mime, conditional=True)


@app.route("/ping")
def ping():
    """Krate usa esto para verificar que el servidor local está corriendo."""
    return ("pong", 200)


if __name__ == "__main__":
    print("\n╔══════════════════════════════════════╗")
    print("║   KRATE — Servidor de Audio Local    ║")
    print("║   Escuchando en localhost:5001       ║")
    print("║   Deja esta ventana abierta          ║")
    print("╚══════════════════════════════════════╝\n")
    if not os.path.exists(XML_PATH):
        print("  AVISO: No se encontro ningun Rekordbox*.xml en esta carpeta.")
        print("  Copia tu XML de Rekordbox aqui para que el audio funcione:")
        print(f"  {BASE}")
        print()
    else:
        print(f"  XML: {os.path.basename(XML_PATH)}")
        print()
    app.run(host="127.0.0.1", port=5001, debug=False)
