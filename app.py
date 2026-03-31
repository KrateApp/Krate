import json
import os
import xml.etree.ElementTree as ET
from urllib.parse import unquote
from flask import Flask, jsonify, request, render_template, send_file, Response

app = Flask(__name__)

BASE       = os.path.dirname(os.path.abspath(__file__))
XML_PATH   = os.path.join(BASE, "Rekordbox.xml")
VIBES_FILE = os.path.join(BASE, "playlist_vibes.json")

# In-memory session: {track_id: {playlist, name, artist, bpm, key}}
session_assignments = {}
# Playlists touched this session (accumulates, survives unassign)
session_modified_playlists = set()


# ---------------------------------------------------------------
# DATA HELPERS  (mirrors krate.py, no Anthropic import at startup)
# ---------------------------------------------------------------

def load_library():
    tree = ET.parse(get_xml_path())
    root = tree.getroot()
    tracks = {}
    for track in root.find("COLLECTION"):
        tid = track.get("TrackID")
        tracks[tid] = {"name": track.get("Name"), "artist": track.get("Artist")}
    playlists = {}
    for node in root.find("PLAYLISTS")[0]:
        name = node.get("Name")
        if name:
            playlists[name] = [e.get("Key") for e in node]
    return tracks, playlists


def load_vibes():
    if os.path.exists(VIBES_FILE):
        for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
            try:
                with open(VIBES_FILE, encoding=enc) as f:
                    return json.load(f)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
    return {}


def save_vibes(vibes):
    with open(VIBES_FILE, "w", encoding="utf-8") as f:
        json.dump(vibes, f, indent=2, ensure_ascii=False)


def get_xml_path():
    vibes = load_vibes()
    custom = vibes.get("_xml_path")
    if custom and os.path.exists(custom):
        return custom
    return XML_PATH


# ---------------------------------------------------------------
# ROUTES
# ---------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/stats")
def stats():
    try:
        tracks, playlists = load_library()
        track_count    = len(tracks)
        playlist_count = len(playlists)
    except Exception:
        track_count = playlist_count = 0

    vibes   = load_vibes()
    ignored = vibes.get("_ignored", [])
    vibes_count = sum(
        1 for k, v in vibes.items()
        if k != "_ignored" and not k.startswith("_")
        and isinstance(v, str) and v.strip() and v != "SKIP"
    )
    active_count = max(0, playlist_count - len(ignored))

    return jsonify({
        "tracks":      track_count,
        "playlists":   playlist_count,
        "hidden":      len(ignored),
        "vibes_set":   vibes_count,
        "vibes_total": active_count,
        "inbox_name":  vibes.get("_inbox"),
    })


@app.route("/api/playlists")
def get_playlists():
    try:
        _, playlists_data = load_library()
    except Exception:
        playlists_data = {}

    vibes   = load_vibes()
    ignored = vibes.get("_ignored", [])
    deleted = vibes.get("_deleted", [])
    order   = vibes.get("_order", [])
    virtual = vibes.get("_virtual", [])
    names   = vibes.get("_names", {})

    result = []
    xml_names = set()
    for name, track_ids in playlists_data.items():
        if name in deleted:
            continue
        xml_names.add(name)
        result.append({
            "name":        name,
            "count":       len(track_ids),
            "vibe":        vibes.get(name, ""),
            "hidden":      name in ignored,
            "virtual":     False,
            "custom_name": names.get(name, ""),
        })

    for name in virtual:
        if name in deleted or name in xml_names:
            continue
        result.append({
            "name":        name,
            "count":       0,
            "vibe":        vibes.get(name, ""),
            "hidden":      name in ignored,
            "virtual":     True,
            "custom_name": names.get(name, ""),
        })

    if order:
        order_map = {name: i for i, name in enumerate(order)}
        result.sort(key=lambda p: order_map.get(p["name"], len(order)))

    return jsonify(result)


@app.route("/api/vibes/<path:playlist>", methods=["POST"])
def set_vibe(playlist):
    data  = request.get_json()
    vibes = load_vibes()
    vibes[playlist] = data.get("vibe", "")
    save_vibes(vibes)
    return jsonify({"ok": True})


