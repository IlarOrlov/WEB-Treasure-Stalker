# Code signing (Windows)

Windows **Smart App Control** blocks every executable *and every DLL/.pyd it loads* that has neither a
valid code signature nor a known reputation. **SmartScreen** (the "Windows protected your PC" dialog) warns
about new executables without reputation. Both look at Authenticode signatures.

## What the build already does (free, no account needed)

The single-file executable bundles native modules from its Python dependencies. Most are signed by their
publishers (Python itself by the Python Software Foundation, Qt/PySide6 and Shiboken by The Qt Company,
Playwright's Node.js by Microsoft). The unsigned ones were removed where a pure-Python fallback exists:

| Module | Status | Effect |
|---|---|---|
| `charset_normalizer` `cd`/`md` `.pyd` (the file in the user report) | replaced by the project's pure-Python wheel (CI step) | none (same code, slightly slower charset detection) |
| `pycryptodomex` (42 `.pyd`) | excluded in `wts.spec` | yt-dlp uses its built-in AES (slower only for AES-encrypted HLS) |
| `brotli` | excluded | servers send gzip instead of brotli |
| `websockets.speedups` | excluded | pure-Python frame masking |
| `greenlet` test modules | excluded | none |
| **`greenlet._greenlet`** | **still bundled, unsigned** | required by Playwright (browser analysis); cannot be removed |
| **the executable itself** (PyInstaller bootloader) | **unsigned** | needs a certificate |

On Windows builds `wts.spec` prints every unsigned native module still bundled (`[signing] …` lines in the
CI log).

## Free certificate: SignPath Foundation (open-source projects)

SignPath Foundation signs releases of open-source projects for free, with a certificate issued to
"SignPath Foundation" (shown as the publisher). Conditions (https://signpath.org/terms), in short:

- an OSI-approved open-source license for the whole project (the repository uses MIT);
- public repository, actively maintained, already released in the form that will be signed;
- multi-factor authentication on GitHub and SignPath for all team members;
- every release approved by hand on SignPath before it is signed;
- the home page and release page show a **"Code signing policy"** section (text below);
- only the project's own binaries are signed: upstream binaries such as `greenlet` stay unsigned
  (they may be included in the signed package);
- acceptance is at SignPath's discretion. Their terms exclude malware/PUPs and "hacking or white-hat
  security-scanning tools that run inside the execution environment" (this app scans remote sites and
  downloaded media, not the machine it runs on, but they decide).

### Steps

1. ~~Add an OSI license~~ done: MIT (`LICENSE`).
2. Enable MFA on GitHub.
3. Apply at https://signpath.org/apply.
4. Once accepted, in SignPath: create the project, an artifact configuration for a ZIP containing
   `WEB-Treasure-Stalker-windows-x64.exe` (GitHub delivers artifacts zipped) with product name
   `WEB-Treasure-Stalker`, and a release signing policy. Create an API token for a submitter user and
   install the SignPath GitHub App on the repository.
5. In GitHub → Settings → Secrets and variables → Actions:
   - secret `SIGNPATH_API_TOKEN`
   - variables `SIGNPATH_ORGANIZATION_ID`, `SIGNPATH_PROJECT_SLUG`, `SIGNPATH_POLICY_SLUG`
6. Push a release tag. `release.yml` then sends the Windows executable to SignPath, waits (up to 1 hour)
   for the manual approval, checks the signature and releases the signed file. Without those variables the
   workflow releases the unsigned executable as before.
7. Add the policy section below to README.md and the release notes.

```markdown
## Code signing policy

Free code signing provided by [SignPath.io](https://signpath.io), certificate by
[SignPath Foundation](https://signpath.org).

- Committers and reviewers: <GitHub user names>
- Approvers: <GitHub user names>

Privacy policy: this program does not transfer any information to other networked systems unless
specifically requested by the user or the person installing or operating it.
```

### What signing fixes and what it does not

- Signed executable: Smart App Control accepts it; SmartScreen warnings fade as the certificate builds
  reputation.
- `greenlet._greenlet` stays unsigned, so on a machine with Smart App Control **on**, the browser-based
  analysis can still be blocked until that file gains reputation with Microsoft. Everything else
  (GUI, yt-dlp platforms, downloads) runs.

## Other routes

- **Microsoft Store (MSIX)**: Microsoft signs Store packages, which covers every file in the package
  (including `greenlet`). Individual developer accounts are free. Store policies forbid apps that facilitate
  copyright infringement, so certification of this app is uncertain.
- **Paid certificates**: Azure Artifact Signing (monthly fee) or a commercial/OV certificate would let
  the build sign *all* bundled binaries, including `greenlet`.
- A self-signed certificate does **not** help: other machines do not trust it.
