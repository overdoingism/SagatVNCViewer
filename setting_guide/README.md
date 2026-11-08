# Setting guide

[README](../README.md) · [Troubleshooting](../troubleshooting/README.md)

- [Connecting](#connecting)
- [Security type](#security-type)
- [X509 certificates](#x509-certificates)
- [Which screen encoding?](#which-screen-encoding)
- [Special keys](#special-keys)

## Connecting

Enter `host:display` (`myhost:1` = port 5901) or `host::port`.

If connecting takes a moment, a small window shows how far it got
(waiting for the server, negotiating security, starting the session),
with *Cancel*. A server that stops answering is given up on after
`ConnectTimeout` seconds (default 10; `0` waits forever, as TigerVNC
does). A running session has no timeout, so an idle desktop stays
connected.

## Security type

The viewer takes the **safest** type that the server offers and
`SecurityTypes` allows:

1. X509 (encrypted, server verified)
2. TLS (encrypted only)
3. VncAuth / Plain / None (not encrypted)

Within each, the stronger login comes first. TigerVNC takes the server's
first choice instead, so a server listing None first gets an unencrypted
session there.

If the server offers several, the login dialog has a *Security* list. Pick
another and OK becomes *Reconnect* (RFB settles security once per
connection); what you typed is reused. `PreferredSecurity` remembers the
pick.

## X509 certificates

Nothing is needed on the viewer side. As in TigerVNC, the server's
certificate is checked against the system's CAs plus, if present, these
files in the SagatVNC folder (`%APPDATA%\SagatVNC` on Windows,
`~/.config/sagatvnc` elsewhere):

- `x509_ca.pem`: extra CAs
- `x509_crl.pem`: revocation list

Other files: F8 > *X509 certificates*, or `X509CA` / `X509CRL`. They apply
from the next connection.

| Certificate | Result |
|-------------|--------|
| Checks out | Used without a question |
| Unknown or self-signed issuer, wrong host name, expired, weak algorithm | TigerVNC's question with the details and SHA-256 fingerprint. *Add exception* stores it in `x509_known_hosts` |
| Revoked | Refused |
| Known server, different certificate | Silent if it checks out, otherwise a warning |

Asking costs a second connection, because Python's TLS can't pause a
handshake. The log then says "connecting again".

## Which screen encoding?

Leave *Auto select* on unless you have a reason. Like TigerVNC, it uses
Tight and picks colour depth and JPEG quality from the measured
bandwidth. Picking an encoding, colour level or quality turns it off.

| Encoding | Compression | Good for | Costs |
|----------|-------------|----------|-------|
| Tight    | zlib + filters, JPEG for photo-like areas | almost everything, especially slow links and video | JPEG is lossy (turn it off under *JPEG*); most server CPU |
| ZRLE     | zlib + run-length, lossless | text and UI on medium links | photos and video get big |
| Hextile  | tiles of rectangles, no zlib | fast LAN, weak server | 5-50x the data of Tight/ZRLE |
| Raw      | none | same machine, 1 Gbit+ LAN | 16 MB per 2560x1600 frame |

*Compression level* (zlib, 0 fastest to 9 smallest) applies to Tight and
ZRLE and trades server CPU for bandwidth. CopyRect (moved windows,
scrolling) is always used.

## Special keys

While the viewer has focus (Windows), every key goes to the remote
computer, including Alt+Esc, Ctrl+Esc, Alt+F4, Alt+Space and the
input-language hotkeys, so you switch the *remote* input method.
Exceptions:

| Keys | Default | To change (also in F8 > *Keyboard*) |
|------|---------|---------------------------|
| Print Screen | local screenshot | `PassPrintScreen=1` |
| Alt+Tab | switches local windows | `PassAltTab=1` |
| Windows key, Win+anything | local only | `PassWinKey=1` |
| Ctrl+Alt+Enter | toggles full screen | `PassCtrlAltEnter=1` |
| Ctrl+Alt+Del, Win+L | always local (Windows doesn't let programs catch them) | F8 > *Send Ctrl-Alt-Del* |
| Menu key (F8) | opens the menu | `MenuKey=F9` (F7-F12); F8 > *Send F8* |

`SystemKeys=0` (*Send system keys to remote*) turns the whole keyboard
grab off.

Keys going missing? See
[Troubleshooting](../troubleshooting/README.md#ctrlv-reaches-the-server-as-ctrl).
