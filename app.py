import os
import re
import sys
import json
import time
import glob
import shutil
import threading
import subprocess
import urllib.request
import urllib.parse
import io
import zipfile
import tomllib
import secrets
import hmac

from flask import Flask, render_template, request, jsonify, session, make_response

APP_NAME = "RamFreeMCHosting"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LEGACY_DIR = os.path.join(BASE_DIR, "server_data")
CONFIG_FILE = os.path.join(BASE_DIR, "panel_config.json")
UA = {"User-Agent": "RamFreeMCHosting/1.0"}
MODRINTH = "https://api.modrinth.com/v2"

RAM_OPTIONS = ["1G", "2G", "3G", "4G", "6G", "8G", "10G", "12G", "16G", "24G", "30G"]
MAX_SERVERS = 10
NAME_MAX = 30
SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Har naye server me ye plugins apne aap lagte hain (purane/naye versions ke players ke liye)
VIA_PLUGINS = ["viaversion", "viabackwards"]

# Internet se list na aaye to ye common versions dikhenge
FALLBACK_VERSIONS = [
    "1.21.8", "1.21.7", "1.21.6", "1.21.5", "1.21.4", "1.21.3", "1.21.1",
    "1.20.6", "1.20.4", "1.20.2", "1.20.1", "1.19.4", "1.18.2", "1.17.1",
]

# Properties editor me ye settings badal sakte hain
PROP_SCHEMA = [
    {"key": "motd", "label": "Server ka naam (MOTD)", "type": "text"},
    {"key": "difficulty", "label": "Difficulty", "type": "select",
     "options": ["peaceful", "easy", "normal", "hard"]},
    {"key": "gamemode", "label": "Default gamemode", "type": "select",
     "options": ["survival", "creative", "adventure", "spectator"]},
    {"key": "max-players", "label": "Max players", "type": "int", "min": 1, "max": 200},
    {"key": "view-distance", "label": "View distance", "type": "int", "min": 3, "max": 32},
    {"key": "simulation-distance", "label": "Simulation distance", "type": "int", "min": 3, "max": 32},
    {"key": "spawn-protection", "label": "Spawn protection (blocks)", "type": "int", "min": 0, "max": 100},
    {"key": "pvp", "label": "PvP (ladai)", "type": "bool"},
    {"key": "allow-flight", "label": "Flight allow", "type": "bool"},
    {"key": "hardcore", "label": "Hardcore", "type": "bool"},
    {"key": "allow-nether", "label": "Nether", "type": "bool"},
    {"key": "spawn-monsters", "label": "Monsters spawn", "type": "bool"},
    {"key": "white-list", "label": "Whitelist on (internet pe ON rakho)", "type": "bool"},
    {"key": "enforce-whitelist", "label": "Whitelist enforce", "type": "bool"},
    {"key": "online-mode", "label": "Online mode (TLauncher ke liye OFF)", "type": "bool"},
]

# File me koi key na ho to editor me ye default dikhte hain
DEFAULT_PROPS = {
    "motd": "A Minecraft Server",
    "difficulty": "easy",
    "gamemode": "survival",
    "max-players": "20",
    "view-distance": "10",
    "simulation-distance": "10",
    "spawn-protection": "16",
    "pvp": "true",
    "allow-flight": "false",
    "hardcore": "false",
    "allow-nether": "true",
    "spawn-monsters": "true",
    "white-list": "false",
    "enforce-whitelist": "false",
    "online-mode": "true",
}

# Naya server banate waqt world type chun sakte hain
WORLD_TYPES = [
    {"value": "minecraft\\:normal", "label": "Normal"},
    {"value": "minecraft\\:flat", "label": "Flat (superflat)"},
    {"value": "minecraft\\:large_biomes", "label": "Large Biomes"},
    {"value": "minecraft\\:amplified", "label": "Amplified (ooche pahad)"},
]

app = Flask(__name__)

# ---------------- Session + CSRF security ----------------
# Secret key ko disk par persist karte hain, taaki app restart ke baad sessions invalidate na hon.
SECRET_KEY_FILE = os.path.join(BASE_DIR, ".flask_secret_key")

def load_or_create_secret_key():
    env_key = os.environ.get("FLASK_SECRET_KEY", "").strip()
    if env_key:
        return env_key
    try:
        with open(SECRET_KEY_FILE, "r", encoding="utf-8") as f:
            existing = f.read().strip()
        if len(existing) >= 32:
            return existing
    except OSError:
        pass
    new_key = secrets.token_hex(32)
    try:
        with open(SECRET_KEY_FILE, "w", encoding="utf-8") as f:
            f.write(new_key)
    except OSError as exc:
        raise RuntimeError("Flask secret key file create nahi hui: " + str(exc))
    return new_key

app.config.update(
    SECRET_KEY=load_or_create_secret_key(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # Localhost HTTP par False; HTTPS hosting par FLASK_COOKIE_SECURE=1 set karo.
    SESSION_COOKIE_SECURE=os.environ.get("FLASK_COOKIE_SECURE", "0") == "1",
)

CSRF_SESSION_KEY = "csrf_token"

@app.before_request
def protect_state_changing_requests():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        expected = session.get(CSRF_SESSION_KEY, "")
        supplied = request.headers.get("X-CSRF-Token", "")
        if not expected or not supplied or not hmac.compare_digest(expected, supplied):
            return jsonify({
                "ok": False,
                "message": "CSRF token missing ya invalid hai. Page refresh karke dobara try karo."
            }), 403

@app.route("/api/csrf-token", methods=["GET"])
def api_csrf_token():
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    response = make_response(jsonify({"ok": True, "csrf_token": token}))
    response.headers["Cache-Control"] = "no-store"
    return response

@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response

state = {
    "proc": None,
    "run_id": "",
    "setting_up": False,
    "setup_id": "",
    "logs": [],
    "next_id": 0,
    "lock": threading.Lock(),
}

cfg_lock = threading.Lock()
config = {"servers": [], "selected": ""}
versions_cache = {"time": 0.0, "list": []}


# ---------------- Helpers ----------------
def add_log(text):
    with state["lock"]:
        state["next_id"] += 1
        state["logs"].append((state["next_id"], text))
        if len(state["logs"]) > 2000:
            del state["logs"][:500]


def is_running():
    p = state["proc"]
    return p is not None and p.poll() is None


def fetch_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def download(url, dest):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=180) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            f.write(chunk)


def creation_flags():
    return subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0


def java_major(java_path):
    """Java executable ka major version check karta hai."""
    try:
        result = subprocess.run(
            [java_path, "-version"],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=creation_flags(),
        )
        output = (result.stderr or "") + (result.stdout or "")
        match = re.search(r'version\s+"(\d+)', output)
        if not match:
            match = re.search(r'openjdk\s+(\d+)', output, re.IGNORECASE)
        return int(match.group(1)) if match else None
    except Exception:
        return None


