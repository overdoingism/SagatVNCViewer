# SagatVNC Viewer

A VNC viewer written in pure Python (standard library only: `socket`, `ssl`,
`zlib`, `tkinter`). It is a port of the viewer in
[TigerVNC](https://github.com/TigerVNC/tigervnc).

> Not affiliated with or endorsed by the TigerVNC project.

## Running

Requires Python 3.9+ with Tk (included in the python.org Windows and macOS
installers; on Debian/Ubuntu install `python3-tk`).

```bash
python -m sagatvnc                      # shows the connection dialog
python -m sagatvnc myhost:1             # display 1 = port 5901
python -m sagatvnc myhost::5900 -Shared -FullScreen
python -m sagatvnc -listen 5500         # wait for a reverse connection
```

Or install it with `pip install .` and run `sagatvncviewer`. Options work like
`vncviewer`: they are case-insensitive, and you can write them as
`-Name value`, `-Name=value`, `--Name=value` or `Name=value`. Run
`python -m sagatvnc -help` for the full list.

Press **F8** (`-MenuKey`) inside the session to open the popup menu. It has
full screen, Ctrl/Alt toggles, Send Ctrl-Alt-Del, refresh, view-only,
connection info and disconnect.

## What is supported

| Area            | Supported                                                     |
|-----------------|---------------------------------------------------------------|
| Protocol        | RFB 3.3, 3.7, 3.8                                             |
| Security        | None, VncAuth, VeNCrypt: Plain, TLSNone/TLSVnc/TLSPlain, X509None/X509Vnc/X509Plain |
| Encodings       | Raw, CopyRect, RRE, CoRRE, Hextile, ZRLE, Tight (no JPEG)     |
| Pseudo-encodings| DesktopSize, ExtendedDesktopSize (remote resize), LastRect, DesktopName, CompressLevel |
| Input           | Keyboard (X keysyms, with Unicode keysyms for other characters), mouse, wheel |
| Clipboard       | Plain RFB cut text (Latin-1) in both directions               |
| Other           | Password files (`vncpasswd` format), reverse connections, server history |

Not supported yet: Tight JPEG and H.264 (Tk can't decode JPEG without extra
libraries), RSA-AES/DH/MSLogonII security, rich cursors, extended
(UTF-8) clipboard, continuous updates/fences, audio, touch/gestures,
low-colour mode and the options dialog.

When X509 certificates are used without `-X509CA`, they are trusted on first
use. The fingerprints are stored in `%APPDATA%\SagatVNC\x509_known_hosts` on
Windows, or `~/.config/sagatvnc/x509_known_hosts` elsewhere.

## Tests

```bash
python -m unittest discover -s tests -t .
```

The tests include a small scripted RFB server (`tests/fakeserver.py`), so the
handshake, authentication and all decoders run without a real VNC server.

## License

GNU General Public License version 2 or later; see [LICENSE](LICENSE). This
program is a derivative work of TigerVNC, and source files keep the upstream
copyright notices of the code they were ported from.
