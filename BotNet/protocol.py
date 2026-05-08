import struct
import msgpack


def recv_exact(sock, size):
    data = b""

    while len(data) < size:
        chunk = sock.recv(size - len(data))

        if not chunk:
            return None

        data += chunk

    return data


def pack_message(message):
    payload = msgpack.packb(message, use_bin_type=True)
    length = struct.pack("!I", len(payload))
    return length + payload


def unpack_message(sock):
    header = recv_exact(sock, 4)

    if not header:
        return None

    length = struct.unpack("!I", header)[0]

    payload = recv_exact(sock, length)

    if not payload:
        return None

    return msgpack.unpackb(payload, raw=False)