def find_java(required_major=21):
    """Required Java version (21 ya 25) ko installed locations me dhoondhta hai."""
    local = os.environ.get("LOCALAPPDATA", "")
    patterns = [
        os.path.join(local, "Programs", "Eclipse Adoptium", "jdk-*", "bin", "java.exe"),
        r"C:\Program Files\Eclipse Adoptium\jdk-*\bin\java.exe",
        r"C:\Program Files\Java\jdk-*\bin\java.exe",
        r"C:\Program Files\Microsoft\jdk-*\bin\java.exe",
        r"C:\Program Files\Zulu\zulu-*\bin\java.exe",
    ]

    candidates = []
    for pattern in patterns:
        candidates.extend(glob.glob(pattern))

    # PATH wala Java bhi check karo.
    path_java = shutil.which("java")
    if path_java:
        candidates.append(path_java)

    seen = set()
    for java_path in candidates:
        java_path = os.path.abspath(java_path)
        if java_path in seen:
            continue
        seen.add(java_path)
        if java_major(java_path) == required_major:
            return java_path

    return None


def clean_version(v):
    """Version sirf digits aur dots wala hona chahiye (jaise 1.21.8)."""
    v = (v or "").strip()
    if v and all(c.isdigit() or c == "." for c in v):
        return v
    return "1.21.8"


def version_key(v):
    """'1.21.8' ko [1, 21, 8] banata hai, taaki versions ko sahi order me rakh sakein."""
    return [int(x) for x in str(v).split(".") if x.isdigit()]


def get_paper_versions():
    """Paper ke saare available versions internet se laata hai (naya sabse upar)."""
    now = time.time()
    if versions_cache["list"] and now - versions_cache["time"] < 600:
        return versions_cache["list"]

    data = fetch_json("https://fill.papermc.io/v3/projects/paper")
    raw = data.get("versions", {})
    flat = []
    if isinstance(raw, dict):
        for group in raw.values():
            flat.extend(group)
    elif isinstance(raw, list):
        flat = raw

    result = []
    for v in flat:
        if isinstance(v, str) and v and all(c.isdigit() or c == "." for c in v):
            if v not in result:
                result.append(v)
    result.sort(key=version_key, reverse=True)
    versions_cache["list"] = result
    versions_cache["time"] = now
    return result


# ---------------- Config (servers ki list) ----------------
def save_config():
    tmp = CONFIG_FILE + ".tmp"
    with cfg_lock:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        os.replace(tmp, CONFIG_FILE)


def get_server(sid):
    for srv in config["servers"]:
        if srv.get("id") == sid:
            return srv
    return None


def current_server():
    return get_server(config["selected"])


def server_dir(srv):
    """Server ka folder. Panel ke folder ke bahar nahi ja sakta."""
    path = os.path.realpath(os.path.join(BASE_DIR, srv["folder"]))
    base = os.path.realpath(BASE_DIR)
    if not path.startswith(base + os.sep):
        raise ValueError("Server folder galat jagah hai.")
    return path


def plugins_dir(srv):
    return os.path.join(server_dir(srv), "plugins")

def mods_dir(srv):
    return os.path.join(server_dir(srv), "mods")

def server_jar(srv):
    loader = srv.get("loader", "paper")
    if loader == "fabric":
        filename = "fabric-server-launch.jar"
    elif loader == "quilt":
        filename = "quilt-server-launch.jar"
    else:
        filename = "server.jar"
    return os.path.join(server_dir(srv), filename)


def forge_args_file(srv):
    """Forge/NeoForge installer ki launch args file dhoondhta hai (Windows pe win_args.txt)."""
    args_name = "win_args.txt" if sys.platform.startswith("win") else "unix_args.txt"
    sdir = server_dir(srv)
    if srv.get("loader") == "forge":
        root = os.path.join(sdir, "libraries", "net", "minecraftforge", "forge")
    else:
        root = os.path.join(sdir, "libraries", "net", "neoforged", "neoforge")
    if not os.path.isdir(root):
        return None
    candidates = []
    for current, _, files in os.walk(root):
        if args_name in files:
            candidates.append(os.path.join(current, args_name))
    return sorted(candidates)[-1] if candidates else None


def server_ready(srv):
    if srv.get("loader") in ("forge", "neoforge"):
        return forge_args_file(srv) is not None
    return os.path.isfile(server_jar(srv))


def load_config():
    global config
    loaded = None
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except Exception:
        loaded = None

    if isinstance(loaded, dict) and isinstance(loaded.get("servers"), list):
        config = {"servers": loaded["servers"], "selected": str(loaded.get("selected", ""))}
    else:
        config = {"servers": [], "selected": ""}
        # Purana server_data folder hai to wo pehla server ban jayega
        if os.path.isdir(LEGACY_DIR):
            version = "1.21.8"
            try:
                with open(os.path.join(LEGACY_DIR, "panel_version.txt"), "r", encoding="utf-8") as f:
                    version = clean_version(f.read())
            except Exception:
                pass
            config["servers"].append({
                "id": "main",
                "name": "Mera Server",
                "folder": "server_data",
                "version": version,
                "ram": "2G",
            })
            config["selected"] = "main"
        save_config()

    if config["selected"] and get_server(config["selected"]) is None:
        config["selected"] = config["servers"][0]["id"] if config["servers"] else ""
        save_config()


def new_server_id():
    base = "s" + str(int(time.time()))
    sid = base
    n = 1
    while get_server(sid) is not None:
        n += 1
        sid = base + "x" + str(n)
    return sid


# ---------------- Properties helpers ----------------
def props_path(srv):
    return os.path.join(server_dir(srv), "server.properties")


def read_props(srv):
    """server.properties ko dictionary bana ke deta hai (na ho to default values)."""
    values = dict(DEFAULT_PROPS)
    path = props_path(srv)
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                values[key.strip()] = val.strip()
    return values


def write_props(srv, updates):
    """server.properties me diye hue keys badalta hai (baaki lines waise hi rehti hain)."""
    os.makedirs(server_dir(srv), exist_ok=True)
    path = props_path(srv)
    lines = []
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()

    done = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                new_lines.append(key + "=" + updates[key])
                done.add(key)
                continue
        new_lines.append(line)

    for key, val in updates.items():
        if key not in done:
            new_lines.append(key + "=" + val)

    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(new_lines) + "\n")


def check_prop(item, raw):
    """Ek value ko check karta hai. (True, saaf_value) ya (False, error) deta hai."""
    kind = item["type"]
    if kind == "bool":
        if raw is True or str(raw).lower() == "true":
            return True, "true"
        if raw is False or str(raw).lower() == "false":
            return True, "false"
        return False, item["label"] + ": true ya false hona chahiye."
    if kind == "int":
        try:
            num = int(str(raw).strip())
        except ValueError:
            return False, item["label"] + ": number likho."
        if num < item["min"] or num > item["max"]:
            return False, item["label"] + ": " + str(item["min"]) + " se " + str(item["max"]) + " ke beech likho."
        return True, str(num)
    if kind == "select":
        if str(raw) in item["options"]:
            return True, str(raw)
        return False, item["label"] + ": galat option."
    if kind == "text":
        text = str(raw).replace("\\", "").replace("\n", " ").replace("\r", " ").strip()
        return True, text[:80]
    return False, "Unknown setting."


