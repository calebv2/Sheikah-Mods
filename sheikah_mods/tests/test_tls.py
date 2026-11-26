"""Exercise certificate validation over real TLS without internet access."""
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import os
from pathlib import Path
import socket
import ssl
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
core = importlib.import_module('sheikah_mods.core')
ui_state = importlib.import_module('sheikah_mods.ui_state')
CORE_FILE = core.__file__
FIXTURES = Path(__file__).with_name('fixtures') / 'tls'
BODY = b'local verified mod download'


class CertificateBundleTests(unittest.TestCase):
    def test_shipped_bundle_loads_with_hostname_and_certificate_checks_enabled(self):
        context = core._download_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        names = {
            value
            for certificate in context.get_ca_certs()
            for component in certificate['subject']
            for key, value in component
            if key == 'commonName'
        }
        self.assertIn('ISRG Root X1', names)
        self.assertIn('ISRG Root X2', names)

    def test_certificate_failure_has_specific_recovery_instead_of_generic_offline_text(self):
        certificate = core.CertificateError('private SSL diagnostic details')
        message = ui_state.friendly_error(certificate)
        self.assertIsInstance(certificate, core.Offline)
        self.assertIn('certificate', message)
        self.assertIn('date and time', message)
        self.assertIn('Retry', message)
        self.assertNotIn('private SSL diagnostic details', message)
        self.assertEqual(ui_state.friendly_error(core.Offline('network down')),
                         ui_state.offline_message(None))


class LocalTLSTests(unittest.TestCase):
    def setUp(self):
        # Resolve the normal bundled-CA lookup to our private test CA. This
        # exercises the real context factory without trusting the test CA in
        # the machine's certificate store.
        path = patch.object(core, '__file__', str(FIXTURES / 'core.py'))
        path.start()
        self.addCleanup(path.stop)
        proxies = patch('urllib.request.getproxies', return_value={})
        proxies.start()
        self.addCleanup(proxies.stop)

    @contextmanager
    def server(self, certificate='valid.pem', redirects=None):
        requests = []
        connections = []
        routes = redirects or {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                if self.path in routes:
                    self.send_response(302)
                    self.send_header('Location', routes[self.path])
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header('Content-Length', str(len(BODY)))
                self.end_headers()
                self.wfile.write(BODY)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(FIXTURES / certificate), str(FIXTURES / 'server.key'))
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={'poll_interval': 0.02}, daemon=True)
        thread.start()
        create_connection = socket.create_connection

        def local_connection(address, *args, **kwargs):
            # Keep the production hostname, SNI, and port in the HTTP/TLS
            # client, but route its TCP connection to our loopback server.
            # Unexpected destinations fail here instead of reaching a network.
            self.assertEqual(address, (core.HOST, 443))
            connections.append(address)
            return create_connection(('127.0.0.1', server.server_port), *args, **kwargs)

        try:
            with patch('http.client.socket.create_connection', side_effect=local_connection):
                yield requests, connections
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive(), 'local TLS server failed to stop')

    def test_manifest_fetch_and_streamed_mod_download_accept_trusted_certificate(self):
        with self.server() as (requests, connections), tempfile.TemporaryDirectory() as directory:
            self.assertEqual(core.fetch(core.MANIFEST_URL, 1024), BODY)
            destination = Path(directory) / 'A.dll'
            mod = core.Mod('A.dll', hashlib.sha256(BODY).hexdigest().upper(),
                           'https://' + core.HOST + '/A.dll')
            sizes = []
            core._stage_download(mod, destination, sizes.append, threading.Event(), None)
            self.assertEqual(destination.read_bytes(), BODY)
            self.assertEqual(sum(sizes), len(BODY))
        self.assertEqual(requests, ['/manifest.json', '/A.dll'])
        self.assertEqual(len(connections), 2)

    def test_expired_certificate_is_rejected_without_an_unverified_retry(self):
        with self.server('expired.pem') as (requests, connections):
            with self.assertRaises(core.CertificateError) as caught:
                core.fetch(core.MANIFEST_URL, 1024)
        reason = getattr(caught.exception.__cause__, 'reason', caught.exception.__cause__)
        self.assertIsInstance(reason, ssl.SSLCertVerificationError)
        self.assertEqual(reason.verify_code, 10)
        self.assertEqual(requests, [])
        self.assertEqual(len(connections), 1)

    def test_hostname_mismatch_is_rejected_without_an_unverified_retry(self):
        with self.server('wrong-host.pem') as (requests, connections):
            with self.assertRaises(core.CertificateError) as caught:
                core.fetch(core.MANIFEST_URL, 1024)
        reason = getattr(caught.exception.__cause__, 'reason', caught.exception.__cause__)
        self.assertIsInstance(reason, ssl.SSLCertVerificationError)
        self.assertEqual(reason.verify_code, 62)
        self.assertEqual(requests, [])
        self.assertEqual(len(connections), 1)

    def test_untrusted_issuer_is_rejected_by_the_shipped_bundle(self):
        with patch.object(core, '__file__', CORE_FILE), self.server() as (requests, connections):
            with self.assertRaises(core.CertificateError) as caught:
                core.fetch(core.MANIFEST_URL, 1024)
        reason = getattr(caught.exception.__cause__, 'reason', caught.exception.__cause__)
        self.assertIsInstance(reason, ssl.SSLCertVerificationError)
        self.assertEqual(requests, [])
        self.assertEqual(len(connections), 1)

    def test_same_site_https_redirect_is_followed_with_verification(self):
        with self.server(redirects={'/manifest.json': 'https://' + core.HOST + '/next'}) as (requests, connections):
            self.assertEqual(core.fetch(core.MANIFEST_URL, 1024), BODY)
        self.assertEqual(requests, ['/manifest.json', '/next'])
        self.assertEqual(len(connections), 2)

    def test_external_or_http_redirect_is_rejected_before_connecting(self):
        for location in ('https://different.example.test/A.dll', 'http://' + core.HOST + '/A.dll'):
            with self.subTest(location=location):
                with self.server(redirects={'/manifest.json': location}) as (requests, connections):
                    with self.assertRaisesRegex(core.Error, 'valid HTTPS download URL'):
                        core.fetch(core.MANIFEST_URL, 1024)
                self.assertEqual(requests, ['/manifest.json'])
                self.assertEqual(len(connections), 1)

    def test_explicit_bundle_ignores_broken_machine_certificate_paths(self):
        environment = {
            'SSL_CERT_FILE': str(FIXTURES / 'missing-machine-certificates.pem'),
            'SSL_CERT_DIR': str(FIXTURES / 'missing-machine-certificate-directory'),
        }
        with patch.dict(os.environ, environment):
            context = core._download_context()
            self.assertEqual(context.cert_store_stats()['x509_ca'], 1)
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(context.check_hostname)
            with self.server():
                self.assertEqual(core.fetch(core.MANIFEST_URL, 1024), BODY)


if __name__ == '__main__':
    unittest.main()
