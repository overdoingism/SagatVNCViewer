# SagatVNC Viewer - a Python port of the TigerVNC viewer
# Copyright (C) 2026 SagatVNC Viewer contributors
# Based on TigerVNC common/rfb/CSecurity{VncAuth,Plain,VeNCrypt,TLS}.cxx,
# Copyright (C) 2002-2005 RealVNC Ltd., Copyright (C) 2005 Martin Koegler,
# Copyright (C) 2006 OCCAM Financial Technology, Copyright (C) 2010
# TigerVNC Team, and TigerVNC contributors.
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
"""Client side security types: None, VncAuth, Plain and VeNCrypt (TLS/X509)."""

import hashlib
import os
import socket
import ssl
import struct

from . import encodings as enc
from .des import vnc_auth_response


class AuthFailure(Exception):
    pass


class SecurityError(Exception):
    pass


class CertificateRetry(SecurityError):
    """The server certificate needs another look, on a new connection.

    Not in upstream: GnuTLS verifies a certificate and still lets
    CSecurityTLS::checkSession() go on with the same handshake, so
    CConn::verifyCertificate() can ask the user.  Python's ssl either
    verifies (and aborts the handshake on any problem) or doesn't verify
    at all.  So a check that needs both runs as two connections: the
    caller stores ``mode`` and ``problem`` in ConnectionOptions
    (certificate_mode, certificate_problem) and connects again."""

    def __init__(self, msg, mode, problem=None):
        super().__init__(msg)
        self.mode = mode
        self.problem = problem


# OpenSSL X509_V_ERR_* codes upstream lets the user override
# (CConn::verifyCertificate's allowed_errors, GnuTLS names in comments);
# anything else, e.g. a revoked certificate, ends the connection
CERT_PROBLEMS = {
    2: "authority", 18: "authority", 19: "authority",   # SIGNER_NOT_FOUND
    20: "authority", 21: "authority", 24: "authority",  # SIGNER_NOT_CA
    9: "not_yet_valid",                                 # NOT_ACTIVATED
    10: "expired",                                      # EXPIRED
    66: "algorithm", 67: "algorithm", 68: "algorithm",  # INSECURE_ALGORITHM
    62: "hostname", 64: "hostname",                     # UNEXPECTED_OWNER
    # UNABLE_TO_GET_CRL: GnuTLS only applies a CRL to the CA that issued
    # it; OpenSSL wants one for every CA once CRL checking is on
    3: "crl",
}


def read_known_hosts(path):
    """``host sha256-fingerprint`` lines (SagatVNC's own format; upstream
    keeps GnuTLS's x509_known_hosts with the public key)."""
    known = {}
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) == 2:
                    known[parts[0]] = parts[1]
    return known


def certificate_details(der):
    """Subject, issuer and validity of a DER certificate for the user, or
    {} if this Python can't decode it (the decoder is ssl's own, which
    only reads files)."""
    import tempfile
    try:
        fd, path = tempfile.mkstemp(suffix=".pem")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(ssl.DER_cert_to_PEM_cert(der))
            d = ssl._ssl._test_decode_cert(path)
        finally:
            os.remove(path)
    except Exception:
        return {}

    short = {"commonName": "CN", "organizationName": "O",
             "organizationalUnitName": "OU", "countryName": "C",
             "localityName": "L", "stateOrProvinceName": "ST"}

    def name(rdns):
        return ", ".join("%s=%s" % (short.get(k, k), v)
                         for rdn in rdns for k, v in rdn)
    return {"subject": name(d.get("subject", ())),
            "issuer": name(d.get("issuer", ())),
            "valid": "%s - %s" % (d.get("notBefore", "?"),
                                  d.get("notAfter", "?")),
            "names": ", ".join(v for _, v in d.get("subjectAltName", ()))}


