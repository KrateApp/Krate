import xml.etree.ElementTree as ET
import anthropic
import json
import os
import re

def _strip_json_fences(raw: str) -> str:
    """Quita fences de markdown (```json ... ```) de forma robusta."""
    raw = raw.strip()
    raw = re.sub(r'^```(?:json)?\s*\n?', '', raw)
    raw = re.sub(r'\n?```\s*$', '', raw)
    return raw.strip()


# ---------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------

XML_PATH   = "rekordbox.xml"
VIBES_FILE = "playlist_vibes.json"   # lives next to rekordbox.xml

client = anthropic.Anthropic()       # reads ANTHROPIC_API_KEY from env


# ---------------------------------------------------------------
# LIBRARY LOADER  (Phase 2 — unchanged)
# ---------------------------------------------------------------

def load_library(xml_path=XML_PATH):
    tree = ET.parse(xml_path)
    root = tree.getroot()

    tracks = {}
    for track in root.find("COLLECTION"):
        track_id = track.get("TrackID")
        tracks[track_id] = {
            "name":   track.get("Name"),
            "artist": track.get("Artist"),
            "bpm":    track.get("AverageBpm"),
            "key":    track.get("Tonality"),
        }

    playlists = {}
    for node in root.find("PLAYLISTS")[0]:
        name      = node.get("Name")
        track_ids = [entry.get("Key") for entry in node]
        playlists[name] = track_ids

    return tracks, playlists


# ---------------------------------------------------------------
# VIBE STORAGE
# ---------------------------------------------------------------

def load_vibes():
    if os.path.exists(VIBES_FILE):
        with open(VIBES_FILE) as f:
            return json.load(f)
    return {}


def save_vibes(vibes):
    with open(VIBES_FILE, "w") as f:
        json.dump(vibes, f, indent=2, ensure_ascii=False)
    print(f"\n✓ Vibes saved → {VIBES_FILE}")


# ---------------------------------------------------------------
# VIBE MATCHING  (the actual AI call)
# ---------------------------------------------------------------

def match_vibe(song_description: str, playlist_vibes: dict, mode: str = "review", excluded_playlists: list = None):
    """
    Send a song description + playlist vibes to Claude.
    Returns parsed JSON — either a single suggestion (auto)
    or a list of suggestions (review).
    """

    playlist_block = "\n".join(
        f'- "{name}": {vibe}'
        for name, vibe in playlist_vibes.items()
    )

    excluded_block = ""
    if excluded_playlists:
        excluded_list = ", ".join(f'"{p}"' for p in excluded_playlists)
        excluded_block = f"\nIMPORTANT: The user already saw suggestions for {excluded_list} and wants different options. Do NOT suggest any of those playlists. Explore other options.\n"

    if mode == "auto":
        prompt = f"""You are Krate, a vibe-based DJ playlist sorter.
Your job is to match tracks to playlists based on feeling and energy — not genre tags.
The track description may be in any language — English, Spanish, or anything else. Understand it as-is and write your reason in the same language.
{excluded_block}
TRACK DESCRIPTION:
{song_description}

PLAYLISTS AND THEIR VIBES:
{playlist_block}

Pick the single best playlist. If nothing fits well, say so honestly.

Respond with JSON only — no markdown, no explanation outside the JSON:
{{"playlist": "exact playlist name", "reason": "one punchy sentence on why the vibe matches"}}"""

    else:  # review mode
        prompt = f"""You are Krate, a vibe-based DJ playlist sorter.
Your job is to match tracks to playlists based on feeling and energy — not genre tags.
The track description may be in any language — English, Spanish, or anything else. Understand it as-is and write your reasons in the same language.
{excluded_block}
TRACK DESCRIPTION:
{song_description}

PLAYLISTS AND THEIR VIBES:
{playlist_block}

Suggest up to 5 best fits. Be honest — if only one or two actually work, give those.
If nothing fits, say so with a brief reason.

If the track truly doesn't fit any existing playlist well, you may suggest creating a new one by including a "new_playlist" field.
Only include "new_playlist" if genuinely warranted — omit it entirely when existing playlists cover this track well.

Respond with JSON only — no markdown, no explanation outside the JSON:
{{
  "suggestions": [
    {{"playlist": "exact playlist name", "reason": "one punchy sentence"}},
    {{"playlist": "exact playlist name", "reason": "one punchy sentence"}},
    {{"playlist": "exact playlist name", "reason": "one punchy sentence"}}
  ],
  "new_playlist": {{"name": "Short Playlist Name", "vibe": "one-sentence vibe description"}}
}}

Omit "new_playlist" entirely if existing playlists cover this track well."""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = _strip_json_fences(message.content[0].text)
    return json.loads(raw)


