# SagatVNC Viewer

A VNC viewer written in pure Python (standard library only: `socket`, `ssl`,
`zlib`, `tkinter`). It is a port of the viewer in
[TigerVNC](https://github.com/TigerVNC/tigervnc).

> Not affiliated with or endorsed by the TigerVNC project.

## Running

Requires Python 3.9+ with Tk, plus Pillow if you want scaling to the window (included in the python.org Windows and macOS
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

### `.tigervnc` files

Connection files saved by TigerVNC (`TigerVNC Configuration file Version 1.0`)
can be passed in place of the server name, and you can drag them onto
`SagatVNCViewer.exe`. As in TigerVNC, values in the file override the
command line. If `ServerName` is empty, the connection dialog appears.
TigerVNC parameters that this port doesn't implement (e.g. `FullScreenMode`)
are ignored and logged.

### Windows executable

```bash
python -m venv .venv
.venv\Scripts\python -m pip install pyinstaller pillow
.venv\Scripts\python packaging\build_exe.py
```

This produces a single windowed `dist\SagatVNCViewer.exe`. Because it has no
console, it logs to `%APPDATA%\SagatVNC\sagatvnc.log` and reports errors in
dialogs.

### Scaling and high-DPI displays

The viewer declares itself DPI aware (per-monitor v2), so Windows no longer
stretches it into a blurry, magnified bitmap on 125%/150%/200% displays.
Instead, the remote desktop is scaled to fit the window
(`-Scaling=Auto`, the default), keeping its aspect ratio:

- shrinking uses **Lanczos-3**, which is sharp and anti-aliased (good for text)
- enlarging uses **bicubic** (Catmull-Rom), which rings less than Lanczos
- at exactly 1:1 there is no resampling, so pixels are exact

Only the areas that changed are rescaled. Each patch is resampled with enough
margin for the filter, so patches leave no seams. `-Scaling=Off` (or
*Scale to window* in the F8 menu) gives 1:1 with scrollbars. Scaling needs
Pillow (`pip install .[scaling]`); the exe already bundles it. Without Pillow
the viewer falls back to 1:1. `RemoteResize` still works as in TigerVNC: if
the server supports it, it changes the remote resolution to match the
window, and then no scaling is needed.

### Pointer drifting off on Windows servers

A server that isn't DPI aware, such as TigerVNC's winvnc on a desktop
scaled to 150%, can draw its cursor picture in the wrong place. The arrow
then drifts towards the top left and seems to hit an invisible wall at the
right and bottom, even though clicks land correctly. The viewer asks for
the cursor *shape* (as TigerVNC does) and shows it as the real local
cursor, so this is handled automatically and needs no setting.

Only if the **clicks** themselves land in the wrong place:

- Fix on the server: open the properties of the server's .exe, go to
  Compatibility, choose *Change high DPI settings*, tick *Override high DPI
  scaling behavior* and pick *Application*. Then restart the server.
- Or compensate in the viewer: open the F8 menu and choose *Remote DPI
  compensation* > *Calibrate...*. Move the mouse until the remote cursor
  touches the bottom-right corner of the remote screen, then click. The
  factor doesn't always equal the Windows scale setting, so calibrating
  beats guessing. A value that is too large shrinks the area the cursor
  can reach. Keep the result with `-RemoteDpiScale=N`, or with
  `RemoteDpiScale=N` (or the shorter `DPI=N`) in the .tigervnc file.

### Kicked off at the Windows login / lock screen

A server started in the user's session, rather than as a service, can show
the lock screen picture, because that runs on the normal desktop. When
someone presses a key or clicks, Windows switches to the secure Winlogon
desktop for the password box. The server can't attach to it, and it drops
every client. Until someone logs in locally, new connections are cut off
right after authentication. TigerVNC's winvnc needs to run as a service
for the login screen to work: run `winvnc4.exe -register` and then
`winvnc4.exe -start` as administrator.

### Input method (IME)

By default the local IME is disabled inside the viewer window. Keys go
straight to the server, and you type Chinese/Japanese/Korean with the
*remote* computer's IME, so no local composition window floats over the
desktop. Turn it back on with *Local input method* in the F8 menu, or
`-LocalInputMethod`.

Press **F8** (`-MenuKey`) inside the session to open the popup menu. It has
full screen, Ctrl/Alt toggles, Send Ctrl-Alt-Del, refresh, view-only,
connection info and disconnect.

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
| Cursor          | Local system arrow by default; the server's shape with `-RemoteCursorShape` (Windows) |
| Other           | Password files (`vncpasswd` format), reverse connections, server history |

Not supported yet: H.264, RSA-AES/DH/MSLogonII security, audio,
extended mouse buttons, touch/gestures, keyboard grab and the options
dialog. JPEG needs Pillow (bundled in
the exe).  (Scaling to the window is an addition that
TigerVNC itself does not have.)

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
