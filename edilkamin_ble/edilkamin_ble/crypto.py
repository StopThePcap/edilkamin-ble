"""AES encryption/decryption for Edilkamin BLE protocol."""

import logging

from Crypto.Cipher import AES

from .const import AES_KEY, AES_IV

_LOGGER = logging.getLogger(__name__)


class EdilkaminCrypto:
    """Handles AES-128-CBC encryption/decryption for Edilkamin BLE communication."""

    def __init__(self, key: bytes = AES_KEY, iv: bytes = AES_IV):
        """Initialize with encryption key and IV.

        Args:
            key: 16-byte AES-128 key.
            iv: 16-byte initialization vector.
        """
        if len(key) != 16:
            raise ValueError("AES key must be 16 bytes")
        if len(iv) != 16:
            raise ValueError("AES IV must be 16 bytes")
        self._key = key
        self._iv = iv
        _LOGGER.debug("Crypto initialized: AES-128-CBC")

    def encrypt(self, plaintext: bytes) -> bytes:
        """Encrypt data using AES-128-CBC.

        Args:
            plaintext: Data to encrypt (must be multiple of 16 bytes).

        Returns:
            Encrypted ciphertext.
        """
        if len(plaintext) % 16 != 0:
            raise ValueError(f"Plaintext length must be multiple of 16, got {len(plaintext)}")

        cipher = AES.new(self._key, AES.MODE_CBC, iv=self._iv)
        return cipher.encrypt(plaintext)

    def decrypt(self, ciphertext: bytes) -> bytes:
        """Decrypt data using AES-128-CBC.

        Args:
            ciphertext: Data to decrypt (must be multiple of 16 bytes).

        Returns:
            Decrypted plaintext.
        """
        if len(ciphertext) % 16 != 0:
            raise ValueError(f"Ciphertext length must be multiple of 16, got {len(ciphertext)}")

        cipher = AES.new(self._key, AES.MODE_CBC, iv=self._iv)
        return cipher.decrypt(ciphertext)
