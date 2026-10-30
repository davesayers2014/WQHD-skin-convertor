#!/usr/bin/env python3
"""
Download the skin repository a link points to, for the "Convert a skin from a link" workflow.

Accepted links (https only):
  https://github.com/OWNER/REPO
  https://github.com/OWNER/REPO/tree/BRANCH/some/folder      (a folder, even a single skin)
  https://github.com/OWNER/REPO/blob/BRANCH/path/skin.xml    (a file: its folder is used)
  https://github.com/OWNER/REPO/archive/refs/heads/BRANCH.zip
  https://github.com/OWNER/REPO/commit/SHA
  OWNER/REPO                                                  (short form, GitHub)
  https://gitlab.com/... , https://bitbucket.org/... , any https git URL

Usage:
  python3 fetch_source.py LINK [--ref BRANCH_TAG_OR_COMMIT] [--dest src]

Writes to $GITHUB_OUTPUT (and prints):
  repo_root    folder to give wqhd_convert.py --repo-root
  only_skin    set when the link points at one skin's own folder
  repo_name    skin name to use if skin.xml sits at the repo root
  commit       the exact commit that was downloaded
  source       human readable "owner/repo @ ref" for messages
  slug         safe short name for tags and folders, e.g. littlesat-skin-PLiHD
"""
import argparse
import base64
import os
import re
import subprocess
import sys
import urllib.parse

