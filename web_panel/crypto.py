"""رمزنگاری API key ها با Fernet"""
import os
from cryptography.fernet import Fernet

_fernet = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet

    key = os.getenv("ENCRYPTION_KEY", "")
    if not key:
        # اگه نبود، یه کلید موقت بساز (فقط برای dev)
        # روی Railway حتماً ENCRYPTION_KEY رو ست کن!
        raise RuntimeError(
            "ENCRYPTION_KEY تنظیم نشده! "
            "این کلید رو با دستور زیر بساز و توی .env بذار:\n"
            "  python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )

    _fernet = Fernet(key.encode())
    return _fernet


def encrypt(plaintext: str) -> str:
    """رمزنگاری متن"""
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """رمزگشایی متن"""
    if not ciphertext:
        return ""
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except Exception:
        # اگه نتونست رمزگشایی کنه، احتمالاً plaintext قدیمیه
        return ciphertext
