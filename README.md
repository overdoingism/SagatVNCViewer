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

This produces a single windowed `dist\SagatVNCViewer.exe`. If Rust
(`cargo`) is installed, the build also compiles the native decoders in
`accel/` and bundles them; they make Hextile, ZRLE and Tight's gradient
filter 7-40x faster. Without Rust the exe uses the same algorithms in
Python. `python packaging\build_accel.py` builds them for running from
source. Because it has no
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
margin for the filter, so patches leave no seams. `-Scaling=Off` gives 1:1
with scrollbars.

### Resizing the window: remote resolution or scaling?

F8 → *When the window is resized* decides who adapts:

- *Change the remote resolution* (`RemoteResize=1`, the TigerVNC default):
  the server switches to the window's size. Linux Xvnc does this, so
  maximizing the window changes the remote desktop's resolution. Windows
  servers (winvnc) refuse such requests, so there the picture is scaled
  instead.
- *Scale the picture* (`RemoteResize=0`): the remote resolution stays, and
  the viewer scales the picture to the window.
- *Keep 1:1, show scrollbars* (`RemoteResize=0 Scaling=Off`).

*Save settings* keeps the choice in the `.tigervnc` file. Scaling needs
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

Press **F8** (`-MenuKey`) inside the session to open the popup menu, and
again (or Esc) to close it:

- Disconnect
- Window: full screen, resize window to session, *When the window is
  resized*, remote DPI compensation, *Traffic in title bar*
- *Picture quality*: automatic (recommended), encoding, color depth,
  JPEG (Tight only), zlib level (Tight and ZRLE)
- *Input*: view only, remote cursor shape, local input method (IME),
  *Accept clipboard from remote* and *Send clipboard to remote*
  (`AcceptClipboard`, `SendClipboard`: each direction on its own)
- *X509 certificates*: CA and revocation list files (see below)
- *Keyboard* (menu key F7-F12, and which special keys go to the remote
  computer, see below), Send F8, Send Ctrl-Alt-Del
- Refresh screen
- Save settings, Save settings as...
- *Rust decoders*: ticked when the native decoders are loaded (untick to
  compare with the Python code); otherwise it says why they are not
- Connection info, About

### Special keys

While the viewer window has focus (Windows), every key goes to the remote
computer, including Alt+Esc, Ctrl+Esc, Alt+F4, Alt+Space and the
input-language hotkeys (Alt+Shift, Ctrl+Shift), so you switch the
*remote* input method. Exceptions:

