import os

import supybot.conf as conf

from nacl.signing import SigningKey


KEY_FILE = conf.supybot.directories.data.dirize(
    "BotNet_identity.key"
)


def generate_identity():
    signing_key = SigningKey.generate()

    with open(KEY_FILE, "wb") as f:
        f.write(bytes(signing_key))

    return signing_key


def load_identity():
    if not os.path.exists(KEY_FILE):
        return generate_identity()

    with open(KEY_FILE, "rb") as f:
        key_data = f.read()

    return SigningKey(key_data)


def get_public_key_hex(signing_key):
    return signing_key.verify_key.encode().hex()