# ---------------- Plugin helper (Modrinth) ----------------
def install_plugin(srv, pid):
    """Modrinth se plugin download karke server ke plugins folder me daalta hai."""
    if not SLUG_RE.match(pid):
        return False, "Plugin id galat hai."
    version = clean_version(srv.get("version", "1.21.8"))
    try:
        loaders = urllib.parse.quote(json.dumps(["paper", "spigot", "bukkit"]))
        gv = urllib.parse.quote(json.dumps([version]))
        versions = fetch_json(
            MODRINTH + "/project/" + pid + "/version?loaders=" + loaders + "&game_versions=" + gv
        )
        if not versions:
            return False, "Is Minecraft version ke liye file nahi mili."
        files = versions[0].get("files", [])
        if not files:
            return False, "Download file nahi mili."
        chosen = files[0]
        for f in files:
            if f.get("primary"):
                chosen = f
                break
        url = chosen.get("url", "")
        filename = os.path.basename(chosen.get("filename", ""))
        if not url.startswith("https://cdn.modrinth.com/") or not filename.endswith(".jar"):
            return False, "Ye file jar plugin nahi lagti, install nahi ki."
        os.makedirs(plugins_dir(srv), exist_ok=True)
        download(url, os.path.join(plugins_dir(srv), filename))
        return True, filename
    except Exception as e:
        return False, str(e)


# ---------------- Modrinth mods (Fabric / Forge / NeoForge) ----------------
def mod_loader(srv):
    loader = srv.get("loader", "fabric")
    return loader if loader in ("fabric", "quilt", "forge", "neoforge") else "fabric"


def _download_mod_version(srv, project_id, installed=None, depth=0):
    """Mod aur uske required Modrinth dependencies ko safely install karta hai."""
    installed = installed if installed is not None else set()
    if depth > 8 or project_id in installed:
        return []
    if not SLUG_RE.match(project_id):
        raise ValueError("Mod ID galat hai: " + project_id)
    loader = mod_loader(srv)
    version = clean_version(srv.get("version", "1.21.8"))
    compatible_loaders = [loader, "fabric"] if loader == "quilt" else [loader]
    loaders = urllib.parse.quote(json.dumps(compatible_loaders))
    game_versions = urllib.parse.quote(json.dumps([version]))
    versions = fetch_json(MODRINTH + "/project/" + project_id + "/version?loaders=" + loaders + "&game_versions=" + game_versions)
    if not versions:
        raise RuntimeError(f"{project_id}: {version} + {loader} ke liye compatible file nahi mili.")
    chosen_version = versions[0]
    files = chosen_version.get("files", [])
    chosen = next((f for f in files if f.get("primary")), files[0] if files else None)
    if not chosen:
        raise RuntimeError(project_id + ": download file nahi mili.")
    url = chosen.get("url", "")
    filename = os.path.basename(chosen.get("filename", ""))
    if not url.startswith("https://cdn.modrinth.com/") or not filename.lower().endswith(".jar"):
        raise RuntimeError(project_id + ": verified JAR file nahi mili.")
    target_dir = mods_dir(srv)
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, filename)
    if not os.path.isfile(target):
        download(url, target)
    installed.add(project_id)
    added = [filename]
    # Required dependencies automatically add honge (e.g. Fabric API).
    for dep in chosen_version.get("dependencies", []):
        if dep.get("dependency_type") == "required" and dep.get("project_id"):
            added.extend(_download_mod_version(srv, dep["project_id"], installed, depth + 1))
    return added


def install_mod(srv, pid):
    try:
        added = _download_mod_version(srv, pid)
        return True, ", ".join(dict.fromkeys(added))
    except Exception as e:
        return False, str(e)


def _fabric_ids_from_zip(archive, depth=0):
    """Fabric jar ke mod ids (id + provides + jar-in-jar ke ids) aur uski depends nikalta hai."""
    ids = set()
    depends = {}
    names = set(archive.namelist())
    if "fabric.mod.json" not in names:
        return ids, depends
    data = json.loads(archive.read("fabric.mod.json").decode("utf-8"))
    if data.get("id"):
        ids.add(str(data["id"]))
    for item in data.get("provides") or []:
        if isinstance(item, dict):
            item = item.get("id")
        if item:
            ids.add(str(item))
    raw_depends = data.get("depends") or {}
    if isinstance(raw_depends, dict):
        depends = dict(raw_depends)
    # Fabric API jaise mods ke andar chhote mods (jar-in-jar) hote hain, unke ids bhi gino.
    if depth < 2:
        for nested in data.get("jars") or []:
            nested_name = nested.get("file") if isinstance(nested, dict) else None
            if nested_name and nested_name in names:
                try:
                    with zipfile.ZipFile(io.BytesIO(archive.read(nested_name))) as inner:
                        inner_ids, _ = _fabric_ids_from_zip(inner, depth + 1)
                        ids.update(inner_ids)
                except Exception:
                    pass
    return ids, depends


def _read_mod_metadata(jar_path, loader):
    """Selected loader ke hisaab se mod ke ids aur required dependencies padhta hai."""
    result = {"mod_ids": set(), "required": []}
    loader = (loader or "").lower()
    # Ye Minecraft ya loader khud dete hain, alag mod jar nahi hote.
    builtin_ids = {
        "minecraft", "java", "forge", "neoforge", "fml", "javafml",
        "lowcodefml", "modlauncher", "fabricloader", "fabric-loader",
        "quilt_loader", "quilt-loader"
    }
    try:
        with zipfile.ZipFile(jar_path) as archive:
            names = set(archive.namelist())

            # Fabric ka metadata Forge/NeoForge check me kabhi mix nahi hota.
            if loader in ("fabric", "quilt") and "fabric.mod.json" in names:
                ids, depends = _fabric_ids_from_zip(archive)
                result["mod_ids"].update(ids)
                for dep_id, dep_version in depends.items():
                    dep_id = str(dep_id)
                    if dep_id not in builtin_ids:
                        result["required"].append({
                            "id": dep_id, "version": str(dep_version),
                            "source": os.path.basename(jar_path)
                        })

            # Quilt-native metadata (Quilt mods can also include Fabric metadata).
            if loader == "quilt" and "quilt.mod.json" in names:
                try:
                    qdata = json.loads(archive.read("quilt.mod.json").decode("utf-8"))
                    qloader = qdata.get("quilt_loader", {})
                    if qloader.get("id"):
                        result["mod_ids"].add(str(qloader["id"]))
                    for dep in qloader.get("depends", []) or []:
                        dep_id = str(dep.get("id", ""))
                        if dep_id and dep_id not in builtin_ids:
                            result["required"].append({
                                "id": dep_id,
                                "version": str(dep.get("versions", "*")),
                                "source": os.path.basename(jar_path)
                            })
                except Exception:
                    pass

            # Forge/NeoForge ka metadata sirf unhi loaders ke liye.
            if loader in ("forge", "neoforge"):
                toml_names = (
                    ("META-INF/neoforge.mods.toml", "META-INF/mods.toml")
                    if loader == "neoforge" else
                    ("META-INF/mods.toml",)
                )
                toml_name = next((n for n in toml_names if n in names), None)
                if toml_name:
                    data = tomllib.loads(archive.read(toml_name).decode("utf-8"))
                    for mod in data.get("mods", []):
                        if mod.get("modId"):
                            result["mod_ids"].add(str(mod["modId"]))
                    for deps in (data.get("dependencies") or {}).values():
                        for dep in deps if isinstance(deps, list) else []:
                            dep_id = str(dep.get("modId", ""))
                            side = str(dep.get("side", "BOTH")).upper()
                            # Naye NeoForge me type="required", purane me mandatory=true
                            required = bool(dep.get("mandatory", False)) or \
                                str(dep.get("type", "")).lower() == "required"
                            # Sirf client pe chalne wali dependency server ke liye zaroori nahi.
                            if side == "CLIENT":
                                continue
                            if required and dep_id and dep_id not in builtin_ids:
                                result["required"].append({
                                    "id": dep_id,
                                    "version": str(dep.get("versionRange", "*")),
                                    "source": os.path.basename(jar_path)
                                })
    except Exception:
        # Jo jar mod nahi hain ya jinka metadata samajh nahi aaya, unhe chhod do.
        pass
    return result


