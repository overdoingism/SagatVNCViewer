Built from `@TAG@` (commit @SHA@) by
[this workflow run](@RUN@). 繁體中文說明見
[README.zh-TW.md](https://github.com/@REPO@/blob/@TAG@/README.zh-TW.md).

| File | For |
|------|-----|
| `SagatVNCViewer-*-windows-x64.zip` | Windows 10/11 x64: unzip and run the exe |
| `SagatVNCViewer-*-windows-arm64.zip` | Windows 11 on ARM |
| `*-py3-none-<platform>.whl` | `pip install` with the Rust decoders built in (Windows, Linux, macOS) |
| `*-py3-none-any.whl` | `pip install` anywhere else (pure Python, slower decoding) |
| `*.tar.gz` | source; builds the Rust decoders if `cargo` is installed |

Only Windows gets real use. Linux and macOS builds pass the tests, but
nobody has really used them yet.

Every file has a signed build provenance attestation. To check that a
download was built here, from this commit:

    gh attestation verify <file> --repo @REPO@

or compare it with `SHA256SUMS`.

The exe is not code-signed, so SmartScreen may warn on the first start
(*More info* > *Run anyway*).
