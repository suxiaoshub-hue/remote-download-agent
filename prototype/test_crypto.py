import base64
import hashlib
import unittest

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
from cryptography.hazmat.primitives.poly1305 import Poly1305

import inventory


class CryptoTests(unittest.TestCase):
    def test_independent_encrypted_pages_and_tampering(self):
        salt = bytes(range(16))
        password = hashlib.md5(base64.b64encode(b'http://pcstory.ml')).hexdigest().encode('utf-16le')[:32]
        master = hashlib.pbkdf2_hmac('sha256', password, salt, 64007, 32)
        source = bytearray()
        expected = bytearray()
        for number in (1, 2):
            plain = bytearray((position * number + 7) % 256 for position in range(4096))
            nonce = bytes(range(number, number + 16))
            counter = int.from_bytes(nonce[12:], 'little') ^ number
            derived = Cipher(algorithms.ChaCha20(master, counter.to_bytes(4, 'little') + nonce[:12]), None).encryptor().update(bytes(64))
            start = 24 if number == 1 else 0
            if number == 1:
                plain[:16] = salt
                plain[16:24] = b'\x10\x00\x01\x01\x20\x40\x20\x20'
            page = plain.copy()
            page[start:-32] = Cipher(algorithms.ChaCha20(derived[32:], ((counter + 1) & 0xffffffff).to_bytes(4, 'little') + nonce[:12]), None).encryptor().update(plain[start:-32])
            page[-32:-16] = nonce
            page[-16:] = Poly1305.generate_tag(derived[:32], page[:-16])
            plain[-32:] = page[-32:]
            expected.extend(plain)
            source.extend(page)
        expected[:16] = b'SQLite format 3\0'
        self.assertEqual(inventory.decode(source), expected)
        for position in (0, 128, 4064, 4080, 4096 + 128, 8191):
            changed = source.copy()
            changed[position] ^= 1
            with self.subTest(position=position), self.assertRaisesRegex(ValueError, '认证失败'):
                inventory.decode(changed)