@app.route("/api/match", methods=["POST"])
def do_match():
    # Lazy import so Anthropic client is only created on first match call
    from krate import match_vibe

    data        = request.get_json()
    description = (data.get("description") or "").strip()
    mode        = data.get("mode", "review")

    if not description:
        return jsonify({"error": "No description provided"}), 400

    vibes = load_vibes()
    active_vibes = {
        k: v for k, v in vibes.items()
        if k != "_ignored" and not k.startswith("_")
        and isinstance(v, str) and v.strip() and v != "SKIP"
    }

    if not active_vibes:
        return jsonify({"error": "No vibes set up yet"}), 400

    result = match_vibe(description, active_vibes, mode=mode)
    return jsonify(result)


@app.route("/api/playlists/reorder", methods=["POST"])
def reorder_playlists():
    order = request.get_json().get("order", [])
    vibes = load_vibes()
    vibes["_order"] = order
    save_vibes(vibes)
    return jsonify({"ok": True})


@app.route("/api/playlists/hide", methods=["POST"])
def hide_playlist():
    name    = request.get_json().get("name")
    vibes   = load_vibes()
    ignored = vibes.get("_ignored", [])
    if name and name not in ignored:
        ignored.append(name)
    vibes["_ignored"] = ignored
    save_vibes(vibes)
    return jsonify({"ok": True})


@app.route("/api/playlists/restore", methods=["POST"])
def restore_playlist():
    name    = request.get_json().get("name")
    vibes   = load_vibes()
    ignored = vibes.get("_ignored", [])
    if name in ignored:
        ignored.remove(name)
    vibes["_ignored"] = ignored
    save_vibes(vibes)
    return jsonify({"ok": True})


@app.route("/api/playlists/rename", methods=["POST"])
def rename_playlist():
    data         = request.get_json()
    original     = (data.get("original_name") or "").strip()
    display_name = (data.get("new_display_name") or "").strip()
    if not original:
        return jsonify({"error": "original_name required"}), 400
    vibes = load_vibes()
    names = vibes.setdefault("_names", {})
    if display_name:
        names[original] = display_name
    else:
        names.pop(original, None)   # empty = revert to raw name
    vibes["_names"] = names
    save_vibes(vibes)
    return jsonify({"ok": True})


