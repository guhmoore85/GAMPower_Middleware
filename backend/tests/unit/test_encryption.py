"""
Unit Tests for Encryption Module

CRITICAL: Tests verify encryption works correctly.
"""

import pytest

from app.core.exceptions import EncryptionError
from app.utils.encryption import decrypt_value, encrypt_value, is_encrypted


class TestEncryption:
    """Test encryption utilities."""

    def test_encrypt_decrypt_roundtrip(self):
        """Test that encryption and decryption are reversible."""
        plaintext = "secret_key_12345"

        encrypted = encrypt_value(plaintext)
        decrypted = decrypt_value(encrypted)

        assert decrypted == plaintext
        assert encrypted != plaintext

    def test_encrypt_with_aad(self):
        """Test encryption with associated data."""
        plaintext = "secret_value"
        aad = "device_id_123"

        encrypted = encrypt_value(plaintext, associated_data=aad)
        decrypted = decrypt_value(encrypted, associated_data=aad)

        assert decrypted == plaintext

    def test_decrypt_wrong_aad_fails(self):
        """Test that decryption fails with wrong AAD."""
        plaintext = "secret_value"

        encrypted = encrypt_value(plaintext, associated_data="correct_aad")

        with pytest.raises(EncryptionError):
            decrypt_value(encrypted, associated_data="wrong_aad")

    def test_encrypt_empty_value_fails(self):
        """Test that encrypting empty value fails."""
        with pytest.raises(EncryptionError):
            encrypt_value("")

    def test_decrypt_empty_value_fails(self):
        """Test that decrypting empty value fails."""
        with pytest.raises(EncryptionError):
            decrypt_value("")

    def test_decrypt_invalid_format_fails(self):
        """Test that decrypting invalid format fails."""
        with pytest.raises(EncryptionError):
            decrypt_value("not_base64_encrypted")

    def test_is_encrypted_detection(self):
        """Test encrypted value detection."""
        plaintext = "secret_value"
        encrypted = encrypt_value(plaintext)

        assert is_encrypted(encrypted) is True
        assert is_encrypted(plaintext) is False
        assert is_encrypted("") is False

    def test_different_plaintexts_different_ciphertexts(self):
        """Test that same plaintext encrypts to different ciphertext."""
        plaintext = "secret_value"

        encrypted1 = encrypt_value(plaintext)
        encrypted2 = encrypt_value(plaintext)

        # Due to random nonce, ciphertexts should differ
        assert encrypted1 != encrypted2

        # But both should decrypt to same plaintext
        assert decrypt_value(encrypted1) == plaintext
        assert decrypt_value(encrypted2) == plaintext
