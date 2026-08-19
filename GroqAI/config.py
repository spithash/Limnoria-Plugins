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
    _ = lambda x: x


def configure(advanced):
    from supybot.questions import expect, anything, something, yn

    conf.registerPlugin(
        'GroqAI',
        True
    )

    GroqAI = conf.registerPlugin(
        'GroqAI'
    )


# ----------------------------------------------------------------------
# GROQ API
# ----------------------------------------------------------------------

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


conf.registerGlobalValue(
    GroqAI,
    'model',
    registry.String(
        'groq/compound-mini',
        _(
            'The Groq model to use.'
        )
    )
)


# ----------------------------------------------------------------------
# OUTPUT / QUESTION
# ----------------------------------------------------------------------

conf.registerGlobalValue(
    GroqAI,
    'maxQuestionChars',
    registry.Integer(
        220,
        _(
            'Maximum characters allowed in a question.'
        ),
        0,
        1000000
    )
)


conf.registerGlobalValue(
    GroqAI,
    'maxResponseChars',
    registry.Integer(
        1200,
        _(
            'Maximum characters allowed in the final IRC response. '
            'Set to 0 to disable the local response length limit.'
        ),
        0,
        1000000
    )
)


# ----------------------------------------------------------------------
# CHANNELS
# ----------------------------------------------------------------------

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


# ----------------------------------------------------------------------
# PER-USER THROTTLING
# ----------------------------------------------------------------------

conf.registerGlobalValue(
    GroqAI,
    'throttleSeconds',
    registry.Integer(
        12,
        _(
            'Number of seconds a user must wait between @ask commands.'
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
            'Whether per-user throttling is enabled.'
        )
    )
)


# ----------------------------------------------------------------------
# GLOBAL RPM
# ----------------------------------------------------------------------

conf.registerGlobalValue(
    GroqAI,
    'localRpmLimit',
    registry.Integer(
        30,
        _(
            'Maximum number of Groq API requests the bot will '
            'start within one rolling 60-second period.'
        ),
        0,
        1000
    )
)


# ----------------------------------------------------------------------
# DAILY REQUEST LIMITS
# ----------------------------------------------------------------------

conf.registerGlobalValue(
    GroqAI,
    'dailyLimitPerUser',
    registry.Integer(
        25,
        _(
            'Maximum successful requests per user per day. '
            'Set to 0 to disable the per-user daily limit.'
        ),
        0,
        1000
    )
)


conf.registerGlobalValue(
    GroqAI,
    'globalDailyLimit',
    registry.Integer(
        250,
        _(
            'Maximum successful requests per day across all users. '
            'Set to 0 to disable the global daily limit.'
        ),
        0,
        10000
    )
)


# ----------------------------------------------------------------------
# OPTIONAL TOKEN LIMITS
# ----------------------------------------------------------------------

conf.registerGlobalValue(
    GroqAI,
    'dailyTokensPerUser',
    registry.Integer(
        0,
        _(
            'Optional daily token limit per user. '
            'Set to 0 to disable.'
        ),
        0,
        1000000
    )
)


conf.registerGlobalValue(
    GroqAI,
    'globalDailyTokens',
    registry.Integer(
        0,
        _(
            'Optional global daily token limit. '
            'Set to 0 to disable.'
        ),
        0,
        10000000
    )
)


# ----------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------

conf.registerGlobalValue(
    GroqAI,
    'requestTimeout',
    registry.Integer(
        45,
        _(
            'Timeout in seconds for Groq API requests.'
        ),
        5,
        300
    )
)


# ----------------------------------------------------------------------
# VISIBILITY
# ----------------------------------------------------------------------

conf.registerGlobalValue(
    GroqAI,
    'public',
    registry.Boolean(
        True,
        _(
            'Determines whether the plugin is publicly visible.'
        )
    )
)


# vim:set shiftwidth=4 tabstop=4 expandtab textwidth=79:
