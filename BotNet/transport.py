import socket
import threading
import time

from .protocol import pack_message, unpack_message, create_handshake_message, create_handshake_ack
from .messages import HELLO, PING, PONG, BROADCAST, PARTYLINE_CMD, PARTYLINE_MSG, STATUS_QUERY, STATUS_RESPONSE, PROTOCOL_VERSION
from .crypto import EncryptionManager


class BotNetListener(threading.Thread):
    def __init__(self, plugin, host="0.0.0.0", port=4557):
        super().__init__(daemon=True)
        self.plugin = plugin
        self.host = host
        self.port = port
        self.error = None
        self.sock = None
        self.running = True
        self.active_handlers = []

    def run(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind((self.host, self.port))
            self.sock.listen(5)
            self.sock.settimeout(1)

            self.plugin.log.info(f"BotNet encrypted listener on {self.host}:{self.port}")

            while self.running:
                try:
                    client, addr = self.sock.accept()
                    self.plugin.log.info(f"Incoming connection from {addr[0]}:{addr[1]}")
                    
                    handler = threading.Thread(
                        target=self.handle_client,
                        args=(client, addr),
                        daemon=True
                    )
                    handler.start()
                    self.active_handlers.append(handler)
                    
                    # Cleanup finished handlers
                    self.active_handlers = [h for h in self.active_handlers if h.is_alive()]

                except socket.timeout:
                    continue
                except OSError:
                    break
                except Exception as e:
                    self.plugin.log.error(f"Listener error: {e}")

        except OSError as e:
            self.error = e
            self.plugin.log.error(f"Failed to start listener: {e}")
            if hasattr(self.plugin, '_listener_error'):
                self.plugin._listener_error(e)
        except Exception as e:
            self.error = e
            self.plugin.log.error(f"Unexpected error in listener: {e}")
        finally:
            self.stop()

    def handle_client(self, client, addr):
        peer_signing_pubkey = None
        peer_bot_name = None
        encryption_manager = None
        
        try:
            # Step 1: Receive HELLO (plaintext)
            client.settimeout(30)
            message = unpack_message(client, timeout=30)
            
            if not message or message.get("type") != HELLO:
                self.plugin.log.info(f"Invalid handshake from {addr}")
                return
            
            peer_signing_pubkey = message.get("pubkey_signing")
            peer_encryption_pubkey = message.get("pubkey_encryption")
            peer_bot_name = message.get("bot_name")
            
            # Step 2: Check if trusted
            if not self.plugin.is_trusted(peer_signing_pubkey):
                self.plugin.log.info(f"Rejected untrusted peer {peer_signing_pubkey[:16]}... from {addr}")
                client.close()
                return
            
            self.plugin.log.info(f"Accepted trusted peer {peer_bot_name} ({peer_signing_pubkey[:16]}...) from {addr}")
            
            # Update peer info with host/port if missing
            if peer_signing_pubkey in self.plugin.trusted_peers:
                if not self.plugin.trusted_peers[peer_signing_pubkey].get('host'):
                    self.plugin.trusted_peers[peer_signing_pubkey]['host'] = addr[0]
                    self.plugin.trusted_peers[peer_signing_pubkey]['port'] = addr[1]
                    self.plugin.trusted_peers[peer_signing_pubkey]['bot_name'] = peer_bot_name
                    self.plugin._save_trusted_peers()
            
            # Step 3: Create encryption manager for this peer
            encryption_manager = EncryptionManager(self.plugin.encryption_private_key)
            encryption_manager.add_peer(peer_signing_pubkey, peer_encryption_pubkey)
            
            # Step 4: Send ACK (plaintext for handshake completion)
            ack = create_handshake_ack(
                status="accepted",
                bot_name=self.plugin.irc.nick,
                pubkey_signing=self.plugin.pubkey_signing,
                pubkey_encryption=self.plugin.encryption_public_key_hex
            )
            client.sendall(pack_message(ack))
            
            self.plugin.log.info(f"Sent encrypted handshake ACK to {peer_bot_name}")
            
            # Step 5: Create peer object
            from .peer import Peer
            peer = Peer(sock=client, address=addr)
            peer.authenticated = True
            peer.connected = True
            peer.bot_name = peer_bot_name
            peer.pubkey_signing = peer_signing_pubkey
            peer.encryption_manager = encryption_manager
            peer.last_pong = time.time()
            
            # Store by signing pubkey
            self.plugin.peers[peer_signing_pubkey] = peer
            
            # Notify partyline about new peer
            self.plugin._notify_partyline_peer_joined(peer_bot_name, peer_signing_pubkey)
            
            # Step 6: Start encrypted receive loop
            self._receive_loop(client, peer, encryption_manager)
            
        except socket.timeout:
            self.plugin.log.info(f"Handshake timeout from {addr}")
        except Exception as e:
            self.plugin.log.error(f"Client handler error from {addr}: {e}")
        finally:
            client.close()
            if peer_signing_pubkey and peer_signing_pubkey in self.plugin.peers:
                # Notify partyline about peer leaving
                if peer_bot_name:
                    self.plugin._notify_partyline_peer_left(peer_bot_name)
                del self.plugin.peers[peer_signing_pubkey]
            self.plugin.log.info(f"Connection closed from {addr}")

    def _receive_loop(self, sock, peer, encryption_manager):
        """Receive encrypted messages from connected peer"""
        heartbeat_timeout = self.plugin.registryValue('heartbeatTimeout', self.plugin.irc.network)
        
        while self.running and peer.pubkey_signing in self.plugin.peers:
            try:
                # Use peer's encryption box for decryption
                message = unpack_message(sock, decryption_box=encryption_manager.peer_boxes.get(peer.pubkey_signing), timeout=50)
                
                if not message:
                    self.plugin.log.info(f"Peer {peer.bot_name} disconnected")
                    break
                
                msg_type = message.get("type")
                
                if msg_type == PING:
                    # Respond to ping with encrypted PONG
                    pong = {"type": PONG, "timestamp": message.get("timestamp", time.time())}
                    encrypted_pong = pack_message(pong, encryption_box=encryption_manager.peer_boxes.get(peer.pubkey_signing))
                    sock.sendall(encrypted_pong)
                    peer.last_pong = time.time()
                    
                elif msg_type == PONG:
                    peer.last_pong = time.time()
                    
                elif msg_type == BROADCAST:
                    self.plugin.handle_broadcast(message, from_peer=peer.pubkey_signing)
                    
                elif msg_type == PARTYLINE_CMD:
                    # Handle partyline command from peer
                    self.plugin.handle_partyline_command(message, peer)
                    
                elif msg_type == PARTYLINE_MSG:
                    # Handle partyline message from peer (for wholine)
                    self.plugin.handle_partyline_message(message, peer)
                    
                elif msg_type == STATUS_QUERY:
                    # Respond with status
                    status_response = self.plugin.get_status_response()
                    encrypted_response = pack_message(status_response, encryption_box=encryption_manager.peer_boxes.get(peer.pubkey_signing))
                    sock.sendall(encrypted_response)
                    
                else:
                    self.plugin.log.debug(f"Unknown message type {msg_type} from {peer.bot_name}")
                    
            except socket.timeout:
                # Check if peer is still alive
                if time.time() - peer.last_pong > heartbeat_timeout:
                    self.plugin.log.warning(f"Peer {peer.bot_name} timed out (no PONG for {heartbeat_timeout}s)")
                    break
                continue
            except Exception as e:
                self.plugin.log.error(f"Error in receive loop from {peer.bot_name}: {e}")
                break

    def stop(self):
        self.running = False
        for handler in self.active_handlers:
            if handler.is_alive():
                handler.join(timeout=1)
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
        self.plugin.log.info("BotNet listener stopped")


class BotNetClient:
    def __init__(self, plugin):
        self.plugin = plugin

    def connect(self, host, port, peer_signing_pubkey=None):
        """Connect to a peer with encrypted handshake"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        
        try:
            sock.connect((host, port))
            self.plugin.log.info(f"Connected to {host}:{port}")
            
            # Send HELLO (plaintext)
            hello = create_handshake_message(
                bot_name=self.plugin.irc.nick,
                pubkey_signing=self.plugin.pubkey_signing,
                pubkey_encryption=self.plugin.encryption_public_key_hex,
                protocol_version=PROTOCOL_VERSION
            )
            sock.sendall(pack_message(hello))
            
            # Wait for ACK (plaintext)
            sock.settimeout(30)
            response = unpack_message(sock, timeout=30)
            
            if response and response.get("type") == "hello_ack" and response.get("status") == "accepted":
                peer_signing = response.get("pubkey_signing")
                peer_encryption = response.get("pubkey_encryption")
                peer_bot_name = response.get("bot_name")
                
                # Check if trusted (if pubkey provided, verify it matches)
                if not self.plugin.is_trusted(peer_signing):
                    self.plugin.log.info(f"Peer {peer_signing[:16]}... not trusted, disconnecting")
                    sock.close()
                    return False
                
                # Update peer info
                if peer_signing in self.plugin.trusted_peers:
                    if not self.plugin.trusted_peers[peer_signing].get('host'):
                        self.plugin.trusted_peers[peer_signing]['host'] = host
                        self.plugin.trusted_peers[peer_signing]['port'] = port
                        self.plugin.trusted_peers[peer_signing]['bot_name'] = peer_bot_name
                        self.plugin._save_trusted_peers()
                
                # Create encryption manager
                encryption_manager = EncryptionManager(self.plugin.encryption_private_key)
                encryption_manager.add_peer(peer_signing, peer_encryption)
                
                from .peer import Peer
                peer = Peer(sock=sock, address=(host, port))
                peer.authenticated = True
                peer.connected = True
                peer.bot_name = peer_bot_name
                peer.pubkey_signing = peer_signing
                peer.encryption_manager = encryption_manager
                peer.last_pong = time.time()
                
                # Store by signing pubkey
                self.plugin.peers[peer_signing] = peer
                
                # Notify partyline
                self.plugin._notify_partyline_peer_joined(peer_bot_name, peer_signing)
                
                # Start encrypted receive thread
                receive_thread = threading.Thread(
                    target=self._receive_loop,
                    args=(sock, peer, encryption_manager),
                    daemon=True
                )
                receive_thread.start()
                
                self.plugin.log.info(f"Successfully connected and encrypted with {peer_bot_name}")
                return True
            else:
                self.plugin.log.info(f"Connection rejected by peer")
                sock.close()
                return False
                
        except Exception as e:
            self.plugin.log.error(f"Connection failed to {host}:{port}: {e}")
            sock.close()
            return False

    def _receive_loop(self, sock, peer, encryption_manager):
        """Receive encrypted messages"""
        heartbeat_timeout = self.plugin.registryValue('heartbeatTimeout', self.plugin.irc.network)
        
        while peer.pubkey_signing in self.plugin.peers:
            try:
                message = unpack_message(sock, decryption_box=encryption_manager.peer_boxes.get(peer.pubkey_signing), timeout=50)
                
                if not message:
                    break
                
                msg_type = message.get("type")
                
                if msg_type == PING:
                    pong = {"type": PONG, "timestamp": message.get("timestamp", time.time())}
                    encrypted_pong = pack_message(pong, encryption_box=encryption_manager.peer_boxes.get(peer.pubkey_signing))
                    sock.sendall(encrypted_pong)
                    peer.last_pong = time.time()
                elif msg_type == PONG:
                    peer.last_pong = time.time()
                elif msg_type == BROADCAST:
                    self.plugin.handle_broadcast(message, from_peer=peer.pubkey_signing)
                elif msg_type == PARTYLINE_CMD:
                    self.plugin.handle_partyline_command(message, peer)
                elif msg_type == PARTYLINE_MSG:
                    self.plugin.handle_partyline_message(message, peer)
                    
            except socket.timeout:
                if time.time() - peer.last_pong > heartbeat_timeout:
                    break
                continue
            except Exception as e:
                self.plugin.log.error(f"Receive loop error for {peer.bot_name}: {e}")
                break
        
        # Clean up on disconnect
        if peer.pubkey_signing in self.plugin.peers:
            self.plugin._notify_partyline_peer_left(peer.bot_name)
            del self.plugin.peers[peer.pubkey_signing]
