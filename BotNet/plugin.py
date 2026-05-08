###
# Copyright (c) 2026, Stathis Xantinidis spithash@Libera
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#   * Redistributions of source code must retain the above copyright notice,
#     this list of conditions, and the following disclaimer.
#   * Redistributions in binary form must reproduce the above copyright notice,
#     this list of conditions, and the following disclaimer in the
#     documentation and/or other materials provided with the distribution.
#   * Neither the name of the author of this software nor the name of
#     contributors to this software may be used to endorse or promote products
#     derived from this software without specific prior written consent.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

###

import socket
import threading
import traceback
import json
import os
from supybot import ircdb, conf
from supybot import callbacks
from supybot.commands import wrap
from supybot import ircmsgs

from .crypto import load_identity, get_public_key_hex
from .transport import BotNetListener, BotNetClient
from .protocol import pack_message, unpack_message
from .messages import HELLO, PROTOCOL_VERSION
from .peer import Peer


class BotNet(callbacks.Plugin):
    threaded = True

    def __init__(self, irc):
        super().__init__(irc)
        
        # Store irc reference for callbacks
        self.irc = irc

        # identity system
        self.identity = load_identity()
        self.pubkey = get_public_key_hex(self.identity)

        # runtime state
        self.listener = None
        self.peers = {}

        # TRUSTED PEERS with persistence
        self.trusted_file = conf.supybot.directories.data.dirize("BotNet_trusted.json")
        self.trusted_peers = self._load_trusted_peers()

        self.log.info(f"BotNet identity: {self.pubkey}")
        self.log.info(f"Loaded {len(self.trusted_peers)} trusted peers")

    def die(self):
        """Called when plugin unloads."""
        self._save_trusted_peers()  # Save on unload
        
        if self.listener:
            self.listener.stop()
            self.listener = None

        for peer in self.peers.values():
            try:
                if peer.sock:
                    peer.sock.close()
            except:
                pass
        self.peers.clear()

        super().die()

    def _load_trusted_peers(self):
        """Load trusted peers from disk."""
        if os.path.exists(self.trusted_file):
            try:
                with open(self.trusted_file, 'r') as f:
                    data = json.load(f)
                    return set(data.get('trusted_peers', []))
            except Exception as e:
                self.log.error(f"Failed to load trusted peers: {e}")
        return set()

    def _save_trusted_peers(self):
        """Save trusted peers to disk."""
        try:
            data = {
                'trusted_peers': list(self.trusted_peers)
            }
            with open(self.trusted_file, 'w') as f:
                json.dump(data, f, indent=2)
            self.log.info(f"Saved {len(self.trusted_peers)} trusted peers")
        except Exception as e:
            self.log.error(f"Failed to save trusted peers: {e}")

    def _notice(self, irc, msg, text):
        """Send a notice to the user."""
        irc.sendMsg(ircmsgs.notice(msg.nick, text))

    def _check_owner(self, irc, msg):
        """Check if user has owner capability."""
        if not ircdb.checkCapability(msg.prefix, 'owner'):
            self._notice(irc, msg, "Permission denied.")
            return False
        return True

    def _listener_error(self, error):
        """Called by listener thread when it fails to start."""
        error_msg = str(error)
        self.log.error(f"Listener failed to start: {error_msg}")
        self.listener = None

    def _receive_loop(self, sock, host, port, irc):
        """Receive messages from a connected peer."""
        self.log.info(f"Starting receive loop for {host}:{port}")
        
        while True:
            try:
                # unpack_message now handles timeout internally (default 60s)
                message = unpack_message(sock, timeout=60)
                
                if not message:
                    self.log.info(f"Peer {host}:{port} disconnected")
                    break
                
                self.log.info(f"Received from {host}:{port}: {message}")
                
                # Handle different message types
                msg_type = message.get("type")
                if msg_type == "partyline":
                    text = message.get("text", "")
                    self.log.info(f"Partyline message from {host}: {text}")
                    # Here you could forward to IRC channel or notice the user
                    
            except socket.timeout:
                # No message received in 60 seconds, connection is still alive
                # This is normal for idle connections
                continue
            except Exception as e:
                self.log.error(f"Error in receive loop for {host}:{port}: {e}")
                break
        
        # Clean up on disconnect
        if host in self.peers:
            del self.peers[host]
        try:
            sock.close()
        except:
            pass
        self.log.info(f"Connection to {host}:{port} closed")

    def is_trusted(self, pubkey):
        """Check if a public key is trusted."""
        return pubkey in self.trusted_peers

    def trust(self, irc, msg, args, pubkey):
        """<pubkey> -- Trust a remote bot's public key."""
        if not self._check_owner(irc, msg):
            return

        self.trusted_peers.add(pubkey)
        self._save_trusted_peers()
        self._notice(irc, msg, f"Trusted peer added: {pubkey}")
        self.log.info(f"Trusted peer added: {pubkey}")
    trust = wrap(trust, ['text'])

    def untrust(self, irc, msg, args, pubkey):
        """<pubkey> -- Remove trust from a remote bot's public key."""
        if not self._check_owner(irc, msg):
            return

        if pubkey in self.trusted_peers:
            self.trusted_peers.remove(pubkey)
            self._save_trusted_peers()
            self._notice(irc, msg, f"Trusted peer removed: {pubkey}")
            self.log.info(f"Trusted peer removed: {pubkey}")
        else:
            self._notice(irc, msg, f"Pubkey not in trusted list: {pubkey}")
    untrust = wrap(untrust, ['text'])

    def list_trusted(self, irc, msg, args):
        """List all trusted peers."""
        if not self._check_owner(irc, msg):
            return

        if not self.trusted_peers:
            self._notice(irc, msg, "No trusted peers.")
            return

        for pubkey in self.trusted_peers:
            self._notice(irc, msg, f"Trusted: {pubkey}")
    list_trusted = wrap(list_trusted)

    def listen(self, irc, msg, args, port):
        """<port> -- Start BotNet listener on specified port."""
        if not self._check_owner(irc, msg):
            return

        if self.listener:
            self._notice(irc, msg, "Already listening.")
            return

        try:
            self.listener = BotNetListener(self, host="0.0.0.0", port=port)
            self.listener.start()
            
            def check_listener():
                import time
                time.sleep(0.1)
                if self.listener and self.listener.error:
                    self._notice(irc, msg, f"❌ Failed to start listener: {self.listener.error}")
                    self.listener = None
                else:
                    self._notice(irc, msg, f"✅ Listening on port {port}")
            
            threading.Thread(target=check_listener, daemon=True).start()
            
            self.log.info(f"Started listener on port {port}")
        except Exception as e:
            self._notice(irc, msg, f"❌ Failed to start listener: {e}")
            self.log.error(f"Failed to start listener: {e}")
    listen = wrap(listen, ['int'])

    def stop(self, irc, msg, args):
        """Stop the BotNet listener."""
        if not self._check_owner(irc, msg):
            return

        if not self.listener:
            self._notice(irc, msg, "Listener is not running.")
            return

        self.listener.stop()
        self.listener = None
        self._notice(irc, msg, "✅ Listener stopped.")
        self.log.info("Listener stopped")
    stop = wrap(stop)

    def connect(self, irc, msg, args, hostport):
        """<host:port> -- Connect to a BotNet peer (example: 100.113.68.117:4557)."""
        
        # Parse host:port format
        try:
            if ':' not in hostport:
                self._notice(irc, msg, "❌ Usage: host:port (example: 100.113.68.117:4557)")
                return
            
            parts = hostport.rsplit(':', 1)
            host = parts[0]
            port = int(parts[1])
        except ValueError:
            self._notice(irc, msg, "❌ Invalid port number")
            return
        
        self._notice(irc, msg, f"Connecting to {host}:{port}...")
        self.log.info(f"connect() called with {host}:{port}")

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # Don't set timeout here, let the protocol handle it
            sock.connect((host, port))
            
            self._notice(irc, msg, "TCP connected!")
            self.log.info(f"TCP connected to {host}:{port}")

            hello = {
                "protocol": PROTOCOL_VERSION,
                "type": HELLO,
                "bot_name": str(irc.nick),
                "pubkey": self.pubkey,
            }
            
            sock.sendall(pack_message(hello))
            self.log.info("HELLO sent")

            # Wait for response with 30 second timeout
            response = unpack_message(sock, timeout=30)
            
            if not response:
                self._notice(irc, msg, "ERROR: No response from peer")
                sock.close()
                return

            self.log.info(f"Response: {response}")

            if response.get("type") == "hello_ack" and response.get("status") == "accepted":
                peer = Peer(sock=sock, address=(host, port))
                peer.authenticated = True
                peer.connected = True
                peer.bot_name = response.get("bot_name", "unknown")
                peer.pubkey = response.get("pubkey", "unknown")
                self.peers[host] = peer

                # Start receive thread for this connection
                receive_thread = threading.Thread(
                    target=self._receive_loop,
                    args=(sock, host, port, irc),
                    daemon=True
                )
                receive_thread.start()

                self._notice(irc, msg, f"✅ Connected and authenticated to {host}:{port}")
                self.log.info(f"Successfully connected to peer {host}:{port}")
            else:
                self._notice(irc, msg, f"❌ Connection rejected by peer")
                sock.close()

        except socket.timeout:
            self._notice(irc, msg, "❌ Connection TIMEOUT - peer not responding")
            self.log.error(f"Connection timeout to {host}:{port}")
        except ConnectionRefusedError:
            self._notice(irc, msg, "❌ Connection REFUSED - nothing listening on that port")
            self.log.error(f"Connection refused to {host}:{port}")
        except Exception as e:
            self._notice(irc, msg, f"❌ Connection FAILED: {str(e)}")
            self.log.error(f"Connect error: {e}\n{traceback.format_exc()}")
    connect = wrap(connect, ['text'])

    def send(self, irc, msg, args, host, port, text):
        """<host> <port> <text> -- Send a message to a connected peer."""
        if host in self.peers:
            peer = self.peers[host]
            message = {"type": "partyline", "text": text, "target": msg.channel}
            try:
                peer.sock.sendall(pack_message(message))
                self._notice(irc, msg, f"Sent to {host}")
            except Exception as e:
                self._notice(irc, msg, f"Send failed: {e}")
        else:
            self._notice(irc, msg, f"No connection to {host}")
    send = wrap(send, ['text', 'int', 'text'])


Class = BotNet


# vim:set shiftwidth=4 tabstop=4 expandtab textwidth=79:
