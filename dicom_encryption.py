"""
================================================================================
ARTIFACT ZERO LABS — Payload Encryption
Merge into: utils/ or crypto/ directory

Handles end-to-end encryption of measurement payloads.
Customer provides their RSA public key at registration.
We encrypt the JSON payload before sending — we cannot read what we send.

Algorithm:
  - RSA-OAEP for key encapsulation (asymmetric, customer's public key)
  - AES-256-GCM for payload encryption (symmetric, ephemeral key)
  - Standard hybrid encryption — fast for large payloads, secure

The customer decrypts with their private key (never sent to us).

Dependencies: cryptography (already common in Flask apps)
  pip install cryptography
================================================================================
"""

import base64
import json
import os
from typing import tuple as Tuple

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


# ════════════════════════════════════════════════════════════════════
# ENCRYPT
# ════════════════════════════════════════════════════════════════════

def encrypt_payload(payload: dict, public_key_pem: str) -> dict:
    """
    Encrypt a measurement payload with the customer's RSA public key.

    Returns a dict with:
      encrypted_key   — RSA-encrypted AES key (base64)
      nonce           — AES-GCM nonce (base64)
      ciphertext      — encrypted payload (base64)
      algorithm       — description of what was used

    The customer decrypts by:
      1. RSA-decrypt encrypted_key with their private key -> aes_key
      2. AES-GCM decrypt ciphertext with aes_key + nonce -> plaintext JSON
    """
    # Serialize payload to bytes
    plaintext = json.dumps(payload, default=str).encode('utf-8')

    # Load customer's public key
    public_key = serialization.load_pem_public_key(
        public_key_pem.encode('utf-8')
    )

    # Generate ephemeral AES-256 key
    aes_key = os.urandom(32)
    nonce   = os.urandom(12)

    # Encrypt payload with AES-GCM
    aesgcm     = AESGCM(aes_key)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)

    # Encrypt AES key with customer's RSA public key
    encrypted_key = public_key.encrypt(
        aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None
        )
    )

    return {
        'encrypted':      True,
        'algorithm':      'RSA-OAEP + AES-256-GCM',
        'encrypted_key':  base64.b64encode(encrypted_key).decode(),
        'nonce':          base64.b64encode(nonce).decode(),
        'ciphertext':     base64.b64encode(ciphertext).decode(),
    }


# ════════════════════════════════════════════════════════════════════
# DECRYPT (for testing and customer tooling)
# ════════════════════════════════════════════════════════════════════

def decrypt_payload(encrypted_response: dict, private_key_pem: str,
                    private_key_password: bytes = None) -> dict:
    """
    Decrypt a payload encrypted by encrypt_payload.
    This runs on the CUSTOMER side — they hold the private key.

    Provide this code to customers so they can decrypt responses.
    """
    private_key = serialization.load_pem_private_key(
        private_key_pem.encode('utf-8'),
        password=private_key_password
    )

    # Decrypt AES key
    encrypted_key = base64.b64decode(encrypted_response['encrypted_key'])
    aes_key = private_key.decrypt(
        encrypted_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None
        )
    )

    # Decrypt payload
    nonce      = base64.b64decode(encrypted_response['nonce'])
    ciphertext = base64.b64decode(encrypted_response['ciphertext'])
    aesgcm     = AESGCM(aes_key)
    plaintext  = aesgcm.decrypt(nonce, ciphertext, None)

    return json.loads(plaintext.decode('utf-8'))


# ════════════════════════════════════════════════════════════════════
# KEY GENERATION UTILITY (for customer onboarding)
# ════════════════════════════════════════════════════════════════════

def generate_customer_keypair() -> Tuple[str, str]:
    """
    Generate an RSA-2048 keypair for a new customer.
    Returns (public_key_pem, private_key_pem).

    Public key is stored with us.
    Private key is given to the customer ONCE and never stored.
    """
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    public_key = private_key.public_key()

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    ).decode('utf-8')

    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode('utf-8')

    return public_pem, private_pem


# ════════════════════════════════════════════════════════════════════
# VALIDATION
# ════════════════════════════════════════════════════════════════════

def validate_public_key(public_key_pem: str) -> bool:
    """Validate that a PEM string is a valid RSA public key."""
    try:
        key = serialization.load_pem_public_key(public_key_pem.encode())
        return hasattr(key, 'key_size') and key.key_size >= 2048
    except Exception:
        return False


def is_encrypted_response(response: dict) -> bool:
    """Check if a response dict is an encrypted payload."""
    return bool(response.get('encrypted') and
                response.get('encrypted_key') and
                response.get('ciphertext'))
