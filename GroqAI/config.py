###
# Copyright (c) 2026, Stathis Xantinidis spithash@Libera https://github.com/spithash
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
    _ = PluginInternationalization('GroqAI')
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
    conf.registerPlugin('GroqAI', True)


GroqAI = conf.registerPlugin('GroqAI')

# API Key configuration (private, so only the bot owner can see it)
conf.registerGlobalValue(GroqAI, 'apiKey',
    registry.String('', """Your Groq API key. Get it from https://console.groq.com/keys.""", private=True))

# Model configuration - default to the fast, free model
conf.registerGlobalValue(GroqAI, 'model',
    registry.String('llama-3.1-8b-instant', """The Groq model to use. 
Available models: llama-3.1-8b-instant, llama-3.3-70b-versatile, llama3-70b-8192, mixtral-8x7b-32768, gemma2-9b-it"""))

# Maximum tokens for the response (up to 131,072 for llama-3.1-8b-instant)
conf.registerGlobalValue(GroqAI, 'maxTokens',
    registry.Integer(1024, """Maximum number of tokens in the response. Max 131,072 for llama-3.1-8b-instant.""", 1, 131072))

# Temperature for response creativity (0.0 - 1.0)
conf.registerGlobalValue(GroqAI, 'temperature',
    registry.Float(0.7, """Temperature for response randomness. Lower = more deterministic, higher = more creative.""", 0.0, 1.0))

# Store enabled channels
conf.registerGlobalValue(GroqAI, 'enabledChannels',
    registry.String('', """Comma-separated list of channels where GroqAI is enabled.""", private=False))

# Throttle settings - default 12 seconds
conf.registerGlobalValue(GroqAI, 'throttleSeconds',
    registry.Integer(12, """Number of seconds a user must wait between @ask commands. Set to 0 to disable throttling.""", 0, 3600))

conf.registerGlobalValue(GroqAI, 'throttleEnabled',
    registry.Boolean(True, """Whether to enable per-user throttling for @ask commands."""))

# Daily request limits
conf.registerGlobalValue(GroqAI, 'dailyLimitPerUser',
    registry.Integer(50, """Maximum number of @ask requests per user per day. Set to 0 for unlimited.""", 0, 1000))

conf.registerGlobalValue(GroqAI, 'globalDailyLimit',
    registry.Integer(950, """Maximum total @ask requests per day across all users. Set to 0 for unlimited.""", 0, 10000))

# Input token limit per request (now used as a safety check before API call)
conf.registerGlobalValue(GroqAI, 'maxInputTokens',
    registry.Integer(4000, """Maximum input tokens allowed per question. Set to 0 for unlimited.""", 0, 8192))

# Daily token limits (based on Groq's limits: 12K/min, 100K/day for llama-3.3-70b-versatile)
conf.registerGlobalValue(GroqAI, 'dailyTokensPerUser',
    registry.Integer(10000, """Maximum tokens a user can use per day. Set to 0 for unlimited.""", 0, 100000))

conf.registerGlobalValue(GroqAI, 'globalDailyTokens',
    registry.Integer(90000, """Maximum total tokens per day across all users. Set to 0 for unlimited.""", 0, 100000))

# vim:set shiftwidth=4 tabstop=4 expandtab textwidth=79:
