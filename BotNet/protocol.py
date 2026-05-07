import struct
import msgpack


def pack_message(message):
    payload = msgpack.packb(message, use_bin_type=True)
    length = struct.pack("!I", len(payload))
    return length + payload


def unpack_message(sock):
    sock.settimeout(10)

    header = sock.recv(4)

    if not header:
        return None

    length = struct.unpack("!I", header)[0]

    payload = b""

    while len(payload) < length:
        chunk = sock.recv(length - len(payload))

        if not chunk:
            return None

        payload += chunk

    return msgpack.unpackb(payload, raw=False)
