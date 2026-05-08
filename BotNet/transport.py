import socket
import threading

from .protocol import pack_message, unpack_message
from .messages import HELLO


class BotNetListener(threading.Thread):
    def __init__(self, plugin, host="0.0.0.0", port=4557):
        super().__init__(daemon=True)

        self.plugin = plugin
        self.host = host
        self.port = port
        self.error = None

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
                        f"BotNet listener error: {e}"
                    )

        except OSError as e:
            self.error = e
            self.plugin.log.error(f"Failed to start listener: {e}")
            if hasattr(self.plugin, '_listener_error'):
                self.plugin._listener_error(e)
        except Exception as e:
            self.error = e
            self.plugin.log.error(f"Unexpected error in listener: {e}")
        finally:
            if self.error:
                self.stop()
            else:
                self.stop()

    def handle_client(self, client, addr):
        peer_key = None
        bot_name = None

        try:
            # STEP 1: read HELLO packet (uses default 60s timeout)
            message = unpack_message(client)

            if not message:
                self.plugin.log.info(f"Empty handshake from {addr}")
                return

            self.plugin.log.info(f"Received handshake from {addr}: {message}")

            if message.get("type") == HELLO:
                peer_key = message.get("pubkey")
                bot_name = message.get("bot_name")

                # STEP 2: AUTH CHECK
                if not self.plugin.is_trusted(peer_key):
                    self.plugin.log.info(
                        f"Rejected untrusted peer {peer_key} from {addr}"
                    )
                    client.close()
                    return

                self.plugin.log.info(
                    f"Accepted trusted peer {bot_name} ({peer_key}) from {addr}"
                )

                # STEP 3: Send acknowledgment
                ack = {
                    "type": "hello_ack",
                    "status": "accepted",
                    "bot_name": self.plugin.irc.nick if hasattr(self.plugin, 'irc') else "unknown",
                    "pubkey": self.plugin.pubkey
                }
                client.sendall(pack_message(ack))
                self.plugin.log.info(f"Sent acknowledgment to {addr}")

                # Store peer in plugin
                from .peer import Peer
                peer = Peer(sock=client, address=addr)
                peer.authenticated = True
                peer.connected = True
                peer.bot_name = bot_name
                peer.pubkey = peer_key
                self.plugin.peers[addr[0]] = peer

            # STEP 4: normal message loop (timeouts handled by unpack_message)
            while self.running:
                try:
                    message = unpack_message(client, timeout=60)
                    
                    if not message:
                        break
                    
                    self.plugin.log.info(
                        f"Message from {bot_name or peer_key or addr}: {message}"
                    )
                    
                except socket.timeout:
                    # No message received within timeout, but connection is still alive
                    # Send a ping to keep alive? (optional)
                    continue
                except Exception as e:
                    self.plugin.log.error(f"Error reading message: {e}")
                    break

        except socket.timeout:
            self.plugin.log.info(f"Handshake timeout from {addr}")
        except Exception as e:
            self.plugin.log.error(
                f"Client handler error from {addr}: {e}"
            )

        finally:
            client.close()
            # Remove from peers if present
            for key, peer in list(self.plugin.peers.items()):
                if peer.address == addr:
                    del self.plugin.peers[key]
                    break
            self.plugin.log.info(f"Connection closed from {addr}")

    def stop(self):
        self.running = False

        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass

        self.plugin.log.info("BotNet listener stopped")


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
