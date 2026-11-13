# Open Legal Data (Germany) – agent plugin

Plugin package for AI agents that connects them to the MCP server of the
[Open Legal Data Platform (Germany)](https://de.openlegaldata.io/): German
court decisions and laws, full texts, citation checks and the citation graph
between decisions and laws.

The package follows the vendor-neutral [Agent Plugins](https://agent-plugins.org/)
standard (1.0.0), so one package serves every client that supports the
standard. Client-specific data lives in reverse-domain extension namespaces,
here `extensions["com.openai"]` for the ChatGPT plugin directory listing.

## The MCP server

| | |
|---|---|
| Endpoint | `https://de.openlegaldata.io/mcp` (Streamable HTTP) |
| Authentication | none for reading; OAuth sign-in with an OLDP account for higher rate limits and the `submit_feedback` tool |
| MCP Registry | `io.openlegaldata/de` |
| Server source | [openlegaldata/oldp](https://github.com/openlegaldata/oldp) (MCP docs in `docs/mcp.md`) |

Every MCP client can also connect to the endpoint directly, without this
package. Claude, for example, uses its own connector directory instead of
Agent Plugins:

```bash
claude mcp add --transport http oldp https://de.openlegaldata.io/mcp
```

## Contents

| File | Content |
|------|---------|
| `plugin.json` | Manifest: name, description, author, links, keywords. `extensions["com.openai"]` holds the ChatGPT listing (texts, category, prompts, review test cases, release notes, German translation). No `version`, see below. |
| `mcp.json` | The one MCP server, `https://de.openlegaldata.io/mcp`, without headers |
| `assets/logo.png`, `assets/icon.png` | OLDP logo, 512x512 |
| `scripts/build_agent_plugin.py` | Validator and ZIP builder (Python 3.11+, stdlib only) |

The package never contains credentials. Test accounts for reviews are entered
in the respective portal, not here.

## Build and validate

```bash
python3 scripts/build_agent_plugin.py --version 0.16.5 --check-urls
# -> dist/open-legal-data-germany-0.16.5.zip (dist/ is gitignored)
```

The script checks:

- `plugin.json` and `mcp.json` against the Agent Plugins 1.0.0 schemas
  (allowed fields, name format, server types);
- if `extensions["com.openai"]` is present, the limits of OpenAI's
  [submission guide](https://developers.openai.com/plugins/deploy/submission):
  `displayName` and `shortDescription` max. 30 characters, `longDescription`
  max. 4000, max. 3 `defaultPrompt` of 128 characters, exactly 5 positive and
  3 negative test cases with their required fields, square PNG icons, HTTPS
  listing URLs, exactly one MCP server without headers, no credential keys.
  `--check-urls` also fetches the listing URLs.

The ZIP contains `plugin.json` (with `version`), `mcp.json`, `assets/` and
`skills/` if present. CI runs the same checks on every push.

## Versioning

The plugin version is the version of the OLDP release that serves the MCP
server. Without `--version`, the script reads it from the `pyproject.toml` of
an oldp checkout next to this repository (`../oldp`, or `--oldp-src DIR`).
Each upload to a plugin portal needs a new version, so to publish a changed
listing without a new OLDP release add a suffix, e.g. `--version 0.16.5-1`.

The package `name` (`open-legal-data-germany`) and the server URL cannot
change after publication in the ChatGPT plugin directory. Changes to the MCP
tools themselves need no new package: clients read the tool list from the
server.

## License

[MIT](LICENSE)
