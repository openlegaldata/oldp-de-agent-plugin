"""Validate the Agent Plugins package and build its ZIP.

The package is the repository root: ``plugin.json``, ``mcp.json`` and
``assets/`` (plus ``skills/`` if it exists). The script checks it against the
Agent Plugins 1.0.0 schemas (https://agent-plugins.org/) and, when
``plugin.json`` has a ``com.openai`` extension, against the limits of OpenAI's
plugin submission guide (https://developers.openai.com/plugins/deploy/submission).
It writes ``dist/<name>-<version>.zip``, the file to upload in the OpenAI
plugin portal (https://platform.openai.com/plugins).

The plugin version is the OLDP version: ``version`` from the ``pyproject.toml``
of an oldp checkout (default ``../oldp``), written into the ZIP's
``plugin.json``; the source ``plugin.json`` has no version. ``--version``
overrides it, e.g. to upload a changed listing without a new OLDP release
(each upload needs a new version).

Usage:
    python3 scripts/build_agent_plugin.py [--check-urls] [--src DIR]
        [--oldp-src DIR | --version X.Y.Z]

Stdlib only (Python 3.11+). Exit code 1 when a check fails (no ZIP is written).
"""

import argparse
import json
import re
import struct
import sys
import tomllib
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OLDP_SRC = REPO_ROOT.parent / "oldp"
#: Semantic version, optionally with a pre-release/build suffix.
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?$")

# --- Agent Plugins 1.0.0 (plugin.schema.json, mcp.schema.json) -------------
PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
PLUGIN_FIELDS = {
    "$schema", "name", "version", "description", "author", "homepage",
    "repository", "license", "keywords", "extensions",
}
AUTHOR_FIELDS = {"name", "email", "url"}
NAME_RE = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
#: server type -> (required fields, allowed fields)
SERVER_FIELDS = {
    "stdio": ({"type", "command"}, {"type", "command", "args", "env", "cwd"}),
    "streamable-http": ({"type", "url"}, {"type", "url", "headers"}),
    "sse": ({"type", "url"}, {"type", "url", "headers"}),
}

# --- OpenAI plugin submission guide (extensions["com.openai"]) -------------
#: interface field -> max. characters.
TEXT_LIMITS = {
    "displayName": 30,
    "shortDescription": 30,
    "longDescription": 4000,
    "developerName": 80,
}
REQUIRED_INTERFACE = [
    "displayName",
    "shortDescription",
    "longDescription",
    "developerName",
    "category",
    "websiteURL",
    "supportURL",
    "privacyPolicyURL",
    "termsOfServiceURL",
    "logo",
]
URL_FIELDS = ["websiteURL", "supportURL", "privacyPolicyURL", "termsOfServiceURL"]
#: Keys the portal rejects in the package (credentials belong in the dashboard).
FORBIDDEN_KEYS = {"test_credentials", "reviewer_instructions", "clientSecret"}
POSITIVE_CASES = 5
NEGATIVE_CASES = 3