def parse_security_types(text, log=lambda msg: None):
    """Parse a TigerVNC SecurityTypes list.

    Types TigerVNC supports but this port lacks (RA2, DH, ...) are logged
    and skipped, so .tigervnc files written by TigerVNC still work.  Only
    a list with no usable type at all, or a name nobody knows, is an error.
    """
    types = []
    skipped = []
    lower = {k.lower(): v for k, v in enc.SEC_NAMES.items()}
    unsupported = {k.lower(): k for k in enc.SEC_UNSUPPORTED_NAMES}
    for name in text.split(","):
        name = name.strip()
        if not name:
            continue
        key = name.lower()
        if key in lower:
            if lower[key] not in types:
                types.append(lower[key])
        elif key in unsupported:
            skipped.append(unsupported[key])
        else:
            raise ValueError("Unknown security type %r" % name)
    if skipped:
        log("Security types not supported by SagatVNC, skipped: %s" %
            ", ".join(skipped))
    if not types:
        raise ValueError("None of the requested security types (%s) are "
                         "supported by SagatVNC" % text)
    return types


def is_vencrypt_subtype(t):
    return t >= 256


# Safest first: encryption with a verified server, then encryption only,
# then none; within each, the stronger authentication first.  Plain
# without TLS sends the password in clear text, so it ranks below VncAuth.
SAFETY_ORDER = (enc.SEC_X509_PLAIN, enc.SEC_X509_VNC, enc.SEC_X509_NONE,
                enc.SEC_TLS_PLAIN, enc.SEC_TLS_VNC, enc.SEC_TLS_NONE,
                enc.SEC_VNC_AUTH, enc.SEC_PLAIN, enc.SEC_NONE)


def safety_rank(t):
    """0 = safest; unknown types last."""
    try:
        return SAFETY_ORDER.index(t)
    except ValueError:
        return len(SAFETY_ORDER)


def describe(t):
    """(encryption, authentication) of a security type:
    encryption "x509" (verified server), "tls" (anonymous) or None;
    authentication "none", "vnc" or "plain"."""
    encryption = ("x509" if t in (enc.SEC_X509_NONE, enc.SEC_X509_VNC,
                                  enc.SEC_X509_PLAIN) else
                  "tls" if t in (enc.SEC_TLS_NONE, enc.SEC_TLS_VNC,
                                 enc.SEC_TLS_PLAIN) else None)
    auth = ("vnc" if t in (enc.SEC_VNC_AUTH, enc.SEC_TLS_VNC,
                           enc.SEC_X509_VNC) else
            "plain" if t in (enc.SEC_PLAIN, enc.SEC_TLS_PLAIN,
                             enc.SEC_X509_PLAIN) else "none")
    return encryption, auth


def choose_security(conn, candidates):
    """Pick one of ``candidates`` (offered by the server and enabled here).

    Not upstream: TigerVNC takes the first in the server's order, so a
    server listing None before TLS gets an unencrypted session.  Here the
    safest comes first; with more than one left the user is asked
    (``conn.choose_security``), with the safest preselected.
    """
    ranked = sorted(dict.fromkeys(candidates), key=safety_rank)
    pref = getattr(conn.options, "preferred_security", None)
    if pref in ranked:
        return pref
    if len(ranked) == 1:
        return ranked[0]
    return conn.choose_security(ranked)


