"""WeChat CDN 多媒体文件加解密与下载."""

import re
import base64
import requests

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad, pad

CDN_BASE = "https://novac2c.cdn.weixin.qq.com/c2c"


def parse_aes_key(aes_key_b64: str) -> bytes:
    """解析 AES key: base64(16 bytes) 或 base64(32 char hex)."""
    raw = base64.b64decode(aes_key_b64)
    if len(raw) == 16:
        return raw
    if len(raw) == 32 and re.fullmatch(r"[0-9a-fA-F]{32}", raw.decode("ascii")):
        return bytes.fromhex(raw.decode("ascii"))
    raise ValueError(f"Invalid AES key length: {len(raw)}")


def aes_ecb_encrypt(plaintext: bytes, key: bytes) -> bytes:
    cipher = AES.new(key, AES.MODE_ECB)
    return cipher.encrypt(pad(plaintext, AES.block_size))


def aes_ecb_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    cipher = AES.new(key, AES.MODE_ECB)
    return unpad(cipher.decrypt(ciphertext), AES.block_size)


def download_and_decrypt(encrypt_query_param: str, aes_key_b64: str | None) -> bytes:
    """从 CDN 下载文件并 AES-128-ECB 解密. aes_key_b64 为 None 时不解密."""
    url = f"{CDN_BASE}/download?encrypted_query_param={encrypt_query_param}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()

    encrypted = r.content
    if not aes_key_b64:
        return encrypted

    key = parse_aes_key(aes_key_b64)
    return aes_ecb_decrypt(encrypted, key)
