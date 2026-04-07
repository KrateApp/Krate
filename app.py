import json
import os
import re
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
# ID-BASED VIBES  —  internal format
#
# playlist_vibes.json schema (new):
#   "_id_map":   { "42": "Dark Room", ... }   ← id→current_name (from XML)
#   "42":        "vibe text"                  ← vibe keyed by playlist ID
#   "_names":    { "42": "My Custom Name" }   ← user display-name overrides
#   "_inbox":    "42"                         ← playlist ID
#   "_ignored":  ["7", "12"]                  ← list of playlist IDs
#   "_order":    ["42", "7", "12"]            ← list of playlist IDs
#   "_virtual":  ["v_1"]                      ← virtual playlist IDs (user-created)
#   "_deleted":  ["5"]                        ← deleted playlist IDs
#   "_xml_path": "/path/to/file.xml"          ← stored path
#
# Virtual playlists (AI-suggested, not yet in XML) use string IDs like "v_1".
# ---------------------------------------------------------------


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
# XML PLAYLIST PARSING
# ---------------------------------------------------------------

def parse_xml_playlists(xml_root):
    """
    Returns:
        id_to_name:  { playlist_id: name }
        name_to_id:  { name: playlist_id }
        id_to_tracks: { playlist_id: [track_id, ...] }
    """
    id_to_name   = {}
    name_to_id   = {}
    id_to_tracks = {}
    playlists_root = xml_root.find("PLAYLISTS")
    if playlists_root is None or len(playlists_root) == 0:
        return id_to_name, name_to_id, id_to_tracks
    for node in playlists_root[0]:
        pl_id   = node.get("Id")
        pl_name = node.get("Name")
        if not pl_id or not pl_name:
            continue
        id_to_name[pl_id]   = pl_name
        name_to_id[pl_name] = pl_id
        id_to_tracks[pl_id] = [e.get("Key") for e in node.findall("TRACK")]
    return id_to_name, name_to_id, id_to_tracks


# ---------------------------------------------------------------
# MIGRATION  —  name-keyed JSON → ID-keyed JSON
# ---------------------------------------------------------------

def _is_old_format(vibes):
    """Return True if the JSON uses playlist names as keys (old format)."""
    return "_id_map" not in vibes


def migrate_vibes_to_id_keys(vibes, xml_root):
    """
    Convert a name-keyed vibes dict to ID-keyed in-place (returns new dict).
    Uses the XML to build the name→ID mapping.
    Any playlist name that doesn't appear in the XML is dropped silently.
    """
    id_to_name, name_to_id, _ = parse_xml_playlists(xml_root)

    new_vibes = {"_id_map": id_to_name}

    # Preserve xml_path
    if "_xml_path" in vibes:
        new_vibes["_xml_path"] = vibes["_xml_path"]

    # Migrate vibe texts  (skip meta keys)
    meta_keys = {"_ignored", "_order", "_inbox", "_virtual", "_deleted",
                 "_names", "_xml_path"}
    for key, val in vibes.items():
        if key in meta_keys:
            continue
        if key.startswith("_"):
            continue
        pl_id = name_to_id.get(key)
        if pl_id and isinstance(val, str):
            new_vibes[pl_id] = val

    # Migrate _ignored  (names → IDs)
    old_ignored = vibes.get("_ignored", [])
    new_vibes["_ignored"] = [
        name_to_id[n] for n in old_ignored if n in name_to_id
    ]

    # Migrate _order  (names → IDs)
    old_order = vibes.get("_order", [])
    migrated_order = [name_to_id[n] for n in old_order if n in name_to_id]
    # Append any IDs not in the migrated order
    in_order = set(migrated_order)
    for pl_id in id_to_name:
        if pl_id not in in_order:
            migrated_order.append(pl_id)
    new_vibes["_order"] = migrated_order

    # Migrate _inbox
    old_inbox = vibes.get("_inbox")
    if old_inbox and old_inbox in name_to_id:
        new_vibes["_inbox"] = name_to_id[old_inbox]

    # Migrate _virtual  (names, keep as-is since they're user-generated IDs)
    new_vibes["_virtual"] = vibes.get("_virtual", [])

    # Migrate _deleted  (names → IDs; drop unknown)
    old_deleted = vibes.get("_deleted", [])
    new_vibes["_deleted"] = [
        name_to_id[n] for n in old_deleted if n in name_to_id
    ]

    # Migrate _names  ({ name: display } → { id: display })
    old_names = vibes.get("_names", {})
    new_names = {}
    for n, display in old_names.items():
        pl_id = name_to_id.get(n)
        if pl_id:
            new_names[pl_id] = display
    new_vibes["_names"] = new_names

    return new_vibes