def check_mod_dependencies(srv):
    """Check mandatory dependencies using only the selected loader's metadata."""
    folder = mods_dir(srv)
    if not os.path.isdir(folder):
        return []
    loader = (srv.get("loader") or "").lower()
    installed_ids, requirements = set(), []
    for filename in os.listdir(folder):
        path = os.path.join(folder, filename)
        if filename.lower().endswith(".jar") and os.path.isfile(path):
            meta = _read_mod_metadata(path, loader)
            installed_ids.update(meta["mod_ids"])
            requirements.extend(meta["required"])
    missing = {}
    for req in requirements:
        dep_id = req["id"]
        if dep_id and dep_id not in installed_ids:
            item = missing.setdefault(dep_id, {
                "id": dep_id, "version": req["version"], "required_by": []
            })
            if req["source"] not in item["required_by"]:
                item["required_by"].append(req["source"])
    return list(missing.values())


# ---------------- Setup (Paper + Geyser + Floodgate + Via) ----------------
def setup_worker(sid, version):
    state["setting_up"] = True
    state["setup_id"] = sid
    srv = get_server(sid)
    try:
        if srv is None:
            raise ValueError("Server nahi mila.")
        sdir = server_dir(srv)
        os.makedirs(sdir, exist_ok=True)
        loader = srv.get("loader", "paper")
        old = srv.get("version", "")
        if old and os.path.isfile(server_jar(srv)) and version_key(version) < version_key(old):
            add_log(f"[{APP_NAME}] WARNING: purane Minecraft version par ja rahe ho; pehle server folder ka backup bana lo.")

        if loader == "fabric":
            add_log(f"[{APP_NAME}] Fabric installer version dhoondh raha hoon...")
            installers = fetch_json("https://meta.fabricmc.net/v2/versions/installer")
            if not installers:
                raise RuntimeError("Fabric installer version nahi mili.")
            installer_version = installers[0]["version"]
            installer_url = f"https://maven.fabricmc.net/net/fabricmc/fabric-installer/{installer_version}/fabric-installer-{installer_version}.jar"
            installer_path = os.path.join(sdir, "fabric-installer.jar")
            add_log(f"[{APP_NAME}] Fabric installer download ho raha hai...")
            download(installer_url, installer_path)
            java = find_java(25 if version_key(version) and version_key(version)[0] >= 26 else 21)
            if not java:
                raise RuntimeError("Is Minecraft version ke liye required Java nahi mila.")
            add_log(f"[{APP_NAME}] Fabric server install ho raha hai...")
            result = subprocess.run([java, "-jar", installer_path, "server", "-mcversion", version, "-downloadMinecraft", "-dir", sdir], cwd=sdir, capture_output=True, text=True, timeout=900, creationflags=creation_flags())
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or "Fabric installer fail")[-1200:])
            if not os.path.isfile(os.path.join(sdir, "fabric-server-launch.jar")):
                raise RuntimeError("Fabric installer complete hua, lekin fabric-server-launch.jar nahi mila.")
            with open(os.path.join(sdir, "eula.txt"), "w", encoding="utf-8") as f:
                f.write("eula=true\n")
            os.makedirs(mods_dir(srv), exist_ok=True)
            srv["version"] = version
            save_config()
            add_log(f"[{APP_NAME}] Fabric setup complete! Mods /mods folder me install honge.")
        elif loader == "quilt":
            java_major_required = 25 if version_key(version) and version_key(version)[0] >= 26 else 21
            java = find_java(java_major_required)
            if not java:
                raise RuntimeError(f"Minecraft {version} ke liye Java {java_major_required} nahi mila.")
            add_log(f"[{APP_NAME}] Quilt installer ka latest version dhoondh raha hoon...")
            import xml.etree.ElementTree as ET
            meta_url = "https://maven.quiltmc.org/repository/release/org/quiltmc/quilt-installer/maven-metadata.xml"
            req = urllib.request.Request(meta_url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as response:
                root = ET.fromstring(response.read())
            installer_version = root.findtext(".//release") or root.findtext(".//latest")
            if not installer_version:
                versions = [v.text for v in root.findall(".//version") if v.text]
                if not versions:
                    raise RuntimeError("Quilt installer version nahi mili.")
                installer_version = versions[-1]
            installer_name = f"quilt-installer-{installer_version}.jar"
            installer_path = os.path.join(sdir, installer_name)
            installer_url = f"https://maven.quiltmc.org/repository/release/org/quiltmc/quilt-installer/{installer_version}/{installer_name}"
            add_log(f"[{APP_NAME}] Quilt installer download ho raha hai...")
            download(installer_url, installer_path)
            add_log(f"[{APP_NAME}] Quilt {version} server install ho raha hai...")
            result = subprocess.run(
                [java, "-jar", installer_path, "install", "server", version,
                 "--download-server"],
                cwd=sdir, capture_output=True, text=True, timeout=1200,
                creationflags=creation_flags()
            )
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or "Quilt installer fail")[-1800:])
            if not os.path.isfile(os.path.join(sdir, "quilt-server-launch.jar")):
                raise RuntimeError("Quilt installer complete hua, lekin quilt-server-launch.jar nahi mila.")
            with open(os.path.join(sdir, "eula.txt"), "w", encoding="utf-8") as f:
                f.write("eula=true\n")
            os.makedirs(mods_dir(srv), exist_ok=True)
            srv["version"] = version
            save_config()
            add_log(f"[{APP_NAME}] Quilt setup complete! Mods /mods folder me install honge.")
        elif loader in ("forge", "neoforge"):
            java_major_required = 25 if version_key(version) and version_key(version)[0] >= 26 else 21
            java = find_java(java_major_required)
            if not java:
                raise RuntimeError(f"Minecraft {version} ke liye Java {java_major_required} nahi mila.")
            if loader == "forge":
                add_log(f"[{APP_NAME}] Forge {version} ka recommended installer dhoondh raha hoon...")
                promo = fetch_json("https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json")
                promos = promo.get("promos", {})
                forge_version = promos.get(version + "-recommended") or promos.get(version + "-latest")
                if not forge_version:
                    raise RuntimeError(f"Minecraft {version} ke liye Forge release available nahi mili.")
                forge_full = version + "-" + forge_version
                installer_url = f"https://maven.minecraftforge.net/net/minecraftforge/forge/{forge_full}/forge-{forge_full}-installer.jar"
                installer_path = os.path.join(sdir, "forge-installer.jar")
            else:
                add_log(f"[{APP_NAME}] NeoForge {version} installer dhoondh raha hoon...")
                import xml.etree.ElementTree as ET
                meta_url = "https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml"
                req = urllib.request.Request(meta_url, headers=UA)
                with urllib.request.urlopen(req, timeout=30) as response:
                    root = ET.fromstring(response.read())
                parts = version.split(".")
                if parts[0] == "1":
                    series = (parts[1] + "." + parts[2]) if len(parts) >= 3 else (parts[1] + ".0")
                else:
                    series = version
                all_versions = [el.text for el in root.findall(".//version") if el.text]
                matching = [v for v in all_versions if v.startswith(series + ".")]
                if not matching:
                    raise RuntimeError(f"Minecraft {version} ke liye NeoForge release nahi mili.")
                def numeric_parts(value):
                    return [int(x) if x.isdigit() else 0 for x in re.findall(r"\d+", value)]
                neoforge_version = sorted(matching, key=numeric_parts)[-1]
                installer_url = f"https://maven.neoforged.net/releases/net/neoforged/neoforge/{neoforge_version}/neoforge-{neoforge_version}-installer.jar"
                installer_path = os.path.join(sdir, "neoforge-installer.jar")
            add_log(f"[{APP_NAME}] {loader.upper()} installer download ho raha hai...")
            download(installer_url, installer_path)
            install_args = [java, "-jar", installer_path, "--installServer"]
            result = subprocess.run(install_args, cwd=sdir, capture_output=True, text=True, timeout=1200, creationflags=creation_flags())
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or (loader + " installer fail"))[-1800:])
            if forge_args_file(srv) is None:
                raise RuntimeError("Installer complete hua, lekin server launch arguments file nahi mili.")
            with open(os.path.join(sdir, "eula.txt"), "w", encoding="utf-8") as f:
                f.write("eula=true\n")
            os.makedirs(mods_dir(srv), exist_ok=True)
            srv["version"] = version
            save_config()
            add_log(f"[{APP_NAME}] {loader.upper()} setup complete! Mods /mods folder me install honge.")
        elif loader in ("paper", "purpur"):
            pdir = plugins_dir(srv)
            os.makedirs(pdir, exist_ok=True)
            if loader == "purpur":
                add_log(f"[{APP_NAME}] Purpur {version} ka latest build download ho raha hai...")
                url = f"https://api.purpurmc.org/v2/purpur/{version}/latest/download"
                download(url, os.path.join(sdir, "server.jar"))
            else:
                add_log(f"[{APP_NAME}] PaperMC {version} ka latest build dhoondh raha hoon...")
                info = fetch_json(f"https://fill.papermc.io/v3/projects/paper/versions/{version}/builds/latest")
                url = info["downloads"]["server:default"]["url"]
                add_log(f"[{APP_NAME}] Paper download ho raha hai...")
                download(url, os.path.join(sdir, "server.jar"))
            base_url = "https://download.geysermc.org/v2/projects/{}/versions/latest/builds/latest/downloads/spigot"
            for project, filename in [("geyser", "Geyser-Spigot.jar"), ("floodgate", "floodgate-spigot.jar")]:
                try:
                    add_log(f"[{APP_NAME}] {project} download ho raha hai...")
                    download(base_url.format(project), os.path.join(pdir, filename))
                except Exception as e:
                    add_log(f"[{APP_NAME}] {project} optional download skip: {e}")
            for slug in VIA_PLUGINS:
                ok, msg = install_plugin({"folder": srv["folder"], "version": version}, slug)
                if ok:
                    add_log(f"[{APP_NAME}] {msg} lag gaya.")
                else:
                    add_log(f"[{APP_NAME}] {slug} optional plugin install nahi hua: {msg}")
            with open(os.path.join(sdir, "eula.txt"), "w", encoding="utf-8") as f:
                f.write("eula=true\n")
            srv["version"] = version
            save_config()
            add_log(f"[{APP_NAME}] {loader.upper()} setup complete!")
        else:
            raise RuntimeError("Ye server software samajh nahi aaya: " + str(loader))
    except Exception as e:
        add_log(f"[{APP_NAME}] ERROR setup mein: {e}")
    finally:
        state["setting_up"] = False
        state["setup_id"] = ""


