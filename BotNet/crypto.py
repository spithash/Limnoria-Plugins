import os
import supybot.conf as conf
from nacl.signing import SigningKey
from nacl.public import PrivateKey, PublicKey, Box
from nacl.encoding import HexEncoder


def get_botnet_dir():
    """Get the BotNet data directory path"""
    return conf.supybot.directories.data.dirize("BotNet")


def get_key_file():
    """Get path to identity key file"""
    botnet_dir = get_botnet_dir()
    os.makedirs(botnet_dir, exist_ok=True)
    return os.path.join(botnet_dir, "identity.key")


def get_encryption_key_file():
    """Get path to encryption key file"""
    botnet_dir = get_botnet_dir()
    os.makedirs(botnet_dir, exist_ok=True)
    return os.path.join(botnet_dir, "encryption.key")


def generate_identity():
    """Generate Ed25519 signing key for authentication"""
    signing_key = SigningKey.generate()
    key_file = get_key_file()
    
    with open(key_file, "wb") as f:
        f.write(bytes(signing_key))
    return signing_key


def load_identity():
    """Load Ed25519 signing key"""
    key_file = get_key_file()
    if not os.path.exists(key_file):
        return generate_identity()
    
    with open(key_file, "rb") as f:
        key_data = f.read()
    return SigningKey(key_data)


def get_public_key_hex(signing_key):
    """Get hex of Ed25519 public key"""
    return signing_key.verify_key.encode().hex()


def generate_encryption_key():
    """Generate X25519 encryption key pair"""
    private_key = PrivateKey.generate()
    key_file = get_encryption_key_file()
    
    with open(key_file, "wb") as f:
        f.write(bytes(private_key))
    return private_key


def load_encryption_key():
    """Load X25519 encryption key"""
    key_file = get_encryption_key_file()
    if not os.path.exists(key_file):
        return generate_encryption_key()
    
    with open(key_file, "rb") as f:
        key_data = f.read()
    return PrivateKey(key_data)


def get_encryption_public_key_hex(private_key):
    """Get hex of X25519 public key"""
    return private_key.public_key.encode(encoder=HexEncoder).decode()


class EncryptionManager:
    """Manages encrypted communication with peers"""
    
    def __init__(self, my_private_key):
        self.private_key = my_private_key
        self.public_key = my_private_key.public_key
        self.peer_boxes = {}  # peer_pubkey_signing -> Box
        self.peer_public_keys = {}  # peer_pubkey_signing -> PublicKey
    
    def get_public_key_hex(self):
        return self.public_key.encode(encoder=HexEncoder).decode()
    
    def add_peer(self, peer_pubkey_signing, peer_encryption_key_hex=None):
        """Add a peer and create shared box"""
        try:
            if peer_encryption_key_hex:
                peer_key = PublicKey(peer_encryption_key_hex, encoder=HexEncoder)
            elif peer_pubkey_signing in self.peer_public_keys:
                peer_key = self.peer_public_keys[peer_pubkey_signing]
            else:
                raise ValueError(f"No encryption key for peer {peer_pubkey_signing}")
            
            box = Box(self.private_key, peer_key)
            self.peer_boxes[peer_pubkey_signing] = box
            self.peer_public_keys[peer_pubkey_signing] = peer_key
            return True
        except Exception as e:
            return False
    
    def encrypt_for_peer(self, peer_pubkey_signing, message_bytes):
        """Encrypt message for specific peer"""
        box = self.peer_boxes.get(peer_pubkey_signing)
        if not box:
            raise ValueError(f"No encryption box for peer {peer_pubkey_signing}")
        return box.encrypt(message_bytes)
    
    def decrypt_from_peer(self, peer_pubkey_signing, encrypted_bytes):
        """Decrypt message from specific peer"""
        box = self.peer_boxes.get(peer_pubkey_signing)
        if not box:
            raise ValueError(f"No encryption box for peer {peer_pubkey_signing}")
        return box.decrypt(encrypted_bytes)
    
    def create_ephemeral_box(self, peer_encryption_key_hex):
        """Create a one-time box for handshake (optional)"""
        peer_key = PublicKey(peer_encryption_key_hex, encoder=HexEncoder)
        return Box(self.private_key, peer_key)