def refresh_id_map(vibes, xml_root):
    """
    After loading an XML, update _id_map with current names from the XML.
    This handles renames: the ID stays the same but the name may have changed.
    """
    id_to_name, _, _ = parse_xml_playlists(xml_root)
    vibes["_id_map"] = id_to_name
    return vibes


# ---------------------------------------------------------------
# NAME ↔ ID RESOLUTION HELPERS
# (used by all routes to translate between API (name) and storage (ID))
# ---------------------------------------------------------------

def _get_id_map(vibes):
    """Return { pl_id: name } from vibes."""
    return vibes.get("_id_map", {})


def _name_to_id(vibes, name):
    """Resolve a playlist name to its ID. Returns None if not found."""
    id_map = _get_id_map(vibes)
    for pl_id, pl_name in id_map.items():
        if pl_name == name:
            return pl_id
    # Check virtual playlists — match by display name stored in _names
    names = vibes.get("_names", {})
    for v_id in vibes.get("_virtual", []):
        if names.get(v_id) == name or v_id == name:
            return v_id
    return None


def _id_to_name(vibes, pl_id):
    """Resolve an ID back to the current Rekordbox name (or display name for virtuals)."""
    id_map = _get_id_map(vibes)
    if pl_id in id_map:
        return id_map[pl_id]
    # Virtual playlists: return their display name
    if pl_id in vibes.get("_virtual", []):
        return vibes.get("_names", {}).get(pl_id) or pl_id
    return pl_id   # fallback: return ID as-is


# ---------------------------------------------------------------
# DATA HELPERS
# ---------------------------------------------------------------

def load_library():
    """
    Returns:
        tracks:    { track_id: {name, artist} }
        playlists: { name: [track_id, ...] }   ← keyed by name for backwards compat
    """
    tree = ET.parse(get_xml_path())
    root = tree.getroot()
    tracks = {}
    for track in root.find("COLLECTION"):
        tid = track.get("TrackID")
        tracks[tid] = {"name": track.get("Name"), "artist": track.get("Artist")}
    _, _, id_to_tracks = parse_xml_playlists(root)
    vibes = load_vibes()
    id_map = _get_id_map(vibes)
    playlists = {}
    for pl_id, track_ids in id_to_tracks.items():
        name = id_map.get(pl_id, pl_id)
        playlists[name] = track_ids
    return tracks, playlists


def _load_xml_root():
    tree = ET.parse(get_xml_path())
    return tree.getroot()


def _ensure_migrated(vibes, xml_root):
    """If vibes are in old name-keyed format, migrate and save them."""
    if _is_old_format(vibes):
        vibes = migrate_vibes_to_id_keys(vibes, xml_root)
        save_vibes(vibes)
    return vibes