def oldp_version(oldp_src: Path) -> str:
    """``version`` from the ``pyproject.toml`` of the oldp checkout."""
    with (oldp_src / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def _png_size(path: Path) -> tuple[int, int] | None:
    """Width and height of a PNG file, or None if it is not a PNG."""
    with path.open("rb") as fh:
        header = fh.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", header[16:24])


def _find_keys(obj, keys: set[str], path: str = "") -> list[str]:
    """Paths of all dict keys in ``obj`` that are in ``keys``."""
    found = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in keys:
                found.append(f"{path}.{key}".lstrip("."))
            found += _find_keys(value, keys, f"{path}.{key}")
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            found += _find_keys(value, keys, f"{path}[{i}]")
    return found


def _check_url(url: str) -> str | None:
    """Error message if ``url`` does not answer with HTTP 200."""
    request = urllib.request.Request(url, headers={"User-Agent": "oldp-plugin-check"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.status != 200:
                return f"{url} -> HTTP {response.status}"
    except Exception as exc:  # noqa: BLE001 - report any failure
        return f"{url} -> {exc}"
    return None


def check_agent_plugins(plugin: dict, mcp: dict) -> list[str]:
    """Errors against the Agent Plugins 1.0.0 manifest and MCP schemas."""
    errors = []
    if plugin.get("$schema") != PLUGIN_SCHEMA:
        errors.append(f"plugin.json: $schema must be {PLUGIN_SCHEMA}")
    for key in sorted(set(plugin) - PLUGIN_FIELDS):
        errors.append(f"plugin.json: unknown top-level field {key!r}")
    name = plugin.get("name", "")
    if not isinstance(name, str) or not 1 <= len(name) <= 64 or not NAME_RE.match(name):
        errors.append(f"plugin.json: name {name!r}: lowercase letters, digits, '-' and '.', max. 64")
    author = plugin.get("author", {})
    if not isinstance(author, dict) or set(author) - AUTHOR_FIELDS:
        errors.append(f"plugin.json: author may only have {sorted(AUTHOR_FIELDS)}")
    keywords = plugin.get("keywords", [])
    if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
        errors.append("plugin.json: keywords must be a list of strings")
    extensions = plugin.get("extensions", {})
    if not isinstance(extensions, dict) or not all(isinstance(v, dict) for v in extensions.values()):
        errors.append("plugin.json: extensions must map namespaces to objects")

    if mcp.get("$schema") != MCP_SCHEMA:
        errors.append(f"mcp.json: $schema must be {MCP_SCHEMA}")
    for key in sorted(set(mcp) - {"$schema", "mcpServers"}):
        errors.append(f"mcp.json: unknown top-level field {key!r}")
    servers = mcp.get("mcpServers")
    if not isinstance(servers, dict):
        errors.append("mcp.json: mcpServers must be an object")
        servers = {}
    for server_name, server in servers.items():
        kind = server.get("type") if isinstance(server, dict) else None
        if kind not in SERVER_FIELDS:
            errors.append(f"mcp.json {server_name}: type must be one of {sorted(SERVER_FIELDS)}")
            continue
        required, allowed = SERVER_FIELDS[kind]
        for field in sorted(required - set(server)):
            errors.append(f"mcp.json {server_name}: {field} is required")
        for field in sorted(set(server) - allowed):
            errors.append(f"mcp.json {server_name}: unknown field {field!r} for type {kind}")
    return errors


def check_openai(src: Path, plugin: dict, mcp: dict, check_urls: bool) -> list[str]:
    """Errors against the OpenAI plugin submission limits."""
    errors = []
    openai = plugin["extensions"]["com.openai"]
    interface = openai.get("interface", {})
    for field in REQUIRED_INTERFACE:
        if not interface.get(field):
            errors.append(f"interface.{field} is required")
    for field, limit in TEXT_LIMITS.items():
        value = interface.get(field, "")
        if len(value) > limit:
            errors.append(f"interface.{field}: {len(value)} > {limit} characters")
    for field in URL_FIELDS:
        url = interface.get(field, "")
        if url and not url.startswith("https://"):
            errors.append(f"interface.{field} must be HTTPS")
        elif url and check_urls and (problem := _check_url(url)):
            errors.append(f"interface.{field}: {problem}")

    prompts = interface.get("defaultPrompt", [])
    if len(prompts) > 3:
        errors.append(f"interface.defaultPrompt: {len(prompts)} > 3 prompts")
    for prompt in prompts:
        if len(prompt) > 128:
            errors.append(f"defaultPrompt longer than 128 characters: {prompt!r}")
        if "@" in prompt:
            errors.append(f"defaultPrompt must not @mention apps: {prompt!r}")
    capabilities = interface.get("capabilities", [])
    if len(capabilities) > 20 or any(len(c) > 120 for c in capabilities):
        errors.append("interface.capabilities: max. 20 items of 120 characters")

    for field in ("logo", "logoDark", "composerIcon", "composerIconDark"):
        rel = interface.get(field)
        if not rel:
            continue
        if not rel.startswith("./"):
            errors.append(f"interface.{field} must start with ./")
            continue
        path = src / rel[2:]
        if not path.is_file():
            errors.append(f"interface.{field}: {rel} not found")
            continue
        if path.stat().st_size > 5 * 1024 * 1024:
            errors.append(f"interface.{field}: larger than 5 MiB")
        if path.suffix.lower() == ".png":
            size = _png_size(path)
            if size is None:
                errors.append(f"interface.{field}: {rel} is not a PNG")
            elif size[0] != size[1] or size[0] < 48 or size[0] > 4096:
                errors.append(f"interface.{field}: {size[0]}x{size[1]}, must be square 48..4096 px")

    cases = openai.get("review", {}).get("test_cases", {})
    positive, negative = cases.get("positive", []), cases.get("negative", [])
    if len(positive) != POSITIVE_CASES:
        errors.append(f"review: {len(positive)} positive test cases, need exactly {POSITIVE_CASES}")
    if len(negative) != NEGATIVE_CASES:
        errors.append(f"review: {len(negative)} negative test cases, need exactly {NEGATIVE_CASES}")
    for i, case in enumerate(positive):
        for field in ("description", "prompt", "tools_triggered", "expected_behavior"):
            if not case.get(field):
                errors.append(f"positive case {i + 1}: {field} is required")
    for i, case in enumerate(negative):
        for field in ("description", "prompt"):
            if not case.get(field):
                errors.append(f"negative case {i + 1}: {field} is required")

    for locale, text in (openai.get("publication", {}).get("translations") or {}).items():
        subtitle = (text or {}).get("subtitle") or ""
        if len(subtitle) > 30 or "\n" in subtitle:
            errors.append(f"translation {locale}: subtitle max. 30 characters, one line")
        if len((text or {}).get("description") or "") > 4000:
            errors.append(f"translation {locale}: description > 4000 characters")

    servers = mcp.get("mcpServers") or {}
    if len(servers) != 1:
        errors.append(f"mcp.json: {len(servers)} servers, OpenAI needs exactly one")
    for server_name, server in servers.items():
        if not str(server.get("url", "")).startswith("https://"):
            errors.append(f"mcp.json {server_name}: url must be HTTPS")
        if server.get("headers"):
            errors.append(f"mcp.json {server_name}: headers must be empty")

    for key_path in _find_keys(plugin, FORBIDDEN_KEYS) + _find_keys(mcp, FORBIDDEN_KEYS):
        errors.append(f"{key_path}: credentials and reviewer instructions do not belong in the package")

    if not openai.get("review", {}).get("demo_recording_url"):
        print("note: com.openai review.demo_recording_url not set; required before submitting (package or dashboard)")
    return errors


def package_files(src: Path) -> list[Path]:
    """Files to zip besides ``plugin.json``: mcp.json, assets/ and skills/."""
    files = [src / "mcp.json"]
    for folder in ("assets", "skills"):
        if (src / folder).is_dir():
            files += sorted(p for p in (src / folder).rglob("*") if p.is_file())
    return files


def validate(src: Path, version: str, check_urls: bool = False) -> tuple[dict, list[str]]:
    """Check the package in ``src`` with the plugin version ``version``.

    Returns:
        The parsed ``plugin.json`` with ``version`` set, and a list of errors.
    """
    errors = []
    plugin = json.loads((src / "plugin.json").read_text(encoding="utf-8"))
    mcp = json.loads((src / "mcp.json").read_text(encoding="utf-8"))

    if "version" in plugin:
        errors.append("plugin.json must not set version (it is the OLDP version)")
    if not VERSION_RE.match(version):
        errors.append(f"version {version!r} is not a semantic version")
    # Insert version right after name, as in the OpenAI examples.
    with_version = {}
    for key, value in plugin.items():
        with_version[key] = value
        if key == "name":
            with_version["version"] = version
    plugin = with_version

    errors += check_agent_plugins(plugin, mcp)
    if isinstance(plugin.get("extensions", {}).get("com.openai"), dict):
        errors += check_openai(src, plugin, mcp, check_urls)
    return plugin, errors


def build_zip(src: Path, plugin: dict, files: list[Path]) -> Path:
    """Write the ZIP: ``plugin`` as ``plugin.json``, ``files`` relative to ``src``."""
    dist = src / "dist"
    dist.mkdir(exist_ok=True)
    target = dist / f"{plugin['name']}-{plugin['version']}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("plugin.json", json.dumps(plugin, ensure_ascii=False, indent=2) + "\n")
        for path in files:
            zf.write(path, path.relative_to(src).as_posix())
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--src", type=Path, default=REPO_ROOT, help="package directory")
    parser.add_argument("--check-urls", action="store_true", help="fetch the OpenAI listing URLs")
    version_group = parser.add_mutually_exclusive_group()
    version_group.add_argument(
        "--oldp-src", type=Path, default=DEFAULT_OLDP_SRC, help="oldp checkout to take the version from"
    )
    version_group.add_argument("--version", help="plugin version instead of the OLDP version")
    args = parser.parse_args()

    if args.version:
        version = args.version
    else:
        try:
            version = oldp_version(args.oldp_src)
        except FileNotFoundError:
            parser.error(f"no pyproject.toml in {args.oldp_src}; pass --oldp-src DIR or --version X.Y.Z")
    plugin, errors = validate(args.src, version, check_urls=args.check_urls)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    files = package_files(args.src)
    target = build_zip(args.src, plugin, files)
    names = ["plugin.json"] + [p.relative_to(args.src).as_posix() for p in files]
    print(f"ok: {target} (version {version}; {', '.join(names)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
