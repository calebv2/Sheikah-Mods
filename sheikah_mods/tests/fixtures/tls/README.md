These certificates and the dummy server private key are used only by the
offline TLS regression tests. They grant no access to the production site.
The test CA is deliberately absent from the shipped production CA bundle.

The three server certificates share `server.key` and are signed by
`cacert.pem`:

- `valid.pem`: DNS name `att.aservertown.fans`, valid through January 1, 2045.
- `expired.pem`: the same DNS name, expired January 1, 2021.
- `wrong-host.pem`: DNS name `different.example.test`, valid through January 1, 2045.

Fixtures were generated using OpenSSL 3.0.13. The CA certificate is valid
for 20 years from November 20, 2026. Tests route the fixed production
hostname's TCP connections to an ephemeral loopback port while retaining
real SNI, hostname, certificate-chain, and expiry verification.
