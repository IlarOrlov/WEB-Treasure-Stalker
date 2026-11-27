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

### Status

| Requirement | State |
|---|---|
| OSI license | done: MIT (`LICENSE`) |
| Public repository, released in the form that will be signed | done: GitHub releases built by CI |
| "Code signing policy" on home page and release page | done: README section + link in every release's notes |
| Privacy statement, uninstall instructions | done: README ("Code signing policy", "Uninstalling") |
| Product name / version in the executable | done: `packaging/version_info.txt`; CI fails if it does not match the tag |
| CI signing step | done: `release.yml` (inactive until the variables below exist) |
| Artifact configuration | ready: `packaging/signpath/artifact-configuration.xml` |
| MFA on GitHub (and later SignPath) | **you** |
| Application | **you** |

### Your steps

1. **GitHub MFA**: GitHub → Settings → Password and authentication → enable two-factor authentication.
2. **Apply** at https://signpath.org/apply. Suggested answers:
   - Project: WEB-Treasure-Stalker, https://github.com/IlarOrlov/WEB-Treasure-Stalker
   - License: MIT
   - Description: desktop app (Windows/Linux) that analyses a web page the user enters, finds the videos
     it plays, downloads them on request with yt-dlp, scans the page and the downloaded media for malware
     indicators, and reports whether the site looks like a piracy portal. It does not scan or modify the
     computer it runs on.
   - Artifacts to sign: one PyInstaller executable per release (`WEB-Treasure-Stalker-windows-x64.exe`),
     built by GitHub Actions from the tagged source.
   - Code signing policy: https://github.com/IlarOrlov/WEB-Treasure-Stalker#code-signing-policy
3. **After acceptance**, in SignPath:
   - enable MFA on your SignPath account;
   - create the project (slug e.g. `WEB-Treasure-Stalker`) and link the GitHub repository as a trusted
     build system (install the SignPath GitHub App on the repository if asked);
   - add an artifact configuration with the contents of `packaging/signpath/artifact-configuration.xml`
     and make it the project's **default** (the workflow uses the default);
   - use the release signing policy SignPath Foundation sets up (with manual approval);
   - create a CI user with *submitter* permission for that policy and an API token for it.
4. **GitHub → repository → Settings → Secrets and variables → Actions**:
   - *Secrets*: `SIGNPATH_API_TOKEN` = the API token
   - *Variables*: `SIGNPATH_ORGANIZATION_ID`, `SIGNPATH_PROJECT_SLUG`, `SIGNPATH_POLICY_SLUG`
5. **README**: replace the first sentence of "Code signing policy" with SignPath's required wording:
   `Free code signing provided by [SignPath.io](https://signpath.io), certificate by [SignPath Foundation](https://signpath.org).`
6. **Release**: bump the version (pyproject.toml, `wts/__init__.py`, `packaging/version_info.txt`) and push
   a tag. The workflow sends the Windows executable to SignPath and waits up to 1 hour; approve the request
   on SignPath; the workflow then checks the signature (`Get-AuthenticodeSignature` must be *Valid*) and
   publishes the signed file.

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
