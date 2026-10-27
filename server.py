"""Public host for sites Friday shares (Flow A, SHARE-to-Replit).

Friday pushes each approved site's static export to sites/<slug>/ in this repo. This server keeps its own clone of
the repo fresh (git fetch every POLL_SECONDS) and serves sites/<slug>/ at /api/sites/<slug>/ - the same path and
headers Friday uses for its local preview, so the live site is byte-identical to what the user approved.

Python stdlib only. Environment:
  GITHUB_TOKEN   read-only fine-grained token for REPO (Replit Secrets). Without it, serves ./sites (local test).
  REPO           owner/name, default up987up/friday-sites
  BRANCH         default main
  PORT           default 8080
  POLL_SECONDS   default 60
"""
import base64
import mimetypes
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPO = os.getenv("REPO", "up987up/friday-sites")
BRANCH = os.getenv("BRANCH", "main")
PORT = int(os.getenv("PORT", "8080"))
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "60"))
TOKEN = os.getenv("GITHUB_TOKEN", "").strip()

LIVE_DIR = Path(tempfile.gettempdir()) / "friday-sites-live"  # our own clone; never inside the served tree
SAFE_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,120}")
SITE_PATH = re.compile(r"^/api/sites/([^/]+)(/.*)?$")
HEADERS = {  # same as Friday's local preview (main.py get_site_file)
    "Content-Security-Policy": "sandbox allow-scripts allow-downloads",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}

state = {"sha": None, "synced": None, "error": None}


def _git(*args: str, cwd: Path | None = None) -> str:
    """Run git with the token as an HTTP header (never in the URL, config or logs)."""
    cmd = ["git"]
    if TOKEN:
        basic = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
        cmd += ["-c", f"http.extraHeader=Authorization: Basic {basic}"]
    cmd += list(args)
    out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120)
    if out.returncode:
        raise RuntimeError((out.stderr or out.stdout).replace(TOKEN, "***")[-300:] if TOKEN else out.stderr[-300:])
    return out.stdout.strip()


def sync() -> None:
    """Clone once, then fetch + hard reset to the remote branch. Errors keep serving the last good copy."""
    try:
        url = f"https://github.com/{REPO}.git"
        if not (LIVE_DIR / ".git").is_dir():
            _git("clone", "--depth", "1", "--branch", BRANCH, url, str(LIVE_DIR))
        else:
            _git("fetch", "--depth", "1", "origin", BRANCH, cwd=LIVE_DIR)
            _git("reset", "--hard", "FETCH_HEAD", cwd=LIVE_DIR)
        state.update(sha=_git("rev-parse", "--short", "HEAD", cwd=LIVE_DIR), synced=time.time(), error=None)
    except Exception as exc:  # network blip, bad token: log and keep the old files
        state["error"] = str(exc)
        print(f"[sync] failed: {exc}", file=sys.stderr, flush=True)


def poll_forever() -> None:
    while True:
        time.sleep(POLL_SECONDS)
        sync()


def sites_root() -> Path:
    return (LIVE_DIR / "sites") if TOKEN else (Path(__file__).parent / "sites")


def site_file(slug: str, relpath: str) -> Path | None:
    """sites/<slug>/<relpath>, or None. Same rules as Friday's core/sites.py: strict slug, no escaping the site
    folder, a directory serves its index.html."""
    if not SAFE_SLUG.fullmatch(slug):
        return None
    root = (sites_root() / slug).resolve()
    if not root.is_dir():
        return None
    target = (root / relpath.lstrip("/")).resolve()
    if target != root and root not in target.parents:
        return None
    if target.is_dir():
        target = target / "index.html"
    return target if target.is_file() else None


class Handler(BaseHTTPRequestHandler):
    server_version = "friday-sites"
    sys_version = ""

    def _send(self, status: int, body: bytes, ctype: str = "text/plain; charset=utf-8", extra: dict | None = None,
              head: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in {**HEADERS, **(extra or {})}.items():
            self.send_header(k, v)
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _route(self, head: bool = False) -> None:
        path = unquote(urlsplit(self.path).path)
        if path == "/healthz":
            ok = state["error"] is None
            return self._send(200 if ok else 503, f"sha={state['sha']} error={state['error']}".encode(), head=head)
        if m := SITE_PATH.match(path):
            slug, rest = m.group(1), m.group(2)
            if rest is None:  # /api/sites/<slug> -> trailing slash so relative assets resolve
                return self._send(301, b"", extra={"Location": f"/api/sites/{slug}/"}, head=head)
            if f := site_file(slug, rest):
                ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
                return self._send(200, f.read_bytes(), ctype, head=head)
            return self._send(404, b"Not found", head=head)
        short = path.strip("/")
        if SAFE_SLUG.fullmatch(short) and site_file(short, ""):  # /<slug> -> the real site path
            return self._send(302, b"", extra={"Location": f"/api/sites/{short}/"}, head=head)
        if path == "/":
            return self._send(200, b"Friday sites", head=head)
        return self._send(404, b"Not found", head=head)

    def do_GET(self) -> None:
        self._route()

    def do_HEAD(self) -> None:
        self._route(head=True)

    def log_message(self, fmt: str, *args) -> None:
        print(f"[http] {self.address_string()} {fmt % args}", flush=True)


def main() -> None:
    if TOKEN:
        sync()
        threading.Thread(target=poll_forever, daemon=True).start()
    else:
        print("[sync] no GITHUB_TOKEN: serving ./sites only (local test mode)", flush=True)
    print(f"[http] listening on 0.0.0.0:{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
