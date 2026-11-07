# SagatVNC Viewer tests
# Copyright (C) 2026 SagatVNC Viewer contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""X509 server certificates, against real TLS with the certificates in
tests/data/x509 (CSecurityTLS::checkSession, CConn::verifyCertificate).

Python's ssl can't verify and then go on with the same handshake, so a
check that needs the user takes a second connection
(security.CertificateRetry); these tests run each connection in turn."""

import os
import shutil
import socket
import ssl
import tempfile
import threading
import unittest

from sagatvnc.rfb import encodings as enc
from sagatvnc.rfb.connection import Connection, ConnectionOptions
from sagatvnc.rfb.security import (CertificateRetry, SecurityError,
                                   parse_security_types, read_known_hosts)
from sagatvnc.viewer.dialogs import certificate_question

from tests import fakeserver as fs
from tests.test_rfb import RecordingHandler

DATA = os.path.join(os.path.dirname(__file__), "data", "x509")
CA = os.path.join(DATA, "ca.pem")
CRL = os.path.join(DATA, "crl.pem")


def server_context(name):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(os.path.join(DATA, name + ".pem"),
                        os.path.join(DATA, name + ".key"))
    return ctx


class Asker(RecordingHandler):
    """Answers the certificate question with ``accept``, recording it."""

    def __init__(self, accept):
        super().__init__(password="pw")
        self.accept = accept
        self.asked = []

    def get_credentials(self, need_username):
        return "user", "pw"

    def verify_certificate(self, info):
        self.asked.append(info)
        return self.accept


class X509Test(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.known = os.path.join(self.dir, "x509_known_hosts")
        self.options = ConnectionOptions(
            security_types=parse_security_types("X509Vnc,X509Plain"),
            known_hosts=self.known, handshake_timeout=5)

    def connect(self, cert, accept=False, sub_type=enc.SEC_X509_VNC,
                host="127.0.0.1"):
        """One connection to a server presenting ``cert``.  Returns the
        exception that ended it (None: the session started), the
        connection and the handler."""
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        handler = Asker(accept)
        conn = Connection(a, host, handler, self.options)
        server = fs.FakeServer(b, width=8, height=8, password="pw")
        done = threading.Event()        # either side finished

        def serve():
            try:
                server.handshake(offer=[enc.SEC_VENCRYPT],
                                 vencrypt=[sub_type],
                                 tls=server_context(cert))
                server.wait_for("fbur")
            except Exception:
                pass                     # the client gave up on the TLS
            done.set()
        threading.Thread(target=serve, daemon=True).start()
        result = {}

        def run():
            try:
                conn.run()
            except Exception as e:
                result["error"] = e
            done.set()
        t = threading.Thread(target=run, daemon=True)
        t.start()
        done.wait(10)
        conn.close()
        server.sock.close()
        b.close()
        t.join(10)
        self.server = server
        return result.get("error"), conn, handler

    def retry(self, error):
        """What the viewer does with a CertificateRetry."""
        self.assertIsInstance(error, CertificateRetry)
        self.options.certificate_mode = error.mode
        self.options.certificate_problem = error.problem

    # -- verified by a CA ----------------------------------------------------
    def test_signed_by_the_configured_ca(self):
        self.options.x509_ca = CA
        error, conn, handler = self.connect("server")
        self.assertIsNone(error)
        self.assertEqual(handler.asked, [])
        self.assertEqual(conn.security.certificate, "ca")
        self.assertEqual(conn.security.type_name, "X509Vnc")
        self.assertTrue(conn.security.secure)
        self.assertEqual(read_known_hosts(self.known), {})

    def test_hostname_as_a_name(self):
        self.options.x509_ca = CA
        error, conn, _ = self.connect("server", host="localhost")
        self.assertIsNone(error)

    def test_x509plain_sends_the_password_inside_tls(self):
        self.options.x509_ca = CA
        error, _, _ = self.connect("server", sub_type=enc.SEC_X509_PLAIN)
        self.assertIsNone(error)
        self.assertEqual(self.server.plain, ("user", "pw"))

    # -- problems the user may override --------------------------------------
    def check_problem(self, cert, kind, host="127.0.0.1"):
        """First connection: verification fails -> retry asking; second:
        the question, accepted; third: the stored exception, no question."""
        error, _, handler = self.connect(cert, host=host)
        self.retry(error)
        self.assertEqual(error.mode, "ask")
        self.assertEqual(error.problem[0], kind)
        self.assertEqual(handler.asked, [])
        error, conn, handler = self.connect(cert, accept=True, host=host)
        self.assertIsNone(error)
        info, = handler.asked
        self.assertEqual(info["problem"][0], kind)
        self.assertFalse(info["changed"])
        self.assertEqual(conn.security.certificate, "exception")
        self.assertIn(host, read_known_hosts(self.known))
        # The viewer resets the mode once the session has started
        self.options.certificate_mode = "auto"
        self.options.certificate_problem = None
        error, conn, handler = self.connect(cert, host=host)
        self.assertIsNone(error)
        self.assertEqual(handler.asked, [])
        return info

    def test_unknown_issuer(self):
        info = self.check_problem("server", "authority")      # no X509CA
        self.assertEqual(info["issuer"], "CN=SagatVNC Test CA")
        self.assertEqual(info["subject"], "CN=server")
        self.assertEqual(info["names"], "localhost, 127.0.0.1")
        self.assertEqual(len(info["fingerprint"]), 64)

    def test_self_signed(self):
        self.options.x509_ca = CA
        self.check_problem("selfsigned", "authority")

    def test_hostname_mismatch(self):
        self.options.x509_ca = CA
        info = self.check_problem("otherhost", "hostname")
        title, text = certificate_question(info)
        self.assertEqual(title, "Certificate hostname mismatch")
        self.assertIn('hostname "127.0.0.1" does not match', text)

    def test_expired(self):
        self.options.x509_ca = CA
        self.check_problem("expired", "expired")

    def test_declined(self):
        error, _, _ = self.connect("selfsigned")
        self.retry(error)
        error, _, handler = self.connect("selfsigned", accept=False)
        self.assertEqual(len(handler.asked), 1)
        self.assertIsInstance(error, SecurityError)
        self.assertNotIsInstance(error, CertificateRetry)
        self.assertEqual(read_known_hosts(self.known), {})

    # -- not overridable -----------------------------------------------------
    def test_revoked(self):
        """GNUTLS_CERT_REVOKED is not in allowed_errors: no question."""
        self.options.x509_ca = CA
        self.options.x509_crl = CRL
        error, _, handler = self.connect("revoked")
        self.assertIsInstance(error, SecurityError)
        self.assertNotIsInstance(error, CertificateRetry)
        self.assertIn("Invalid server certificate", str(error))
        self.assertIn("revoked", str(error))
        self.assertEqual(handler.asked, [])
        # The CRL doesn't get in the way of a good certificate
        error, _, _ = self.connect("server")
        self.assertIsNone(error)

    # -- a known host shows another certificate ------------------------------
    def accept_once(self, cert):
        error, _, _ = self.connect(cert)
        self.retry(error)
        error, _, _ = self.connect(cert, accept=True)
        self.assertIsNone(error)
        self.options.certificate_mode = "auto"
        self.options.certificate_problem = None

    def test_changed_to_a_verified_certificate(self):
        """Upstream takes a certificate that verifies without asking, even
        for a host it knows with another one."""
        self.accept_once("selfsigned")
        self.options.x509_ca = CA
        error, _, handler = self.connect("server")
        self.retry(error)
        self.assertEqual(error.mode, "verify")
        error, conn, handler = self.connect("server")
        self.assertIsNone(error)
        self.assertEqual(handler.asked, [])
        self.assertEqual(conn.security.certificate, "ca")

    def test_changed_to_an_unverified_certificate(self):
        self.accept_once("selfsigned")
        old = read_known_hosts(self.known)["127.0.0.1"]
        error, _, _ = self.connect("server")       # test CA not trusted
        self.retry(error)
        error, _, _ = self.connect("server")
        self.retry(error)
        self.assertEqual(error.mode, "ask")
        error, _, handler = self.connect("server", accept=True)
        self.assertIsNone(error)
        info, = handler.asked
        self.assertTrue(info["changed"])
        title, text = certificate_question(info)
        self.assertEqual(title, "Unexpected server certificate")
        self.assertIn("previously known with a different certificate, and "
                      "the new certificate has been signed by an unknown "
                      "authority", text)
        self.assertNotEqual(read_known_hosts(self.known)["127.0.0.1"], old)


class CertificateFilesMenuTest(unittest.TestCase):
    """F8 > X509 certificates (stands in for the options dialog's
    Security tab)."""

    def setUp(self):
        from unittest import mock
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        patcher = mock.patch("sagatvnc.viewer.app.config_dir",
                             lambda: self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def label(self, opt, value):
        from types import SimpleNamespace
        from sagatvnc.viewer.app import ViewerApp
        app = SimpleNamespace(opts=SimpleNamespace(**{opt: value}))
        for o, default, what in ViewerApp.CERT_FILES:
            if o == opt:
                return ViewerApp.cert_file_label(app, opt, default, what)

    def test_labels(self):
        self.assertEqual(self.label("x509ca", ""),
                         "CA certificates: system CAs only...")
        self.assertEqual(self.label("x509crl", ""),
                         "Revocation list (CRL): none...")
        self.assertEqual(self.label("x509ca", CA), "CA certificates: ca.pem...")
        missing = os.path.join(self.dir, "x.pem")
        self.assertEqual(self.label("x509ca", missing),
                         "CA certificates: x.pem (missing)...")
        shutil.copy(CA, os.path.join(self.dir, "x509_ca.pem"))
        self.assertEqual(self.label("x509ca", ""),
                         "CA certificates: x509_ca.pem (in the folder)...")

    def test_only_pem_certificates_or_crls(self):
        from sagatvnc.viewer.app import check_pem_file
        self.assertIsNone(check_pem_file(CA))
        self.assertIsNone(check_pem_file(CRL))
        self.assertIsNotNone(check_pem_file(os.path.join(DATA,
                                                         "server.key")))
        self.assertIsNotNone(check_pem_file(os.path.join(self.dir, "no")))


class QuestionTextTest(unittest.TestCase):
    def test_new_host(self):
        title, text = certificate_question({
            "host": "h", "fingerprint": "ab" * 32, "changed": False,
            "problem": ("expired", "certificate has expired"),
            "subject": "CN=h", "valid": "a - b"})
        self.assertEqual(title, "Expired certificate")
        self.assertTrue(text.startswith("This certificate has expired:"))
        self.assertIn("Subject: CN=h\nValid: a - b\nSHA-256: " +
                      " ".join(["ab" * 8] * 4), text)
        self.assertTrue(text.endswith("Do you want to make an exception for "
                                      "this server?"))


if __name__ == "__main__":
    unittest.main()
