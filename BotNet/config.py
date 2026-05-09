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

from supybot import conf, registry
try:
    from supybot.i18n import PluginInternationalization
    _ = PluginInternationalization('BotNet')
except:
    # Placeholder that allows to run the plugin on a bot
    # without the i18n module
    _ = lambda x: x


def configure(advanced):
    # This will be called by supybot to configure this module.  advanced is
    # a bool that specifies whether the user identified themself as an advanced
    # user or not.  You should effect your configuration by manipulating the
    # registry as appropriate.
    from supybot.questions import expect, anything, something, yn
    conf.registerPlugin('BotNet', True)


BotNet = conf.registerPlugin('BotNet')

# Configuration variables
conf.registerGlobalValue(BotNet, 'defaultPort',
    registry.PositiveInteger(4557, _("""Default port for BotNet listener.""")))

conf.registerGlobalValue(BotNet, 'heartbeatInterval',
    registry.PositiveInteger(60, _("""Interval in seconds between PING messages.""")))

conf.registerGlobalValue(BotNet, 'heartbeatTimeout',
    registry.PositiveInteger(90, _("""Seconds without PONG before considering peer disconnected.""")))

conf.registerGlobalValue(BotNet, 'messageCacheSize',
    registry.PositiveInteger(1000, _("""Maximum number of message IDs to cache for flood prevention.""")))

conf.registerGlobalValue(BotNet, 'maxTTL',
    registry.PositiveInteger(10, _("""Maximum Time-To-Live for broadcast messages.""")))

conf.registerGlobalValue(BotNet, 'partylineBufferSize',
    registry.PositiveInteger(100, _("""Number of recent messages to keep in partyline buffer.""")))

conf.registerGlobalValue(BotNet, 'autoReconnect',
    registry.Boolean(True, _("""Automatically reconnect to trusted peers on plugin load.""")))

conf.registerGlobalValue(BotNet, 'maxReconnectAttempts',
    registry.PositiveInteger(10, _("""Maximum number of reconnection attempts before giving up.""")))

conf.registerGlobalValue(BotNet, 'reconnectDelay',
    registry.PositiveInteger(60, _("""Seconds to wait between reconnection attempts.""")))


# vim:set shiftwidth=4 tabstop=4 expandtab textwidth=79:
