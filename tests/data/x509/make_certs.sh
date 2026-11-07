#!/bin/sh
# Test certificates for tests/test_x509.py (regenerate with OpenSSL 3.4+:
# sh make_certs.sh; in Git Bash set MSYS2_ARG_CONV_EXCL="*" first).
# Test-only keys, never use them for anything else.
set -e
cd "$(dirname "$0")"
rm -rf db && mkdir db && : > db/index.txt && echo 1000 > db/serial
cat > ca.cnf <<'CNF'
[ca]
default_ca = test_ca
[test_ca]
database = db/index.txt
serial = db/serial
new_certs_dir = db
default_md = sha256
default_crl_days = 36500
policy = any
[any]
commonName = supplied
CNF
key() { openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 \
        -out "$1.key" 2>/dev/null; }
# signed by the CA: sign NAME SAN [extra x509 args]
sign() {
    key "$1"
    openssl req -new -key "$1.key" -subj "/CN=$1" -out "$1.csr"
    printf 'subjectAltName=%s\n' "$2" > "$1.ext"
    name=$1; san=$2; shift 2
    openssl x509 -req -in "$name.csr" -CA ca.pem -CAkey ca.key \
        -set_serial "0x$(openssl rand -hex 8)" -extfile "$name.ext" \
        -out "$name.pem" "$@"
    rm "$name.csr" "$name.ext"
}
key ca
openssl req -x509 -new -key ca.key -subj "/CN=SagatVNC Test CA" \
    -days 36500 -out ca.pem
sign server "DNS:localhost,IP:127.0.0.1" -days 36500
sign otherhost "DNS:otherhost.example" -days 36500
sign expired "DNS:localhost,IP:127.0.0.1" \
    -not_before 20200101000000Z -not_after 20200201000000Z
sign revoked "DNS:localhost,IP:127.0.0.1" -days 36500
openssl ca -config ca.cnf -keyfile ca.key -cert ca.pem \
    -revoke revoked.pem 2>/dev/null
openssl ca -config ca.cnf -keyfile ca.key -cert ca.pem -gencrl \
    -out crl.pem 2>/dev/null
key selfsigned
openssl req -x509 -new -key selfsigned.key -subj "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" -days 36500 \
    -out selfsigned.pem
rm -rf db ca.cnf ca.key