| Keys | Default | F8 > Keyboard / parameter |
|------|---------|---------------------------|
| Print Screen | local screenshot | *Send Print Screen to remote*, `PassPrintScreen=1` |
| Alt+Tab | switch local windows | *Send Alt+Tab to remote*, `PassAltTab=1` |
| Windows key, Win+anything | local only | *Send Windows key to remote*, `PassWinKey=1` |
| Ctrl+Alt+Enter | toggle full screen | *Send Ctrl+Alt+Enter to remote*, `PassCtrlAltEnter=1` |
| Ctrl+Alt+Del, Win+L | always local (Windows doesn't let programs catch them) | F8 > *Send Ctrl-Alt-Del* |
| The menu key (F8) | opens the menu | *Menu key*: F7-F12, `MenuKey=F9`; F8 > *Send F8* |

*Send system keys to remote* (`SystemKeys=0` to turn it off) switches the
whole keyboard grab off; then Windows acts on its shortcuts as in any
program. *Save settings* keeps all of these.

If keys go missing (say Ctrl+V reaches the remote computer as Ctrl
only), tick F8 > Keyboard > *Log key events* (`LogKeys=1`) and
reproduce it. Every key then gets lines in the log
(`%APPDATA%\SagatVNC\sagatvnc.log`): `hook` (what the keyboard hook got
from Windows and what it did with it), `queue` (the message reaching
the viewer), `tk` (the key event) and `-> sent` (what went to the
server). The first layer without the key is where it was lost. If the
log shows it sent, look at the server: TigerVNC's winvnc logs
`Key pressed` lines with `Log=*:file:100`.

The title bar shows the live traffic and what the session uses, e.g.
`UP: 0.4 KiB/s DL: 2.1 MiB/s   2560x1600 Tight+JPEG 24bit TLSVnc TLSv1.2`
(remote size, the encodings of the last update, color depth, the security
type in use and, when the connection is encrypted, the TLS version). Turn
it off with *Traffic in title bar* or `-TitleStats=0`. *Connection info*
also names the TLS cipher and whether the server's certificate was
checked.

### Connecting

If connecting takes more than a moment, a small window shows how far it
got (waiting for the server, negotiating security, starting the session)
with a *Cancel* button; it steps aside while the password or certificate
question is open. A server that accepts the connection but stops
answering is given up on after `ConnectTimeout` seconds (default 10;
`0` waits forever, as TigerVNC does), with a message saying what it was
waiting for. Once the session runs there is no timeout, so an idle
desktop stays connected.

### Security type

The server lists the security types it accepts; the viewer picks one of
those that `SecurityTypes` allows. It takes the **safest**: X509 (encrypted,
server verified) before TLS (encrypted only) before VncAuth/Plain/None
(not encrypted), the stronger login first within each. TigerVNC instead
takes the server's first, so a server listing None first gets an
unencrypted session.

When the server offers more than one, the login dialog has a *Security*
list with the one in use selected. Pick another and OK becomes
*Reconnect*: the viewer reconnects with it, reusing what you typed.
`PreferredSecurity` remembers the pick (*Save settings* keeps it). The
security type can only change by reconnecting; RFB negotiates it once per
connection.

*Save settings* writes what you changed in the menu back to the
`.tigervnc` file the session was opened from (or asks where, if there was
none). *Save settings as...* writes a new file. Parameters SagatVNC does
not know (TigerVNC-only ones) and comments in an existing file are kept.
Passwords are never saved. While something differs from what was last
loaded or saved, the menu shows *▶ Save settings (unsaved changes)* in
orange.

### Which encoding?

Leave *Auto select* on unless you have a reason: like TigerVNC, it uses
Tight and picks color depth and JPEG quality from the measured bandwidth.
Picking an encoding, color level or quality in the menu turns it off.

| Encoding | Compression | Good for | Costs |
|----------|-------------|----------|-------|
| Tight    | zlib + palette/gradient filters, JPEG for photo-like areas | almost everything, especially slow links and video/photos | JPEG is lossy (turn it off under *JPEG*); most server CPU |
| ZRLE     | zlib + run-length on 64x64 tiles, lossless | text and UI on medium links, exact colors | no JPEG, so photos and video are big |
| Hextile  | 16x16 tiles of rectangles, no zlib | fast LAN with an old or weak server | 5-50x the data of Tight/ZRLE |
| Raw      | none | the same machine or a 1 Gbit+ LAN | a 2560x1600 frame is 16 MB |

*Compression level* (zlib, 0 fastest to 9 smallest) applies to Tight and
ZRLE and trades server CPU for bandwidth; *Server default* leaves it to
the server. CopyRect (moved windows, scrolling) is always used.

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
extended mouse buttons, touch/gestures, keyboard grab and the options
dialog. JPEG needs Pillow (bundled in
the exe).  (Scaling to the window is an addition that
TigerVNC itself does not have.)

### X509 certificates

X509None/X509Vnc/X509Plain need no files on the viewer side. As in
TigerVNC, the server's certificate is checked against the system's
trusted CAs plus, if they exist, `x509_ca.pem` (extra CAs) and
`x509_crl.pem` (revocation list) in the SagatVNC folder
(`%APPDATA%\SagatVNC` on Windows, `~/.config/sagatvnc` elsewhere), or
the files picked in the F8 menu under *X509 certificates* (also
`X509CA` / `X509CRL` on the command line or in a `.tigervnc` file). The
menu shows which files are in use, refuses files that aren't PEM
certificates or revocation lists, can go back to the defaults and opens
the SagatVNC folder; picks apply from the next connection, and *Save
settings* keeps them.

A certificate that checks out is used without a question. One that
doesn't (unknown or self-signed issuer, wrong host name, expired or not
yet valid, weak algorithm) brings up TigerVNC's question with the
certificate's details and SHA-256 fingerprint; *Add exception* remembers
it in `x509_known_hosts` in the same folder, and later connections take
it silently. A revoked certificate is refused outright. If a remembered
server shows a different certificate, it is accepted silently only if it
checks out; otherwise you are warned that it changed.

Python's TLS can't check a certificate and then go on with the same
handshake, so asking takes a second connection (servers don't mind; the
log says "connecting again").

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
license texts to `dist\licenses\`.
