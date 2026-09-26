# Randy Hub

Randy Hub is a local-first, extensible macOS workspace for AI agents, tools, model providers, and desktop workflows.

It provides a Python/JavaScript extension host with capability permissions, isolated extension origins, macOS Keychain-backed credentials, local-only bridge controls, provider management, backup/restore, self-tests, and an optional read-only adapter to an external Strategist gateway.

## Highlights

- Local-first macOS host with native window and menu-bar integration
- Extension manifests, permission grants, identity isolation, and preflight checks
- Per-extension storage/config/secret boundaries
- macOS Keychain credential storage
- Local bridge guarded by host/token/origin checks
- Provider connectivity and model discovery
- Security-focused tests and built-in self-test
- Extension authoring skill and templates

## Run

From the repository root:

```bash
python3 -m strategist.hub
python3 -m strategist.hub --browser
python3 -m strategist.hub doctor
python3 -m strategist.hub selftest
```

For native macOS integration, install the required Python dependencies including PyObjC. Tests are under `strategist/hub/tests`.

## Project layout

- `strategist/hub/` — Randy Hub runtime, UI, extension host, security boundaries, tests
- `skills/randy-hub-extension/` — extension authoring guidance, references, and templates

## Security model

Randy Hub treats extensions as untrusted by default. Capabilities must be declared and granted, extension identity includes the source path, secrets are stored in Keychain, remote provider URLs require HTTPS, and the Strategist integration is constrained to loopback access and whitelisted status fields.

Please do not commit real API keys, runtime databases, `.env` files, private keys, or personal Strategist data.

## Upstream attribution

Randy Hub was designed after studying the MIT-licensed x-hub project by dckxx and keeps compatibility with parts of its extension interface model. Randy Hub does not present itself as the original x-hub project.

See:
- `strategist/hub/THIRD_PARTY_NOTICES.md`
- `strategist/hub/LICENSE-x-hub.txt`

Those files document the referenced upstream commit, adapted interface concepts, and Randy Hub's independently implemented modules.

## License

Randy Hub's original code in this repository is released under the MIT License. Third-party portions and adapted interface concepts remain subject to their respective notices and licenses.