def _load_and_migrate():
    """Load vibes, migrating to ID-keyed format if necessary. Returns vibes."""
    vibes = load_vibes()
    if _is_old_format(vibes):
        try:
            xml_root = _load_xml_root()
            vibes = _ensure_migrated(vibes, xml_root)
        except Exception:
            pass  # No XML yet — leave as-is until XML is uploaded
    return vibes


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

    vibes   = _load_and_migrate()
    ignored = vibes.get("_ignored", [])
    inbox_id = vibes.get("_inbox")

    vibes_count = sum(
        1 for k, v in vibes.items()
        if not k.startswith("_")
        and isinstance(v, str) and v.strip() and v != "SKIP"
        and k not in ignored
    )
    active_count = max(0, playlist_count - len(ignored))

    inbox_name = _id_to_name(vibes, inbox_id) if inbox_id else None

    return jsonify({
        "tracks":      track_count,
        "playlists":   playlist_count,
        "hidden":      len(ignored),
        "vibes_set":   vibes_count,
        "vibes_total": active_count,
        "inbox_name":  inbox_name,
    })


@app.route("/api/playlists")
def get_playlists():
    try:
        xml_root = _load_xml_root()
        id_to_name, _, id_to_tracks = parse_xml_playlists(xml_root)
    except Exception:
        id_to_name   = {}
        id_to_tracks = {}

    vibes   = _load_and_migrate()
    ignored = vibes.get("_ignored", [])
    deleted = vibes.get("_deleted", [])
    order   = vibes.get("_order", [])
    virtual = vibes.get("_virtual", [])
    names   = vibes.get("_names", {})

    result  = []
    seen_ids = set()

    for pl_id, pl_name in id_to_name.items():
        if pl_id in deleted:
            continue
        seen_ids.add(pl_id)
        custom_name = names.get(pl_id, "")
        result.append({
            "name":        pl_name,
            "count":       len(id_to_tracks.get(pl_id, [])),
            "vibe":        vibes.get(pl_id, ""),
            "hidden":      pl_id in ignored,
            "virtual":     False,
            "custom_name": custom_name,
        })

    for v_id in virtual:
        if v_id in deleted or v_id in seen_ids:
            continue
        # Virtual playlists: use the stored display name as the external name
        v_name = names.get(v_id) or v_id
        result.append({
            "name":        v_name,
            "count":       0,
            "vibe":        vibes.get(v_id, ""),
            "hidden":      v_id in ignored,
            "virtual":     True,
            "custom_name": "",   # already the display name
        })

    if order:
        # Build a name-based order for the result list
        def _order_key(p):
            pl_id = _name_to_id(vibes, p["name"])
            if pl_id is None:
                pl_id = p["name"]
            try:
                return order.index(pl_id)
            except ValueError:
                return len(order)
        result.sort(key=_order_key)

    return jsonify(result)


@app.route("/api/vibes/<path:playlist>", methods=["POST"])
def set_vibe(playlist):
    """playlist param is a playlist NAME (from the frontend)."""
    data  = request.get_json()
    vibes = _load_and_migrate()
    pl_id = _name_to_id(vibes, playlist)
    if pl_id is None:
        # Unknown playlist — store by name as fallback (shouldn't happen)
        pl_id = playlist
    vibes[pl_id] = data.get("vibe", "")
    save_vibes(vibes)
    return jsonify({"ok": True})


_CREATE_PATTERNS = re.compile(
    r'\b(crea(r|me)?|nueva?\s+(playlist|lista)|new\s+playlist|make\s+(a\s+)?playlist'
    r'|quiero\s+una\s+(lista|playlist)|lista\s+nueva|playlist\s+nueva'
    r'|nueva\s+lista|a\s+new\s+list|create\s+(a\s+)?(playlist|list)'
    r'|hazme\s+una|ponme\s+una|pon\s+una)\b',
    re.IGNORECASE
)

def _is_create_intent(text: str) -> bool:
    return bool(_CREATE_PATTERNS.search(text))


def _display_name(pl_id, vibes):
    """
    Return a clean display name for a playlist ID, suitable for the AI prompt.
    Strips the MASMASMAS prefix and trailing (N) counters.
    """
    custom = vibes.get("_names", {}).get(pl_id)
    raw = custom or _id_to_name(vibes, pl_id)
    raw = re.sub(r'^MASMASMAS\s*[-\u2013]\s*', '', raw, flags=re.IGNORECASE)
    raw = re.sub(r'\s*\(\d+\)\s*$', '', raw)
    return raw.strip()


