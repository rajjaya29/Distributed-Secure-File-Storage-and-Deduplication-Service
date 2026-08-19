import os
from typing import Tuple
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings


class CryptoService:
    def __init__(self, master_key_hex: str = settings.MASTER_ENCRYPTION_KEY):
        try:
            self.key = bytes.fromhex(master_key_hex)[:32]
        except Exception:
            # Fallback to 32 bytes derived
            self.key = master_key_hex.encode("utf-8")[:32].ljust(32, b"0")
        self.aesgcm = AESGCM(self.key)

    def encrypt_chunk(self, data: bytes) -> bytes:
        """
        Encrypt data chunk using AES-256-GCM.
        Prepend a 12-byte random nonce to the ciphertext.
        """
        nonce = os.urandom(12)
        ciphertext = self.aesgcm.encrypt(nonce, data, None)
        return nonce + ciphertext

    def decrypt_chunk(self, encrypted_data: bytes) -> bytes:
        """
        Decrypt data chunk.
        Extract the first 12 bytes as nonce and decrypt the remainder.
        """
        if len(encrypted_data) < 12:
            raise ValueError("Invalid encrypted data payload")
        nonce = encrypted_data[:12]
        ciphertext = encrypted_data[12:]
        return self.aesgcm.decrypt(nonce, ciphertext, None)


crypto_service = CryptoService()
