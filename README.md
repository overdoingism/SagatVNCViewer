<p align="center"><img src="https://raw.githubusercontent.com/overdoingism/SagatVNCViewer/main/docs/logo.png" alt="SagatVNC Viewer logo" width="128"></p>

# SagatVNC Viewer

[繁體中文說明](README.zh-TW.md)

I couldn't find a VNC viewer I liked on Windows, so I wrote one. It is
a port of the [TigerVNC](https://github.com/TigerVNC/tigervnc) viewer to
Python (standard library only: `socket`, `ssl`, `zlib`, `tkinter`), with
optional Rust code for the slow decoder loops.

It is made for Windows and used there every day. It also runs on Linux
and macOS and passes the tests there, but nobody has really used it on
them yet: reports are welcome.

> Not affiliated with or endorsed by the TigerVNC project.

## Download

From [Releases](https://github.com/overdoingism/SagatVNCViewer/releases):

- **Windows**: `SagatVNCViewer-<version>-windows-x64.zip` (or `-arm64`).
  Unzip it and run `SagatVNCViewer.exe`. Nothing to install.
- **Python** (any OS): `pip install` the `.whl` for your platform. The
  wheels for Windows, Linux and macOS have the Rust decoders built in;
  the `py3-none-any` wheel is pure Python.

Everything there is built by GitHub Actions from the tagged commit, not
on someone's PC, and each file has a signed build provenance
attestation. To check a download:

```bash
gh attestation verify SagatVNCViewer-0.2.0-windows-x64.zip --repo overdoingism/SagatVNCViewer
```

The exe isn't code-signed, so SmartScreen may warn the first time
(*More info* > *Run anyway*).

## Running

Needs Python 3.9+ with Tk (in the python.org installers; on
Debian/Ubuntu install `python3-tk`), plus Pillow for scaling.

```bash
python -m sagatvnc                      # connection dialog
python -m sagatvnc myhost:1             # display 1 = port 5901
python -m sagatvnc myhost::5900 -Shared -FullScreen
python -m sagatvnc -listen 5500         # wait for a reverse connection
```

Or `pip install .` and run `sagatvncviewer`. Options work as in
`vncviewer`: case-insensitive, written `-Name value`, `-Name=value`,
`--Name=value` or `Name=value`. `python -m sagatvnc -help` lists them.

**`.tigervnc` files** saved by TigerVNC can replace the server name, or be
dropped onto `SagatVNCViewer.exe`. As in TigerVNC, the file overrides the
command line; an empty `ServerName` shows the connection dialog.
Parameters this port doesn't implement are ignored and logged.

### The F8 menu

Press **F8** (`MenuKey`) in the session, or click the icon at the
top left of the window, to open it. It stays open while you change
settings; *Close menu*, Esc or F8 closes it.

- Disconnect
- Window: full screen, resize to session, *Window at startup*
  (window, maximized or full screen), *When the window is resized*,
  remote DPI compensation, *Traffic in title bar*
- *Picture quality*: auto select, encoding, colour depth, JPEG, zlib level
- *Input*: view only, remote cursor shape, local input method,
  clipboard each way (`AcceptClipboard`, `SendClipboard`)
- *X509 certificates*
- *Keyboard*: menu key, special keys; Send F8, Send Ctrl-Alt-Del
- Refresh screen
- Save settings, Save settings as...
- *Rust decoders*: ticked when loaded (untick to compare with Python)
- *Troubleshooting*: log level, log folder, the troubleshooting guide
- Connection info, About

*Save settings* writes your menu changes to the `.tigervnc` file the
session came from (or asks where). Unknown parameters and comments in the
file are kept; passwords are never saved. Unsaved changes show as
*▶ Save settings (unsaved changes)* in orange.

The **title bar** shows traffic and the session, e.g.
`UP: 0.4 KiB/s DL: 2.1 MiB/s   2560x1600 Tight+JPEG 24bit TLSVnc TLSv1.2`
(`TitleStats=0` turns it off). *Connection info* adds the TLS cipher and
whether the certificate was checked.

The **local IME** is off in the viewer window, so you type CJK with the
*remote* computer's IME. Turn it on with *Local input method*
(`LocalInputMethod`).

The exe logs to `log\sagatvnc.log` next to it (or `%APPDATA%\SagatVNC`
if that can't be written) and keeps the last four logs.

## Setting guide

In [setting_guide/](setting_guide/):

- [Connecting](setting_guide/README.md#connecting)
- [Security type](setting_guide/README.md#security-type)
- [X509 certificates](setting_guide/README.md#x509-certificates)
- [Which screen encoding?](setting_guide/README.md#which-screen-encoding)
- [Special keys](setting_guide/README.md#special-keys)

## Troubleshooting

In [troubleshooting/](troubleshooting/):

- [Scaling and high-DPI displays](troubleshooting/README.md#scaling-and-high-dpi-displays)
- [Pointer drifting off on Windows servers](troubleshooting/README.md#pointer-drifting-off-on-windows-servers)
- [Ctrl+V reaches the server as Ctrl](troubleshooting/README.md#ctrlv-reaches-the-server-as-ctrl) (x0vncserver `-RawKeyboard=1`)
- [Resizing the window: remote resolution or scaling?](troubleshooting/README.md#resizing-the-window-remote-resolution-or-scaling)
- [Disconnected at the Windows login / lock screen](troubleshooting/README.md#disconnected-at-the-windows-login--lock-screen) (use UltraVNC)

## Building

```bash
python packaging\build_accel.py      # Rust decoders, for running from source
python -m venv .venv
.venv\Scripts\python -m pip install pyinstaller pillow
.venv\Scripts\python packaging\build_exe.py   # dist\SagatVNCViewer.exe
```

With Rust (`cargo`) installed, the exe includes the native decoders
(Hextile, ZRLE and Tight's gradient filter 7-40x faster). Without it, the
same algorithms run in Python.

## What is supported

| Area            | Supported                                                     |
|-----------------|---------------------------------------------------------------|
| Protocol        | RFB 3.3, 3.7, 3.8                                             |
| Security        | None, VncAuth, Plain, VeNCrypt (incl. None/VncAuth sub-types), TLSNone/TLSVnc/TLSPlain, X509None/X509Vnc/X509Plain; `VNC_USERNAME`/`VNC_PASSWORD`, keep password for reconnect |
| Encodings       | Raw, CopyRect, RRE, Hextile, ZRLE, Tight (incl. JPEG), JPEG (CoRRE decoded but, like upstream, not advertised) |
| Pseudo-encodings| Same list and order as TigerVNC: Cursor, CursorWithAlpha, XCursor, VMwareCursor(+Position), DesktopSize, ExtendedDesktopSize, LEDState, VMwareLEDState, DesktopName, LastRect, ExtendedClipboard, ContinuousUpdates, Fence, QEMUKeyEvent, CompressLevel, QualityLevel |
| Picture         | Full colour or 256/64/8 colours (`FullColor`, `LowColorLevel`), JPEG quality (`QualityLevel`, `NoJPEG`), TigerVNC's `AutoSelect` bandwidth logic; switchable at runtime from the F8 menu |
| Input           | Keyboard tracked per physical key, Windows AltGr merging, QEMU scan codes, lock-key LED sync; mouse, wheel (one click per event), pointer warps |
| Clipboard       | Extended Clipboard (UTF-8, so Chinese/emoji survive) both ways; classic Latin-1 cut text for older servers |
| Cursor          | The server's cursor shape as the local cursor (Windows; padded to the system cursor size so Windows doesn't stretch it), or the plain arrow with `-RemoteCursorShape=0` / the F8 menu |
| Other           | Password files (`vncpasswd` format), reverse connections, server history, save settings to `.tigervnc` from the F8 menu |

Not supported yet: H.264, RSA-AES/DH/MSLogonII security, audio,
extended mouse buttons, touch/gestures and the options dialog. JPEG and
scaling (not in TigerVNC) need Pillow, which the exe bundles.

## Tests

```bash
python -m unittest discover -s tests -t .
```

The tests include a small scripted RFB server (`tests/fakeserver.py`), so the
handshake, authentication and all decoders run without a real VNC server.

## License

GNU General Public License version 3 or later; see [LICENSE](LICENSE). This
program is a derivative work of TigerVNC, which is licensed under the GPL
"version 2 or (at your option) any later version"; SagatVNC Viewer takes
that option and is distributed under version 3 or later. Source files keep
the upstream copyright notices of the code they were ported from.

The optional Rust decoders in `accel/` are part of this program and under
the same license; [accel/README.md](accel/README.md) says where they come
from. What the Windows exe bundles (Python, OpenSSL, Tcl/Tk, Pillow,
PyInstaller's bootloader, the Rust standard library) is listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md); the build copies their
license texts to `dist\licenses\`. [licenses/](licenses) holds
TigerVNC's own licence (GPLv2, the upstream's) and the texts that go
with the wheels and the exe.
