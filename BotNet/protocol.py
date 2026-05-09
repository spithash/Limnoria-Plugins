import socket
import struct
import msgpack
import time
import os


def pack_message(message, encryption_box=None):
    """
    Pack message with optional encryption.
    
    Message format:
    - Mode byte: 'P' (plain) or 'E' (encrypted)
    - 4-byte length (network byte order)
    - Payload (plain or encrypted msgpack)
    """
    payload = msgpack.packb(message, use_bin_type=True)
    
    if encryption_box:
        # Encrypt the payload
        encrypted = encryption_box.encrypt(payload)
        mode = b'E'
        length = len(encrypted)
        return mode + struct.pack("!I", length) + encrypted
    else:
        mode = b'P'
        length = len(payload)
        return mode + struct.pack("!I", length) + payload


def unpack_message(sock, decryption_box=None, timeout=30, max_size=10*1024*1024):
    """
    Unpack message with optional decryption.
    
    Security features:
    - Total read timeout of (timeout + 5) seconds for complete message
    - Maximum message size limit
    - Non-blocking reads with deadline
    - Per-chunk timeouts to prevent hanging
    
    Returns:
        Decoded message dict, or None if connection closed
    """
    # Set initial timeout
    sock.settimeout(timeout)
    start_time = time.time()
    deadline = start_time + timeout + 5  # Total max time for complete message
    
    try:
        # Read mode flag
        mode = sock.recv(1)
        if not mode:
            return None
        
        # Check if we've exceeded deadline
        if time.time() > deadline:
            raise socket.timeout("Message read deadline exceeded")
        
        # Read length with per-chunk timeout
        header = sock.recv(4)
        if not header:
            return None
        
        length = struct.unpack("!I", header)[0]
        
        # Security: Prevent memory bombs
        if length > max_size:
            raise ValueError(f"Message too large: {length} bytes (max: {max_size})")
        
        # Read payload with incremental timeout
        payload = b""
        remaining = length
        chunk_size = 8192  # Read in 8KB chunks
        
        while remaining > 0:
            # Reset timeout for each chunk (keep connection alive)
            sock.settimeout(timeout)
            to_read = min(chunk_size, remaining)
            chunk = sock.recv(to_read)
            if not chunk:
                return None
            payload += chunk
            remaining -= len(chunk)
            
            # Check deadline
            if time.time() > deadline:
                raise socket.timeout("Message read deadline exceeded")
        
        # Decrypt if encrypted
        if mode == b'E':
            if not decryption_box:
                raise ValueError("Received encrypted message but no decryption box")
            payload = decryption_box.decrypt(payload)
        elif mode != b'P':
            raise ValueError(f"Unknown mode byte: {mode}")
        
        return msgpack.unpackb(payload, raw=False)
        
    except socket.timeout:
        raise
    except Exception as e:
        raise ConnectionError(f"Failed to unpack message: {e}") from e


def create_handshake_message(bot_name, pubkey_signing, pubkey_encryption, protocol_version=1, nonce=None):
    """Create a HELLO message for handshake with nonce"""
    if nonce is None:
        nonce = os.urandom(16).hex()
    return {
        "type": "hello",
        "protocol": protocol_version,
        "bot_name": bot_name,
        "pubkey_signing": pubkey_signing,
        "pubkey_encryption": pubkey_encryption,
        "nonce": nonce
    }


def create_handshake_ack(status, bot_name, pubkey_signing, pubkey_encryption, nonce):
    """Create a HELLO_ACK message with nonce"""
    return {
        "type": "hello_ack",
        "status": status,
        "bot_name": bot_name,
        "pubkey_signing": pubkey_signing,
        "pubkey_encryption": pubkey_encryption,
        "nonce": nonce
    }


def sign_message(message, signing_key):
    """Sign a message with Ed25519 signing key for authentication"""
    import msgpack
    from nacl.signing import SigningKey
    from nacl.encoding import HexEncoder
    
    # Remove signature field if exists
    msg_copy = message.copy()
    msg_copy.pop('signature', None)
    
    # Serialize and sign
    serialized = msgpack.packb(msg_copy, use_bin_type=True)
    signature = signing_key.sign(serialized).signature
    
    message['signature'] = signature.hex()
    return message


def verify_message(message, verify_key_hex):
    """Verify message signature"""
    import msgpack
    from nacl.signing import VerifyKey
    from nacl.encoding import HexEncoder
    
    signature = bytes.fromhex(message.pop('signature', ''))
    if not signature:
        return False
    
    serialized = msgpack.packb(message, use_bin_type=True)
    
    try:
        verify_key = VerifyKey(verify_key_hex, encoder=HexEncoder)
        verify_key.verify(serialized, signature)
        return True
    except Exception:
        return False