SHORT = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def fail(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(2)


def parse(link, ref_in=""):
    """-> (clone_url, ref or '', path inside repo or '', owner/repo label)"""
    link = link.strip().strip("<>").strip()
    if not link:
        fail("no link given")
    if SHORT.match(link) and not link.endswith((".zip", ".git")):
        link = f"https://github.com/{link}"
    if link.startswith(("github.com/", "gitlab.com/", "bitbucket.org/", "codeberg.org/")):
        link = "https://" + link
    if link.startswith("http://"):
        link = "https://" + link[len("http://"):]
    u = urllib.parse.urlsplit(link)
    if u.scheme != "https" or not u.hostname:
        fail(f"'{link}' is not an https link. Paste the address from your browser, e.g. https://github.com/owner/repo")
    if u.username or u.password:
        fail("don't put passwords or tokens in the link - add a SKIN_REPO_TOKEN secret instead (see README)")
    parts = [urllib.parse.unquote(p) for p in u.path.split("/") if p]
    host = u.hostname.lower()
    ref, path = ref_in.strip(), ""

    if host in ("github.com", "www.github.com", "codeberg.org") and len(parts) >= 2:
        owner, repo = parts[0], re.sub(r"\.git$", "", parts[1])
        rest = parts[2:]
        if rest and rest[0] in ("tree", "blob", "src") and len(rest) >= 2:
            if rest[0] == "src" and rest[1] in ("branch", "tag", "commit"):  # codeberg
                rest = rest[1:]
            tail = rest[1:]
            if ref:  # branch given separately: it may contain slashes
                rp = [p for p in ref.split("/") if p]
                if tail[:len(rp)] == rp:
                    tail = tail[len(rp):]
                else:
                    fail(f"the link says branch '{tail[0]}' but you asked for '{ref}'")
            else:
                ref, tail = tail[0], tail[1:]
            if rest[0] == "blob" and tail:
                tail = tail[:-1]  # a file: use its folder
            path = "/".join(tail)
        elif rest[:3] == ["archive", "refs", "heads"] and len(rest) >= 4:
            ref = ref or re.sub(r"\.(zip|tar\.gz)$", "", "/".join(rest[3:]))
        elif rest[:3] == ["archive", "refs", "tags"] and len(rest) >= 4:
            ref = ref or re.sub(r"\.(zip|tar\.gz)$", "", "/".join(rest[3:]))
        elif rest[:1] == ["archive"] and len(rest) == 2:
            ref = ref or re.sub(r"\.(zip|tar\.gz)$", "", rest[1])
        elif rest[:1] == ["commit"] and len(rest) >= 2:
            ref = ref or rest[1]
        elif rest[:1] == ["releases"]:
            fail("that's a release page - link to the repository (https://github.com/owner/repo) instead")
        return f"https://{host}/{owner}/{repo}.git", ref, path, f"{owner}/{repo}"

    if host == "gitlab.com" and len(parts) >= 2:
        if "-" in parts:  # gitlab.com/group/sub/repo/-/tree/branch/path
            i = parts.index("-")
            proj, rest = parts[:i], parts[i + 1:]
            if rest and rest[0] in ("tree", "blob") and len(rest) >= 2:
                tail = rest[1:]
                if ref:
                    rp = [p for p in ref.split("/") if p]
                    tail = tail[len(rp):] if tail[:len(rp)] == rp else fail(f"branch in link doesn't match '{ref}'")
                else:
                    ref, tail = tail[0], tail[1:]
                if rest[0] == "blob" and tail:
                    tail = tail[:-1]
                path = "/".join(tail)
        else:
            proj = parts
        proj[-1] = re.sub(r"\.git$", "", proj[-1])
        return f"https://gitlab.com/{'/'.join(proj)}.git", ref, path, "/".join(proj)

    if host == "bitbucket.org" and len(parts) >= 2:
        owner, repo = parts[0], re.sub(r"\.git$", "", parts[1])
        rest = parts[2:]
        if rest[:1] == ["src"] and len(rest) >= 2:
            ref = ref or rest[1]
            path = "/".join(rest[2:])
        return f"https://bitbucket.org/{owner}/{repo}.git", ref, path, f"{owner}/{repo}"

    if link.endswith(".zip"):
        fail("for zip downloads from sites other than GitHub, link to the git repository instead")
    # any other git server: clone the URL as given
    label = "/".join(parts[-2:]) if len(parts) >= 2 else host
    return urllib.parse.urlunsplit(("https", u.netloc, u.path, "", "")), ref, "", re.sub(r"\.git$", "", label)


def git(*args, cwd=None, token=None, host=None):
    cmd = ["git"]
    if token and host:
        if host in ("github.com", "www.github.com"):
            auth = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            cmd += ["-c", f"http.https://{host}/.extraheader=AUTHORIZATION: basic {auth}"]
        else:
            cmd += ["-c", f"http.https://{host}/.extraheader=Authorization: Bearer {token}"]
    r = subprocess.run(cmd + list(args), cwd=cwd, capture_output=True, text=True,
                       env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    return r


def fetch(url, ref, dest, token=None):
    host = urllib.parse.urlsplit(url).hostname
    os.makedirs(dest, exist_ok=True)
    if git("init", "-q", cwd=dest).returncode:
        fail("git init failed")
    git("remote", "add", "origin", url, cwd=dest)
    want = ref or "HEAD"
    r = git("fetch", "-q", "--depth", "1", "origin", want, cwd=dest, token=token, host=host)
    if r.returncode and ref:
        # a commit some servers won't hand out on its own: fetch everything and look for it
        r = git("fetch", "-q", "origin", "+refs/heads/*:refs/remotes/origin/*", "+refs/tags/*:refs/tags/*",
                cwd=dest, token=token, host=host)
        if r.returncode == 0:
            c = git("rev-parse", "--verify", "-q", f"{ref}^{{commit}}", cwd=dest)
            if c.returncode:
                c = git("rev-parse", "--verify", "-q", f"origin/{ref}^{{commit}}", cwd=dest)
            if c.returncode:
                fail(f"branch, tag or commit '{ref}' not found in {url}")
            git("checkout", "-q", c.stdout.strip(), cwd=dest)
            return git("rev-parse", "HEAD", cwd=dest).stdout.strip()
    if r.returncode:
        err = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "unknown error"
        hint = (" Check the link is right. If the repository is private, add a SKIN_REPO_TOKEN secret (see README)."
                if re.search(r"Authentication|could not read Username|not found|403|401", r.stderr, re.I) else "")
        fail(f"couldn't download {url}{' @ ' + ref if ref else ''}: {err}.{hint}")
    if git("checkout", "-q", "FETCH_HEAD", cwd=dest).returncode:
        fail("checkout failed")
    # skins sometimes keep images in git LFS; fetch them when the runner has git-lfs
    if os.path.exists(os.path.join(dest, ".gitattributes")) and "filter=lfs" in open(
            os.path.join(dest, ".gitattributes"), errors="replace").read():
        if git("lfs", "version").returncode == 0:
            git("lfs", "pull", cwd=dest, token=token, host=host)
    return git("rev-parse", "HEAD", cwd=dest).stdout.strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("link")
    ap.add_argument("--ref", default="")
    ap.add_argument("--dest", default="src")
    ap.add_argument("--parse-only", action="store_true", help="just show how the link is understood")
    a = ap.parse_args()
    url, ref, path, label = parse(a.link, a.ref)
    if a.parse_only:
        print(f"url={url}\nref={ref}\npath={path}\nlabel={label}")
        return
    commit = fetch(url, ref, a.dest, os.environ.get("SKIN_REPO_TOKEN") or None)

    dest = os.path.realpath(a.dest)
    root = os.path.realpath(os.path.join(dest, path)) if path else dest
    if not (root == dest or root.startswith(dest + os.sep)) or not os.path.isdir(root):
        fail(f"folder '{path}' not found in {label} @ {ref or 'default branch'}")
    only = ""
    if root != dest and os.path.isfile(os.path.join(root, "skin.xml")):
        # a link to one skin's folder: look at its parent too, for shared folders next to it
        only, root = os.path.basename(root), os.path.dirname(root)
    repo_name = re.sub(r"[^A-Za-z0-9_.-]", "-", label.split("/")[-1])
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", label).strip("-")
    out = {
        "repo_root": os.path.relpath(root),
        "only_skin": only,
        "repo_name": repo_name,
        "commit": commit,
        "source": f"{label} @ {ref or 'default branch'}{(' / ' + path) if path else ''}",
        "slug": slug,
        "url": url,
    }
    for k, v in out.items():
        print(f"{k}={v}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as gh:
            for k, v in out.items():
                gh.write(f"{k}={v}\n")


if __name__ == "__main__":
    main()
