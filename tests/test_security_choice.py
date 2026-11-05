# SagatVNC Viewer tests
# Copyright (C) 2026 SagatVNC Viewer contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Which security type the viewer picks when the server offers several
(SagatVNC: safest first, or the user's pick; upstream takes the server's
order)."""

import unittest

from sagatvnc.rfb import encodings as enc
from sagatvnc.rfb.security import SAFETY_ORDER, describe, safety_rank

from tests.test_upstream_fidelity import Base

NONE, VNC, VENCRYPT = enc.SEC_NONE, enc.SEC_VNC_AUTH, enc.SEC_VENCRYPT


class RankTest(unittest.TestCase):
    def test_order(self):
        self.assertEqual([enc.sec_name(t) for t in SAFETY_ORDER],
                         ["X509Plain", "X509Vnc", "X509None", "TLSPlain",
                          "TLSVnc", "TLSNone", "VncAuth", "Plain", "None"])
        self.assertLess(safety_rank(VNC), safety_rank(NONE))
        self.assertEqual(safety_rank(99), len(SAFETY_ORDER))

    def test_describe(self):
        self.assertEqual(describe(enc.SEC_X509_VNC), ("x509", "vnc"))
        self.assertEqual(describe(enc.SEC_TLS_PLAIN), ("tls", "plain"))
        self.assertEqual(describe(NONE), (None, "none"))


class ChoiceTest(Base):
    def connect(self, offer, vencrypt=(NONE, VNC), **opts):
        self.start(sec=opts.pop("sec", enc.DEFAULT_SECURITY),
                   password="pw", **opts)
        self.assertTrue(self.server.handshake(offer=offer,
                                              vencrypt=vencrypt))
        self.server.wait_for("fbur")
        return self.server.chosen

    def test_safest_not_the_servers_first(self):
        """Upstream would take None here (the server's first)."""
        self.assertEqual(self.connect([NONE, VNC]), [VNC])
        self.assertEqual(self.conn.security_choices, [VNC, NONE])
        self.assertEqual(self.conn.security.type_num, VNC)

    def test_the_users_pick_wins(self):
        self.assertEqual(self.connect([NONE, VNC], preferred_security=NONE),
                         [NONE])

    def test_vencrypt_is_taken_then_its_safest_sub_type(self):
        # TLS types are allowed, so VeNCrypt may hold something better
        self.assertEqual(self.connect([VNC, VENCRYPT]), [VENCRYPT, VNC])
        # Both stages' choices, merged, safest first
        self.assertEqual(self.conn.security_choices, [VNC, NONE])

    def test_pick_inside_vencrypt(self):
        self.assertEqual(self.connect([VNC, VENCRYPT],
                                      preferred_security=NONE),
                         [VENCRYPT, NONE])

    def test_no_sub_types_allowed_means_no_vencrypt(self):
        """GetEnabledSecTypes(): VeNCrypt only with a sub-type >= 256"""
        self.assertEqual(self.connect([VENCRYPT, VNC], sec="None,VncAuth"),
                         [VNC])

    def test_handler_can_be_asked(self):
        asked = []

        def ask(ranked):
            asked.append(list(ranked))
            return ranked[-1]
        self.start(sec="None,VncAuth", password="pw")
        self.handler.choose_security = ask
        self.assertTrue(self.server.handshake(offer=[VNC, NONE]))
        self.server.wait_for("fbur")
        self.assertEqual(asked, [[VNC, NONE]])
        self.assertEqual(self.server.chosen, [NONE])


if __name__ == "__main__":
    unittest.main()