# ---------------- Server control ----------------
def reader_thread(proc):
    for line in proc.stdout:
        add_log(line.rstrip("\n"))
    proc.wait()
    add_log(f"[{APP_NAME}] Server band ho gaya.")


def start_server(srv):
    if is_running():
        running = get_server(state["run_id"])
        name = running["name"] if running else "koi server"
        if state["run_id"] == srv["id"]:
            return False, "Ye server pehle se chal raha hai."
        return False, "'" + name + "' chal raha hai. Pehle usse Stop karo."
    try:
        sdir = server_dir(srv)
    except ValueError as e:
        return False, str(e)
    jar_path = server_jar(srv)
    if not server_ready(srv):
        return False, "Pehle Setup / Update complete karo (server launcher nahi mila)."

    if srv.get("loader") in ("fabric", "quilt", "forge", "neoforge"):
        missing = check_mod_dependencies(srv)
        if missing:
            details = "; ".join(
                f"{m['id']} (chahiye: {', '.join(m['required_by'])}, version {m['version']})"
                for m in missing
            )
            add_log(f"[{APP_NAME}] WARNING: ye mod dependencies nahi mil rahi: {details}")
            add_log(f"[{APP_NAME}] Server phir bhi start kar raha hoon. Crash ho to ye mods /mods me daalo.")

    version = clean_version(srv.get("version", "1.21.8"))
    version_parts = version_key(version)

    # Minecraft 26.x aur naye versions ko Java 25 chahiye.
    # 1.x series (jaise 1.21.x) ke liye Java 21 use karo.
    required_java = 25 if version_parts and version_parts[0] >= 26 else 21
    java = find_java(required_java)
    if not java:
        return False, (
            f"Minecraft {version} ke liye Java {required_java} nahi mila. "
            f"Java {required_java} install karke panel dobara kholo."
        )

    ram = sane_ram(srv.get("ram", "2G"))
    add_log(
        f"[{APP_NAME}] '{srv['name']}' start ho raha hai. "
        f"Minecraft: {version} | Java {required_java}: {java}"
    )
    if srv.get("loader") in ("forge", "neoforge"):
        args_file = forge_args_file(srv)
        if not args_file:
            return False, "Forge/NeoForge launch file nahi mili. Setup dobara karo."
        user_jvm = os.path.join(sdir, "user_jvm_args.txt")
        existing = []
        if os.path.isfile(user_jvm):
            with open(user_jvm, "r", encoding="utf-8", errors="replace") as f:
                existing = [line.rstrip("\r\n") for line in f if line.strip()]
        existing = [line for line in existing if not re.match(r"^-Xm[sx]", line.strip())]
        with open(user_jvm, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join([f"-Xms{ram}", f"-Xmx{ram}"] + existing) + "\n")
        cmd = [java, "@" + user_jvm, "@" + args_file, "nogui"]
    else:
        cmd = [java, f"-Xms{ram}", f"-Xmx{ram}", "-jar", os.path.basename(jar_path), "nogui"]
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=sdir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=creation_flags(),
        )
    except FileNotFoundError:
        return False, f"Java {required_java} executable nahi chal paya. Installation check karo."
    state["proc"] = proc
    state["run_id"] = srv["id"]
    add_log(f"[{APP_NAME}] Server start ho raha hai ({ram} RAM)...")
    threading.Thread(target=reader_thread, args=(proc,), daemon=True).start()
    return True, "Server start ho raha hai."


