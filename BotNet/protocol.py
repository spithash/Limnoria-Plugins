import socket
import struct
import msgpack


def pack_message(message):
    payload = msgpack.packb(message, use_bin_type=True)
    length = struct.pack("!I", len(payload))
    return length + payload


def unpack_message(sock, timeout=60):
    sock.settimeout(timeout)
    
    try:
        header = sock.recv(4)
        
        if not header:
            return None
        
        length = struct.unpack("!I", header)[0]
        
        MAX_MESSAGE_SIZE = 10 * 1024 * 1024
        if length > MAX_MESSAGE_SIZE:
            raise ValueError(f"Message too large: {length} bytes")
        
        payload = b""
        while len(payload) < length:
            chunk = sock.recv(length - len(payload))
            if not chunk:
                return None
            payload += chunk
        
        return msgpack.unpackb(payload, raw=False)
        
    except socket.timeout:
        raise
    except Exception as e:
        raise ConnectionError(f"Failed to unpack message: {e}") from e