@app.route("/api/match", methods=["POST"])
def do_match():
    from krate import match_vibe, create_vibe

    data        = request.get_json()
    description = (data.get("description") or "").strip()
    mode        = data.get("mode", "review")

    if not description:
        return jsonify({"error": "No description provided"}), 400

    if _is_create_intent(description):
        try:
            np = create_vibe(description)
            return jsonify({
                "intent":       "create",
                "new_playlist": {"name": np["name"], "vibe": np["vibe"]},
                "suggestions":  [],
            })
        except Exception:
            pass

    vibes   = _load_and_migrate()
    ignored = vibes.get("_ignored", [])

    # Build active_vibes: { display_name: vibe }
    # Keys are display names (for the AI); values are vibe texts.
    active_vibes = {}
    for k, v in vibes.items():
        if k.startswith("_") or not isinstance(v, str) or not v.strip() or v == "SKIP":
            continue
        # k is a playlist ID (or virtual ID)
        pl_id = k
        if pl_id in ignored:
            continue
        display = _display_name(pl_id, vibes)
        active_vibes[display] = v

    # Reverse map: display_name → playlist NAME (for the frontend)
    reverse_map = {}
    id_map = _get_id_map(vibes)
    for pl_id in id_map:
        if pl_id.startswith("_"):
            continue
        display = _display_name(pl_id, vibes)
        reverse_map[display] = id_map[pl_id]
    for v_id in vibes.get("_virtual", []):
        display = _display_name(v_id, vibes)
        reverse_map[display] = v_id

    if not active_vibes:
        return jsonify({"error": "No vibes set up yet"}), 400

    try:
        result = match_vibe(description, active_vibes, mode=mode)
        if mode == "auto":
            pl = result.get("playlist", "")
            result["playlist"] = reverse_map.get(pl, pl)
        else:
            for s in result.get("suggestions", []):
                pl = s.get("playlist", "")
                s["playlist"] = reverse_map.get(pl, pl)
        return jsonify(result)
    except Exception as e:
        import anthropic
        if isinstance(e, anthropic.AuthenticationError):
            return jsonify({"error": "API key inválida. Verifica ANTHROPIC_API_KEY en Railway."}), 500
        if isinstance(e, anthropic.APIStatusError) and e.status_code == 529:
            return jsonify({"error": "overloaded", "message": "La API de Anthropic está saturada. Intenta de nuevo en unos segundos."}), 529
        return jsonify({"error": str(e)}), 500


@app.route("/api/playlists/reorder", methods=["POST"])
def reorder_playlists():
    """Receives a list of playlist NAMES, converts to IDs, saves."""
    names_order = request.get_json().get("order", [])
    vibes = _load_and_migrate()
    id_order = []
    for name in names_order:
        pl_id = _name_to_id(vibes, name)
        id_order.append(pl_id if pl_id else name)
    vibes["_order"] = id_order
    save_vibes(vibes)
    return jsonify({"ok": True})


@app.route("/api/playlists/hide", methods=["POST"])
def hide_playlist():
    name  = request.get_json().get("name")
    vibes = _load_and_migrate()
    pl_id = _name_to_id(vibes, name)
    if not pl_id:
        return jsonify({"ok": True})   # unknown playlist
    ignored = vibes.get("_ignored", [])
    if pl_id not in ignored:
        ignored.append(pl_id)
    vibes["_ignored"] = ignored
    save_vibes(vibes)
    return jsonify({"ok": True})


@app.route("/api/playlists/restore", methods=["POST"])
def restore_playlist():
    name  = request.get_json().get("name")
    vibes = _load_and_migrate()
    pl_id = _name_to_id(vibes, name)
    if not pl_id:
        return jsonify({"ok": True})
    ignored = vibes.get("_ignored", [])
    if pl_id in ignored:
        ignored.remove(pl_id)
    vibes["_ignored"] = ignored
    save_vibes(vibes)
    return jsonify({"ok": True})


