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
import time
from collections import deque
from supybot import ircdb, conf, ircmsgs, ircutils, world
from supybot import callbacks
from supybot.commands import wrap, optional

from .crypto import (
    load_identity, 
    load_encryption_key, 
    get_public_key_hex, 
    get_encryption_public_key_hex,
    EncryptionManager,
    get_botnet_dir
)
from .transport import BotNetListener, BotNetClient
from .protocol import pack_message, unpack_message
from .messages import (
    HELLO, PING, PONG, BROADCAST,
    STATUS_QUERY, STATUS_RESPONSE, PROTOCOL_VERSION, MessageIDGenerator
)
from .peer import Peer


class BotNet(callbacks.Plugin):
    threaded = True

    def __init__(self, irc):
        super().__init__(irc)
        
        # Store irc reference for callbacks
        self.irc = irc

        # Get BotNet data directory
        self.data_dir = get_botnet_dir()
        
        # Load identity system (Ed25519 signing keys)
        self.identity = load_identity()
        self.pubkey_signing = get_public_key_hex(self.identity)
        
        # Load encryption system (X25519 encryption keys)
        self.encryption_private_key = load_encryption_key()
        self.encryption_public_key_hex = get_encryption_public_key_hex(self.encryption_private_key)

        # Runtime state
        self.listener = None
        self.peers = {}  # pubkey_signing -> Peer object

        # Botnet membership (which botnets this bot belongs to)
        self.my_botnets = {"Nest"}  # Default botnet is always Nest
        
        # Message flooding state
        self.seen_messages = set()  # For flood deduplication
        self.message_cache_max = self.registryValue('messageCacheSize')
        self.msg_id_gen = MessageIDGenerator(self.pubkey_signing)
        
        # Message buffer for partyline
        self.message_buffer = deque(maxlen=self.registryValue('partylineBufferSize'))
        
        # Track users in partyline mode (nick -> session data)
        self.partyline_users = {}  # nick -> {'last_activity': time, 'current_botnet': str}
        
        # Heartbeat control
        self.heartbeat_running = True
        self.heartbeat_thread = threading.Thread(target=self._heartbeat_sender, daemon=True)
        self.heartbeat_thread.start()
        
        # Retry state for reconnection
        self.reconnect_retries = {}  # pubkey -> retry_count
        self.start_time = time.time()

        # TRUSTED PEERS with persistence
        self.trusted_file = os.path.join(self.data_dir, "trusted_peers.json")
        self.trusted_peers = self._load_trusted_peers()  # pubkey -> {bot_name, host, port, botnets}

        self.log.info(f"BotNet data directory: {self.data_dir}")
        self.log.info(f"BotNet signing key: {self.pubkey_signing}")
        self.log.info(f"BotNet encryption key: {self.encryption_public_key_hex}")
        self.log.info(f"Loaded {len(self.trusted_peers)} trusted peers")
        
        # Attempt to reconnect to all trusted peers on startup if configured
        if self.registryValue('autoReconnect'):
            self._schedule_reconnect_all()

    def die(self):
        """Called when plugin unloads."""
        self.heartbeat_running = False
        
        # Clear partyline users
        self.partyline_users.clear()
        
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
                    # Migrate old format if needed
                    trusted = data.get('trusted_peers', {})
                    if isinstance(trusted, list):
                        # Old format (list of pubkeys), convert to new format
                        new_trusted = {}
                        for pubkey in trusted:
                            new_trusted[pubkey] = {
                                'bot_name': 'unknown',
                                'host': None,
                                'port': None,
                                'botnets': ['Nest']
                            }
                        return new_trusted
                    return trusted
            except Exception as e:
                self.log.error(f"Failed to load trusted peers: {e}")
        return {}

    def _save_trusted_peers(self):
        """Save trusted peers to disk."""
        try:
            data = {
                'trusted_peers': self.trusted_peers
            }
            with open(self.trusted_file, 'w') as f:
                json.dump(data, f, indent=2)
            self.log.info(f"Saved {len(self.trusted_peers)} trusted peers")
        except Exception as e:
            self.log.error(f"Failed to save trusted peers: {e}")

    def _notice(self, irc, msg, text):
        """Send a notice to the user."""
        irc.sendMsg(ircmsgs.notice(msg.nick, text))

    def _reply(self, irc, msg, text, private=False):
        """Send a reply to the user."""
        if private:
            irc.reply(text, private=True)
        else:
            self._notice(irc, msg, text)

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

    def _schedule_reconnect_all(self):
        """Attempt to reconnect to all trusted peers asynchronously"""
        def reconnect_task():
            max_attempts = self.registryValue('maxReconnectAttempts')
            for pubkey, peer_info in self.trusted_peers.items():
                if pubkey not in self.peers and peer_info.get('host') and peer_info.get('port'):
                    self._attempt_reconnect(pubkey, peer_info)
        
        threading.Thread(target=reconnect_task, daemon=True).start()

    def _attempt_reconnect(self, pubkey, peer_info, retry_delay=None):
        """Attempt to reconnect with retry logic"""
        if pubkey in self.peers:
            return  # Already connected
        
        if retry_delay is None:
            retry_delay = self.registryValue('reconnectDelay')
        
        max_attempts = self.registryValue('maxReconnectAttempts')
        retries = self.reconnect_retries.get(pubkey, 0)
        
        if retries >= max_attempts:
            self.log.error(f"Failed to reconnect to {peer_info.get('bot_name', pubkey[:16])} after {max_attempts} attempts, giving up")
            return
        
        try:
            client = BotNetClient(self)
            success = client.connect(
                peer_info['host'], 
                peer_info['port'], 
                pubkey
            )
            if success:
                self.log.info(f"Reconnected to {peer_info.get('bot_name', pubkey[:16])}")
                self.reconnect_retries[pubkey] = 0
                if pubkey in self.peers:
                    self.peers[pubkey].connected = True
            else:
                self.reconnect_retries[pubkey] = retries + 1
                # Schedule retry
                threading.Timer(retry_delay, self._attempt_reconnect, args=(pubkey, peer_info, retry_delay)).start()
        except Exception as e:
            self.log.error(f"Reconnect error for {peer_info.get('bot_name', pubkey[:16])}: {e}")
            self.reconnect_retries[pubkey] = retries + 1
            threading.Timer(retry_delay, self._attempt_reconnect, args=(pubkey, peer_info, retry_delay)).start()

    def _heartbeat_sender(self):
        """Send PING to all connected peers every 60 seconds"""
        interval = self.registryValue('heartbeatInterval')
        while self.heartbeat_running:
            time.sleep(interval)
            for pubkey, peer in list(self.peers.items()):
                if peer.connected and peer.sock and peer.encryption_manager:
                    try:
                        ping = {"type": PING, "timestamp": time.time()}
                        encryption_box = peer.encryption_manager.peer_boxes.get(pubkey)
                        if encryption_box:
                            peer.sock.sendall(pack_message(ping, encryption_box=encryption_box))
                            self.log.debug(f"PING sent to {peer.bot_name}")
                    except Exception as e:
                        self.log.error(f"Failed to send PING to {peer.bot_name}: {e}")
                        peer.connected = False

    def _peer_in_botnet(self, pubkey, botnet_name):
        """Check if a trusted peer belongs to a botnet"""
        if pubkey in self.trusted_peers:
            botnets = self.trusted_peers[pubkey].get('botnets', ['Nest'])
            return botnet_name in botnets
        return False

    def is_trusted(self, pubkey):
        """Check if a public key is trusted."""
        return pubkey in self.trusted_peers

    def get_recent_messages(self, limit=100):
        """Get recent messages from buffer"""
        return list(self.message_buffer)[-limit:]

    def _add_to_message_buffer(self, botnet, sender, content):
        """Add a message to the buffer and notify partyline users"""
        msg = {
            'timestamp': time.time(),
            'botnet': botnet,
            'sender': sender,
            'content': content
        }
        self.message_buffer.append(msg)
        
        # Notify all active partyline users via PM
        for nick in list(self.partyline_users.keys()):
            try:
                self.irc.sendMsg(ircmsgs.privmsg(nick, f"[{botnet}] {sender}: {content}"))
            except Exception as e:
                self.log.error(f"Failed to send partyline message to {nick}: {e}")

    def _notify_partyline_peer_joined(self, bot_name):
        """Notify partyline users about a new peer"""
        for user_nick in list(self.partyline_users.keys()):
            try:
                self.irc.sendMsg(ircmsgs.privmsg(user_nick, f"*** {bot_name} has joined the botnet"))
            except Exception as e:
                self.log.error(f"Failed to send join notification to {user_nick}: {e}")

    def _notify_partyline_peer_left(self, bot_name):
        """Notify partyline users about a peer leaving"""
        for user_nick in list(self.partyline_users.keys()):
            try:
                self.irc.sendMsg(ircmsgs.privmsg(user_nick, f"*** {bot_name} has left the botnet"))
            except Exception as e:
                self.log.error(f"Failed to send leave notification to {user_nick}: {e}")

    def handle_broadcast(self, message, from_peer):
        """Handle incoming broadcast with flood prevention"""
        msg_id = message.get("msg_id")
        
        # Check if we've seen this message
        if msg_id in self.seen_messages:
            self.log.debug(f"Duplicate broadcast {msg_id}, ignoring")
            return
        
        # Add to seen cache (maintain size limit)
        self.seen_messages.add(msg_id)
        if len(self.seen_messages) > self.message_cache_max:
            # Remove oldest 200 messages
            self.seen_messages = set(list(self.seen_messages)[-800:])
        
        target_botnet = message.get("botnet")
        content = message.get("content")
        sender_botname = message.get("sender_botname")
        ttl = message.get("ttl", 10)
        
        # Check if we should display this message (if we're in this botnet)
        if target_botnet in self.my_botnets:
            self.log.info(f"[{target_botnet}] {sender_botname}: {content}")
            self._add_to_message_buffer(target_botnet, sender_botname, content)
        
        # Forward to other peers if TTL > 0
        if ttl > 0 and target_botnet in self.my_botnets:
            message["ttl"] = ttl - 1
            for pubkey, peer in self.peers.items():
                if pubkey != from_peer and peer.connected:
                    try:
                        # Only forward to peers in the target botnet
                        if self._peer_in_botnet(pubkey, target_botnet):
                            encryption_box = peer.encryption_manager.peer_boxes.get(pubkey)
                            if encryption_box:
                                peer.sock.sendall(pack_message(message, encryption_box=encryption_box))
                                self.log.debug(f"Forwarded broadcast to {peer.bot_name}")
                    except Exception as e:
                        self.log.error(f"Failed to forward broadcast to {peer.bot_name}: {e}")

    def broadcast(self, botnet_name, message_text):
        """Send a broadcast message to a botnet"""
        if botnet_name not in self.my_botnets:
            self.log.error(f"Cannot broadcast to {botnet_name}: not a member")
            return False
        
        msg_id = self.msg_id_gen.next()
        broadcast_msg = {
            "type": BROADCAST,
            "msg_id": msg_id,
            "sender_pubkey": self.pubkey_signing,
            "sender_botname": self.irc.nick,
            "botnet": botnet_name,
            "content": message_text,
            "ttl": self.registryValue('maxTTL'),
            "timestamp": time.time()
        }
        
        # Add to seen messages (so we don't process our own broadcast)
        self.seen_messages.add(msg_id)
        
        # Add to local buffer
        self._add_to_message_buffer(botnet_name, self.irc.nick, message_text)
        
        # Send to all connected peers in this botnet
        count = 0
        for pubkey, peer in self.peers.items():
            if peer.connected and self._peer_in_botnet(pubkey, botnet_name):
                try:
                    encryption_box = peer.encryption_manager.peer_boxes.get(pubkey)
                    if encryption_box:
                        peer.sock.sendall(pack_message(broadcast_msg, encryption_box=encryption_box))
                        count += 1
                except Exception as e:
                    self.log.error(f"Failed to send broadcast to {peer.bot_name}: {e}")
        
        self.log.info(f"Broadcast to '{botnet_name}' sent to {count} peers")
        return True

    def get_status_response(self):
        """Generate status response for STATUS_QUERY"""
        return {
            "type": STATUS_RESPONSE,
            "bot_name": self.irc.nick,
            "pubkey_signing": self.pubkey_signing,
            "connected_peers": len(self.peers),
            "trusted_peers": len(self.trusted_peers),
            "botnets": list(self.my_botnets),
            "uptime": time.time() - self.start_time
        }

    # Partyline PM Interface Methods
    def _send_partyline_welcome(self, nick):
        """Send welcome message to user entering partyline"""
        welcome = [
            "\x02=== BotNet Partyline ===\x02",
            f"Connected to: {self.irc.nick}",
            f"Your botnets: {', '.join(self.my_botnets)}",
            f"Peers online: {len(self.peers)}",
            "",
            "\x02Commands:\x02",
            "  .broadcast <botnet> <msg>  - Send message to botnet",
            "  .b <botnet> <msg>          - Shortcut for broadcast",
            "  .who                       - Show online users",
            "  .map                       - Show mesh topology tree",
            "  .quit                      - Exit partyline",
            "",
            f"\x02Default botnet: {self.partyline_users.get(nick, {}).get('current_botnet', 'Nest')}\x02",
            "Just type a message to broadcast to the current botnet",
            "-" * 40
        ]
        for line in welcome:
            self.irc.sendMsg(ircmsgs.privmsg(nick, line))
        
        # Send recent messages
        recent = self.get_recent_messages(limit=10)
        if recent:
            self.irc.sendMsg(ircmsgs.privmsg(nick, ""))
            self.irc.sendMsg(ircmsgs.privmsg(nick, "\x02Recent messages:\x02"))
            for msg in recent:
                self.irc.sendMsg(ircmsgs.privmsg(nick, f"[{msg['botnet']}] {msg['sender']}: {msg['content']}"))
            self.irc.sendMsg(ircmsgs.privmsg(nick, "-" * 40))

    def _handle_partyline_command(self, nick, cmd, args):
        """Handle partyline commands from PM"""
        session = self.partyline_users.get(nick, {})
        current_botnet = session.get('current_botnet', 'Nest')
        
        if cmd == 'broadcast' or cmd == 'b':
            if args:
                parts = args.split(' ', 1)
                if len(parts) == 2:
                    botnet, message = parts
                    if botnet in self.my_botnets:
                        self.broadcast(botnet, message)
                        self.irc.sendMsg(ircmsgs.privmsg(nick, f"[{botnet}] You: {message}"))
                    else:
                        self.irc.sendMsg(ircmsgs.privmsg(nick, f"\x02Error: Not in botnet '{botnet}'\x02"))
                        self.irc.sendMsg(ircmsgs.privmsg(nick, f"Your botnets: {', '.join(self.my_botnets)}"))
                else:
                    self.irc.sendMsg(ircmsgs.privmsg(nick, "\x02Usage: .broadcast <botnet> <message>\x02"))
            else:
                self.irc.sendMsg(ircmsgs.privmsg(nick, "\x02Usage: .broadcast <botnet> <message>\x02"))
        
        elif cmd == 'who':
            response = ["\x02Online Users:\x02"]
            response.append(f"  {self.irc.nick} (you) - {', '.join(self.my_botnets)}")
            for pubkey, peer in self.peers.items():
                if peer.connected:
                    peer_info = self.trusted_peers.get(pubkey, {})
                    botnets = peer_info.get('botnets', ['Nest'])
                    response.append(f"  {peer.bot_name} - {', '.join(botnets)}")
            response.append(f"\nTotal: {len(self.peers) + 1} users online")
            for line in response:
                self.irc.sendMsg(ircmsgs.privmsg(nick, line))
        
        elif cmd == 'map':
            response = ["\x02Mesh Topology:\x02"]
            response.append(f"  └─ {self.irc.nick} (you) - {', '.join(self.my_botnets)}")
            for pubkey, peer in self.peers.items():
                if peer.connected:
                    peer_info = self.trusted_peers.get(pubkey, {})
                    botnets = peer_info.get('botnets', ['Nest'])
                    response.append(f"     └─ {peer.bot_name} - {', '.join(botnets)}")
            if not self.peers:
                response.append("     └─ (no connected peers)")
            for line in response:
                self.irc.sendMsg(ircmsgs.privmsg(nick, line))
        
        elif cmd == 'quit' or cmd == 'exit':
            self.partyline_users.pop(nick, None)
            self.irc.sendMsg(ircmsgs.privmsg(nick, "Leaving partyline. Type 'partyline' to return."))
        
        elif cmd == 'help':
            help_text = [
                "\x02BotNet Partyline Commands:\x02",
                "",
                "  .broadcast <botnet> <msg>  - Send message to a botnet",
                "  .b <botnet> <msg>          - Shortcut for broadcast",
                "  .who                       - Show online users in the mesh",
                "  .map                       - Show network topology tree",
                "  .quit                      - Exit partyline",
                "",
                f"\x02Your botnets: {', '.join(self.my_botnets)}\x02"
            ]
            for line in help_text:
                self.irc.sendMsg(ircmsgs.privmsg(nick, line))
        
        else:
            self.irc.sendMsg(ircmsgs.privmsg(nick, f"Unknown command: {cmd}. Type .help for commands."))

    # IRC Message Handler
    def doPrivmsg(self, irc, msg):
        """Handle private messages as partyline commands"""
        if msg.channel != msg.nick:  # Only in private query
            return
        
        text = msg.args[1].strip() if len(msg.args) > 1 else ""
        
        # Check if user is entering partyline
        if text.lower() == 'partyline':
            if msg.nick not in self.partyline_users:
                # Check if user is owner
                if not ircdb.checkCapability(msg.prefix, 'owner'):
                    irc.reply("Permission denied. Owner-only command.", private=True)
                    return
                
                # Enter partyline mode
                self.partyline_users[msg.nick] = {
                    'last_activity': time.time(),
                    'current_botnet': 'Nest'
                }
                self._send_partyline_welcome(msg.nick)
                self.log.info(f"User {msg.nick} entered partyline mode")
            else:
                irc.reply("Already in partyline mode. Type .quit to exit.", private=True)
            return
        
        # Handle partyline input if user is in partyline mode
        if msg.nick in self.partyline_users:
            # Update last activity
            self.partyline_users[msg.nick]['last_activity'] = time.time()
            
            if text.startswith('.'):
                # Command
                parts = text[1:].split(' ', 1)
                cmd = parts[0].lower()
                args = parts[1] if len(parts) > 1 else ''
                self._handle_partyline_command(msg.nick, cmd, args)
            else:
                # No command prefix - send to default botnet (Nest)
                current_botnet = self.partyline_users[msg.nick].get('current_botnet', 'Nest')
                if current_botnet in self.my_botnets:
                    self.broadcast(current_botnet, text)
                    irc.reply(f"[{current_botnet}] You: {text}", private=True)
                else:
                    irc.reply(f"Not in botnet '{current_botnet}'. Use .broadcast <botnet> <message>", private=True)
    
    # IRC Commands
    
    def mykey(self, irc, msg, args):
        """Show your bot's full public keys for sharing with other bots."""
        if not self._check_owner(irc, msg):
            return
        self._notice(irc, msg, "\x02Your BotNet Public Keys:\x02")
        self._notice(irc, msg, f"Signing key (use this for 'trust' command):")
        self._notice(irc, msg, f"  {self.pubkey_signing}")
        self._notice(irc, msg, f"Encryption key (for reference only):")
        self._notice(irc, msg, f"  {self.encryption_public_key_hex}")
        self._notice(irc, msg, "Share ONLY the signing key with other bot owners.")
    mykey = wrap(mykey)

    def status(self, irc, msg, args):
        """Show BotNet connection status."""
        if not self._check_owner(irc, msg):
            return
        
        status_lines = [
            f"\x02BotNet Status for {irc.nick}:\x02",
            f"  Data directory: {self.data_dir}",
            f"  Signing key: {self.pubkey_signing}",
            f"  Connected peers: {len(self.peers)}/{len(self.trusted_peers)}",
            f"  My botnets: {', '.join(self.my_botnets)}",
            f"  Listener: {'✓ Running' if self.listener else '✗ Stopped'}",
            f"  Uptime: {int(time.time() - self.start_time)}s",
            f"  Partyline users: {len(self.partyline_users)}",
            ""
        ]
        
        if self.peers:
            status_lines.append("\x02Connected peers:\x02")
            for pubkey, peer in self.peers.items():
                peer_info = self.trusted_peers.get(pubkey, {})
                botnets = peer_info.get('botnets', ['Nest'])
                status_lines.append(f"    {peer.bot_name} ({', '.join(botnets)}) - {'✓' if peer.connected else '✗'}")
        else:
            status_lines.append("No connected peers.")
        
        for line in status_lines:
            self._notice(irc, msg, line)
    status = wrap(status)

    def trust(self, irc, msg, args, pubkey, optional_botnets=None):
        """<pubkey> [botnet1,botnet2] -- Trust a remote bot's public key.
        The bot is always added to 'Nest' by default. Optional botnets can be specified as comma-separated list."""
        if not self._check_owner(irc, msg):
            return

        # Parse optional botnets
        botnets = ['Nest']
        if optional_botnets:
            extra = [b.strip() for b in optional_botnets.split(',') if b.strip()]
            botnets.extend(extra)
        
        # Store trusted peer info (host/port will be updated when they connect)
        self.trusted_peers[pubkey] = {
            'bot_name': 'unknown',
            'host': None,
            'port': None,
            'botnets': botnets
        }
        self._save_trusted_peers()
        self._notice(irc, msg, f"✓ Trusted peer added: {pubkey[:32]}... (botnets: {', '.join(botnets)})")
        self.log.info(f"Trusted peer added: {pubkey} botnets: {botnets}")
    trust = wrap(trust, ['text', optional('text')])

    def untrust(self, irc, msg, args, pubkey):
        """<pubkey> -- Remove trust from a remote bot's public key."""
        if not self._check_owner(irc, msg):
            return

        if pubkey in self.trusted_peers:
            # Disconnect if connected
            if pubkey in self.peers:
                try:
                    self.peers[pubkey].sock.close()
                except:
                    pass
                del self.peers[pubkey]
            
            del self.trusted_peers[pubkey]
            self._save_trusted_peers()
            self._notice(irc, msg, f"✓ Trusted peer removed: {pubkey[:32]}...")
            self.log.info(f"Trusted peer removed: {pubkey}")
        else:
            self._notice(irc, msg, f"✗ Pubkey not in trusted list: {pubkey[:32]}...")
    untrust = wrap(untrust, ['text'])

    def list_trusted(self, irc, msg, args):
        """List all trusted peers."""
        if not self._check_owner(irc, msg):
            return

        if not self.trusted_peers:
            self._notice(irc, msg, "No trusted peers.")
            return

        self._notice(irc, msg, "\x02Trusted Peers:\x02")
        for pubkey, info in self.trusted_peers.items():
            botnets = info.get('botnets', ['Nest'])
            connected = "✓" if pubkey in self.peers and self.peers[pubkey].connected else "✗"
            self._notice(irc, msg, f"{connected} {info.get('bot_name', 'unknown')[:20]} - {pubkey[:32]}... ({', '.join(botnets)})")
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
                time.sleep(0.1)
                if self.listener and self.listener.error:
                    self._notice(irc, msg, f"❌ Failed to start listener: {self.listener.error}")
                    self.listener = None
                else:
                    self._notice(irc, msg, f"✅ Encrypted listener running on port {port}")
            
            threading.Thread(target=check_listener, daemon=True).start()
            
            self.log.info(f"Started encrypted listener on port {port}")
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
        """<host:port> -- Connect to a BotNet peer (example: 192.168.1.100:4557)."""
        
        # Parse host:port format
        try:
            if ':' not in hostport:
                self._notice(irc, msg, "❌ Usage: host:port (example: 192.168.1.100:4557)")
                return
            
            parts = hostport.rsplit(':', 1)
            host = parts[0]
            port = int(parts[1])
        except ValueError:
            self._notice(irc, msg, "❌ Invalid port number")
            return
        
        self._notice(irc, msg, f"Connecting to {host}:{port}...")
        self.log.info(f"Connecting to {host}:{port}")

        try:
            client = BotNetClient(self)
            success = client.connect(host, port, None)
            
            if success:
                self._notice(irc, msg, f"✅ Connected and encrypted with peer")
            else:
                self._notice(irc, msg, f"❌ Connection failed")
                
        except Exception as e:
            self._notice(irc, msg, f"❌ Connection FAILED: {str(e)}")
            self.log.error(f"Connect error: {e}\n{traceback.format_exc()}")
    connect = wrap(connect, ['text'])

    def leavenest(self, irc, msg, args):
        """Leave the Nest botnet. You will no longer receive Nest broadcasts."""
        if not self._check_owner(irc, msg):
            return
        
        if "Nest" not in self.my_botnets:
            self._notice(irc, msg, "Already not in Nest")
            return
        
        self.my_botnets.remove("Nest")
        self._notice(irc, msg, "✅ Left Nest. You will no longer receive Nest broadcasts.")
        self.log.info(f"Left Nest botnet")
    leavenest = wrap(leavenest)

    def joinnest(self, irc, msg, args):
        """Join the Nest botnet (default botnet for all trusted bots)."""
        if not self._check_owner(irc, msg):
            return
        
        if "Nest" in self.my_botnets:
            self._notice(irc, msg, "Already in Nest")
            return
        
        self.my_botnets.add("Nest")
        self._notice(irc, msg, "✅ Joined Nest. You will now receive Nest broadcasts.")
        self.log.info(f"Joined Nest botnet")
    joinnest = wrap(joinnest)

    def partyline(self, irc, msg, args):
        """Enter BotNet partyline mode via private messages.
        Once in partyline mode, send '.help' for commands."""
        if not self._check_owner(irc, msg):
            return
        
        if msg.nick in self.partyline_users:
            self._notice(irc, msg, "Already in partyline mode. Type .quit to exit.")
            return
        
        # Enter partyline mode
        self.partyline_users[msg.nick] = {
            'last_activity': time.time(),
            'current_botnet': 'Nest'
        }
        self._notice(irc, msg, "✓ Entered partyline mode. Check your private messages!")
        
        # Send welcome via PM
        self._send_partyline_welcome(msg.nick)
        self.log.info(f"User {msg.nick} entered partyline mode via command")
    partyline = wrap(partyline)


Class = BotNet


# vim:set shiftwidth=4 tabstop=4 expandtab textwidth=79:
