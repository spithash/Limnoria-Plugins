import socket
import threading

from .protocol import pack_message, unpack_message
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

            # prevents accept() blocking forever
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
                        f"BotNet listener crashed: {e}"
                    )

        finally:
            self.stop()

    def handle_client(self, client, addr):
        peer_key = None

        try:
            # -----------------------------
            # STEP 1: read HELLO packet
            # -----------------------------
            message = unpack_message(client)

            if not message:
                return

            self.plugin.log.info(
                f"Received handshake from {addr}: {message}"
            )

            if message.get("type") == HELLO:
                peer_key = message.get("pubkey")

                # -----------------------------
                # STEP 2: AUTH CHECK
                # -----------------------------
                if not self.plugin.is_trusted(peer_key):
                    self.plugin.log.info(
                        f"Rejected untrusted peer {peer_key} from {addr}"
                    )
                    client.close()
                    return

                self.plugin.log.info(
                    f"Accepted trusted peer {peer_key} from {addr}"
                )

            # -----------------------------
            # STEP 3: normal message loop
            # -----------------------------
            while self.running:
                message = unpack_message(client)

                if not message:
                    break

                self.plugin.log.info(
                    f"Message from {peer_key or addr}: {message}"
                )

        except Exception as e:
            self.plugin.log.error(
                f"Client handler error from {addr}: {e}"
            )

        finally:
            client.close()

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

        sock.connect((host, port))

        self.plugin.log.info(
            f"Connected to BotNet peer {host}:{port}"
        )

        return sock
