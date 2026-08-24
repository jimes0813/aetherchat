# Code signing policy

AetherChat intends to use SignPath for Authenticode signing of official Windows
release artifacts after the project has been accepted into the SignPath
Foundation open-source program.

Free code signing provided by [SignPath.io](https://signpath.io/), certificate by
[SignPath Foundation](https://signpath.org/).

Until the SignPath application and signing configuration are approved, Windows
CI artifacts are unsigned and Windows may display an unknown-publisher or
SmartScreen warning. A build must not be described as Authenticode-signed unless
its signature has been verified in the release workflow.

## Trusted source and build

Official Windows release artifacts must:

1. originate from the official
   [jimes0813/aetherchat](https://github.com/jimes0813/aetherchat) repository;
2. be built by the repository's GitHub Actions workflow on a GitHub-hosted
   Windows runner;
3. remain traceable to the public source commit and workflow run recorded on
   the release page;
4. pass the repository's automated tests and Windows install, upgrade, and
   uninstall smoke checks before publication; and
5. be submitted to SignPath from the same trusted workflow. Locally supplied
   replacement binaries are not eligible for official signing.

Release signing requests require manual approval. Signing credentials and
service tokens are stored only as GitHub Actions secrets and must never be
committed to this repository.

## Team roles

- Committer and reviewer: [@jimes0813](https://github.com/jimes0813)
- Signing approver: [@jimes0813](https://github.com/jimes0813)

Changes proposed by anyone other than the committer must be reviewed before
they are merged. The committer, reviewer, and approver must use multi-factor
authentication for GitHub and SignPath access.

## Privacy

AetherChat does not send information to networked systems unless the user asks
it to use a configured network service or expressly approves the connection.
The precise data flows and exceptions are documented in the
[privacy policy](PRIVACY.md).

## Verification and reporting

Each release publishes SHA-256 checksums, build metadata, and a CycloneDX SBOM.
The Windows workflow verifies the resulting Authenticode status when signing is
required. Suspected compromised releases or signing-policy violations should be
reported through a private GitHub security advisory for this repository.