class SecurityHandler:
    """Runs the security phase on behalf of a Connection.

    ``conn`` provides: ``stream``, ``host``, ``options`` (``security_types``
    list, ``x509_ca`` path, ``known_hosts`` path) and the callbacks
    ``get_credentials(need_username) -> (user, password)`` and
    ``verify_certificate(info) -> bool``.
    """

    def __init__(self, conn):
        self.conn = conn
        self.secure = False
        # What this session ended up using, for the title bar and the
        # connection info: the security type that did the authentication
        # (a VeNCrypt sub-type such as "TLSVnc" when there was one), and
        # the TLS version and cipher if the stream is encrypted
        self.type_name = None
        self.type_num = None
        self.tls_version = None
        self.tls_cipher = None
        # How the server certificate was accepted: "ca" (verified against
        # the trusted CAs), "exception" (accepted by the user before or
        # now), None (anonymous TLS or no TLS)
        self.certificate = None

    # -- entry point --------------------------------------------------------
    def run(self, sec_type):
        self.type_name = enc.sec_name(sec_type)
        self.type_num = sec_type
        if sec_type == enc.SEC_NONE:
            return
        if sec_type == enc.SEC_VNC_AUTH:
            self.vnc_auth()
        elif sec_type == enc.SEC_VENCRYPT:
            self.vencrypt()
        elif sec_type == enc.SEC_PLAIN:
            self.plain()
        else:
            raise SecurityError("Security type %s not supported" %
                                enc.sec_name(sec_type))

    # -- simple types -------------------------------------------------------
    def vnc_auth(self):
        challenge = self.conn.stream.read(16)
        self.conn.log("VNC authentication challenge received")
        _, password = self.conn.get_credentials(False)
        if len(password) > 8:
            self.conn.log("Note: VNC passwords are limited to 8 characters; "
                          "only the first 8 are used")
        self.conn.stream.write(vnc_auth_response(password, challenge))
        self.conn.log("VNC authentication response sent")

    def plain(self):
        user, password = self.conn.get_credentials(True)
        u = user.encode("utf-8")
        p = password.encode("utf-8")
        self.conn.stream.write(struct.pack(">II", len(u), len(p)) + u + p)

    # -- VeNCrypt -----------------------------------------------------------
    def vencrypt(self):
        s = self.conn.stream
        major, minor = s.u8(), s.u8()
        if (major << 8 | minor) < 0x0002:
            s.write(b"\0\0")
            raise SecurityError("The server reported an unsupported "
                                "VeNCrypt version")
        s.write(b"\0\2")
        if s.u8():
            raise SecurityError("The server reported that it could not "
                                "support the client's VeNCrypt version")
        count = s.u8()
        if not count:
            raise SecurityError("The server reported no VeNCrypt sub-types")
        offered = [s.u32() for _ in range(count)]
        # Security::GetEnabledExtSecTypes(): every enabled type except
        # VeNCrypt itself, so None/VncAuth work as sub-types too (Vino);
        # the server's order is honoured (CSecurityVeNCrypt::processMsg)
        enabled = [t for t in self.conn.options.security_types
                   if t != enc.SEC_VENCRYPT]
        candidates = [t for t in offered if t in enabled]
        if not candidates:
            raise SecurityError("No valid VeNCrypt sub-type (server offers %s)"
                                % ", ".join(enc.sec_name(t) for t in offered))
        self.conn.log("Server offers VeNCrypt sub-types %s" %
                      ", ".join(enc.sec_name(t) for t in offered))
        self.conn.add_security_choices(candidates)
        chosen = choose_security(self.conn, candidates)
        self.conn.log("Choosing security type %s (%d)" %
                      (enc.sec_name(chosen), chosen))
        s.write(struct.pack(">I", chosen))
        self.type_name = enc.sec_name(chosen)
        self.type_num = chosen

        if chosen == enc.SEC_NONE:
            return
        if chosen == enc.SEC_VNC_AUTH:
            self.vnc_auth()
            return
        if chosen == enc.SEC_PLAIN:
            self.plain()
            return
        anon = chosen in (enc.SEC_TLS_NONE, enc.SEC_TLS_VNC, enc.SEC_TLS_PLAIN)
        if s.u8() == 0:
            raise SecurityError("Server failed to initialize TLS session")
        self.conn._set_stage("the TLS handshake", "Starting TLS...")
        self.start_tls(anon)
        if chosen in (enc.SEC_TLS_VNC, enc.SEC_X509_VNC):
            self.vnc_auth()
        elif chosen in (enc.SEC_TLS_PLAIN, enc.SEC_X509_PLAIN):
            self.plain()

    def start_tls(self, anon):
        raw = self.conn.stream.sock
        if anon:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            # Anonymous Diffie-Hellman only exists up to TLS 1.2
            ctx.maximum_version = ssl.TLSVersion.TLSv1_2
            try:
                ctx.set_ciphers("aNULL:@SECLEVEL=0")
            except ssl.SSLError:
                raise SecurityError("This Python/OpenSSL build has no "
                                    "anonymous TLS ciphers")
            sock = ctx.wrap_socket(raw)
        else:
            sock = self._x509_socket(raw)
        self.conn.stream.replace_socket(sock)
        cipher = sock.cipher()
        self.tls_version = sock.version()
        self.tls_cipher = cipher[0] if cipher else None
        self.conn.log("TLS established: %s, %s" %
                      (sock.version(), cipher[0] if cipher else "?"))
        self.secure = not anon

    def _verifying_context(self):
        """CSecurityTLS::setParam(): the system's trusted CAs, plus X509CA
        and X509CRL; a file that fails to load is logged, not fatal."""
        opts = self.conn.options
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.check_hostname = True
        try:
            ctx.load_default_certs()
        except (OSError, ssl.SSLError):
            self.conn.log("Failed to load the system certificate trust store")
        for path, what in ((opts.x509_ca, "certificate authority"),
                           (opts.x509_crl, "certificate revocation list")):
            if not path or not os.path.exists(path):
                continue
            try:
                ctx.load_verify_locations(cafile=path)
            except (OSError, ssl.SSLError) as e:
                self.conn.log("Failed to load the user specified %s "
                              "(%s): %s" % (what, path, e))
                continue
            if path == opts.x509_crl:
                ctx.verify_flags |= ssl.VERIFY_CRL_CHECK_LEAF
        return ctx

    def _x509_socket(self, raw):
        """CSecurityTLS::checkSession() + CConn::verifyCertificate().

        ``certificate_mode`` "auto": a host with a stored exception is
        checked against it; any other host is verified.  "verify": verify
        (an excepted host showed another certificate).  "ask": a
        verification found ``certificate_problem``; ask the user, as
        upstream does, and store the exception."""
        opts = self.conn.options
        host = self.conn.host
        known = read_known_hosts(opts.known_hosts)
        mode = opts.certificate_mode
        if mode == "verify" or (mode == "auto" and host not in known):
            ctx = self._verifying_context()
            try:
                sock = ctx.wrap_socket(raw, server_hostname=host)
            except ssl.SSLCertVerificationError as e:
                msg = e.verify_message or str(e)
                self.conn.log("Server certificate problem: %s" % msg)
                kind = CERT_PROBLEMS.get(e.verify_code)
                if kind is None:
                    raise SecurityError("Invalid server certificate: %s" %
                                        msg)
                raise CertificateRetry("Server certificate: %s" % msg,
                                       "ask", (kind, msg))
            self.conn.log("Server certificate verified")
            self.certificate = "ca"
            return sock

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        sock = ctx.wrap_socket(raw)
        # The stream owns it from now on, so closing the connection closes
        # it even when a check below gives up
        self.conn.stream.replace_socket(sock)
        der = sock.getpeercert(binary_form=True)
        if not der:
            raise SecurityError("The server did not present a certificate")
        fp = hashlib.sha256(der).hexdigest()
        if known.get(host) == fp:
            self.conn.log("Server has an existing security exception")
            self.certificate = "exception"
            return sock
        if mode == "auto":
            # Another certificate than the one excepted: if it verifies,
            # upstream takes it without asking
            self.conn.log("Server host certificate has changed")
            raise CertificateRetry("The server certificate has changed",
                                   "verify")
        info = {"host": host, "fingerprint": fp, "changed": host in known,
                "problem": opts.certificate_problem}
        info.update(certificate_details(der))
        if not self.conn.verify_certificate(info):
            raise SecurityError("The server certificate was not accepted")
        self.certificate = "exception"
        path = opts.known_hosts
        if path:
            known[host] = fp
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                for h, v in known.items():
                    f.write("%s %s\n" % (h, v))
        return sock


def connect_socket(host, port, timeout=None, register=None):
    """socket.create_connection(), except that ``register(sock)`` is told
    about each socket before it connects, so another thread can close it
    to cancel the attempt."""
    err = None
    for family, kind, proto, _, addr in socket.getaddrinfo(
            host, port, 0, socket.SOCK_STREAM):
        sock = socket.socket(family, kind, proto)
        if register:
            register(sock)
        try:
            sock.settimeout(timeout)
            sock.connect(addr)
        except OSError as e:
            err = e
            sock.close()
            continue
        sock.settimeout(None)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return sock
    if isinstance(err, socket.timeout):
        raise OSError("No answer from %s port %d within %d seconds" %
                      (host, port, timeout))
    raise err or OSError("Could not resolve %s" % host)
