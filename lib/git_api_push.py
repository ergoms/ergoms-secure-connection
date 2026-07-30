#!/usr/bin/env python3
"""Push commits to GitHub via REST Git Data API (works through ops-content Netlify relay).

Native git-receive-pack through Netlify often stalls after the pack is uploaded.
JSON API calls are small and usually succeed via the same relay.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"


class RelayRedirectHandler(HTTPRedirectHandler):
    """Rewrite GitHub absolute redirects back through the Netlify relay."""

    def __init__(self, relay: str) -> None:
        super().__init__()
        self.relay = relay.rstrip("/")

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        rewritten = newurl
        for host in (
            "https://api.github.com",
            "https://github.com",
            "https://uploads.github.com",
            "https://codeload.github.com",
            "https://objects.githubusercontent.com",
            "https://raw.githubusercontent.com",
        ):
            if newurl.startswith(host):
                rewritten = f"{self.relay}/https/{host.removeprefix('https://')}{newurl[len(host):]}"
                break
        return super().redirect_request(req, fp, code, msg, headers, rewritten)


class ApiError(RuntimeError):
    pass


def die(msg: str, code: int = 1) -> None:
    print(f"[ops-content] {msg}", file=sys.stderr)
    raise ApiError(msg)


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        die("config.json not found")
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))


def corporate_proxy(cfg: dict) -> str:
    env_p = os.environ.get("CORPORATE_PROXY", "").strip()
    if env_p:
        return env_p if env_p.startswith("http") else f"http://{env_p}"
    p = cfg.get("corporate_proxy") or "192.0.2.10:3128"
    return p if str(p).startswith("http") else f"http://{p}"


def relay_base(cfg: dict) -> str:
    base = (cfg.get("worker_base_url") or "").rstrip("/")
    if not base:
        die("worker_base_url empty — run ./deploy.sh and ./ops-content.sh on")
    return base


def run_git(args: list[str], *, cwd: Path, input_text: str | None = None) -> str:
    r = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if r.returncode != 0:
        die(f"git {' '.join(args)} failed: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


def run_git_bytes(args: list[str], *, cwd: Path) -> bytes:
    r = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        check=False,
    )
    if r.returncode != 0:
        die(f"git {' '.join(args)} failed: {r.stderr.decode('utf-8', 'replace').strip()}")
    return r.stdout


def parse_remote(url: str) -> tuple[str, str]:
    """Return (owner, repo) from https GitHub remote (possibly rewritten)."""
    u = url.strip().rstrip("/")
    for marker in (
        "github.com/",
        "https/github.com/",
    ):
        if marker in u:
            rest = u.split(marker, 1)[1]
            parts = [p for p in rest.split("/") if p]
            if len(parts) >= 2:
                owner, repo = parts[0], parts[1]
                if repo.endswith(".git"):
                    repo = repo[:-4]
                return owner, repo
    die(f"cannot parse GitHub owner/repo from remote: {url}")


def git_credential(host: str, path: str = "") -> tuple[str, str]:
    payload = f"protocol=https\nhost={host}\n"
    if path:
        payload += f"path={path}\n"
    payload += "\n"
    r = subprocess.run(
        ["git", "credential", "fill"],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )
    if r.returncode != 0:
        die("git credential fill failed — configure a GitHub token (credential.helper)")
    data = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
    user = data.get("username") or ""
    password = data.get("password") or ""
    if not password:
        die("empty token from git credential")
    return user, password


class GhApi:
    def __init__(self, opener, api_root: str, user: str, token: str, relay: str) -> None:
        self.opener = opener
        self.api_root = api_root.rstrip("/")
        self.relay = relay.rstrip("/")
        self.user = user or "x-access-token"
        self.token = token
        self._cache: set[str] = set()
        self.repo_prefix = ""  # /repositories/{id} after resolve
        basic = base64.b64encode(f"{self.user}:{self.token}".encode()).decode("ascii")
        # Same scheme as git-remote-https (Basic). Bearer often fails for stored creds.
        self._auth = f"Basic {basic}"

    def _rewrite_url(self, url: str) -> str:
        for host in (
            "https://api.github.com",
            "http://api.github.com",
            "https://github.com",
            "https://uploads.github.com",
        ):
            if url.startswith(host):
                rest = url[len(host) :]
                host_no_scheme = host.split("://", 1)[1]
                return f"{self.relay}/https/{host_no_scheme}{rest}"
        return url

    def resolve_repo(self, owner: str, repo: str) -> None:
        _, data = self.request("GET", f"/repos/{owner}/{repo}")
        rid = (data or {}).get("id")
        if not rid:
            die(f"cannot resolve repository id for {owner}/{repo}")
        self.repo_prefix = f"/repositories/{rid}"
        print(f"[ops-content] repo id={rid}")

    def _repo_path(self, suffix: str) -> str:
        if not self.repo_prefix:
            die("repo_prefix not resolved")
        if not suffix.startswith("/"):
            suffix = "/" + suffix
        return f"{self.repo_prefix}{suffix}"

    def request(
        self,
        method: str,
        path: str,
        data: dict | None = None,
        *,
        ok: set[int] | None = None,
        timeout: int = 60,
        retries: int = 3,
    ) -> tuple[int, Any]:
        ok = ok or {200, 201}
        url = path if path.startswith("http") else f"{self.api_root}{path}"
        body = None if data is None else json.dumps(data).encode("utf-8")
        headers = {
            "Authorization": self._auth,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ops-content-api-push",
            "Content-Type": "application/json",
            "Accept-Encoding": "identity",
            "Connection": "close",
        }

        last_err: Exception | None = None
        for attempt in range(1, retries + 1):
            cur_url = url
            try:
                for _ in range(6):
                    req = Request(cur_url, data=body, method=method, headers=headers)
                    try:
                        with self.opener.open(req, timeout=timeout) as resp:
                            raw = resp.read()
                            code = resp.status
                            break
                    except urllib.error.HTTPError as e:
                        raw = e.read() if e.fp else b""
                        code = e.code
                        if code in {301, 302, 303, 307, 308}:
                            loc = e.headers.get("Location") or ""
                            if not loc:
                                die(f"GitHub API {method} {path} -> {code} without Location")
                            cur_url = self._rewrite_url(loc)
                            continue
                        if code not in (ok | {404}):
                            msg = raw.decode("utf-8", "replace")[:500]
                            die(f"GitHub API {method} {path} -> {code}: {msg}")
                        break
                else:
                    die(f"GitHub API {method} {path}: too many redirects")

                if code not in ok and code != 404:
                    msg = raw.decode("utf-8", "replace")[:500]
                    die(f"GitHub API {method} {path} -> {code}: {msg}")
                if code == 404 and 404 not in ok:
                    return code, None
                if not raw:
                    return code, None
                return code, json.loads(raw.decode("utf-8"))
            except (TimeoutError, urllib.error.URLError, ConnectionError, OSError) as e:
                last_err = e
                print(f"[ops-content] retry {attempt}/{retries} {method} {path}: {e}")
                time.sleep(min(2 * attempt, 8))
        die(f"GitHub API {method} {path} failed after retries: {last_err}")

    def seed_cache_from_ref(self, cwd: Path, tip: str | None) -> None:
        """Mark all objects reachable from remote tip as already on GitHub (local walk, no API)."""
        if not tip:
            return
        out = run_git(["rev-list", "--objects", tip], cwd=cwd)
        n = 0
        for line in out.splitlines():
            sha = line.split(" ", 1)[0].strip()
            if sha:
                self._cache.add(sha)
                n += 1
        print(f"[ops-content] seeded cache with {n} objects from {tip[:8]}")

    def remote_has(self, kind: str, sha: str) -> bool:
        # Never GET /git/blobs/{sha}: response includes full file body and stalls the relay.
        if kind == "blobs":
            return sha in self._cache
        code, _ = self.request(
            "GET",
            self._repo_path(f"/git/{kind}/{sha}"),
            ok={200, 404},
            timeout=45,
            retries=2,
        )
        return code == 200

    @staticmethod
    def _blob_payload(content: bytes) -> dict[str, str]:
        # utf-8 меньше base64 и стабильнее проходит через Netlify/Squid
        try:
            text = content.decode("utf-8")
            if "\x00" not in text:
                return {"content": text, "encoding": "utf-8"}
        except UnicodeDecodeError:
            pass
        return {
            "content": base64.b64encode(content).decode("ascii"),
            "encoding": "base64",
        }

    def ensure_blob(self, sha: str, cwd: Path) -> None:
        if sha in self._cache:
            return
        content = run_git_bytes(["cat-file", "blob", sha], cwd=cwd)
        payload = self._blob_payload(content)
        print(
            f"  blob {sha[:8]} ({len(content)} bytes, {payload['encoding']}) ...",
            flush=True,
        )
        # POST is idempotent (same content → same sha). On flaky response, retry;
        # if all attempts time out, still mark cached and let tree creation validate.
        last_err: Exception | None = None
        for attempt in range(1, 8):
            try:
                _, data = self.request(
                    "POST",
                    self._repo_path("/git/blobs"),
                    payload,
                    timeout=35,
                    retries=1,
                )
                got = (data or {}).get("sha")
                if got and got != sha:
                    die(f"blob sha mismatch local={sha} remote={got}")
                self._cache.add(sha)
                time.sleep(0.2)
                return
            except ApiError as e:
                last_err = e
                print(f"  blob {sha[:8]} attempt {attempt}/7 failed, retrying...")
                time.sleep(min(1.5 * attempt, 8))
        # Assume object landed (common when Netlify drops only the response)
        print(
            f"  blob {sha[:8]} WARNING: no clean ACK ({last_err}); "
            "continuing — tree create will fail if missing"
        )
        self._cache.add(sha)
        time.sleep(0.5)

    def ensure_tree(self, sha: str, cwd: Path) -> None:
        if sha in self._cache:
            return
        out = run_git(["ls-tree", "-z", sha], cwd=cwd)
        entries = []
        for item in out.split("\0"):
            if not item:
                continue
            meta, name = item.split("\t", 1)
            mode, typ, obj = meta.split(" ", 2)
            if typ == "blob":
                self.ensure_blob(obj, cwd)
                entries.append({"path": name, "mode": mode, "type": "blob", "sha": obj})
            elif typ == "tree":
                self.ensure_tree(obj, cwd)
                entries.append({"path": name, "mode": mode, "type": "tree", "sha": obj})
            elif typ == "commit":
                entries.append({"path": name, "mode": mode, "type": "commit", "sha": obj})
            else:
                die(f"unsupported tree entry type {typ} in {sha}")
        if self.remote_has("trees", sha):
            self._cache.add(sha)
            print(f"  tree {sha[:8]} already on GitHub")
            return
        print(f"  tree {sha[:8]} ({len(entries)} entries) ...", flush=True)
        try:
            _, data = self.request(
                "POST",
                self._repo_path("/git/trees"),
                {"tree": entries},
                timeout=40,
                retries=6,
            )
            got = (data or {}).get("sha")
            if got and got != sha and sha not in self._cache:
                if not self.remote_has("trees", sha):
                    die(f"tree sha mismatch local={sha} remote={got}")
        except ApiError:
            if self.remote_has("trees", sha):
                print(f"  tree {sha[:8]} verified on GitHub after flaky response")
            else:
                raise
        self._cache.add(sha)
        time.sleep(0.25)

    def upload_new_blobs(self, cwd: Path, remote_sha: str | None, local_sha: str) -> None:
        """Upload only blobs from remote..local, smallest first (more reliable via relay)."""
        rng = f"{remote_sha}..{local_sha}" if remote_sha else local_sha
        out = run_git(["rev-list", "--objects", rng], cwd=cwd)
        blobs: list[tuple[int, str]] = []
        for line in out.splitlines():
            sha = line.split(" ", 1)[0].strip()
            if not sha or sha in self._cache:
                continue
            typ = run_git(["cat-file", "-t", sha], cwd=cwd).strip()
            if typ != "blob":
                continue
            size = int(run_git(["cat-file", "-s", sha], cwd=cwd).strip())
            blobs.append((size, sha))
        blobs.sort()
        print(f"[ops-content] uploading {len(blobs)} new blob(s)")
        for _, sha in blobs:
            self.ensure_blob(sha, cwd)

    def create_commit(self, commit: str, cwd: Path, parent: str | None) -> str:
        tree = run_git(["rev-parse", f"{commit}^{{tree}}"], cwd=cwd).strip()
        self.ensure_tree(tree, cwd)
        message = run_git(["log", "-1", "--format=%B", commit], cwd=cwd)
        if message.endswith("\n"):
            message = message[:-1]
        payload: dict[str, Any] = {
            "message": message,
            "tree": tree,
            "author": {
                "name": run_git(["log", "-1", "--format=%an", commit], cwd=cwd).strip(),
                "email": run_git(["log", "-1", "--format=%ae", commit], cwd=cwd).strip(),
                "date": run_git(["log", "-1", "--format=%aI", commit], cwd=cwd).strip(),
            },
            "committer": {
                "name": run_git(["log", "-1", "--format=%cn", commit], cwd=cwd).strip(),
                "email": run_git(["log", "-1", "--format=%ce", commit], cwd=cwd).strip(),
                "date": run_git(["log", "-1", "--format=%cI", commit], cwd=cwd).strip(),
            },
        }
        if parent:
            payload["parents"] = [parent]
        _, data = self.request(
            "POST",
            self._repo_path("/git/commits"),
            payload,
        )
        sha = (data or {}).get("sha") or ""
        if not sha:
            die(f"create commit failed for {commit}")
        self._cache.add(sha)
        print(f"  commit {sha[:8]} <- local {commit[:8]}")
        return sha

    def update_ref(self, ref: str, sha: str, *, force: bool) -> None:
        short = ref.removeprefix("refs/")
        code, _ = self.request(
            "GET",
            self._repo_path(f"/git/ref/{short}"),
            ok={200, 404},
        )
        if code == 404:
            self.request(
                "POST",
                self._repo_path("/git/refs"),
                {"ref": f"refs/{short}", "sha": sha},
            )
            return
        self.request(
            "PATCH",
            self._repo_path(f"/git/refs/{short}"),
            {"sha": sha, "force": bool(force)},
        )


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Push via GitHub Git Data API through ops-content relay")
    ap.add_argument("--cwd", default=".", help="git repo path")
    ap.add_argument("--remote", default="origin")
    ap.add_argument("--refspec", default="", help="e.g. dev:dev or HEAD:refs/heads/dev")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    cwd = Path(args.cwd).resolve()
    if not (cwd / ".git").exists() and not (cwd / ".git").is_file():
        # allow worktrees / submodule .git file
        r = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
        )
        if r.returncode != 0 or r.stdout.strip() != "true":
            die(f"not a git repo: {cwd}")

    cfg = load_config()
    proxy = corporate_proxy(cfg)
    base = relay_base(cfg)
    opener = build_opener(
        ProxyHandler({"http": proxy, "https": proxy}),
        RelayRedirectHandler(base),
    )

    remote_url = run_git(["remote", "get-url", args.remote], cwd=cwd).strip()
    owner, repo = parse_remote(remote_url)

    refspec = args.refspec or "HEAD"
    if ":" in refspec:
        local_part, remote_part = refspec.split(":", 1)
    else:
        local_part, remote_part = refspec, refspec

    local_sha = run_git(["rev-parse", local_part], cwd=cwd).strip()
    if remote_part.startswith("refs/"):
        remote_ref = remote_part
    elif remote_part.startswith("heads/") or remote_part.startswith("tags/"):
        remote_ref = f"refs/{remote_part}"
    else:
        remote_ref = f"refs/heads/{remote_part}"

    # Prefer relay-host creds (same URL git push uses), then github.com
    relay_host = base.split("://", 1)[-1].split("/", 1)[0]
    user, token = "", ""
    tried: list[str] = []
    for host, path in (
        (relay_host, f"https/github.com/{owner}/{repo}"),
        (relay_host, ""),
        ("github.com", f"{owner}/{repo}"),
        ("github.com", ""),
        ("api.github.com", ""),
    ):
        label = f"{host}/{path}" if path else host
        try:
            u, t = git_credential(host, path)
        except SystemExit:
            tried.append(f"{label}:fail")
            continue
        if not t:
            tried.append(f"{label}:empty")
            continue
        # Probe auth without leaking the secret
        probe = GhApi(opener, f"{base}/https/api.github.com", u, t, base)
        code, _ = probe.request("GET", "/user", ok={200, 401, 403})
        tried.append(f"{label}:http{code},len={len(t)}")
        if code == 200:
            user, token = u, t
            break
    if not token:
        die(
            "no working GitHub token in git credential helper "
            f"(tried: {', '.join(tried)}). Store a PAT: "
            "git credential approve <<< $'protocol=https\\nhost=github.com\\nusername=TOKEN_USER\\npassword=ghp_xxx\\n'"
        )
    print(f"[ops-content] auth ok as {user or 'token'} via credential helper")

    api_root = f"{base}/https/api.github.com"
    api = GhApi(opener, api_root, user, token, base)
    api.resolve_repo(owner, repo)

    short_ref = remote_ref.removeprefix("refs/")
    code, ref_data = api.request(
        "GET",
        api._repo_path(f"/git/ref/{short_ref}"),
        ok={200, 404},
    )
    remote_sha = None
    if code == 200 and isinstance(ref_data, dict):
        remote_sha = (ref_data.get("object") or {}).get("sha")

    if remote_sha == local_sha:
        print(f"[ops-content] already up-to-date {remote_ref}={local_sha[:8]}")
        return 0

    if remote_sha:
        r = subprocess.run(
            ["git", "merge-base", "--is-ancestor", remote_sha, local_sha],
            cwd=str(cwd),
            capture_output=True,
        )
        if r.returncode != 0 and not args.force:
            die(
                f"non-fast-forward: remote {remote_sha[:8]} is not ancestor of {local_sha[:8]} "
                f"(use --force)"
            )
        commits = run_git(
            ["rev-list", "--reverse", f"{remote_sha}..{local_sha}"],
            cwd=cwd,
        ).split()
    else:
        commits = run_git(["rev-list", "--reverse", local_sha], cwd=cwd).split()

    if not commits:
        print("[ops-content] nothing to push")
        return 0

    # Avoid thousands of existence GETs — everything reachable from remote tip is on GitHub
    api.seed_cache_from_ref(cwd, remote_sha)
    api.upload_new_blobs(cwd, remote_sha, local_sha)

    print(
        f"[ops-content] API push {owner}/{repo} {local_part} -> {remote_ref} "
        f"({len(commits)} commit(s)) via {base}"
    )
    parent = remote_sha
    new_sha = local_sha
    for c in commits:
        new_sha = api.create_commit(c, cwd, parent)
        parent = new_sha

    if new_sha != local_sha:
        print(
            f"[ops-content] warning: commit sha differs "
            f"(local {local_sha[:8]} remote {new_sha[:8]}); ref will use remote sha"
        )

    api.update_ref(short_ref, new_sha, force=args.force or new_sha != local_sha)

    branch_name = remote_part.split("/")[-1]
    subprocess.run(
        [
            "git",
            "fetch",
            args.remote,
            f"+{remote_ref}:refs/remotes/{args.remote}/{branch_name}",
        ],
        cwd=str(cwd),
        capture_output=True,
    )
    print(f"[ops-content] OK pushed {new_sha[:8]} -> {remote_ref}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ApiError:
        raise SystemExit(1) from None
