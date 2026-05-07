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

from supybot import ircdb
from supybot import callbacks
from supybot.commands import wrap
from supybot import ircmsgs

from .crypto import load_identity, get_public_key_hex
from .transport import BotNetListener, BotNetClient
from .protocol import pack_message
from .messages import HELLO, PROTOCOL_VERSION


class BotNet(callbacks.Plugin):
    threaded = True

    def __init__(self, irc):
        super().__init__(irc)

        # identity system
        self.identity = load_identity()
        self.pubkey = get_public_key_hex(self.identity)

        # runtime state
        self.listener = None
        self.peers = {}

        # 🔐 TRUSTED PEERS (NEW)
        self.trusted_peers = set()

        self.log.info(
            f"BotNet identity: {self.pubkey}"
        )

    def die(self):
        """Called automatically when plugin unloads/reloads."""

        if self.listener:
            self.listener.stop()
            self.listener = None

        super().die()

    # -------------------------
    # HELPERS
    # -------------------------

    def _notice(self, irc, msg, text):
        irc.sendMsg(
            ircmsgs.notice(msg.nick, text)
        )

    def _check_owner(self, irc, msg):
        if not ircdb.checkCapability(msg.prefix, 'owner'):
            self._notice(irc, msg, "Permission denied.")
            return False
        return True

    # -------------------------
    # 🔐 NEW: TRUST SYSTEM
    # -------------------------

    def is_trusted(self, pubkey):
        """Called by transport layer."""
        return pubkey in self.trusted_peers

    @wrap(['text'])
    def trust(self, irc, msg, args, pubkey):
        """Trust a remote bot public key."""

        if not self._check_owner(irc, msg):
            return

        self.trusted_peers.add(pubkey)

        self._notice(
            irc,
            msg,
            f"Trusted peer added: {pubkey}"
        )

    # -------------------------
    # COMMANDS
    # -------------------------

    @wrap(['int'])
    def listen(self, irc, msg, args, port):
        """Start BotNet listener."""

        if not self._check_owner(irc, msg):
            return

        if self.listener:
            self._notice(irc, msg, "Already listening.")
            return

        self.listener = BotNetListener(
            self,
            host="127.0.0.1",
            port=port
        )

        self.listener.start()

        self._notice(
            irc,
            msg,
            f"Listening on port {port}"
        )

    def stop(self, irc, msg, args):
        """Stop BotNet listener."""

        if not self._check_owner(irc, msg):
            return

        if not self.listener:
            self._notice(irc, msg, "Listener is not running.")
            return

        self.listener.stop()
        self.listener = None

        self._notice(irc, msg, "Listener stopped.")

    stop = wrap(stop)

    @wrap(['text', 'int'])
    def connect(self, irc, msg, args, host, port):
        """Connect to peer."""

        if not self._check_owner(irc, msg):
            return

        try:
            client = BotNetClient(self)
            sock = client.connect(host, port)

            hello = {
                "protocol": PROTOCOL_VERSION,
                "type": HELLO,
                "bot_name": str(irc.nick),
                "pubkey": self.pubkey,
            }

            # ✔ FIXED: only send once
            sock.sendall(
                pack_message(hello)
            )

            self._notice(
                irc,
                msg,
                f"Connected to {host}:{port}"
            )

        except Exception as e:
            self.log.error(
                f"BotNet connect error: {e}"
            )

            self._notice(
                irc,
                msg,
                f"Connection failed: {e}"
            )


Class = BotNet
