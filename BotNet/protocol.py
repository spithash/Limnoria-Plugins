import socket
import struct
import msgpack


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


def unpack_message(sock, decryption_box=None, timeout=60):
    """
    Unpack message with optional decryption.
    
    Returns:
        Decoded message dict, or None if connection closed
    """
    sock.settimeout(timeout)
    
    try:
        # Read mode flag
        mode = sock.recv(1)
        if not mode:
            return None
        
        # Read length
        header = sock.recv(4)
        if not header:
            return None
        
        length = struct.unpack("!I", header)[0]
        
        # Security: Prevent memory bombs
        MAX_MESSAGE_SIZE = 10 * 1024 * 1024  # 10MB
        if length > MAX_MESSAGE_SIZE:
            raise ValueError(f"Message too large: {length} bytes")
        
        # Read payload
        payload = b""
        while len(payload) < length:
            chunk = sock.recv(length - len(payload))
            if not chunk:
                return None
            payload += chunk
        
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


def create_handshake_message(bot_name, pubkey_signing, pubkey_encryption, protocol_version=1):
    """Create a HELLO message for handshake"""
    return {
        "type": "hello",
        "protocol": protocol_version,
        "bot_name": bot_name,
        "pubkey_signing": pubkey_signing,
        "pubkey_encryption": pubkey_encryption,
    }


def create_handshake_ack(status, bot_name, pubkey_signing, pubkey_encryption):
    """Create a HELLO_ACK message"""
    return {
        "type": "hello_ack",
        "status": status,
        "bot_name": bot_name,
        "pubkey_signing": pubkey_signing,
        "pubkey_encryption": pubkey_encryption,
    }