def create_vibe(description: str) -> dict:
    """
    Dado el request explícito del usuario para crear una playlist,
    extrae un nombre corto y una descripción de vibe en una oración.
    Returns: {"name": "...", "vibe": "..."}
    """
    prompt = f"""You are Krate, a DJ playlist assistant.
The user wants to CREATE a new playlist. Their request may be in any language.
Extract or invent a short, punchy playlist name and a one-sentence vibe description.
The vibe should describe the energy, mood, and context a DJ would use this playlist for.
Write the vibe in the same language the user wrote in.

USER REQUEST:
{description}

Respond with JSON only — no markdown, no explanation:
{{"name": "Short Playlist Name", "vibe": "one-sentence vibe for a DJ"}}"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = _strip_json_fences(message.content[0].text)
    return json.loads(raw)




def chat_vibe(mode: str, playlist_name: str, history: list, current_vibe: str = None) -> dict:
    """
    Flujo conversacional para crear o refinar el vibe de una playlist.
    mode: 'create' | 'refine'
    history: lista de {role: 'user'|'assistant', content: str}
    Devuelve:
      {"type": "question", "message": "..."}        — IA necesita más info
      {"type": "suggestion", "name": "...", "vibe": "..."}  — IA tiene sugerencia
    """
    current_block = f'\nVibe actual de la playlist: "{current_vibe}"' if current_vibe else ""
    mode_context = (
        "El DJ quiere CREAR una nueva playlist y necesita nombre y descripción de vibe."
        if mode == "create"
        else f"El DJ quiere REFINAR la descripción de vibe de su playlist existente \"{playlist_name}\".{current_block}"
    )

    system = f"""Eres Krate, un asistente para DJs que ayuda a definir el vibe de playlists.
{mode_context}

Tu trabajo es hacer preguntas conversacionales para entender qué siente el DJ sobre esta playlist — su energía, mood, momento en el set, contexto.
Habla en el mismo idioma que use el DJ (español o inglés).
Sé conciso y directo. Máximo 2 preguntas por turno.

Cuando tengas suficiente información (generalmente después de 1-3 intercambios), proporciona una sugerencia.

IMPORTANTE — responde siempre con JSON:
- Si necesitas más información: {{"type": "question", "message": "tu pregunta aquí"}}
- Si tienes suficiente información: {{"type": "suggestion", "name": "Nombre Corto", "vibe": "Una oración que describa el vibe para un DJ"}}