def send_raw(text):
    try:
        state["proc"].stdin.write(text + "\n")
        state["proc"].stdin.flush()
        return True
    except Exception as e:
        add_log(f"[{APP_NAME}] Command nahi bheja ja saka: {e}")
        return False


def restart_worker(sid):
    if is_running():
        send_raw("stop")
        for _ in range(60):
            if not is_running():
                break
            time.sleep(1)
    time.sleep(1)
    srv = get_server(sid)
    if srv is None:
        return
    ok, msg = start_server(srv)
    if not ok:
        add_log(f"[{APP_NAME}] {msg}")


def sane_ram(value, fallback="2G"):
    return value if value in RAM_OPTIONS else fallback


# ---------------- Routes: main ----------------
@app.route("/")
def index():
    return render_template("index.html", app_name=APP_NAME)


@app.route("/api/status")
def api_status():
    running = is_running()
    return jsonify({
        "running": running,
        "run_id": state["run_id"] if running else "",
        "setting_up": state["setting_up"],
        "setup_id": state["setup_id"],
        "selected": config["selected"],
    })


@app.route("/api/logs")
def api_logs():
    since = int(request.args.get("since", 0))
    with state["lock"]:
        new = [{"id": i, "text": t} for (i, t) in state["logs"] if i > since]
    last = new[-1]["id"] if new else since
    return jsonify({"logs": new, "last": last})


@app.route("/api/versions")
def api_versions():
    note = ""
    try:
        versions = get_paper_versions()
    except Exception:
        versions = []
    if not versions:
        versions = list(FALLBACK_VERSIONS)
        note = "Internet se poori list nahi aayi, isliye common versions dikha raha hoon."
    return jsonify({"ok": True, "versions": versions, "note": note})


# ---------------- Routes: servers ----------------
@app.route("/api/servers")
def api_servers():
    running_id = state["run_id"] if is_running() else ""
    items = []
    for srv in config["servers"]:
        try:
            ready = server_ready(srv)
        except Exception:
            ready = False
        items.append({
            "id": srv.get("id", ""),
            "name": srv.get("name", ""),
            "version": srv.get("version", ""),
            "loader": srv.get("loader", "paper"),
            "ram": srv.get("ram", "2G"),
            "ready": ready,
            "running": srv.get("id", "") == running_id,
        })
    return jsonify({
        "ok": True,
        "selected": config["selected"],
        "run_id": running_id,
        "servers": items,
        "types": WORLD_TYPES,
    })


@app.route("/api/servers/create", methods=["POST"])
def api_servers_create():
    if state["setting_up"]:
        return jsonify({"ok": False, "message": "Ek setup chal raha hai, use khatam hone do."})
    body = request.json or {}

    name = " ".join(str(body.get("name", "")).split())[:NAME_MAX]
    if not name:
        return jsonify({"ok": False, "message": "Server ka naam likho."})
    if len(config["servers"]) >= MAX_SERVERS:
        return jsonify({"ok": False, "message": "Zyada se zyada " + str(MAX_SERVERS) + " servers ban sakte hain. Koi purana delete karo."})

    version = clean_version(body.get("version", "1.21.8"))
    loader = str(body.get("loader", "paper")).lower()
    if loader not in ("paper", "purpur", "fabric", "quilt", "forge", "neoforge"):
        loader = "paper"
    ram = sane_ram(body.get("ram", "2G"))
    allowed = [t["value"] for t in WORLD_TYPES]
    wtype = str(body.get("type", ""))
    if wtype not in allowed:
        wtype = allowed[0]
    seed = str(body.get("seed", "")).replace("\\", "").replace("\n", "").replace("\r", "").strip()[:40]

    sid = new_server_id()
    srv = {"id": sid, "name": name, "folder": "servers/" + sid, "version": version, "ram": ram, "loader": loader}
    try:
        ok, motd = check_prop({"type": "text", "label": "MOTD"}, name)
        write_props(srv, {
            "motd": motd,
            "level-seed": seed,
            "level-type": wtype,
            # Current local testing preference: whitelist OFF.
            # For an internet-facing server, turn whitelist ON and use authenticated accounts.
            "online-mode": "false",
            "white-list": "false",
            "enforce-whitelist": "false",
        })
    except Exception as e:
        return jsonify({"ok": False, "message": "Server folder nahi ban paya: " + str(e)})

    config["servers"].append(srv)
    config["selected"] = sid
    save_config()
    add_log(f"[{APP_NAME}] Naya server bana: {name} ({loader} | {version})")
    threading.Thread(target=setup_worker, args=(sid, version), daemon=True).start()
    return jsonify({"ok": True, "message": "'" + name + "' ban gaya. Download shuru ho gaya, console dekho."})


@app.route("/api/servers/select", methods=["POST"])
def api_servers_select():
    sid = str((request.json or {}).get("id", ""))
    srv = get_server(sid)
    if srv is None:
        return jsonify({"ok": False, "message": "Ye server nahi mila."})
    config["selected"] = sid
    save_config()
    return jsonify({"ok": True, "message": "'" + srv["name"] + "' chuna gaya."})


@app.route("/api/servers/delete", methods=["POST"])
def api_servers_delete():
    body = request.get_json(silent=True) or {}
    sid = str(body.get("id", "")).strip()
    srv = get_server(sid)
    if srv is None:
        return jsonify({"ok": False, "message": "Ye server nahi mila. Server list refresh karo."}), 404
    if is_running() and state.get("run_id") == sid:
        return jsonify({"ok": False, "message": "Pehle is server ko Stop karo."}), 409
    if state.get("setting_up") and state.get("setup_id") == sid:
        return jsonify({"ok": False, "message": "Is server ka setup abhi chal raha hai. Setup poora hone do."}), 409
    if body.get("confirm") is not True:
        return jsonify({"ok": False, "message": "Delete confirmation nahi mili."}), 400

    try:
        sdir = server_dir(srv)
        if os.path.exists(sdir):
            def _remove_readonly(func, path, _exc_info):
                # Windows par read-only server files ko delete karne ki permission do.
                try:
                    os.chmod(path, 0o700)
                    func(path)
                except Exception:
                    raise
            shutil.rmtree(sdir, onerror=_remove_readonly)

        # Folder delete hone ke baad hi server config se entry hatao.
        config["servers"] = [item for item in config["servers"] if item.get("id") != sid]
        if config.get("selected") == sid:
            config["selected"] = config["servers"][0]["id"] if config["servers"] else ""
        save_config()
        add_log(f"[{APP_NAME}] Server delete hua: {srv['name']}")
        return jsonify({"ok": True, "message": "'" + srv["name"] + "' aur uski world/files delete ho gayi."})
    except Exception as e:
        return jsonify({"ok": False, "message": "Server delete nahi ho paya: " + str(e)}), 500


@app.route("/api/setup", methods=["POST"])
def api_setup():
    srv = current_server()
    if srv is None:
        return jsonify({"ok": False, "message": "Pehle server chuno ya naya banao."})
    if state["setting_up"]:
        return jsonify({"ok": False, "message": "Setup pehle se chal raha hai."})
    if is_running() and state["run_id"] == srv["id"]:
        return jsonify({"ok": False, "message": "Pehle ye server Stop karo."})
    version = clean_version((request.json or {}).get("version", srv.get("version", "1.21.8")))
    threading.Thread(target=setup_worker, args=(srv["id"], version), daemon=True).start()
    return jsonify({"ok": True, "message": "Setup shuru ho gaya."})


