"""
Encryption Utilities for PAYGO Middleware

Provides AES-256-GCM encryption for sensitive data like device secrets.

CRITICAL: Never log encrypted or decrypted values.
All encryption errors are logged LOUDLY with context.
"""

import base64
import secrets
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings
from app.core.exceptions import EncryptionError
from app.core.logging import get_logger

logger = get_logger(__name__)

# Nonce size for AES-GCM (96 bits / 12 bytes is recommended)
NONCE_SIZE = 12


def _get_cipher() -> AESGCM:
    """
    Get AES-GCM cipher instance with configured key.

    LOUD: Raises EncryptionError if key is invalid.
    """
    try:
        key = settings.get_encryption_key()
        return AESGCM(key)
    except Exception as e:
        logger.error(
            "Failed to initialize encryption cipher",
            error=str(e),
        )
        raise EncryptionError(
            operation="initialize",
            reason="Invalid encryption key configuration",
            original_error=e,
        )


def encrypt_value(
    plaintext: str,
    associated_data: Optional[str] = None,
) -> str:
    """
    Encrypt a string value using AES-256-GCM.

    Returns base64-encoded ciphertext with embedded nonce.

    Args:
        plaintext: The string to encrypt
        associated_data: Optional AAD for authentication

    Returns:
        Base64-encoded encrypted value (format: nonce + ciphertext)

    Raises:
        EncryptionError: If encryption fails
    """
    if not plaintext:
        logger.warning("Attempted to encrypt empty value")
        raise EncryptionError(
            operation="encrypt",
            reason="Cannot encrypt empty value",
        )

    try:
        cipher = _get_cipher()

        # Generate random nonce
        nonce = secrets.token_bytes(NONCE_SIZE)

        # Encrypt
        aad = associated_data.encode("utf-8") if associated_data else None
        ciphertext = cipher.encrypt(nonce, plaintext.encode("utf-8"), aad)

        # Combine nonce + ciphertext and base64 encode
        encrypted = base64.b64encode(nonce + ciphertext).decode("utf-8")

        logger.debug(
            "Value encrypted successfully",
            plaintext_length=len(plaintext),
            encrypted_length=len(encrypted),
            has_aad=bool(associated_data),
        )

        return encrypted

    except EncryptionError:
        raise
    except Exception as e:
        logger.error(
            "Encryption failed",
            error=str(e),
            error_type=type(e).__name__,
        )
        raise EncryptionError(
            operation="encrypt",
            reason=f"Encryption operation failed: {type(e).__name__}",
            original_error=e,
        )


def decrypt_value(
    encrypted_value: str,
    associated_data: Optional[str] = None,
) -> str:
    """
    Decrypt a value encrypted with encrypt_value().

    Args:
        encrypted_value: Base64-encoded encrypted value
        associated_data: Optional AAD (must match what was used for encryption)

    Returns:
        Decrypted plaintext string

    Raises:
        EncryptionError: If decryption fails
    """
    if not encrypted_value:
        logger.warning("Attempted to decrypt empty value")
        raise EncryptionError(
            operation="decrypt",
            reason="Cannot decrypt empty value",
        )

    try:
        cipher = _get_cipher()

        # Base64 decode
        try:
            encrypted_bytes = base64.b64decode(encrypted_value)
        except Exception as e:
            logger.error(
                "Base64 decode failed during decryption",
                error=str(e),
            )
            raise EncryptionError(
                operation="decrypt",
                reason="Invalid encrypted value format (base64 decode failed)",
                original_error=e,
            )

        # Validate length
        if len(encrypted_bytes) < NONCE_SIZE + 16:  # 16 is minimum ciphertext size
            logger.error(
                "Encrypted value too short",
                length=len(encrypted_bytes),
                minimum=NONCE_SIZE + 16,
            )
            raise EncryptionError(
                operation="decrypt",
                reason="Encrypted value too short",
            )

        # Split nonce and ciphertext
        nonce = encrypted_bytes[:NONCE_SIZE]
        ciphertext = encrypted_bytes[NONCE_SIZE:]

        # Decrypt
        aad = associated_data.encode("utf-8") if associated_data else None
        plaintext = cipher.decrypt(nonce, ciphertext, aad)

        logger.debug(
            "Value decrypted successfully",
            encrypted_length=len(encrypted_value),
            plaintext_length=len(plaintext),
            has_aad=bool(associated_data),
        )

        return plaintext.decode("utf-8")

    except EncryptionError:
        raise
    except Exception as e:
        # This could be an authentication failure (tampered data)
        logger.error(
            "Decryption failed",
            error=str(e),
            error_type=type(e).__name__,
        )
        raise EncryptionError(
            operation="decrypt",
            reason=f"Decryption operation failed: {type(e).__name__}. "
            "Data may be corrupted or AAD mismatch.",
            original_error=e,
        )


def is_encrypted(value: str) -> bool:
    """
    Check if a value appears to be encrypted.

    This is a heuristic check based on format.

    Args:
        value: Value to check

    Returns:
        True if value appears to be encrypted
    """
    if not value:
        return False

    try:
        decoded = base64.b64decode(value)
        # Check minimum length for nonce + ciphertext
        return len(decoded) >= NONCE_SIZE + 16
    except Exception:
        return False


def rotate_encryption(
    encrypted_value: str,
    old_key: bytes,
    new_key: bytes,
    associated_data: Optional[str] = None,
) -> str:
    """
    Re-encrypt a value with a new key.

    CRITICAL: This is used during key rotation.
    Logs operation but never the keys or values.

    Args:
        encrypted_value: Currently encrypted value
        old_key: Current encryption key
        new_key: New encryption key
        associated_data: Optional AAD

    Returns:
        Value encrypted with new key

    Raises:
        EncryptionError: If rotation fails
    """
    try:
        # Decrypt with old key
        old_cipher = AESGCM(old_key)
        encrypted_bytes = base64.b64decode(encrypted_value)
        nonce = encrypted_bytes[:NONCE_SIZE]
        ciphertext = encrypted_bytes[NONCE_SIZE:]

        aad = associated_data.encode("utf-8") if associated_data else None
        plaintext = old_cipher.decrypt(nonce, ciphertext, aad)

        # Encrypt with new key
        new_cipher = AESGCM(new_key)
        new_nonce = secrets.token_bytes(NONCE_SIZE)
        new_ciphertext = new_cipher.encrypt(new_nonce, plaintext, aad)

        result = base64.b64encode(new_nonce + new_ciphertext).decode("utf-8")

        logger.info(
            "Encryption key rotation completed",
            has_aad=bool(associated_data),
        )

        return result

    except Exception as e:
        logger.error(
            "Encryption key rotation failed",
            error=str(e),
            error_type=type(e).__name__,
        )
        raise EncryptionError(
            operation="rotate",
            reason=f"Key rotation failed: {type(e).__name__}",
            original_error=e,
        )
