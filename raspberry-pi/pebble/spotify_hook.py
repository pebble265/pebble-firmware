"""Run by spotifyd on every player event (on_song_change_hook).

spotifyd passes the event in environment variables (PLAYER_EVENT, TRACK_ID, POSITION_MS, …,
and in newer versions the track name / artists / album / cover too). This writes them to
~/.cache/pebble-spotifyd/now.json so the app can show what is playing even when MPRIS
(D-Bus) isn't reachable. Standalone: no imports from the app, finishes in milliseconds.
"""
import json
import os
import sys
import time

KEEP_PREFIXES = ("PLAYER_", "TRACK_", "OLD_TRACK", "POSITION", "DURATION", "VOLUME", "NAME", "TITLE",
                 "ARTIST", "ALBUM", "COVER", "URI", "ITEM_", "SHOW_", "EPISODE", "SINK", "SHUFFLE", "REPEAT")


def main() -> None:
    cache = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/.cache/pebble-spotifyd")
    env = {k: v for k, v in os.environ.items() if k.startswith(KEEP_PREFIXES)}
    env["_at"] = time.time()
    path = os.path.join(cache, "now.json")
    try:
        with open(path) as f:
            old = json.load(f)
    except Exception:
        old = {}
    # keep the track details from earlier events (e.g. "paused" carries only the id)
    if env.get("TRACK_ID") and env.get("TRACK_ID") == old.get("TRACK_ID"):
        merged = {**old, **env}
    else:
        merged = env
    events = (old.get("_events") or [])[-9:] + [env.get("PLAYER_EVENT", "")]
    merged["_events"] = events
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(merged, f, ensure_ascii=False)
    os.replace(tmp, path)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