@app.route("/api/playlists/rename", methods=["POST"])
def rename_playlist():
    """
    Stores a user-defined display name for a playlist.
    original_name: the current Rekordbox name (used to look up the ID)
    new_display_name: the custom label the user wants to see
    """
    data         = request.get_json()
    original     = (data.get("original_name") or "").strip()
    display_name = (data.get("new_display_name") or "").strip()
    if not original:
        return jsonify({"error": "original_name required"}), 400
    vibes = _load_and_migrate()
    pl_id = _name_to_id(vibes, original)
    if not pl_id:
        return jsonify({"error": "Playlist not found"}), 404
    names = vibes.setdefault("_names", {})
    if display_name:
        names[pl_id] = display_name
    else:
        names.pop(pl_id, None)
    vibes["_names"] = names
    save_vibes(vibes)
    return jsonify({"ok": True})


def _next_virtual_id(vibes):
    """Generate a new unique virtual playlist ID like 'v_1', 'v_2', …"""
    existing = vibes.get("_virtual", [])
    n = 1
    while f"v_{n}" in existing:
        n += 1
    return f"v_{n}"


@app.route("/api/playlists/create", methods=["POST"])
def create_playlist():
    data = request.get_json()
    name = (data.get("name") or "").strip()
    vibe = (data.get("vibe") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    vibes   = _load_and_migrate()
    virtual = vibes.get("_virtual", [])
    deleted = vibes.get("_deleted", [])

    # Check if a virtual playlist with this name already exists
    existing_id = None
    for v_id in virtual:
        if v_id == name or vibes.get("_names", {}).get(v_id) == name:
            existing_id = v_id
            break

    if existing_id:
        pl_id = existing_id
        if pl_id in deleted:
            deleted.remove(pl_id)
            vibes["_deleted"] = deleted
    else:
        pl_id = _next_virtual_id(vibes)
        virtual.append(pl_id)
        vibes["_virtual"] = virtual
        # Store the user-given name as the display name
        names = vibes.setdefault("_names", {})
        names[pl_id] = name

    vibes[pl_id] = vibe
    save_vibes(vibes)
    return jsonify({"ok": True})


@app.route("/api/playlists/delete", methods=["POST"])
def delete_playlist():
    data = request.get_json()
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    vibes   = _load_and_migrate()
    virtual = vibes.get("_virtual", [])
    deleted = vibes.get("_deleted", [])
    pl_id   = _name_to_id(vibes, name)
    if not pl_id:
        return jsonify({"error": "Playlist not found"}), 404

    if pl_id in virtual:
        virtual.remove(pl_id)
        vibes["_virtual"] = virtual
        vibes.pop(pl_id, None)
        vibes.get("_names", {}).pop(pl_id, None)
    else:
        if pl_id not in deleted:
            deleted.append(pl_id)
        vibes["_deleted"] = deleted
        vibes.pop(pl_id, None)
        vibes.get("_names", {}).pop(pl_id, None)

    ignored = vibes.get("_ignored", [])
    if pl_id in ignored:
        ignored.remove(pl_id)
        vibes["_ignored"] = ignored

    save_vibes(vibes)
    return jsonify({"ok": True})


@app.route("/api/playlists/<path:name>/tracks")
def get_playlist_tracks(name):
    try:
        tree = ET.parse(get_xml_path())
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

        vibes = _load_and_migrate()
        pl_id = _name_to_id(vibes, name)

        # Virtual playlist
        if pl_id and pl_id in vibes.get("_virtual", []):
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

        # XML playlist — find by name
        playlists_root = root.find("PLAYLISTS")[0]
        node = next((n for n in playlists_root if n.get("Name") == name), None)
        if node is None:
            return jsonify({"error": "Playlist not found"}), 404

        tracks = []
        xml_track_ids = set()
        for entry in node.findall("TRACK"):
            tid = entry.get("Key")
            if tid in track_map:
                tracks.append(track_map[tid])
                xml_track_ids.add(tid)

        for tid, info in session_assignments.items():
            if info.get("playlist") == name and tid not in xml_track_ids:
                tracks.append({
                    "id":           tid,
                    "name":         info.get("name", ""),
                    "artist":       info.get("artist", ""),
                    "bpm":          info.get("bpm", ""),
                    "key":          info.get("key", ""),
                    "album":        "", "year": "", "duration": "",
                    "genre":        "", "location": "",
                    "session_new":  True,
                })

        return jsonify(tracks)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/inbox")
def get_inbox():
    vibes    = _load_and_migrate()
    inbox_id = vibes.get("_inbox")
    if not inbox_id:
        return jsonify({"name": None, "tracks": []})
    inbox_name = _id_to_name(vibes, inbox_id)
    xml_path   = get_xml_path()
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
    vibes = _load_and_migrate()
    if name:
        pl_id = _name_to_id(vibes, name)
        vibes["_inbox"] = pl_id if pl_id else name
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

    xml_path = get_xml_path()
    if not xml_path or not os.path.exists(xml_path):
        return jsonify({"error": "XML no encontrado. Sube tu Rekordbox.xml antes de exportar."}), 400

    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as e:
        return jsonify({"error": f"XML inválido: {e}"}), 400

    root = tree.getroot()
    playlists_node = root.find("PLAYLISTS")
    if playlists_node is None or len(playlists_node) == 0:
        return jsonify({"error": "El XML no tiene nodo PLAYLISTS válido."}), 400
    playlists_root = playlists_node[0]

    placed = 0
    for track_id, info in session_assignments.items():
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
    vibes = _load_and_migrate()
    order = vibes.get("_order", [])
    id_map = _get_id_map(vibes)
    if order:
        # Build name-based order for sorting XML nodes
        id_to_pos = {pl_id: i for i, pl_id in enumerate(order)}
        # name → position via id_map
        name_to_pos = {name: id_to_pos[pl_id]
                       for pl_id, name in id_map.items()
                       if pl_id in id_to_pos}
        playlist_nodes = list(playlists_root)
        playlist_nodes.sort(key=lambda n: name_to_pos.get(n.get("Name", ""), len(order)))
        for child in playlist_nodes:
            playlists_root.remove(child)
        for child in playlist_nodes:
            playlists_root.append(child)

    try:
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
    except Exception as e:
        return jsonify({"error": f"Error al generar XML: {e}"}), 500


def location_to_path(location):
    path = unquote(location)
    for prefix in ("file://localhost/", "file:///", "file://"):
        if path.startswith(prefix):
            path = path[len(prefix):]
            break
    return os.path.normpath(path)


def find_track_path(track_id):
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
    global session_assignments, session_modified_playlists

    if "file" in request.files:
        f = request.files["file"]
        if not f.filename.lower().endswith(".xml"):
            return jsonify({"error": "Solo se aceptan archivos .xml"}), 400
        fname     = os.path.basename(f.filename) or "Rekordbox_custom.xml"
        save_path = os.path.join(BASE, fname)
        f.save(save_path)

        # Parse XML and update/migrate vibes
        try:
            tree     = ET.parse(save_path)
            xml_root = tree.getroot()
            vibes    = load_vibes()
            if _is_old_format(vibes):
                vibes = migrate_vibes_to_id_keys(vibes, xml_root)
            else:
                vibes = refresh_id_map(vibes, xml_root)
            vibes["_xml_path"] = save_path
            save_vibes(vibes)
        except Exception:
            vibes = load_vibes()
            vibes["_xml_path"] = save_path
            save_vibes(vibes)

        session_assignments        = {}
        session_modified_playlists = set()
        return jsonify({"ok": True, "filename": fname})

    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    if not path:
        return jsonify({"error": "No se proporcionó archivo ni ruta"}), 400
    if not path.lower().endswith(".xml"):
        return jsonify({"error": "Solo se aceptan archivos .xml"}), 400
    if not os.path.exists(path):
        return jsonify({"error": "Archivo no encontrado"}), 400

    try:
        tree     = ET.parse(path)
        xml_root = tree.getroot()
        vibes    = load_vibes()
        if _is_old_format(vibes):
            vibes = migrate_vibes_to_id_keys(vibes, xml_root)
        else:
            vibes = refresh_id_map(vibes, xml_root)
        vibes["_xml_path"] = path
        save_vibes(vibes)
    except Exception:
        vibes = load_vibes()
        vibes["_xml_path"] = path
        save_vibes(vibes)

    session_assignments        = {}
    session_modified_playlists = set()
    return jsonify({"ok": True, "filename": os.path.basename(path)})


@app.route("/api/startup", methods=["POST"])
def startup():
    global session_assignments, session_modified_playlists

    if "xml" not in request.files:
        return jsonify({"error": "Se requiere el archivo XML"}), 400
    xml_file = request.files["xml"]
    if not xml_file.filename.lower().endswith(".xml"):
        return jsonify({"error": "Solo se aceptan archivos .xml"}), 400

    xml_fname = os.path.basename(xml_file.filename) or "Rekordbox_custom.xml"
    xml_save  = os.path.join(BASE, xml_fname)
    xml_file.save(xml_save)

    # Parse vibes file if provided
    if "vibes" in request.files:
        try:
            vibes_data = json.loads(request.files["vibes"].read().decode("utf-8"))
            if not isinstance(vibes_data, dict):
                vibes_data = {}
        except Exception:
            vibes_data = {}
    else:
        vibes_data = {}

    # Parse XML, migrate/refresh vibes
    try:
        tree     = ET.parse(xml_save)
        xml_root = tree.getroot()
        if _is_old_format(vibes_data):
            vibes_data = migrate_vibes_to_id_keys(vibes_data, xml_root)
        else:
            vibes_data = refresh_id_map(vibes_data, xml_root)
    except Exception:
        pass  # If XML parse fails, proceed with vibes as-is

    vibes_data["_xml_path"] = xml_save
    save_vibes(vibes_data)
    session_assignments        = {}
    session_modified_playlists = set()

    return jsonify({"ok": True, "filename": xml_fname})


@app.route("/api/vibes")
def get_vibes():
    return jsonify(_load_and_migrate())


@app.route("/api/vibes/export")
def export_vibes():
    vibes = _load_and_migrate()
    return Response(
        json.dumps(vibes, indent=2, ensure_ascii=False),
        mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=krate_vibes.json"}
    )


@app.route("/api/vibes/import", methods=["POST"])
def import_vibes():
    if "file" not in request.files:
        return jsonify({"error": "No se proporcionó archivo"}), 400
    f = request.files["file"]
    if not f.filename.lower().endswith(".json"):
        return jsonify({"error": "Solo se aceptan archivos .json"}), 400
    try:
        data = json.loads(f.read().decode("utf-8"))
    except Exception:
        return jsonify({"error": "Archivo JSON inválido"}), 400
    if not isinstance(data, dict):
        return jsonify({"error": "Formato incorrecto"}), 400
    # Preserve the current xml_path
    current_vibes = load_vibes()
    if "_xml_path" in current_vibes:
        data.setdefault("_xml_path", current_vibes["_xml_path"])
    save_vibes(data)
    # Trigger migration if XML is available
    _load_and_migrate()
    return jsonify({"ok": True})


@app.route("/download/krate_audio.bat")
def download_bat():
    return send_file("krate_audio.bat", as_attachment=True)


@app.route("/download/krate_audio.py")
def download_py():
    return send_file("krate_audio.py", as_attachment=True)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
