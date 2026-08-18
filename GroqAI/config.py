###
# Copyright (c) 2026, Stathis Xantinidis spithash@Libera https://github.com/spithash
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#   * Redistributions of source code must retain the above copyright notice,
#     this list of conditions, and the following disclaimer.
#   * Redistributions in binary form must reproduce the above copyright
#     notice, this list of conditions, and the following disclaimer in the
#     documentation and/or other materials provided with the distribution.
#   * Neither the name of the author of this software nor the names of its
#     contributors may be used to endorse or promote products derived from
#     this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
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
    _ = PluginInternationalization('GroqAI')
except Exception:
    # Placeholder that allows the plugin to run on a bot
    # without the i18n module.
    _ = lambda x: x


def configure(advanced):
    """
    Configure the GroqAI plugin.

    This is called by Supybot/Limnoria when the plugin is configured.
    """
    from supybot.questions import expect, anything, something, yn

    conf.registerPlugin('GroqAI', True)


# Register the plugin.
GroqAI = conf.registerPlugin('GroqAI')


# =====================================================================
# API KEY
# =====================================================================

conf.registerGlobalValue(
    GroqAI,
    'apiKey',
    registry.String(
        '',
        _(
            'Your Groq API key. '
            'Get one from https://console.groq.com/keys.'
        ),
        private=True
    )
)


# =====================================================================
# MODEL
# =====================================================================

conf.registerGlobalValue(
    GroqAI,
    'model',
    registry.String(
        'groq/compound-mini',
        _(
            'The Groq model to use. '
            'The default is groq/compound-mini.'
        )
    )
)


# =====================================================================
# MAXIMUM OUTPUT TOKENS
# =====================================================================

conf.registerGlobalValue(
    GroqAI,
    'maxTokens',
    registry.Integer(
        5000,
        _(
            'Maximum number of tokens the model may generate in its '
            'response. For groq/compound-mini, keep this at or below '
            'the model maximum.'
        ),
        1,
        8192
    )
)


# =====================================================================
# TEMPERATURE
# =====================================================================

conf.registerGlobalValue(
    GroqAI,
    'temperature',
    registry.Float(
        0.7,
        _(
            'Temperature for response randomness. '
            'Lower values are more deterministic; higher values are '
            'more creative.'
        ),
        0.0,
        2.0
    )
)


# =====================================================================
# ENABLED CHANNELS
# =====================================================================

conf.registerGlobalValue(
    GroqAI,
    'enabledChannels',
    registry.String(
        '',
        _(
            'Comma-separated list of channels where GroqAI is enabled.'
        ),
        private=False
    )
)


# =====================================================================
# PER-USER THROTTLING
# =====================================================================

conf.registerGlobalValue(
    GroqAI,
    'throttleSeconds',
    registry.Integer(
        12,
        _(
            'Number of seconds a user must wait between @ask commands. '
            'Set to 0 to disable the delay.'
        ),
        0,
        3600
    )
)


conf.registerGlobalValue(
    GroqAI,
    'throttleEnabled',
    registry.Boolean(
        True,
        _(
            'Whether per-user throttling is enabled for @ask commands.'
        )
    )
)


# =====================================================================
# DAILY REQUEST LIMIT - PER USER
#
# These are LOCAL BOT limits.
# They are NOT Groq API limits.
# =====================================================================

conf.registerGlobalValue(
    GroqAI,
    'dailyLimitPerUser',
    registry.Integer(
        15,
        _(
            'Maximum number of @ask requests a single IRC user may '
            'make per day. Set to 0 for unlimited.'
        ),
        0,
        1000
    )
)


# =====================================================================
# DAILY REQUEST LIMIT - GLOBAL
#
# This is a LOCAL BOT limit.
# Groq Free Plan RPD is separate and is reported by the API headers.
# =====================================================================

conf.registerGlobalValue(
    GroqAI,
    'globalDailyLimit',
    registry.Integer(
        250,
        _(
            'Maximum total number of @ask requests allowed by this '
            'bot per day across all users. Set to 0 for unlimited.'
        ),
        0,
        10000
    )
)


# =====================================================================
# DAILY TOKEN LIMIT - PER USER
#
# OPTIONAL LOCAL BOT LIMIT.
#
# 0 = disabled.
#
# This is deliberately disabled by default because Groq TPM is a
# per-minute API rate limit, not a daily token allowance.
# =====================================================================

conf.registerGlobalValue(
    GroqAI,
    'dailyTokensPerUser',
    registry.Integer(
        0,
        _(
            'Optional local daily token limit per IRC user. '
            'Set to 0 to disable. This is a bot-side policy and is '
            'not the same as Groq TPM.'
        ),
        0,
        1000000
    )
)


# =====================================================================
# DAILY TOKEN LIMIT - GLOBAL
#
# OPTIONAL LOCAL BOT LIMIT.
#
# 0 = disabled.
#
# Groq TPM is handled separately through the API rate-limit headers.
# =====================================================================

conf.registerGlobalValue(
    GroqAI,
    'globalDailyTokens',
    registry.Integer(
        0,
        _(
            'Optional local daily token limit across all IRC users. '
            'Set to 0 to disable. This is a bot-side policy and is '
            'not the same as Groq TPM.'
        ),
        0,
        10000000
    )
)


# =====================================================================
# MAXIMUM QUESTION SIZE
#
# This is specifically intended to prevent HTTP 413 errors caused by
# excessively large requests.
# =====================================================================

conf.registerGlobalValue(
    GroqAI,
    'maxQuestionChars',
    registry.Integer(
        12000,
        _(
            'Maximum number of characters allowed in an @ask question. '
            'This protects the bot from sending excessively large HTTP '
            'requests to Groq. Set to 0 for unlimited.'
        ),
        0,
        1000000
    )
)


# =====================================================================
# HTTP REQUEST TIMEOUT
# =====================================================================

conf.registerGlobalValue(
    GroqAI,
    'requestTimeout',
    registry.Integer(
        45,
        _(
            'Maximum number of seconds to wait for a Groq API request '
            'to complete.'
        ),
        5,
        300
    )
)


# =====================================================================
# PUBLIC VISIBILITY
# =====================================================================

conf.registerGlobalValue(
    GroqAI,
    'public',
    registry.Boolean(
        True,
        _(
            'Determines whether the GroqAI plugin is publicly visible '
            'in Limnoria plugin listings.'
        )
    )
)


# vim:set shiftwidth=4 tabstop=4 expandtab textwidth=79:
