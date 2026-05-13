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
import copy
from collections import deque
from supybot import ircdb, conf, ircmsgs, ircutils, world
from supybot import callbacks
from supybot.commands import wrap, optional, getopts

from .crypto import (
    load_identity, 
    load_encryption_key, 
    get_public_key_hex, 
    get_encryption_public_key_hex,
    EncryptionManager,
    get_botnet_dir
)
from .transport import BotNetListener, BotNetClient
from .protocol import pack_message, unpack_message, sign_message, verify_message
from .messages import (
    HELLO, PING, PONG, BROADCAST,
    STATUS_QUERY, STATUS_RESPONSE, PROTOCOL_VERSION, 
    MessageIDGenerator, ReplayProtectionManager,
    PARTYLINE_USERS_SYNC, PARTYLINE_USER_LEFT, PARTYLINE_USERS_REQUEST,
    SEEN_QUERY, SEEN_RESPONSE
)
from .peer import Peer


class BotNet(callbacks.Plugin):
    """Decentralized encrypted peer-to-peer mesh network for Limnoria IRC bots."""
    threaded = True

    def __init__(self, irc):
        super().__init__(irc)
        
        self.irc = irc
        self.data_dir = get_botnet_dir()
        
        self.identity = load_identity()
        self.pubkey_signing = get_public_key_hex(self.identity)
        
        self.encryption_private_key = load_encryption_key()
        self.encryption_public_key_hex = get_encryption_public_key_hex(self.encryption_private_key)

        self.listener = None
        self.peers = {}
        self.my_botnets = {"Nest"}
        
        self.seen_messages = set()
        self.message_cache_max = self.registryValue('messageCacheSize')
        self.msg_id_gen = MessageIDGenerator(self.pubkey_signing)
        
        self.replay_manager = ReplayProtectionManager()
        self.message_buffer = deque(maxlen=self.registryValue('partylineBufferSize'))
        self.partyline_users = {}
        self.mesh_users = {}
        self.user_lock = threading.Lock()
        
        self.seen_queries = {}
        self.seen_lock = threading.Lock()
        
        self.heartbeat_running = True
        self.heartbeat_thread = threading.Thread(target=self._heartbeat_sender, daemon=True)
        self.heartbeat_thread.start()
        
        self.reconnect_retries = {}
        self.start_time = time.time()

        self.trusted_file = os.path.join(self.data_dir, "trusted_peers.json")
        self.trusted_peers = self._load_trusted_peers()
        
        self.state_file = os.path.join(self.data_dir, "state.json")
        self.listener_port = None
        self._load_state()

        self.log.info(f"BotNet data directory: {self.data_dir}")
        self.log.info(f"BotNet signing key: {self.pubkey_signing}")
        self.log.info(f"BotNet encryption key: {self.encryption_public_key_hex}")
        self.log.info(f"Loaded {len(self.trusted_peers)} trusted peers")
        
        if self.registryValue('autoListen') and self.listener_port:
            self.log.info(f"Auto-starting listener on port {self.listener_port}")
            threading.Timer(2, self._auto_start_listener).start()
        
        if self.registryValue('autoReconnect'):
            self._schedule_reconnect_all()

    def die(self):
        """Called when plugin unloads."""
        self.heartbeat_running = False
        self.partyline_users.clear()
        self._save_state()
        
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
    
    def _load_state(self):
        """Load persistent state (listener port, etc.)"""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    state = json.load(f)
                    self.listener_port = state.get('listener_port')
            except Exception as e:
                self.log.error(f"Failed to load state: {e}")
    
    def _save_state(self):
        """Save persistent state"""
        try:
            state = {'listener_port': self.listener_port if self.listener else None}
            with open(self.state_file, 'w') as f:
                json.dump(state, f)
        except Exception as e:
            self.log.error(f"Failed to save state: {e}")
    
    def _auto_start_listener(self):
        """Auto-start listener on saved port"""
        if self.listener_port and not self.listener:
            self._start_listener(self.listener_port)

    def _load_trusted_peers(self):
        """Load trusted peers from disk."""
        if os.path.exists(self.trusted_file):
            try:
                with open(self.trusted_file, 'r') as f:
                    data = json.load(f)
                    trusted = data.get('trusted_peers', {})
                    if isinstance(trusted, list):
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
            data = {'trusted_peers': self.trusted_peers}
            with open(self.trusted_file, 'w') as f:
                json.dump(data, f, indent=2)
            self.log.info(f"Saved {len(self.trusted_peers)} trusted peers")
        except Exception as e:
            self.log.error(f"Failed to save trusted peers: {e}")

    def _check_owner(self, irc, msg):
        """Check if user has owner capability."""
        if not ircdb.checkCapability(msg.prefix, 'owner'):
            irc.reply("Permission denied.", private=True)
            return False
        return True

    def _get_user_identifier(self, irc, msg):
        """Get a unique identifier for the user (hostmask)"""
        return msg.prefix

    def _listener_error(self, error):
        """Handle listener startup error."""
        self.log.error(f"Listener failed to start: {error}")
        self.listener = None

    def _schedule_reconnect_all(self):
        """Schedule reconnection to all trusted peers."""
        def reconnect_task():
            for pubkey, peer_info in self.trusted_peers.items():
                if pubkey not in self.peers and peer_info.get('host') and peer_info.get('port'):
                    self._schedule_peer_reconnect(pubkey, peer_info, peer_info.get('bot_name'))
        threading.Thread(target=reconnect_task, daemon=True).start()

    def _schedule_peer_reconnect(self, pubkey, peer_info, bot_name=None, delay=None):
        """Schedule reconnection for a specific peer with exponential backoff."""
        if pubkey in self.peers:
            return
        
        if delay is None:
            delay = self.registryValue('reconnectDelay')
        
        max_attempts = self.registryValue('maxReconnectAttempts')
        retries = self.reconnect_retries.get(pubkey, 0)
        backoff = min(delay * (2 ** min(retries, 8)), 3600)
        
        if retries >= max_attempts:
            self.log.warning(f"Gave up reconnecting to {bot_name or pubkey[:16]} after {max_attempts} attempts")
            self.reconnect_retries.pop(pubkey, None)
            return
        
        should_log = True
        if retries > 10:
            should_log = (retries % 12 == 0)
        
        if should_log:
            self.log.info(f"Reconnection attempt {retries + 1}/{max_attempts} to {bot_name or pubkey[:16]} in {backoff:.0f}s")
        
        def reconnect():
            if pubkey in self.peers:
                return
            
            try:
                from .transport import BotNetClient
                client = BotNetClient(self)
                success = client.connect(peer_info['host'], peer_info['port'], pubkey)
                if success:
                    self.log.info(f"Successfully reconnected to {bot_name or pubkey[:16]}")
                    self.reconnect_retries[pubkey] = 0
                else:
                    self.reconnect_retries[pubkey] = retries + 1
                    threading.Timer(backoff, lambda: self._schedule_peer_reconnect(pubkey, peer_info, bot_name, delay)).start()
            except Exception as e:
                self.log.error(f"Reconnect error to {bot_name or pubkey[:16]}: {e}")
                self.reconnect_retries[pubkey] = retries + 1
                threading.Timer(backoff, lambda: self._schedule_peer_reconnect(pubkey, peer_info, bot_name, delay)).start()
        
        threading.Timer(backoff, reconnect).start()

    def _heartbeat_sender(self):
        """Send PING to all connected peers periodically."""
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
                    except Exception as e:
                        self.log.error(f"Failed to send PING to {peer.bot_name}: {e}")
                        peer.connected = False

    def _peer_in_botnet(self, pubkey, botnet_name):
        """Check if a peer belongs to a botnet."""
        if pubkey in self.trusted_peers:
            botnets = self.trusted_peers[pubkey].get('botnets', ['Nest'])
            return botnet_name in botnets
        return False

    def is_trusted(self, pubkey):
        """Check if a public key is trusted."""
        return pubkey in self.trusted_peers

    def get_recent_messages(self, limit=100):
        """Get recent broadcast messages from buffer."""
        return list(self.message_buffer)[-limit:]

    def _add_to_message_buffer(self, botnet, sender, content):
        """Add a message to the buffer and notify partyline users."""
        msg = {
            'timestamp': time.time(),
            'botnet': botnet,
            'sender': sender,
            'content': content
        }
        self.message_buffer.append(msg)
        
        for identifier, session in list(self.partyline_users.items()):
            try:
                nick = session.get('nick')
                if nick:
                    self.irc.sendMsg(ircmsgs.privmsg(nick, f"[{botnet}] {sender}: {content}"))
            except Exception as e:
                self.log.error(f"Failed to send to {identifier}: {e}")

    def _send_partyline_message(self, nick, message):
        """Send a message to a partyline user, handling empty messages"""
        if not message or not message.strip():
            message = "---"
        try:
            self.irc.sendMsg(ircmsgs.privmsg(nick, message))
        except Exception as e:
            self.log.error(f"Failed to send to {nick}: {e}")

    def _notify_partyline_peer_joined(self, bot_name):
        """Notify partyline users about a new bot joining."""
        for identifier, session in list(self.partyline_users.items()):
            nick = session.get('nick')
            if nick:
                self._send_partyline_message(nick, f"*** Bot {bot_name} has joined the mesh")

    def _notify_partyline_peer_left(self, bot_name):
        """Notify partyline users about a bot leaving."""
        for identifier, session in list(self.partyline_users.items()):
            nick = session.get('nick')
            if nick:
                self._send_partyline_message(nick, f"*** Bot {bot_name} has left the mesh")

    def _notify_partyline_user_joined(self, nick, bot_name, botnet):
        """Notify local partyline users about a remote user joining"""
        for identifier, session in list(self.partyline_users.items()):
            local_nick = session.get('nick')
            if local_nick:
                if nick == session.get('nick') and bot_name == self.irc.nick:
                    continue
                self._send_partyline_message(local_nick, f"*** User {nick} (via {bot_name}) has joined {botnet}")

    def _notify_partyline_user_left(self, nick, bot_name, botnet):
        """Notify local partyline users about a remote user leaving"""
        for identifier, session in list(self.partyline_users.items()):
            local_nick = session.get('nick')
            if local_nick:
                self._send_partyline_message(local_nick, f"*** User {nick} (via {bot_name}) has left {botnet}")

    def _get_bot_name_by_pubkey(self, pubkey):
        """Get bot name from public key"""
        if pubkey in self.peers:
            return self.peers[pubkey].bot_name
        return pubkey[:16]

    def _broadcast_partyline_users(self, botnet=None):
        """Broadcast current partyline users to the mesh"""
        with self.user_lock:
            if not self.mesh_users:
                return
            
            users_to_broadcast = []
            for user_id, user_info in self.mesh_users.items():
                if botnet is None or user_info.get('botnet') == botnet:
                    users_to_broadcast.append({
                        'nick': user_info['nick'],
                        'bot': user_info['bot'],
                        'botnet': user_info['botnet']
                    })
        
        if not users_to_broadcast:
            return
        
        for pubkey, peer in self.peers.items():
            if peer.connected:
                try:
                    user_msg = {
                        "type": PARTYLINE_USERS_SYNC,
                        "users": users_to_broadcast,
                        "botnet": botnet or "Nest"
                    }
                    encryption_box = peer.encryption_manager.peer_boxes.get(pubkey)
                    if encryption_box:
                        peer.sock.sendall(pack_message(user_msg, encryption_box=encryption_box))
                except Exception as e:
                    self.log.error(f"Failed to broadcast partyline users to {peer.bot_name}: {e}")

    def _request_user_sync(self):
        """Request current partyline users from all connected peers"""
        for pubkey, peer in self.peers.items():
            if peer.connected:
                try:
                    request_msg = {
                        "type": PARTYLINE_USERS_REQUEST,
                        "botnet": "Nest"
                    }
                    encryption_box = peer.encryption_manager.peer_boxes.get(pubkey)
                    if encryption_box:
                        peer.sock.sendall(pack_message(request_msg, encryption_box=encryption_box))
                except Exception as e:
                    self.log.error(f"Failed to request user sync from {peer.bot_name}: {e}")

    def _sync_partyline_with_peer(self, peer_pubkey):
        """Sync partyline users with a newly connected peer"""
        with self.user_lock:
            if not self.mesh_users:
                return
            
            users_to_send = []
            for user_id, user_info in self.mesh_users.items():
                users_to_send.append({
                    'nick': user_info['nick'],
                    'bot': user_info['bot'],
                    'botnet': user_info['botnet']
                })
        
        if users_to_send:
            for pubkey, peer in self.peers.items():
                if pubkey == peer_pubkey and peer.connected:
                    try:
                        sync_msg = {
                            "type": PARTYLINE_USERS_SYNC,
                            "users": users_to_send,
                            "botnet": "Nest"
                        }
                        encryption_box = peer.encryption_manager.peer_boxes.get(pubkey)
                        if encryption_box:
                            peer.sock.sendall(pack_message(sync_msg, encryption_box=encryption_box))
                            self.log.info(f"Synced {len(users_to_send)} partyline users to {peer.bot_name}")
                    except Exception as e:
                        self.log.error(f"Failed to sync partyline users to {peer.bot_name}: {e}")

    def _handle_partyline_users_sync(self, message, from_peer):
        """Handle incoming partyline user sync from mesh"""
        users = message.get("users", [])
        botnet = message.get("botnet", "Nest")
        
        from_bot = self._get_bot_name_by_pubkey(from_peer)
        self.log.info(f"Received partyline user sync: {len(users)} users from bot {from_bot}")
        
        with self.user_lock:
            for user in users:
                user_id = f"{user['nick']}@{user['bot']}"
                if user_id not in self.mesh_users and user['bot'] != self.irc.nick:
                    self.mesh_users[user_id] = user
                    self.log.info(f"Added remote user {user['nick']} from bot {user['bot']}")
                    self._notify_partyline_user_joined(user['nick'], user['bot'], user['botnet'])

    def _handle_partyline_users_request(self, message, from_peer):
        """Handle request for partyline users from a peer"""
        botnet = message.get("botnet", "Nest")
        with self.user_lock:
            users_to_send = []
            for user_id, user_info in self.mesh_users.items():
                if user_info.get('botnet') == botnet:
                    users_to_send.append({
                        'nick': user_info['nick'],
                        'bot': user_info['bot'],
                        'botnet': user_info['botnet']
                    })
        
        if users_to_send:
            for pubkey, peer in self.peers.items():
                if pubkey == from_peer and peer.connected:
                    try:
                        response_msg = {
                            "type": PARTYLINE_USERS_SYNC,
                            "users": users_to_send,
                            "botnet": botnet
                        }
                        encryption_box = peer.encryption_manager.peer_boxes.get(pubkey)
                        if encryption_box:
                            peer.sock.sendall(pack_message(response_msg, encryption_box=encryption_box))
                    except Exception as e:
                        self.log.error(f"Failed to send user sync response: {e}")

    def _handle_partyline_user_left(self, message):
        """Handle user leaving notification from mesh"""
        nick = message.get("nick")
        bot = message.get("bot")
        botnet = message.get("botnet", "Nest")
        
        with self.user_lock:
            user_id = f"{nick}@{bot}"
            if user_id in self.mesh_users:
                del self.mesh_users[user_id]
                self.log.info(f"Removed remote user {nick} from bot {bot}")
                self._notify_partyline_user_left(nick, bot, botnet)

    # Seen plugin integration - Public command
    def bseen(self, irc, msg, args, nick):
        """<nick> -- Show when a nick was last seen across the entire botnet."""
        # Check if publicSeen is enabled, otherwise require owner
        if not self.registryValue('publicSeen') and not self._check_owner(irc, msg):
            return
        
        local_result = self._check_local_seen(nick)
        
        # Determine where to send the response (channel or private)
        target = msg.args[0] if msg.channel else msg.nick
        
        if local_result:
            self._send_bseen_response(irc, target, local_result)
        
        self._query_bseen_peers(irc, target, nick, msg.nick, msg.channel)
    bseen = wrap(bseen, ['text'])

    def _send_bseen_response(self, irc, target, message):
        """Send a bseen response to channel or user"""
        if not message or not message.strip():
            return
        try:
            # If target is a channel, send to channel, otherwise private
            if target.startswith('#'):
                irc.reply(message)
            else:
                irc.reply(message, private=True)
        except Exception as e:
            self.log.error(f"Failed to send bseen response: {e}")

    def _query_bseen_peers(self, irc, target, nick, requester, is_channel):
        """Query all connected peers for seen information"""
        query_id = f"{int(time.time())}_{nick}_{requester}"
        
        with self.seen_lock:
            self.seen_queries[query_id] = {
                'nick': nick,
                'requester': requester,
                'target': target,
                'irc': irc,
                'timestamp': time.time(),
                'responses': [],
                'responded_peers': set()
            }
        
        query_msg = {
            "type": SEEN_QUERY,
            "query_id": query_id,
            "nick": nick,
            "requester": requester,
            "channel": target if target.startswith('#') else None,
            "timestamp": time.time()
        }
        
        sent_count = 0
        for pubkey, peer in self.peers.items():
            if peer.connected:
                try:
                    encryption_box = peer.encryption_manager.peer_boxes.get(pubkey)
                    if encryption_box:
                        peer.sock.sendall(pack_message(query_msg, encryption_box=encryption_box))
                        sent_count += 1
                except Exception as e:
                    self.log.error(f"Failed to send seen query to {peer.bot_name}: {e}")
        
        if sent_count == 0:
            with self.seen_lock:
                self.seen_queries.pop(query_id, None)
            self._send_bseen_response(irc, target, f"No other bots available to query for {nick}.")
        
        threading.Timer(10.0, self._bseen_query_timeout, args=[query_id]).start()

    def _bseen_query_timeout(self, query_id):
        """Handle bseen query timeout"""
        with self.seen_lock:
            if query_id not in self.seen_queries:
                return
            
            query = self.seen_queries.pop(query_id)
            responses = query.get('responses', [])
            target = query.get('target')
            irc = query.get('irc')
            nick = query.get('nick')
            
            if responses:
                self._send_bseen_response(irc, target, f"Searching for {nick} across the botnet...")
                for response in responses:
                    self._send_bseen_response(irc, target, response)

    def _check_local_seen(self, nick):
        """Check local Seen plugin for a nick"""
        try:
            seen_plugin = self.irc.getCallback('Seen')
            if seen_plugin and hasattr(seen_plugin, 'db'):
                db = seen_plugin.db
                for (channel, search_nick), record in db.items():
                    if isinstance(search_nick, int):
                        continue
                    if isinstance(search_nick, str) and ircutils.strEqual(search_nick, nick):
                        if isinstance(record, (tuple, list)) and len(record) >= 2:
                            timestamp = record[0]
                            message = record[1]
                            time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))
                            return f"{nick} was last seen in {channel} on {time_str} saying: {message}"
                
                if hasattr(seen_plugin, 'anydb'):
                    anydb = seen_plugin.anydb
                    for (channel, search_nick), record in anydb.items():
                        if isinstance(search_nick, int):
                            continue
                        if isinstance(search_nick, str) and ircutils.strEqual(search_nick, nick):
                            if isinstance(record, (tuple, list)) and len(record) >= 2:
                                timestamp = record[0]
                                message = record[1]
                                time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))
                                return f"{nick} was last seen in {channel} on {time_str} doing: {message}"
            return None
        except Exception as e:
            self.log.error(f"Failed to check local seen: {e}")
            return None

    def _handle_seen_query(self, message, from_peer):
        """Handle incoming seen query from another bot"""
        query_id = message.get("query_id")
        nick = message.get("nick")
        requester = message.get("requester")
        channel = message.get("channel")
        
        seen_result = self._check_local_seen(nick)
        
        if seen_result:
            response_msg = {
                "type": SEEN_RESPONSE,
                "query_id": query_id,
                "nick": nick,
                "result": seen_result,
                "bot_name": self.irc.nick,
                "timestamp": time.time()
            }
            
            for pubkey, peer in self.peers.items():
                if pubkey == from_peer and peer.connected:
                    try:
                        encryption_box = peer.encryption_manager.peer_boxes.get(pubkey)
                        if encryption_box:
                            peer.sock.sendall(pack_message(response_msg, encryption_box=encryption_box))
                    except Exception as e:
                        self.log.error(f"Failed to send seen response: {e}")
                    break

    def _handle_seen_response(self, message):
        """Handle seen response from another bot"""
        query_id = message.get("query_id")
        result = message.get("result")
        bot_name = message.get("bot_name")
        
        with self.seen_lock:
            if query_id in self.seen_queries:
                query = self.seen_queries[query_id]
                if bot_name not in query['responded_peers']:
                    query['responded_peers'].add(bot_name)
                    query['responses'].append(f"{bot_name} says: {result}")

    def broadcast(self, botnet_name, message_text):
        """Send a signed broadcast message to a botnet with timestamp."""
        if botnet_name not in self.my_botnets:
            return False
        
        # Get the user who sent this broadcast (if from partyline)
        user_nick = None
        for identifier, session in self.partyline_users.items():
            if session.get('nick'):
                user_nick = session.get('nick')
                break
        
        msg_id = self.msg_id_gen.next()
        
        # Create the display name: either "user (via bot)" or just bot name
        if user_nick and user_nick != self.irc.nick:
            display_sender = f"{user_nick} (via {self.irc.nick})"
        else:
            display_sender = self.irc.nick
        
        broadcast_msg = {
            "type": BROADCAST,
            "msg_id": msg_id,
            "sender_pubkey": self.pubkey_signing,
            "sender_botname": self.irc.nick,
            "sender_user": user_nick,
            "display_sender": display_sender,
            "botnet": botnet_name,
            "content": message_text,
            "ttl": self.registryValue('maxTTL'),
            "timestamp": time.time()
        }
        
        broadcast_msg = sign_message(broadcast_msg, self.identity)
        self.seen_messages.add(msg_id)
        self.replay_manager.is_replay(self.pubkey_signing, msg_id, broadcast_msg["timestamp"])
        
        # Add to local buffer with proper display name
        self._add_to_message_buffer(botnet_name, display_sender, message_text)
        
        count = 0
        for pubkey, peer in self.peers.items():
            if peer.connected and self._peer_in_botnet(pubkey, botnet_name):
                try:
                    encryption_box = peer.encryption_manager.peer_boxes.get(pubkey)
                    if encryption_box:
                        peer.sock.sendall(pack_message(broadcast_msg, encryption_box=encryption_box))
                        count += 1
                except Exception as e:
                    self.log.error(f"Failed to send to {peer.bot_name}: {e}")
        
        self.log.info(f"Broadcast to '{botnet_name}' sent to {count} peers")
        return True

    def handle_broadcast(self, message, from_peer):
        """Handle incoming signed broadcast with flood prevention, signature verification, and replay protection."""
        msg_id = message.get("msg_id")
        msg_timestamp = message.get("timestamp", 0)
        sender_pubkey = message.get("sender_pubkey")
        
        if msg_id in self.seen_messages:
            return
        
        if not sender_pubkey:
            self.log.warning("Received broadcast without sender pubkey, ignoring")
            return
        
        if self.replay_manager.is_replay(sender_pubkey, msg_id, msg_timestamp):
            self.log.warning(f"Replay attack detected: msg_id={msg_id} from {sender_pubkey[:16]}...")
            return
        
        msg_copy = copy.deepcopy(message)
        
        if not verify_message(msg_copy, sender_pubkey):
            self.log.warning(f"Invalid signature on broadcast from {sender_pubkey[:16]}..., ignoring")
            return
        
        self.seen_messages.add(msg_id)
        if len(self.seen_messages) > self.message_cache_max:
            self.seen_messages = set(list(self.seen_messages)[-800:])
        
        target_botnet = message.get("botnet")
        content = message.get("content")
        display_sender = message.get("display_sender", message.get("sender_botname"))
        ttl = message.get("ttl", 10)
        
        if target_botnet in self.my_botnets:
            self.log.info(f"[{target_botnet}] {display_sender}: {content}")
            self._add_to_message_buffer(target_botnet, display_sender, content)
        
        if ttl > 0 and target_botnet in self.my_botnets:
            message["ttl"] = ttl - 1
            for pubkey, peer in self.peers.items():
                if pubkey != from_peer and peer.connected and self._peer_in_botnet(pubkey, target_botnet):
                    try:
                        encryption_box = peer.encryption_manager.peer_boxes.get(pubkey)
                        if encryption_box:
                            peer.sock.sendall(pack_message(message, encryption_box=encryption_box))
                    except Exception as e:
                        self.log.error(f"Failed to forward to {peer.bot_name}: {e}")

    def get_status_response(self):
        """Generate status response for STATUS_QUERY."""
        return {
            "type": STATUS_RESPONSE,
            "bot_name": self.irc.nick,
            "pubkey_signing": self.pubkey_signing,
            "connected_peers": len(self.peers),
            "trusted_peers": len(self.trusted_peers),
            "botnets": list(self.my_botnets),
            "uptime": time.time() - self.start_time
        }

    # Partyline Commands - All require owner
    
    def bwho(self, irc, msg, args):
        """Show online bots and all partyline users across the mesh."""
        if not self._check_owner(irc, msg):
            return
        
        identifier = self._get_user_identifier(irc, msg)
        if identifier not in self.partyline_users:
            irc.reply("Not in partyline mode. Type 'partyline' first.", private=True)
            return
        
        self._send_partyline_message(msg.nick, "Connected Bots:")
        self._send_partyline_message(msg.nick, f"  • {self.irc.nick} (this bot) - {', '.join(self.my_botnets)}")
        for pubkey, peer in self.peers.items():
            if peer.connected:
                peer_info = self.trusted_peers.get(pubkey, {})
                botnets = peer_info.get('botnets', ['Nest'])
                self._send_partyline_message(msg.nick, f"  • {peer.bot_name} - {', '.join(botnets)}")
        
        with self.user_lock:
            if self.mesh_users:
                self._send_partyline_message(msg.nick, "")
                self._send_partyline_message(msg.nick, "Partyline Users Across Mesh:")
                for user_id, user in self.mesh_users.items():
                    botnet = user.get('botnet', 'Nest')
                    self._send_partyline_message(msg.nick, f"  • {user['nick']} (via {user['bot']}) - {botnet}")
        
        self._send_partyline_message(msg.nick, "")
        self._send_partyline_message(msg.nick, f"Summary: {len(self.peers)} bot(s) connected, {len(self.mesh_users)} user(s) in partyline")
    bwho = wrap(bwho)

    def busers(self, irc, msg, args):
        """Show all users in partyline mode across the entire mesh."""
        if not self._check_owner(irc, msg):
            return
        
        identifier = self._get_user_identifier(irc, msg)
        if identifier not in self.partyline_users:
            irc.reply("Not in partyline mode. Type 'partyline' first.", private=True)
            return
        
        with self.user_lock:
            if not self.mesh_users:
                self._send_partyline_message(msg.nick, "No users currently in partyline mode.")
                return
            
            users_by_botnet = {}
            for user_id, user in self.mesh_users.items():
                botnet = user.get('botnet', 'Nest')
                if botnet not in users_by_botnet:
                    users_by_botnet[botnet] = []
                users_by_botnet[botnet].append(user)
            
            self._send_partyline_message(msg.nick, "Partyline Users Across Mesh:")
            for botnet, users in users_by_botnet.items():
                self._send_partyline_message(msg.nick, f"  [{botnet}]")
                for user in users:
                    self._send_partyline_message(msg.nick, f"    • {user['nick']} (via {user['bot']})")
            
            self._send_partyline_message(msg.nick, f"Total: {len(self.mesh_users)} user(s) across {len(users_by_botnet)} botnet(s)")
    busers = wrap(busers)

    def bmap(self, irc, msg, args):
        """Show mesh topology."""
        if not self._check_owner(irc, msg):
            return
        
        identifier = self._get_user_identifier(irc, msg)
        if identifier not in self.partyline_users:
            irc.reply("Not in partyline mode. Type 'partyline' first.", private=True)
            return
        
        self._send_partyline_message(msg.nick, "Mesh Topology:")
        self._send_partyline_message(msg.nick, f"  └─ {self.irc.nick} (current bot) - {', '.join(self.my_botnets)}")
        for pubkey, peer in self.peers.items():
            if peer.connected:
                peer_info = self.trusted_peers.get(pubkey, {})
                botnets = peer_info.get('botnets', ['Nest'])
                self._send_partyline_message(msg.nick, f"     └─ {peer.bot_name} - {', '.join(botnets)}")
        if not self.peers:
            self._send_partyline_message(msg.nick, "     └─ (no connected bots)")
    bmap = wrap(bmap)

    def bcast(self, irc, msg, args, botnet, message):
        """<botnet> <message> -- Broadcast a signed message to a botnet."""
        if not self._check_owner(irc, msg):
            return
        
        identifier = self._get_user_identifier(irc, msg)
        if identifier not in self.partyline_users:
            irc.reply("Not in partyline mode. Type 'partyline' first.", private=True)
            return
        
        if botnet not in self.my_botnets:
            irc.reply(f"Not in botnet '{botnet}'. Your botnets: {', '.join(self.my_botnets)}", private=True)
            return
        
        if self.broadcast(botnet, message):
            irc.reply(f"[{botnet}] You: {message}", private=True)
        else:
            irc.reply(f"Failed to broadcast to {botnet}", private=True)
    bcast = wrap(bcast, ['something', 'text'])

    def bquit(self, irc, msg, args):
        """Exit partyline mode."""
        if not self._check_owner(irc, msg):
            return
        
        identifier = self._get_user_identifier(irc, msg)
        if identifier not in self.partyline_users:
            irc.reply("Not in partyline mode.", private=True)
            return
        
        with self.user_lock:
            user_info = self.mesh_users.pop(identifier, None)
        
        self.partyline_users.pop(identifier, None)
        
        if user_info:
            leave_msg = {
                "type": PARTYLINE_USER_LEFT,
                "nick": user_info['nick'],
                "bot": user_info['bot'],
                "botnet": user_info['botnet']
            }
            for pubkey, peer in self.peers.items():
                if peer.connected:
                    try:
                        encryption_box = peer.encryption_manager.peer_boxes.get(pubkey)
                        if encryption_box:
                            peer.sock.sendall(pack_message(leave_msg, encryption_box=encryption_box))
                    except Exception:
                        pass
        
        irc.reply("Leaving partyline. Type 'partyline' to return.", private=True)
    bquit = wrap(bquit)

    def bhelp(self, irc, msg, args):
        """Show partyline help."""
        if not self._check_owner(irc, msg):
            return
        
        identifier = self._get_user_identifier(irc, msg)
        if identifier not in self.partyline_users:
            irc.reply("Not in partyline mode. Type 'partyline' first.", private=True)
            return
        
        self._send_partyline_message(msg.nick, "BotNet Partyline Commands:")
        self._send_partyline_message(msg.nick, "  bcast <botnet> <msg>  - Send message to a botnet")
        self._send_partyline_message(msg.nick, "  bwho                   - Show connected bots and users")
        self._send_partyline_message(msg.nick, "  busers                 - Show partyline users only")
        self._send_partyline_message(msg.nick, "  bmap                   - Show mesh topology")
        self._send_partyline_message(msg.nick, "  bquit                  - Exit partyline")
        self._send_partyline_message(msg.nick, "  bhelp                  - Show this help")
        self._send_partyline_message(msg.nick, f"Your botnets: {', '.join(self.my_botnets)}")
    bhelp = wrap(bhelp)

    # IRC Commands - Most require owner
    
    def mykey(self, irc, msg, args):
        """Show your bot's full public keys for sharing with other bots."""
        if not self._check_owner(irc, msg):
            return
        irc.reply(f"Signing key: {self.pubkey_signing}", private=True)
        irc.reply(f"Encryption key: {self.encryption_public_key_hex}", private=True)
        irc.reply("Share ONLY the signing key with other bot owners.", private=True)
    mykey = wrap(mykey)

    def status(self, irc, msg, args):
        """Show BotNet connection status."""
        if not self._check_owner(irc, msg):
            return
        
        irc.reply(f"BotNet Status for {irc.nick}:", private=True)
        irc.reply(f"  Data directory: {self.data_dir}", private=True)
        irc.reply(f"  Listener: {'Running on port ' + str(self.listener_port) if self.listener else 'Stopped'}", private=True)
        irc.reply(f"  Connected bots: {len(self.peers)}/{len(self.trusted_peers)}", private=True)
        irc.reply(f"  Partyline users: {len(self.partyline_users)}", private=True)
        irc.reply(f"  My botnets: {', '.join(self.my_botnets)}", private=True)
        irc.reply(f"  Uptime: {int(time.time() - self.start_time)}s", private=True)
        
        if self.peers:
            irc.reply("Connected bots:", private=True)
            for pubkey, peer in self.peers.items():
                peer_info = self.trusted_peers.get(pubkey, {})
                botnets = peer_info.get('botnets', ['Nest'])
                irc.reply(f"    {peer.bot_name} ({', '.join(botnets)}) - {'connected' if peer.connected else 'disconnected'}", private=True)
        else:
            irc.reply("No connected bots.", private=True)
    status = wrap(status)

    def trustlist(self, irc, msg, args):
        """List all trusted bots with their connection info."""
        if not self._check_owner(irc, msg):
            return

        if not self.trusted_peers:
            irc.reply("No trusted bots.", private=True)
            return

        irc.reply("Trusted Bots:", private=True)
        for pubkey, info in self.trusted_peers.items():
            bot_name = info.get('bot_name', 'unknown')
            host = info.get('host', 'not set')
            port = info.get('port', 'not set')
            botnets = info.get('botnets', ['Nest'])
            connected = "✓" if pubkey in self.peers and self.peers[pubkey].connected else "✗"
            connection_info = f"{host}:{port}" if host != 'not set' else 'no connection info'
            irc.reply(f"{connected} {bot_name} - {connection_info} - {pubkey[:32]}... ({', '.join(botnets)})", private=True)
        irc.reply(f"Total: {len(self.trusted_peers)} trusted bot(s)", private=True)
    trustlist = wrap(trustlist)

    def trust(self, irc, msg, args, text):
        """<pubkey> [botnet1,botnet2] -- Trust a remote bot's public key."""

        if not self._check_owner(irc, msg):
            return

        parts = text.split()

        if not parts:
            irc.reply("Usage: trust <pubkey> [botnet1,botnet2]", private=True)
            return

        pubkey = parts[0].strip()

        botnets = ['Nest']

        if len(parts) > 1:
            extra = [b.strip() for b in parts[1].split(',') if b.strip()]
            for b in extra:
                if b not in botnets:
                    botnets.append(b)

        self.trusted_peers[pubkey] = {
            'bot_name': 'unknown',
            'host': None,
            'port': None,
            'botnets': botnets
        }

        self._save_trusted_peers()

        irc.reply(
            f"Trusted peer added: {pubkey[:32]}... "
            f"(botnets: {', '.join(botnets)})",
            private=True
        )

    trust = wrap(trust, ['text'])

    def untrust(self, irc, msg, args, pubkey):
        """<pubkey> -- Remove trust from a remote bot's public key."""
        if not self._check_owner(irc, msg):
            return

        if pubkey in self.trusted_peers:
            if pubkey in self.peers:
                try:
                    self.peers[pubkey].sock.close()
                except:
                    pass
                del self.peers[pubkey]
            del self.trusted_peers[pubkey]
            self._save_trusted_peers()
            irc.reply(f"Trusted peer removed: {pubkey[:32]}...", private=True)
        else:
            irc.reply(f"Pubkey not in trusted list: {pubkey[:32]}...", private=True)
    untrust = wrap(untrust, ['text'])

    def joinbotnet(self, irc, msg, args, botnet):
        """<botnet> -- Join a botnet."""
        if not self._check_owner(irc, msg):
            return
        
        if botnet in self.my_botnets:
            irc.reply(f"Already in botnet '{botnet}'.", private=True)
            return
        
        self.my_botnets.add(botnet)
        irc.reply(f"Joined botnet '{botnet}'. You will now receive broadcasts for this botnet.", private=True)
    joinbotnet = wrap(joinbotnet, ['text'])

    def leavebotnet(self, irc, msg, args, botnet):
        """<botnet> -- Leave a botnet."""
        if not self._check_owner(irc, msg):
            return
        
        if botnet == "Nest":
            irc.reply("Cannot leave Nest. Use 'leavenest' if you really want to leave Nest.", private=True)
            return
        
        if botnet not in self.my_botnets:
            irc.reply(f"Not in botnet '{botnet}'.", private=True)
            return
        
        self.my_botnets.remove(botnet)
        irc.reply(f"Left botnet '{botnet}'. You will no longer receive broadcasts for this botnet.", private=True)
    leavebotnet = wrap(leavebotnet, ['text'])

    def _start_listener(self, port):
        """Internal method to start listener."""
        try:
            self.listener = BotNetListener(self, host="0.0.0.0", port=port)
            self.listener.start()
            self.listener_port = port
            self._save_state()
            
            def check_listener():
                time.sleep(0.5)
                if self.listener and self.listener.error:
                    self.log.error(f"Listener failed to start: {self.listener.error}")
                    self.listener = None
                    self.listener_port = None
                    self._save_state()
            
            threading.Thread(target=check_listener, daemon=True).start()
            return True
        except Exception as e:
            self.log.error(f"Failed to start listener: {e}")
            return False

    def listen(self, irc, msg, args, port):
        """<port> -- Start BotNet listener on specified port. Port is saved and will auto-start on plugin reload if autoListen is enabled."""
        if not self._check_owner(irc, msg):
            return

        if self.listener:
            irc.reply(f"Listener already running on port {self.listener_port}. Use 'stop' to stop it first.", private=True)
            return

        if self._start_listener(port):
            irc.reply(f"Listener started on port {port}.", private=True)
            irc.reply(f"Port saved. Set 'config supybot.plugins.BotNet.autoListen True' to auto-start on reload.", private=True)
        else:
            irc.reply(f"Failed to start listener on port {port}. Check logs.", private=True)
    listen = wrap(listen, ['int'])

    def stop(self, irc, msg, args):
        """Stop the BotNet listener. Listener will NOT auto-start on reload after being stopped."""
        if not self._check_owner(irc, msg):
            return

        if not self.listener:
            irc.reply("Listener is not running.", private=True)
            return

        self.listener.stop()
        self.listener = None
        self.listener_port = None
        self._save_state()
        irc.reply("Listener stopped. It will not auto-start on reload (use 'listen' to start again).", private=True)
    stop = wrap(stop)

    def connect(self, irc, msg, args, hostport):
        """<host:port> -- Connect to a BotNet peer (example: 192.168.1.100:4557)."""
        try:
            if ':' not in hostport:
                irc.reply("Usage: host:port (example: 192.168.1.100:4557)", private=True)
                return
            
            parts = hostport.rsplit(':', 1)
            host = parts[0]
            port = int(parts[1])
        except ValueError:
            irc.reply("Invalid port number", private=True)
            return
        
        irc.reply(f"Connecting to {host}:{port}...", private=True)

        try:
            client = BotNetClient(self)
            success = client.connect(host, port, None)
            if success:
                irc.reply(f"Connected successfully.", private=True)
            else:
                irc.reply(f"Connection failed. Check that the remote bot is trusted and has listener running.", private=True)
        except Exception as e:
            irc.reply(f"Connection failed: {e}", private=True)
    connect = wrap(connect, ['text'])

    def reconnect(self, irc, msg, args, pubkey=None):
        """[pubkey] -- Manually reconnect to a trusted peer (or all if no pubkey)."""
        if not self._check_owner(irc, msg):
            return
        
        if pubkey:
            if pubkey in self.trusted_peers:
                peer_info = self.trusted_peers[pubkey]
                if peer_info.get('host') and peer_info.get('port'):
                    self._schedule_peer_reconnect(pubkey, peer_info, peer_info.get('bot_name'), delay=5)
                    irc.reply(f"Scheduling reconnect to {peer_info.get('bot_name', pubkey[:16])}...", private=True)
                else:
                    irc.reply(f"No host/port known for {pubkey[:16]}... Use 'trustlist' to see connection info, then 'connect' first.", private=True)
            else:
                irc.reply(f"Pubkey not trusted: {pubkey[:32]}...", private=True)
        else:
            count = 0
            for pk, info in self.trusted_peers.items():
                if pk not in self.peers and info.get('host') and info.get('port'):
                    self._schedule_peer_reconnect(pk, info, info.get('bot_name'), delay=5)
                    count += 1
            irc.reply(f"Scheduling reconnect to {count} disconnected bot(s)", private=True)
    reconnect = wrap(reconnect, [optional('text')])

    def leavenest(self, irc, msg, args):
        """Leave the Nest botnet. You will no longer receive Nest broadcasts."""
        if not self._check_owner(irc, msg):
            return
        
        if "Nest" not in self.my_botnets:
            irc.reply("Already not in Nest", private=True)
            return
        
        self.my_botnets.remove("Nest")
        irc.reply("Left Nest. You will no longer receive Nest broadcasts.", private=True)
    leavenest = wrap(leavenest)

    def joinnest(self, irc, msg, args):
        """Join the Nest botnet (default botnet for all trusted bots)."""
        if not self._check_owner(irc, msg):
            return
        
        if "Nest" in self.my_botnets:
            irc.reply("Already in Nest", private=True)
            return
        
        self.my_botnets.add("Nest")
        irc.reply("Joined Nest. You will now receive Nest broadcasts.", private=True)
    joinnest = wrap(joinnest)

    def partyline(self, irc, msg, args):
        """Enter BotNet partyline mode. Use 'bhelp' for commands."""
        if not self._check_owner(irc, msg):
            return
        
        identifier = self._get_user_identifier(irc, msg)
        
        if identifier in self.partyline_users:
            irc.reply(f"Already in partyline mode. Use 'bquit' to exit.", private=True)
            return
        
        self.partyline_users[identifier] = {
            'nick': msg.nick,
            'bot_name': self.irc.nick,
            'last_activity': time.time()
        }
        
        with self.user_lock:
            self.mesh_users[identifier] = {
                'nick': msg.nick,
                'bot': self.irc.nick,
                'botnet': 'Nest'
            }
        
        self._broadcast_partyline_users('Nest')
        self._request_user_sync()
        
        self._send_partyline_message(msg.nick, "Entered partyline mode!")
        self._send_partyline_message(msg.nick, "=== BotNet Partyline ===")
        self._send_partyline_message(msg.nick, f"Connected via bot: {self.irc.nick}")
        self._send_partyline_message(msg.nick, f"Your botnets: {', '.join(self.my_botnets)}")
        self._send_partyline_message(msg.nick, f"Bots connected: {len(self.peers)}")
        self._send_partyline_message(msg.nick, "")
        self._send_partyline_message(msg.nick, "Available commands:")
        self._send_partyline_message(msg.nick, "  bcast <botnet> <msg>  - Send message to a botnet")
        self._send_partyline_message(msg.nick, "  bwho                   - Show connected bots and users")
        self._send_partyline_message(msg.nick, "  busers                 - Show partyline users only")
        self._send_partyline_message(msg.nick, "  bmap                   - Show mesh topology")
        self._send_partyline_message(msg.nick, "  bquit                  - Exit partyline")
        self._send_partyline_message(msg.nick, "  bhelp                  - Show this help")
        self._send_partyline_message(msg.nick, "")
        self._send_partyline_message(msg.nick, "Try: bwho")
        self._send_partyline_message(msg.nick, "-" * 40)
        
        recent = self.get_recent_messages(limit=5)
        if recent:
            self._send_partyline_message(msg.nick, "")
            self._send_partyline_message(msg.nick, "Recent messages:")
            for msg_item in recent:
                self._send_partyline_message(msg.nick, f"[{msg_item['botnet']}] {msg_item['sender']}: {msg_item['content']}")
            self._send_partyline_message(msg.nick, "-" * 40)
        
        self.log.info(f"User {msg.nick} ({identifier}) entered partyline mode via {self.irc.nick}")
    partyline = wrap(partyline)


Class = BotNet


# vim:set shiftwidth=4 tabstop=4 expandtab textwidth=79:
