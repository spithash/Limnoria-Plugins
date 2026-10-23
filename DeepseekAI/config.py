###
# Copyright (c) 2026, Stathis Xantinidis
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


def configure(advanced):
    conf.registerPlugin("DeepseekAI", True)


DeepseekAI = conf.registerPlugin("DeepseekAI")
conf.registerGlobalValue(DeepseekAI, "apiKey", registry.String(
    "", "DeepSeek API key from https://platform.deepseek.com/api_keys.", private=True))
conf.registerGlobalValue(DeepseekAI, "model", registry.String(
    "deepseek-flash", "DeepSeek chat model identifier."))
# Limnoria keeps existing registry objects on plugin reload. Upgrade the old
# global setting in place so reloads preserve its configured text and gain
# channel/network support without requiring a bot restart.
try:
    _existing_prompt = DeepseekAI.get("systemPrompt")
except registry.NonExistentRegistryEntry:
    pass
else:
    _existing_prompt._supplyDefault = True
    _existing_prompt._networkValue = True
    _existing_prompt._channelValue = True
    _existing_prompt._opSettable = True
conf.registerChannelValue(DeepseekAI, "systemPrompt", registry.String(
    "You are a helpful IRC assistant. Reply in the user's language, including Greek or English. "
    "Be concise and use plain text without Markdown tables or headings.",
    "Instructions sent with each question. The global value is the default; "
    "network/channel overrides can customize tone, language, and personality."))
conf.registerChannelValue(DeepseekAI, "enabled", registry.Boolean(
    False, "Whether @ai is enabled in this channel. Use DeepseekAI enable as owner."))
conf.registerChannelValue(DeepseekAI, "replyToNick", registry.Boolean(
    False, "Treat nick-addressed text that is not a known command as an AI question. "
    "Requires reply.whenAddressedBy.nick and DeepseekAI.enabled in this channel."))
conf.registerGlobalValue(DeepseekAI, "allowPrivate", registry.Boolean(
    False, "Whether to allow @ai in private messages."))
conf.registerGlobalValue(DeepseekAI, "throttleEnabled", registry.Boolean(
    True, "Whether to enforce the per-user cooldown."))
conf.registerGlobalValue(DeepseekAI, "formatting", registry.Boolean(
    True, "Convert Markdown bold and emphasis to IRC formatting."))
conf.registerGlobalValue(DeepseekAI, "greyItalics", registry.Boolean(
    True, "Render emphasis in grey; False uses native IRC italic instead."))

class BoundedInteger(registry.Integer):
    """A registry integer with inclusive limits."""
    def __init__(self, default, help, minimum=0, maximum=1000000000,
                 **kwargs):
        self.minimum = minimum
        self.maximum = maximum
        # Limnoria reconstructs values with setDefault=False when saving.
        super().__init__(default, help, **kwargs)

    def setValue(self, value):
        if not self.minimum <= value <= self.maximum:
            self.errormsg = "Value must be between %s and %s." % (self.minimum, self.maximum)
            self.error(value)
        super().setValue(value)


# Limits prevent invalid settings from disabling protection accidentally.
for name, default, low, high, help_text in (
    ("webDailyLimit", 15, 0, 10000, "Shared daily @aiweb attempt limit, including failures; 0 disables web search."),
    ("throttleSeconds", 12, 0, 3600, "Seconds between a user's API attempts."),
    ("localRpmLimit", 30, 0, 1000, "Global API attempts per rolling minute; 0 disables."),
    ("maxConcurrentRequests", 2, 1, 10, "Maximum simultaneous API requests; no pending queue."),
    ("dailyLimitPerUser", 25, 0, 10000, "API attempts per user per day, including failures; 0 disables."),
    ("globalDailyLimit", 250, 0, 100000, "Global API attempts per day, including failures; 0 disables."),
    ("dailyTokensPerUser", 0, 0, 100000000, "Daily tokens per user; checked before requests; 0 disables."),
    ("globalDailyTokens", 0, 0, 1000000000, "Global daily tokens; checked before requests; 0 disables."),
    ("maxQuestionChars", 1000, 1, 10000, "Maximum question length in characters."),
    ("maxResponseChars", 800, 80, 2000, "Maximum IRC answer length in characters."),
    ("maxTokens", 512, 1, 8192, "Maximum generated tokens per API request."),
    ("requestTimeout", 45, 5, 300, "HTTP timeout in seconds."),
    ("historyExchanges", 20, 0, 100, "Recent question/reply pairs per user per channel; 0 disables."),
    ("historyHours", 24, 1, 168, "Conversation retention in hours, cleaned when a question arrives."),
):
    conf.registerGlobalValue(DeepseekAI, name, BoundedInteger(default, help_text, low, high))