@app.route("/api/playlists/create", methods=["POST"])
def create_playlist():
    data = request.get_json()
    name = (data.get("name") or "").strip()
    vibe = (data.get("vibe") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    vibes   = load_vibes()
    virtual = vibes.get("_virtual", [])
    deleted = vibes.get("_deleted", [])
    if name in deleted:
        deleted.remove(name)
        vibes["_deleted"] = deleted
    if name not in virtual:
        virtual.append(name)
    vibes["_virtual"] = virtual
    vibes[name] = vibe
    save_vibes(vibes)
    return jsonify({"ok": True})


@app.route("/api/playlists/delete", methods=["POST"])
def delete_playlist():
    data = request.get_json()
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    vibes   = load_vibes()
    virtual = vibes.get("_virtual", [])
    deleted = vibes.get("_deleted", [])
    if name in virtual:
        virtual.remove(name)
        vibes["_virtual"] = virtual
        vibes.pop(name, None)
        vibes.get("_names", {}).pop(name, None)
    else:
        if name not in deleted:
            deleted.append(name)
        vibes["_deleted"] = deleted
        vibes.pop(name, None)
        vibes.get("_names", {}).pop(name, None)
    # Also remove from _ignored if present
    ignored = vibes.get("_ignored", [])
    if name in ignored:
        ignored.remove(name)
        vibes["_ignored"] = ignored
    save_vibes(vibes)
    return jsonify({"ok": True})


@app.route("/api/playlists/<path:name>/tracks")
def get_playlist_tracks(name):
    try:
        tree = ET.parse(get_xml_path())
        root = tree.getroot()

        # Build track lookup from COLLECTION
        track_map = {}
        for t in root.find("COLLECTION"):
            tid = t.get("TrackID")
            track_map[tid] = {
                "id":       tid,
                "name":     t.get("Name", ""),
                "artist":   t.get("Artist", ""),
                "bpm":      t.get("AverageBpm", ""),
                "key":      t.get("Tonality", ""),
                "album":    t.get("Album", ""),
                "year":     t.get("Year", ""),
                "duration": t.get("TotalTime", ""),
                "genre":    t.get("Genre", ""),
                "location": t.get("Location", ""),
            }

        # Find playlist node
        playlists_root = root.find("PLAYLISTS")[0]
        node = next((n for n in playlists_root if n.get("Name") == name), None)
        if node is None:
            # Virtual playlist (not yet exported to XML) — return session tracks
            vibes = load_vibes()
            if name in vibes.get("_virtual", []):
                tracks = [
                    {
                        "id":       tid,
                        "name":     info.get("name", ""),
                        "artist":   info.get("artist", ""),
                        "bpm":      info.get("bpm", ""),
                        "key":      info.get("key", ""),
                        "album":    "", "year": "", "duration": "",
                        "genre":    "", "location": "",
                    }
                    for tid, info in session_assignments.items()
                    if info.get("playlist") == name
                ]
                return jsonify(tracks)
            return jsonify({"error": "Playlist not found"}), 404

        tracks = []
        for entry in node.findall("TRACK"):
            tid = entry.get("Key")
            if tid in track_map:
                tracks.append(track_map[tid])
        return jsonify(tracks)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/inbox")
def get_inbox():
    vibes = load_vibes()
    inbox_name = vibes.get("_inbox")
    if not inbox_name:
        return jsonify({"name": None, "tracks": []})
    xml_path = get_xml_path()
    if not os.path.exists(xml_path):
        return jsonify({"name": None, "tracks": []})
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        track_map = {}
        for t in root.find("COLLECTION"):
            tid = t.get("TrackID")
            track_map[tid] = {
                "id":       tid,
                "name":     t.get("Name", ""),
                "artist":   t.get("Artist", ""),
                "bpm":      t.get("AverageBpm", ""),
                "key":      t.get("Tonality", ""),
                "album":    t.get("Album", ""),
                "year":     t.get("Year", ""),
                "duration": t.get("TotalTime", ""),
                "genre":    t.get("Genre", ""),
                "location": t.get("Location", ""),
            }
        playlists_root = root.find("PLAYLISTS")[0]
        node = next((n for n in playlists_root if n.get("Name") == inbox_name), None)
        if node is None:
            return jsonify({"name": inbox_name, "tracks": []})
        tracks = []
        for entry in node.findall("TRACK"):
            tid = entry.get("Key")
            if tid in track_map:
                tracks.append(track_map[tid])
        return jsonify({"name": inbox_name, "tracks": tracks})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/set-inbox", methods=["POST"])
def set_inbox():
    name  = request.get_json().get("name")
    vibes = load_vibes()
    if name:
        vibes["_inbox"] = name
    else:
        vibes.pop("_inbox", None)
    save_vibes(vibes)
    return jsonify({"ok": True})


@app.route("/api/tracks")
def get_tracks():
    try:
        tree = ET.parse(get_xml_path())
        root = tree.getroot()
        tracks = []
        for t in root.find("COLLECTION"):
            tracks.append({
                "id":     t.get("TrackID"),
                "name":   t.get("Name", ""),
                "artist": t.get("Artist", ""),
                "bpm":    t.get("AverageBpm", ""),
                "key":    t.get("Tonality", ""),
            })
        tracks.sort(key=lambda t: (t["artist"].lower(), t["name"].lower()))
        return jsonify(tracks)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/assign", methods=["POST"])
def assign_track():
    data = request.get_json()
    track_id = data.get("track_id")
    if not track_id:
        return jsonify({"error": "No track_id"}), 400
    playlist = data.get("playlist", "")
    session_assignments[track_id] = {
        "playlist":        playlist,
        "name":            data.get("name", ""),
        "artist":          data.get("artist", ""),
        "bpm":             data.get("bpm", ""),
        "key":             data.get("key", ""),
        "source_playlist": data.get("source_playlist"),
    }
    if playlist:
        session_modified_playlists.add(playlist)
    return jsonify({"ok": True})


@app.route("/api/unassign", methods=["POST"])
def unassign_track():
    data = request.get_json()
    track_id = data.get("track_id")
    if not track_id:
        return jsonify({"error": "No track_id"}), 400
    session_assignments.pop(track_id, None)
    return jsonify({"ok": True})


@app.route("/api/session/modified")
def get_modified_playlists():
    counts = {}
    for info in session_assignments.values():
        pl = info.get("playlist", "")
        if pl:
            counts[pl] = counts.get(pl, 0) + 1
    result = [
        {"playlist": pl, "count": counts.get(pl, 0)}
        for pl in sorted(session_modified_playlists)
    ]
    return jsonify(result)


@app.route("/api/session")
def get_session():
    result = [
        {"track_id": tid, **info}
        for tid, info in session_assignments.items()
    ]
    return jsonify(result)


@app.route("/api/export", methods=["POST"])
def export_xml():
    if not session_assignments:
        return jsonify({"error": "No assignments to export"}), 400

    tree = ET.parse(get_xml_path())
    root = tree.getroot()
    playlists_root = root.find("PLAYLISTS")[0]  # ROOT node

    placed = 0
    for track_id, info in session_assignments.items():
        # Add to target playlist
        playlist_name = info["playlist"]
        node = next(
            (n for n in playlists_root if n.get("Name") == playlist_name),
            None
        )
        if node is None:
            node = ET.SubElement(playlists_root, "NODE",
                                 Type="1", Name=playlist_name,
                                 KeyType="0", Entries="0")
        existing = {t.get("Key") for t in node.findall("TRACK")}
        if track_id not in existing:
            ET.SubElement(node, "TRACK", Key=track_id)
            node.set("Entries", str(len(node.findall("TRACK"))))
            placed += 1

        # Remove from source (queue) playlist
        source = info.get("source_playlist")
        if source:
            src_node = next(
                (n for n in playlists_root if n.get("Name") == source),
                None
            )
            if src_node is not None:
                for t in src_node.findall("TRACK"):
                    if t.get("Key") == track_id:
                        src_node.remove(t)
                        src_node.set("Entries", str(len(src_node.findall("TRACK"))))
                        break

    # Reorder playlist nodes in XML to match user-defined order
    vibes = load_vibes()
    order = vibes.get("_order", [])
    if order:
        playlist_nodes = list(playlists_root)
        order_map = {name: i for i, name in enumerate(order)}
        playlist_nodes.sort(key=lambda n: order_map.get(n.get("Name", ""), len(order)))
        for child in playlist_nodes:
            playlists_root.remove(child)
        for child in playlist_nodes:
            playlists_root.append(child)

    import io
    buf = io.StringIO()
    tree.write(buf, encoding="unicode", xml_declaration=True)
    xml_bytes = buf.getvalue().encode("utf-8")
    return Response(
        xml_bytes,
        mimetype="application/xml",
        headers={
            "Content-Disposition": 'attachment; filename="Rekordbox_krate.xml"',
            "X-Krate-Count": str(placed),
        }
    )


def location_to_path(location):
    """Convert Rekordbox Location URL to OS file path."""
    # file://localhost/D:/... → D:/...
    path = unquote(location)
    for prefix in ("file://localhost/", "file:///", "file://"):
        if path.startswith(prefix):
            path = path[len(prefix):]
            break
    # Normalise slashes on Windows
    return os.path.normpath(path)


def find_track_path(track_id):
    """Return (os_path, mime) for a given TrackID, or (None, None)."""
    try:
        tree = ET.parse(get_xml_path())
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


@app.route("/api/track/<track_id>/audio")
def serve_audio(track_id):
    path, mime = find_track_path(track_id)
    if not path or not os.path.exists(path):
        return jsonify({"error": "File not found"}), 404
    return send_file(path, mimetype=mime, conditional=True)


@app.route("/api/track/<track_id>/art")
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
    except ImportError:
        pass
    except Exception:
        pass
    return ("", 404)


@app.route("/api/xml-info")
def xml_info():
    path = get_xml_path()
    return jsonify({
        "path":     path,
        "filename": os.path.basename(path),
        "exists":   os.path.exists(path),
    })


@app.route("/api/set-xml", methods=["POST"])
def set_xml_route():
    # Option A: file upload
    if "file" in request.files:
        f = request.files["file"]
        if not f.filename.lower().endswith(".xml"):
            return jsonify({"error": "Solo se aceptan archivos .xml"}), 400
        fname = os.path.basename(f.filename) or "Rekordbox_custom.xml"
        save_path = os.path.join(BASE, fname)
        f.save(save_path)
        vibes = load_vibes()
        vibes["_xml_path"] = save_path
        save_vibes(vibes)
        return jsonify({"ok": True, "filename": fname})

    # Option B: path string
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    if not path:
        return jsonify({"error": "No se proporcionó archivo ni ruta"}), 400
    if not path.lower().endswith(".xml"):
        return jsonify({"error": "Solo se aceptan archivos .xml"}), 400
    if not os.path.exists(path):
        return jsonify({"error": "Archivo no encontrado"}), 400
    vibes = load_vibes()
    vibes["_xml_path"] = path
    save_vibes(vibes)
    return jsonify({"ok": True, "filename": os.path.basename(path)})


@app.route("/download/krate_audio.bat")
def download_bat():
    return send_file("krate_audio.bat", as_attachment=True)


@app.route("/download/krate_audio.py")
def download_py():
    return send_file("krate_audio.py", as_attachment=True)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