Para mode=refine, el "name" debe ser el mismo que la playlist actual (no lo cambies a menos que el DJ pida cambiarlo).
Responde SOLO con JSON, sin texto adicional."""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        system=system,
        messages=history
    )
    raw = _strip_json_fences(message.content[0].text)
    return json.loads(raw)


# ---------------------------------------------------------------
# SETUP MODE  — write vibe descriptions for your playlists
# ---------------------------------------------------------------

def setup_vibes(playlists: dict, vibes: dict) -> dict:
    ignored = vibes.get("_ignored", [])
    active_playlists = [k for k in playlists if k not in ignored]

    while True:
        print("\n=== VIBE SETUP ===\n")
        for i, name in enumerate(active_playlists, 1):
            vibe = vibes.get(name, "").strip()
            marker = "✓" if vibe else "○"
            preview = f"  {vibe[:60]}{'…' if len(vibe) > 60 else ''}" if vibe else ""
            print(f"  {i}. {marker} {name}{preview}")
        print("  0. Back to menu\n")

        pick = input("→ Pick a playlist to describe: ").strip()

        if pick == "0" or not pick:
            break
        elif pick.isdigit() and 1 <= int(pick) <= len(active_playlists):
            name = active_playlists[int(pick) - 1]
            current = vibes.get(name, "").strip()
            print(f"\n  {name}")
            if current:
                print(f"  Current: {current}")
            new = input("  New description (Enter to keep): ").strip() if current else input("  Describe the vibe: ").strip()
            if new:
                vibes[name] = new
                save_vibes(vibes)
                print(f"  ✓ Saved.")
        else:
            print("  Invalid choice.")

    return vibes


# ---------------------------------------------------------------
# SORT MODE  — describe a track, get playlist suggestions
# ---------------------------------------------------------------

def sort_track(vibes: dict, mode: str = "review"):
    if not vibes:
        print("\n⚠  No playlist vibes set up yet.")
        print("   Run option 1 first to describe your playlists.")
        return

    active_vibes = {k: v for k, v in vibes.items() if k != "_ignored" and v.strip()}
    print(f"\n=== SORT MODE — {mode.upper()} ===")
    print(f"Matching against {len(active_vibes)} playlists with vibe descriptions.")
    print("Describe a track in your own words. Type 'quit' to exit.\n")

    while True:
        print("─" * 55)
        song_desc = input("Track description > ").strip()

        if song_desc.lower() in ("quit", "exit", "q"):
            break
        if not song_desc:
            continue

        print("  Matching...\n")

        try:
            result = match_vibe(song_desc, active_vibes, mode=mode)
        except json.JSONDecodeError as e:
            print(f"  ⚠  Couldn't parse AI response: {e}")
            continue
        except Exception as e:
            print(f"  ⚠  Error: {e}")
            continue

        if mode == "auto":
            print(f"  → {result['playlist']}")
            print(f"     {result['reason']}")

        else:  # review
            suggestions = result.get("suggestions", [])
            if not suggestions:
                print("  No strong matches found.")
            else:
                for i, s in enumerate(suggestions, 1):
                    print(f"  {i}. {s['playlist']}")
                    print(f"     {s['reason']}")

        print()


# ---------------------------------------------------------------
# MAIN MENU
# ---------------------------------------------------------------

def main():
    print("\n╔═══════════════════════════════════╗")
    print("║          K R A T E                ║")
    print("║   vibe-first playlist sorter      ║")
    print("╚═══════════════════════════════════╝\n")

    try:
        tracks, playlists = load_library()
    except FileNotFoundError:
        print(f"⚠  Could not find {XML_PATH}")
        print("   Make sure rekordbox.xml is in the same folder as krate.py.")
        return

    vibes = load_vibes()

    while True:
        vibes_count = sum(1 for k, v in vibes.items() if k != "_ignored" and v.strip())
        ignored_count = len(vibes.get("_ignored", []))
        active_count = len(playlists) - ignored_count

        print(f"\nLibrary  : {len(tracks)} tracks, {len(playlists)} playlists ({ignored_count} hidden)")
        print(f"Vibes set: {vibes_count} / {active_count} playlists described")

        print("\n  1  Set up / update playlist vibes")
        print("  2  Sort a track — review mode  (AI suggests, you decide)")
        print("  3  Sort a track — auto mode    (AI places it directly)")
        print("  4  Show all current vibe descriptions")
        print("  5  Remove a playlist from Krate")
        print("  6  Restore a hidden playlist")
        print("  0  Quit")
        print()

        choice = input("→ ").strip()

        if choice == "0":
            print("\n  Later.\n")
            break

        elif choice == "1":
            vibes = setup_vibes(playlists, vibes)

        elif choice == "2":
            sort_track(vibes, mode="review")

        elif choice == "3":
            sort_track(vibes, mode="auto")

        elif choice == "4":
            print("\n=== CURRENT VIBE DESCRIPTIONS ===\n")
            ignored = vibes.get("_ignored", [])
            for name, vibe in vibes.items():
                if name == "_ignored" or name in ignored:
                    continue
                marker = "✓" if vibe.strip() else "○"
                print(f"{marker} {name}")
                if vibe.strip():
                    print(f"  {vibe}\n")

        elif choice == "5":
            ignored = vibes.get("_ignored", [])
            active = [p for p in playlists if p not in ignored]
            if not active:
                print("\n  No playlists to remove.")
            else:
                print("\n=== REMOVE PLAYLIST FROM KRATE ===")
                print("  These playlists will be hidden from setup and matching.\n")
                for i, name in enumerate(active, 1):
                    has_vibe = "✓" if vibes.get(name, "").strip() else "○"
                    print(f"  {i}. {has_vibe} {name}")
                print("  0. Cancel")
                pick = input("\n→ ").strip()
                if pick == "0" or not pick:
                    print("  Cancelled.")
                elif pick.isdigit() and 1 <= int(pick) <= len(active):
                    target = active[int(pick) - 1]
                    ignored.append(target)
                    vibes["_ignored"] = ignored
                    if target in vibes:
                        del vibes[target]
                    save_vibes(vibes)
                    print(f"\n  ✓ {target} removed from Krate.")

        elif choice == "6":
            ignored = vibes.get("_ignored", [])
            if not ignored:
                print("\n  No hidden playlists to restore.")
            else:
                print("\n=== RESTORE A PLAYLIST ===\n")
                for i, name in enumerate(ignored, 1):
                    print(f"  {i}. {name}")
                print("  0. Cancel")
                pick = input("\n→ ").strip()
                if pick == "0" or not pick:
                    print("  Cancelled.")
                elif pick.isdigit() and 1 <= int(pick) <= len(ignored):
                    target = ignored[int(pick) - 1]
                    ignored.remove(target)
                    vibes["_ignored"] = ignored
                    save_vibes(vibes)
                    print(f"\n  ✓ {target} restored.")

        else:
            print("  Type a number from the menu.")


if __name__ == "__main__":
    main()