@app.route("/api/start", methods=["POST"])
def api_start():
    srv = current_server()
    if srv is None:
        return jsonify({"ok": False, "message": "Pehle server chuno ya naya banao."})
    srv["ram"] = sane_ram((request.json or {}).get("ram", srv.get("ram", "2G")), srv.get("ram", "2G"))
    save_config()
    ok, msg = start_server(srv)
    return jsonify({"ok": ok, "message": msg})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    if not is_running():
        return jsonify({"ok": False, "message": "Server chal hi nahi raha."})
    send_raw("stop")
    add_log(f"[{APP_NAME}] Stop command bheja gaya...")
    return jsonify({"ok": True, "message": "Server stop ho raha hai."})


@app.route("/api/restart", methods=["POST"])
def api_restart():
    srv = current_server()
    if srv is None:
        return jsonify({"ok": False, "message": "Pehle server chuno ya naya banao."})
    if not is_running() or state["run_id"] != srv["id"]:
        return jsonify({"ok": False, "message": "Restart sirf chalte hue server ka hota hai."})
    srv["ram"] = sane_ram((request.json or {}).get("ram", srv.get("ram", "2G")), srv.get("ram", "2G"))
    save_config()
    threading.Thread(target=restart_worker, args=(srv["id"],), daemon=True).start()
    return jsonify({"ok": True, "message": "Restart ho raha hai."})


@app.route("/api/command", methods=["POST"])
def api_command():
    cmd = ((request.json or {}).get("cmd", "")).strip()
    if not cmd:
        return jsonify({"ok": False, "message": "Command khali hai."})
    if not is_running():
        return jsonify({"ok": False, "message": "Server band hai."})
    add_log("> " + cmd)
    send_raw(cmd)
    return jsonify({"ok": True, "message": "Command bhej diya."})


# ---------------- Routes: properties editor ----------------
@app.route("/api/props")
def api_props():
    srv = current_server()
    if srv is None:
        return jsonify({"ok": False, "message": "Pehle server chuno ya naya banao."})
    return jsonify({"ok": True, "schema": PROP_SCHEMA, "values": read_props(srv)})


@app.route("/api/props/save", methods=["POST"])
def api_props_save():
    srv = current_server()
    if srv is None:
        return jsonify({"ok": False, "message": "Pehle server chuno ya naya banao."})
    if is_running() and state["run_id"] == srv["id"]:
        return jsonify({"ok": False, "message": "Pehle ye server Stop karo, phir Save karo."})

    incoming = (request.json or {}).get("values", {})
    updates = {}
    for item in PROP_SCHEMA:
        key = item["key"]
        if key not in incoming:
            continue
        ok, result = check_prop(item, incoming[key])
        if not ok:
            return jsonify({"ok": False, "message": result})
        updates[key] = result

    try:
        write_props(srv, updates)
        add_log(f"[{APP_NAME}] '{srv['name']}' ki properties save ho gayi.")
        return jsonify({"ok": True, "message": "Save ho gaya. Ab Start dabao."})
    except Exception as e:
        return jsonify({"ok": False, "message": "Save fail ho gaya: " + str(e)})


# ---------------- Routes: plugins ----------------
@app.route("/api/plugins/search")
def api_plugins_search():
    srv = current_server()
    if srv is None:
        return jsonify({"ok": False, "message": "Pehle server chuno ya naya banao."})
    q = request.args.get("q", "").strip()
    version = clean_version(srv.get("version", "1.21.8"))
    if srv.get("loader", "paper") not in ("paper", "purpur"):
        return jsonify({"ok": False, "message": "Plugins ke liye Paper ya Purpur server select karo. Mods ke liye modded server use karo."})
    if not q:
        return jsonify({"ok": False, "message": "Plugin ka naam likho."})
    try:
        facets = json.dumps([
            ["categories:paper", "categories:spigot", "categories:bukkit"],
            ["versions:" + version],
        ])
        url = (
            MODRINTH + "/search?query=" + urllib.parse.quote(q)
            + "&facets=" + urllib.parse.quote(facets)
            + "&limit=20&index=downloads"
        )
        data = fetch_json(url)
        hits = []
        for h in data.get("hits", []):
            hits.append({
                "id": h.get("project_id", ""),
                "title": h.get("title", ""),
                "description": h.get("description", ""),
                "downloads": h.get("downloads", 0),
                "author": h.get("author", ""),
            })
        return jsonify({"ok": True, "hits": hits})
    except Exception as e:
        return jsonify({"ok": False, "message": "Search fail ho gaya: " + str(e)})


@app.route("/api/plugins/install", methods=["POST"])
def api_plugins_install():
    srv = current_server()
    if srv is None:
        return jsonify({"ok": False, "message": "Pehle server chuno ya naya banao."})
    if srv.get("loader", "paper") not in ("paper", "purpur"):
        return jsonify({"ok": False, "message": "Plugins ke liye Paper ya Purpur server select karo."})
    if is_running() and state["run_id"] == srv["id"]:
        return jsonify({"ok": False, "message": "Plugin install karne se pehle server Stop karo."})
    pid = str((request.json or {}).get("project_id", "")).strip()
    ok, msg = install_plugin(srv, pid)
    if ok:
        add_log(f"[{APP_NAME}] '{srv['name']}' me plugin install hua: {msg}")
        return jsonify({"ok": True, "message": msg + " install ho gaya. Restart karo."})
    return jsonify({"ok": False, "message": "Install fail ho gaya: " + msg})


@app.route("/api/plugins/install-bulk", methods=["POST"])
def api_plugins_install_bulk():
    srv = current_server()
    if srv is None:
        return jsonify({"ok": False, "message": "Pehle server chuno."})
    if srv.get("loader", "paper") not in ("paper", "purpur"):
        return jsonify({"ok": False, "message": "Plugins ke liye Paper ya Purpur server select karo."})
    if is_running() and state["run_id"] == srv["id"]:
        return jsonify({"ok": False, "message": "Bulk install se pehle server Stop karo."})
    ids = (request.json or {}).get("project_ids", [])
    if not isinstance(ids, list) or not ids:
        return jsonify({"ok": False, "message": "Kam se kam ek plugin select karo."})
    ids = list(dict.fromkeys(str(x).strip() for x in ids if isinstance(x, str)))[:30]
    results = []
    for pid in ids:
        ok, msg = install_plugin(srv, pid)
        results.append({"id": pid, "ok": ok, "message": msg})
        if ok:
            add_log(f"[{APP_NAME}] Bulk plugin install: {msg}")
    success = sum(1 for item in results if item["ok"])
    return jsonify({"ok": success > 0, "message": f"{success}/{len(results)} plugins install hue. Server restart karo.", "results": results})


@app.route("/api/plugins/list")
def api_plugins_list():
    srv = current_server()
    items = []
    if srv is not None:
        pdir = plugins_dir(srv)
        if os.path.isdir(pdir):
            for name in sorted(os.listdir(pdir)):
                path = os.path.join(pdir, name)
                if name.endswith(".jar") and os.path.isfile(path):
                    size_mb = round(os.path.getsize(path) / (1024 * 1024), 2)
                    items.append({"name": name, "size": size_mb})
    return jsonify({"ok": True, "plugins": items})


