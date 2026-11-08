# Troubleshooting

[README](../README.md) · [Setting guide](../setting_guide/README.md)

- [Scaling and high-DPI displays](#scaling-and-high-dpi-displays)
- [Resizing the window: remote resolution or scaling?](#resizing-the-window-remote-resolution-or-scaling)
- [Pointer drifting off on Windows servers](#pointer-drifting-off-on-windows-servers)
- [Ctrl+V reaches the server as Ctrl](#ctrlv-reaches-the-server-as-ctrl)
- [Disconnected at the Windows login / lock screen](#disconnected-at-the-windows-login--lock-screen)

## Scaling and high-DPI displays

The viewer is per-monitor DPI aware, so Windows doesn't stretch it into a
blurry bitmap at 125-200%. The remote desktop is scaled to the window
instead (`Scaling=Auto`, the default), keeping its aspect ratio:

- shrinking: Lanczos-3 (sharp, good for text)
- enlarging: bicubic (Catmull-Rom, less ringing)
- exactly 1:1: no resampling

Only changed areas are rescaled, without seams. `Scaling=Off` gives 1:1
with scrollbars. Scaling needs Pillow (`pip install .[scaling]`; the exe
has it). Without it the view is 1:1.

## Resizing the window: remote resolution or scaling?

F8 > *When the window is resized*:

| Choice | Parameters | Result |
|--------|------------|--------|
| *Change the remote resolution* | `RemoteResize=1` (TigerVNC's default) | The server switches to the window's size. Only Xvnc does this: Windows servers (winvnc) refuse, so the picture is scaled instead |
| *Scale the picture* | `RemoteResize=0` | Remote resolution stays, the picture is scaled |
| *Keep 1:1, show scrollbars* | `RemoteResize=0 Scaling=Off` | No scaling |

## Pointer drifting off on Windows servers

A server that isn't DPI aware (e.g. winvnc on a 150% desktop) can draw
its cursor in the wrong place: the arrow drifts to the top left and hits
an invisible wall, while clicks land fine. The viewer shows the server's
cursor shape as the local cursor, so this needs no setting.

If the **clicks** land in the wrong place:

- **On the server:** the server .exe's *Properties* > *Compatibility* >
  *Change high DPI settings* > tick *Override high DPI scaling behavior*,
  pick *Application*, restart the server.
- **In the viewer:** F8 > *Remote DPI compensation* > *Calibrate...*.
  Move the mouse until the remote cursor touches the remote screen's
  bottom-right corner, then click. Calibrate rather than guess: the factor
  often differs from the Windows scale, and too large a value shrinks
  the area the cursor can reach. Keep it with `RemoteDpiScale=N`
  (`DPI=N` in a `.tigervnc` file).
- **Or switch servers:** UltraVNC is DPI aware and needs neither.

## Ctrl+V reaches the server as Ctrl

**x0vncserver: start it with `-RawKeyboard=1`.** Without it, x0vncserver
looks for a key that gives the received character under the remote's
current Shift / Caps Lock state. When none does (Caps Lock differs on the
two sides, say), it puts the letter on a spare key code. These leftovers
stay in the keymap and can shadow the real key, so V arrives as, say, key
code 8. Typing still works, but programs that match Ctrl+letter by key
position (Electron apps) ignore Ctrl+V. With `-RawKeyboard=1` the server
uses the scan code, which this viewer always sends; keys then follow the
server's keyboard layout.

**Finding where a key gets lost:** F8 > *Troubleshooting* > *Log: detailed*
(`LogLevel=2`), then reproduce. Each key gets one line per layer in
`log\sagatvnc.log` next to the exe:

| Line | What it shows |
|------|---------------|
| `hook` | what the keyboard hook got from Windows and did with it |
| `queue` | the message reaching the viewer |
| `tk` | the key event |
| `-> sent` | what went to the server |

The first layer without the key is where it was lost. `clip` lines log
clipboard traffic (length and checksum, never the text), because a paste
that does nothing looks just like a lost V. If the key was sent, check the
server's log: winvnc `Log=*:file:100`, x0vncserver and Xvnc
`-Log *:stderr:100`.

## Disconnected at the Windows login / lock screen

This comes from TigerVNC's Windows server (winvnc), not the viewer.

When someone presses a key or clicks on the lock screen, Windows switches
to the secure Winlogon desktop for the password box. winvnc can't follow
it and drops every client. Until someone logs in locally, new connections
are cut off right after authentication. The viewer can't work around it.

**Fix: use [UltraVNC](https://uvnc.com/) server instead of winvnc.**
Install it as a system service; it follows the login screen and UAC
prompts. Two things to know:

- **Security: VncAuth only.** It's the only UltraVNC login that works
  with this viewer (turn off *Require MS Logon*). VncAuth isn't
  encrypted, so outside a trusted LAN use a VPN or an SSH tunnel.
- **No DPI calibration needed.** UltraVNC is DPI aware: leave *Remote
  DPI compensation* off.
