"""Encrypt personal records for one device and sign legacy handoff links.

Only ciphertext is kept in the temporary server recovery table. The private
decryption key remains in Telegram SecureStorage on the same device.
"""
import base64
import hashlib
import hmac
import json
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


def key_id(jwk: dict) -> str:
    """Non-secret device key label; SQL computes md5(public_key->>'n')."""
    validate_public_key(jwk)
    return hashlib.md5(jwk['n'].encode(), usedforsecurity=False).hexdigest()


def validate_public_key(jwk: dict):
    if not isinstance(jwk,dict) or jwk.get('kty')!='RSA' or jwk.get('alg')!='RSA-OAEP-256':
        raise ValueError('private_key_invalid')
    try:
        if not isinstance(jwk.get('n'),str) or not isinstance(jwk.get('e'),str):
            raise ValueError()
        if not 330<=len(jwk['n'])<=350 or not 1<=len(jwk['e'])<=8:
            raise ValueError()
        n=int.from_bytes(_decode(jwk['n']),'big')
        e=int.from_bytes(_decode(jwk['e']),'big')
        key=rsa.RSAPublicNumbers(e,n).public_key()
        if key.key_size!=2048 or e!=65537:
            raise ValueError()
        return key
    except Exception:
        raise ValueError('private_key_invalid') from None


def seal(user_id: int, jwk: dict, record: dict, bot_token: str) -> tuple[str,str]:
    public_key=validate_public_key(jwk)
    plaintext=json.dumps(record,ensure_ascii=False,separators=(',',':')).encode()
    if len(plaintext)>4096:
        raise ValueError('private_record_too_large')
    key=os.urandom(32)
    nonce=os.urandom(12)
    wrapped=public_key.encrypt(key,padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                                                 algorithm=hashes.SHA256(),label=None))
    ciphertext=AESGCM(key).encrypt(nonce,plaintext,str(user_id).encode())
    payload=_encode(b'\x01'+wrapped+nonce+ciphertext)
    signature=hmac.new(bot_token.encode(),f'{user_id}:{payload}'.encode(),hashlib.sha256).hexdigest()
    return payload,signature


def verify(user_id: int, payload: str, signature: str, bot_token: str) -> bool:
    if not isinstance(payload,str) or not isinstance(signature,str) or len(payload)>8192:
        return False
    expected=hmac.new(bot_token.encode(),f'{user_id}:{payload}'.encode(),hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected,signature)
