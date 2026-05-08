import socket
import threading

from .protocol import unpack_message
from .messages import HELLO


class BotNetListener(threading.Thread):
    def __init__(self, plugin, host="127.0.0.1", port=4557):
        super().__init__(daemon=True)

        self.plugin = plugin
        self.host = host
        self.port = port

        self.sock = None
        self.running = True

    def run(self):
        try:
            self.sock = socket.socket(
                socket.AF_INET,
                socket.SOCK_STREAM
            )

            self.sock.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_REUSEADDR,
                1
            )

            self.sock.bind((self.host, self.port))
            self.sock.listen(5)
            self.sock.settimeout(1)

            self.plugin.log.info(
                f"BotNet listening on {self.host}:{self.port}"
            )

            while self.running:
                try:
                    client, addr = self.sock.accept()

                    self.plugin.log.info(
                        f"Incoming BotNet connection from {addr}"
                    )

                    threading.Thread(
                        target=self.handle_client,
                        args=(client, addr),
                        daemon=True
                    ).start()

                except socket.timeout:
                    continue

                except OSError:
                    break

                except Exception as e:
                    self.plugin.log.error(
                        f"Listener error: {e}"
                    )

        finally:
            try:
                if self.sock:
                    self.sock.close()
            except Exception:
                pass

    def handle_client(self, client, addr):
        peer_key = None

        try:
            client.settimeout(10)

            message = unpack_message(client)

            if not message:
                self.plugin.log.info(
                    f"Empty handshake from {addr}"
                )
                return

            self.plugin.log.info(
                f"Handshake from {addr}: {message}"
            )

            if message.get("type") != HELLO:
                self.plugin.log.info(
                    f"Invalid handshake from {addr}"
                )
                return

            peer_key = message.get("pubkey")

            if not peer_key:
                self.plugin.log.info(
                    f"Missing pubkey from {addr}"
                )
                return

            # reject self
            if peer_key == self.plugin.pubkey:
                self.plugin.log.warning(
                    f"Rejected self-connection from {addr}"
                )
                return

            if not self.plugin.is_trusted(peer_key):
                self.plugin.log.info(
                    f"Rejected untrusted peer {peer_key}"
                )
                return

            self.plugin.log.info(
                f"Accepted trusted peer {peer_key}"
            )

            self.plugin.add_peer(peer_key, client, addr)

            client.settimeout(None)

            while self.running:
                message = unpack_message(client)

                if not message:
                    break

                self.plugin.log.info(
                    f"Message from {peer_key}: {message}"
                )

        except Exception as e:
            self.plugin.log.error(
                f"Client error from {addr}: {e}"
            )

        finally:
            try:
                client.close()
            except Exception:
                pass

            if peer_key:
                self.plugin.remove_peer(peer_key)

            self.plugin.log.info(
                f"Connection closed from {addr}"
            )

    def stop(self):
        self.running = False

        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass

        self.plugin.log.info(
            "BotNet listener stopped"
        )


class BotNetClient:
    def __init__(self, plugin):
        self.plugin = plugin

    def connect(self, host, port):
        sock = socket.socket(
            socket.AF_INET,
            socket.SOCK_STREAM
        )

        sock.settimeout(10)

        sock.connect((host, port))

        self.plugin.log.info(
            f"Connected to BotNet peer {host}:{port}"
        )

        return sock
