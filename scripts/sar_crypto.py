"""Password encryption interoperable with the workbench's browser Web Crypto.

Only authenticated AES-256-GCM ciphertext is eligible for repository publication.
Passwords and plaintext are never put in the envelope or written by this module.
"""
import base64
import hashlib
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ITERATIONS = 600000
MAX_BYTES = 16*1024*1024


def validate_password(password):
    if not isinstance(password,str) or not 12<=len(password)<=128 or any(ord(c)<32 or ord(c)>126 for c in password):
        raise ValueError("Use a password of 12–128 printable ASCII characters")


def b64(value):return base64.b64encode(value).decode('ascii')


def aad(purpose):
    if purpose not in {'request','workspace'}:raise ValueError('Unknown encrypted object purpose')
    return f'MOwlSINT-SAR-v1:{purpose}'.encode('ascii')


def encrypt(payload,password,purpose):
    validate_password(password)
    data=json.dumps(payload,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
    if len(data)>MAX_BYTES:raise ValueError('Encrypted object exceeds its size limit')
    salt,nonce=os.urandom(16),os.urandom(12)
    key=hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),salt,ITERATIONS,32)
    ciphertext=AESGCM(key).encrypt(nonce,data,aad(purpose))
    return {'v':1,'purpose':purpose,'kdf':'PBKDF2-SHA256','iterations':ITERATIONS,
            'salt':b64(salt),'nonce':b64(nonce),'ciphertext':b64(ciphertext)}


def decrypt(envelope,password,purpose):
    validate_password(password)
    if not isinstance(envelope,dict) or envelope.get('v')!=1 or envelope.get('purpose')!=purpose or envelope.get('kdf')!='PBKDF2-SHA256' or envelope.get('iterations')!=ITERATIONS:
        raise ValueError('Unsupported encrypted object')
    salt=base64.b64decode(envelope['salt'],validate=True)
    nonce=base64.b64decode(envelope['nonce'],validate=True)
    ciphertext=base64.b64decode(envelope['ciphertext'],validate=True)
    if len(salt)!=16 or len(nonce)!=12 or not 16<=len(ciphertext)<=MAX_BYTES+16:raise ValueError('Invalid encrypted object size')
    key=hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),salt,ITERATIONS,32)
    return json.loads(AESGCM(key).decrypt(nonce,ciphertext,aad(purpose)).decode('utf-8'))
