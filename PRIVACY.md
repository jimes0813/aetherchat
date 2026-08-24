# AetherChat privacy policy

Last updated: 2026-08-24

AetherChat is a local-first desktop application. Its conversation history,
attachments, indexes, memories, task state, settings, and audit records are
stored on the user's device in the AetherChat data directory. The project does
not operate an analytics, advertising, or telemetry collection service.

## Network connections chosen by the user

AetherChat transfers data to another system only when the user configures or
invokes the corresponding feature:

- A configured cloud model provider receives the messages, selected
  attachments, system instructions, and tool context needed to answer the
  user's request. That transfer is governed by the selected provider's privacy
  policy.
- A user-configured MCP, voice, local-model, ComfyUI, or other integration
  receives the data required by the requested operation. These services may be
  local or remote depending on the address supplied by the user.
- Opening a web link uses the selected external browser. The destination site
  receives ordinary browser request data under its own policy.
- Checking for an update requires explicit confirmation. It downloads a signed
  release manifest, and, if an update is selected, the release package from the
  configured HTTPS update location (GitHub Releases by default).

API keys and OAuth credentials are stored in the operating system's keyring
when that facility is available. They are not intentionally placed in project
files, exported conversations, or diagnostic bundles.

## Local permissions and generated data

File access, command execution, screenshots, keyboard or pointer control, and
other state-changing tools are subject to AetherChat's permission controls.
Screenshots and local files remain on the device unless the user attaches them
to a request sent to a configured remote service or explicitly saves or shares
them elsewhere.

Diagnostic bundles are generated locally and redacted for known secret forms.
They are not uploaded automatically. The user decides whether and how to share
them.

## Retention and deletion

Data remains on the device until the user deletes it through AetherChat or the
operating system. Uninstalling the desktop application does not delete the data
directory by default, so users can preserve their work across reinstalls. To
remove all local data, delete the AetherChat application-data directory after
exiting the application.

Remote providers and user-configured integrations may retain submitted data
under their own terms. AetherChat cannot delete data held by those third
parties.

## Contact

Privacy or security concerns can be reported through the official repository's
GitHub issue tracker. Security-sensitive reports should use a private GitHub
security advisory instead of a public issue.
