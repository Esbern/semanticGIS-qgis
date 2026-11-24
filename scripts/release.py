#!/usr/bin/env python3
"""(v2 branch) Release the plugin on an update channel: build the zip, publish it as a GitHub release and list it
in the channel's plugins.xml on the `plugin-repository` branch, which QGIS reads.

  python3 scripts/release.py stable    # from main, a version without suffix (0.7.2)
  python3 scripts/release.py v2        # a v2 test version (0.9.0-dev1), from the v2 branch; this branch releases nothing else

A stable release is listed in both channels (dev users get it when it is newer than their prototype).
Needs the GitHub CLI (gh), logged in with write access to the repository.
"""

import base64
import configparser
import datetime
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

REPO = "Esbern/semanticGIS-qgis"
BRANCH = "plugin-repository"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHANNEL_URLS = [f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{name}" for name in ("v2.xml",)]


def run(*args, capture=True):
    result = subprocess.run(args, cwd=ROOT, capture_output=capture, text=True)
    if result.returncode != 0:
        sys.exit(f"{' '.join(args[:3])}… failed:\n{result.stderr or result.stdout}")
    return result.stdout.strip() if capture else ""


def metadata():
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(os.path.join(ROOT, "semanticgis", "metadata.txt"), encoding="utf-8")
    return parser["general"]


def first_changelog_entry(changelog):
    return changelog.strip().splitlines()[0].strip()


def plugin_entry(meta, tag, file_name, download_url):
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    plugin = ET.Element("pyqgis_plugin", name=meta["name"], version=meta["version"], plugin_id="semanticgis")
    fields = {
        "description": meta["description"],
        "about": meta["about"],
        "version": meta["version"],
        "changelog": meta.get("changelog", ""),
        "qgis_minimum_version": meta["qgisMinimumVersion"],
        "qgis_maximum_version": meta.get("qgisMaximumVersion", "4.99"),
        "homepage": meta["homepage"],
        "file_name": file_name,
        "icon": f"https://raw.githubusercontent.com/{REPO}/{tag}/semanticgis/{meta['icon']}",
        "author_name": meta["author"],
        "download_url": download_url,
        "uploaded_by": meta["author"],
        "create_date": now,
        "update_date": now,
        "experimental": meta.get("experimental", "False"),
        "deprecated": meta.get("deprecated", "False"),
        "tracker": meta["tracker"],
        "repository": meta["repository"],
        "tags": meta.get("tags", ""),
        "server": "False",
    }
    for key, value in fields.items():
        ET.SubElement(plugin, key).text = value
    return plugin


def read_channel(name):
    """(xml text, sha) of a channel file on the repository branch."""
    if name not in [e["name"] for e in json.loads(run("gh", "api", f"repos/{REPO}/contents?ref={BRANCH}"))]:
        return "<plugins/>", None             # the first release makes the file
    data = json.loads(run("gh", "api", f"repos/{REPO}/contents/{name}?ref={BRANCH}"))
    return base64.b64decode(data["content"]).decode("utf-8"), data["sha"]


def listed_version(xml_text):
    plugin = ET.fromstring(xml_text).find("pyqgis_plugin")
    return plugin.get("version") if plugin is not None else None


def write_channel(name, entry, sha, message):
    root = ET.Element("plugins")
    root.append(entry)
    ET.indent(root)
    text = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"
    payload = json.dumps({"message": message, "branch": BRANCH, **({"sha": sha} if sha else {}),
                          "content": base64.b64encode(text.encode("utf-8")).decode("ascii")})
    subprocess.run(["gh", "api", "-X", "PUT", f"repos/{REPO}/contents/{name}", "--input", "-"],
                   cwd=ROOT, input=payload, text=True, capture_output=True, check=True)


def main():
    if len(sys.argv) != 2 or sys.argv[1] != "v2":
        sys.exit(__doc__ + "\nThe v2 branch releases on the v2 channel only.")
    channel = sys.argv[1]
    meta = metadata()
    version = meta["version"]
    if not re.fullmatch(r"0\.9\.\d+-dev\d+", version):
        sys.exit(f"{version} is not a v2 version (0.9.0-dev1)")
    branch = run("git", "rev-parse", "--abbrev-ref", "HEAD")
    if branch != "v2":
        sys.exit(f"v2 releases come from the v2 branch (on {branch})")
    if run("git", "status", "--porcelain", "--untracked-files=no"):
        sys.exit("commit your changes first: a release must match a commit")
    run("git", "push", "-q", "origin", branch)
    tag = f"v{version}"
    if run("git", "ls-remote", "--tags", "origin", tag):
        sys.exit(f"{tag} is already released; raise the version in metadata.txt")

    zip_path = run("sh", "scripts/package.sh")
    file_name = os.path.basename(zip_path)
    commit = run("git", "rev-parse", "HEAD")
    notes = first_changelog_entry(meta.get("changelog", version))
    args = ["gh", "release", "create", tag, zip_path, "--repo", REPO, "--target", commit,
            "--title", f"SemanticGIS {version}", "--notes", notes]
    args.append("--prerelease")              # never GitHub's "latest"
    run(*args)
    download_url = f"https://github.com/{REPO}/releases/download/{tag}/{file_name}"

    targets = ["v2.xml"]                     # never stable.xml or dev.xml
    for name in targets:
        text, sha = read_channel(name)
        current = listed_version(text)
        write_channel(name, plugin_entry(meta, tag, file_name, download_url), sha, f"List SemanticGIS {version} ({name})")
        print(f"  {name}: {current or 'empty'} -> {version}")
    print(f"Released {tag}: {download_url}")
    print("Channels: " + ", ".join(CHANNEL_URLS))


if __name__ == "__main__":
    main()
