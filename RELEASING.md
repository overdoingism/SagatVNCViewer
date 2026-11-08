# Releasing

Everything that gets published is built by GitHub Actions
(`.github/workflows/release.yml`), never on a developer's machine.

1. Set `__version__` in `sagatvnc/__init__.py` and commit.
2. Optional dry run: *Actions > Release > Run workflow*. It builds every
   file and keeps them as workflow artifacts, but publishes nothing.
3. Tag and push:

   ```bash
   git tag -a v0.1.0 -m "SagatVNC Viewer 0.1.0"
   git push origin v0.1.0
   ```

   The workflow refuses a tag that doesn't match `__version__`. It runs
   the tests on every platform, builds the wheels, the sdist and the
   Windows exe, signs build provenance attestations for all of them and
   creates the GitHub release with `SHA256SUMS`.

## What gets built

| File | Runner | Rust decoders |
|------|--------|---------------|
| `*-py3-none-win_amd64.whl`, `SagatVNCViewer-*-windows-x64.zip` | `windows-latest` | yes |
| `*-py3-none-win_arm64.whl`, `SagatVNCViewer-*-windows-arm64.zip` | `windows-11-arm` | yes |
| `*-py3-none-manylinux_*_x86_64.whl` | `ubuntu-latest` (manylinux container) | yes |
| `*-py3-none-manylinux_*_aarch64.whl` | `ubuntu-24.04-arm` (manylinux container) | yes |
| `*-py3-none-macosx_11_0_universal2.whl` | `macos-latest` (lipo of both slices) | yes |
| `*-py3-none-any.whl`, `*.tar.gz` | `ubuntu-latest` | no / built on install if `cargo` exists |

The library is loaded with ctypes and uses no Python C API, so one
wheel per platform covers every Python 3 version (`setup.py` tags it
`py3-none-<platform>`). `SAGATVNC_BUILD_ACCEL` (`auto`, `1`, `0`)
decides whether `setup.py` builds it.

## PyPI (off for now)

The `pypi` job runs only when the repository variable `PYPI_PUBLISH` is
`true`. To turn it on:

1. On PyPI, add a *pending trusted publisher* for project
   `sagatvnc-viewer`: owner `overdoingism`, repository
   `SagatVNCViewer`, workflow `release.yml`, environment `pypi`.
2. On GitHub, create the environment `pypi` (*Settings > Environments*)
   and set the variable `PYPI_PUBLISH` to `true`
   (*Settings > Secrets and variables > Actions > Variables*).

No API token is stored anywhere. PyPI keeps its own attestations of
the upload.

## Code signing

The exe is not Authenticode-signed, so SmartScreen warns on first
start. Attestations prove where a file was built, but Windows doesn't
read them. Free signing for open-source projects is available from the
SignPath Foundation if that becomes worth it.