@app.route("/api/plugins/remove", methods=["POST"])
def api_plugins_remove():
    srv = current_server()
    if srv is None:
        return jsonify({"ok": False, "message": "Pehle server chuno ya naya banao."})
    if is_running() and state["run_id"] == srv["id"]:
        return jsonify({"ok": False, "message": "Pehle ye server Stop karo, tabhi plugin hata sakte ho."})
    name = os.path.basename(str((request.json or {}).get("name", "")))
    path = os.path.join(plugins_dir(srv), name)
    if not name.endswith(".jar") or not os.path.isfile(path):
        return jsonify({"ok": False, "message": "Plugin file nahi mili."})
    try:
        os.remove(path)
        add_log(f"[{APP_NAME}] Plugin hata diya: {name}")
        return jsonify({"ok": True, "message": name + " hata diya."})
    except Exception as e:
        return jsonify({"ok": False, "message": "Hata nahi paya: " + str(e)})


# ---------------- Routes: Fabric mods ----------------
@app.route("/api/mods/search")
def api_mods_search():
    srv = current_server()
    if srv is None:
        return jsonify({"ok": False, "message": "Pehle server chuno."})
    if srv.get("loader", "paper") not in ("fabric", "quilt", "forge", "neoforge"):
        return jsonify({"ok": False, "message": "Mods install karne ke liye Fabric, Forge ya NeoForge server select karo."})
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"ok": False, "message": "Mod ka naam likho."})
    try:
        loader = mod_loader(srv)
        loader_categories = ["categories:" + loader]
        if loader == "quilt":
            loader_categories.append("categories:fabric")
        facets = json.dumps([["project_type:mod"], loader_categories, ["versions:" + clean_version(srv.get("version", "1.21.8"))]])
        url = MODRINTH + "/search?query=" + urllib.parse.quote(q) + "&facets=" + urllib.parse.quote(facets) + "&limit=20&index=downloads"
        data = fetch_json(url)
        hits = [{"id": h.get("project_id", ""), "title": h.get("title", ""), "description": h.get("description", ""), "downloads": h.get("downloads", 0), "author": h.get("author", ""), "client_side": h.get("client_side", "unknown"), "server_side": h.get("server_side", "unknown")} for h in data.get("hits", []) if h.get("server_side") != "unsupported"]
        return jsonify({"ok": True, "hits": hits})
    except Exception as e:
        return jsonify({"ok": False, "message": "Mod search fail: " + str(e)})

@app.route("/api/mods/install", methods=["POST"])
def api_mods_install():
    srv = current_server()
    if srv is None or srv.get("loader", "paper") not in ("fabric", "quilt", "forge", "neoforge"):
        return jsonify({"ok": False, "message": "Mods ke liye Fabric, Forge ya NeoForge server select karo."})
    if is_running() and state["run_id"] == srv["id"]:
        return jsonify({"ok": False, "message": "Mod install karne se pehle server Stop karo, phir install karke Start karo."})
    pid = str((request.json or {}).get("project_id", "")).strip()
    ok, msg = install_mod(srv, pid)
    if ok:
        add_log(f"[{APP_NAME}] Mod install hua: {msg}")
        return jsonify({"ok": True, "message": msg + " install ho gaya. Server restart karo."})
    return jsonify({"ok": False, "message": "Mod install fail: " + msg})

@app.route("/api/mods/install-bulk", methods=["POST"])
def api_mods_install_bulk():
    srv = current_server()
    if srv is None or srv.get("loader", "paper") not in ("fabric", "quilt", "forge", "neoforge"):
        return jsonify({"ok": False, "message": "Mods ke liye Fabric, Quilt, Forge ya NeoForge server select karo."})
    if is_running() and state["run_id"] == srv["id"]:
        return jsonify({"ok": False, "message": "Bulk install se pehle server Stop karo."})
    ids = (request.json or {}).get("project_ids", [])
    if not isinstance(ids, list) or not ids:
        return jsonify({"ok": False, "message": "Kam se kam ek mod select karo."})
    ids = list(dict.fromkeys(str(x).strip() for x in ids if isinstance(x, str)))[:30]
    results = []
    for pid in ids:
        ok, msg = install_mod(srv, pid)
        results.append({"id": pid, "ok": ok, "message": msg})
        if ok:
            add_log(f"[{APP_NAME}] Bulk mod install: {msg}")
    success = sum(1 for item in results if item["ok"])
    return jsonify({"ok": success > 0, "message": f"{success}/{len(results)} mods install hue (required dependencies bhi include ho sakti hain). Server restart karo.", "results": results})


@app.route("/api/mods/check")
def api_mods_check():
    srv = current_server()
    if srv is None:
        return jsonify({"ok": False, "message": "Pehle server chuno."})
    if srv.get("loader", "paper") not in ("fabric", "quilt", "forge", "neoforge"):
        return jsonify({"ok": False, "message": "Dependency check mods wale server ke liye hai."})
    missing = check_mod_dependencies(srv)
    return jsonify({"ok": True, "missing": missing, "count": len(missing)})


@app.route("/api/mods/list")
def api_mods_list():
    srv = current_server()
    items = []
    if srv is not None and os.path.isdir(mods_dir(srv)):
        for name in sorted(os.listdir(mods_dir(srv))):
            path = os.path.join(mods_dir(srv), name)
            if name.endswith(".jar") and os.path.isfile(path):
                items.append({"name": name, "size": round(os.path.getsize(path) / (1024 * 1024), 2)})
    return jsonify({"ok": True, "mods": items})

@app.route("/api/mods/remove", methods=["POST"])
def api_mods_remove():
    srv = current_server()
    if srv is None or srv.get("loader", "paper") not in ("fabric", "quilt", "forge", "neoforge"):
        return jsonify({"ok": False, "message": "Fabric, Forge ya NeoForge server select karo."})
    if is_running() and state["run_id"] == srv["id"]:
        return jsonify({"ok": False, "message": "Mod hatane se pehle server Stop karo."})
    name = str((request.json or {}).get("name", ""))
    if os.path.basename(name) != name or not name.lower().endswith(".jar"):
        return jsonify({"ok": False, "message": "Mod file ka naam valid nahi hai."})
    path = os.path.join(mods_dir(srv), name)
    if not os.path.isfile(path):
        return jsonify({"ok": False, "message": "Mod file nahi mili; list refresh karo."})
    try:
        os.remove(path)
        add_log(f"[{APP_NAME}] Mod remove hua: {name}")
        return jsonify({"ok": True, "message": name + " remove ho gaya."})
    except OSError as e:
        return jsonify({"ok": False, "message": "Mod remove nahi hua: " + str(e)})


# v2 platform services: account, rewards, community and user-file APIs.
from platform_v2 import bp as platform_v2_blueprint
app.register_blueprint(platform_v2_blueprint)

load_config()

if __name__ == "__main__":
    print(f"{APP_NAME} chal raha hai: http://127.0.0.1:5000")
    print(f"Java 21 path: {find_java(21)}")
    print(f"Java 25 path: {find_java(25)}")
    app.run(host="127.0.0.1", port=5000, debug=False)