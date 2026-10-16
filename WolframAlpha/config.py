###
# Copyright (c) 2014, spline
# Copyright (c) 2020, oddluck <oddluck@riseup.net>
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

import supybot.conf as conf
import supybot.registry as registry
import supybot.ircutils as ircutils

def configure(advanced):
    conf.registerPlugin("WolframAlpha", True)


WolframAlpha = conf.registerPlugin("WolframAlpha")


def _register_channel_value(name, value):
    # Limnoria keeps existing registry nodes on reload. Register the same node
    # so a setting that used to be global gains channel support without losing
    # the owner's value or requiring a restart.
    try:
        existing = WolframAlpha.get(name)
    except registry.NonExistentRegistryEntry:
        pass
    else:
        existing._help = value._help
        value = existing
    return conf.registerChannelValue(WolframAlpha, name, value)

conf.registerGlobalValue(
    WolframAlpha,
    "apiKey",
    registry.String("", """Your Wolfram Alpha API key.""", private=True),
)
_register_channel_value(
    "maxOutput",
    registry.Integer(4, """Initial labeled lines per response before using the standard more command, capped by outputLimit."""),
)
_register_channel_value(
    "useImperial",
    registry.Boolean(True, """Use imperial units? Defaults to yes."""),
)
conf.registerGlobalValue(
    WolframAlpha,
    "reinterpretInput",
    registry.Boolean(
        False,
        """Reinterpret input string if WA API cannot understand. Best to leave false.""",
    ),
)
conf.registerChannelValue(
    WolframAlpha,
    "disableANSI",
    registry.Boolean(False, """Do not display any ANSI (color/bold) for channel."""),
)

# vim:set shiftwidth=4 tabstop=4 expandtab textwidth=250:

conf.registerGlobalValue(
    WolframAlpha, "timeout",
    registry.PositiveInteger(20, """Timeout in seconds for a WolframAlpha API request."""),
)
conf.registerGlobalValue(
    WolframAlpha, "outputLimit",
    registry.PositiveInteger(10, """Maximum initial lines, including --fulloutput. Remaining lines are saved for the standard more command."""),
)


class IRCColor(registry.String):
    """A named IRC color, or default to use the reader's text color."""
    errormsg = "Use an IRC color name (such as white or light grey), or default; got %r."

    def setValue(self, value):
        value = value.strip().lower()
        if value != "default" and value not in ircutils.mircColors:
            self.error(value)
        super().setValue(value)


for name, default, help_text in (
    ("answerColor", "default", "Color for main answers, which are also bold."),
    ("labelColor", "teal", "Color for section labels and interpreted questions."),
    ("detailColor", "light grey", "Color for supporting information."),
):
    conf.registerChannelValue(WolframAlpha, name, IRCColor(default, help_text))

conf.registerGlobalValue(
    WolframAlpha, "cacheSeconds",
    registry.NonNegativeInteger(60, """Cache successful queries for this many seconds. Zero disables caching. BTC bypasses the cache."""),
)
conf.registerGlobalValue(
    WolframAlpha, "showLink",
    registry.Boolean(True, """Include one full-result link when the result is long or some sections are omitted and the URL fits. --link requests a link explicitly."""),
)

conf.registerChannelValue(
    WolframAlpha, "showWarnings",
    registry.Boolean(True, """Show spelling corrections, reinterpretations and other API warnings with answers."""),
)
conf.registerChannelValue(
    WolframAlpha, "showAssumptions",
    registry.Boolean(True, """Show the first current interpretation when the API supplies assumptions. Use --assumptions for all choices."""),
)


class OutputStyle(registry.OnlySomeStrings):
    validStrings = ("summary", "detailed", "shortest")


conf.registerChannelValue(
    WolframAlpha, "outputStyle",
    OutputStyle("summary", """Default answer layout: summary prioritizes the main answer and concise facts; detailed keeps API order; shortest selects the main answer."""),
)
conf.registerChannelValue(
    WolframAlpha, "preferredSections",
    registry.CommaSeparatedListOfStrings([], """Section titles or IDs to prioritize after the main answer in summary mode, separated by commas."""),
)
conf.registerChannelValue(
    WolframAlpha, "defaultLocation",
    registry.String("", """Default query location, for example Athens, Greece. Empty uses the API's default. --location overrides it."""),
)
conf.registerChannelValue(
    WolframAlpha, "showUnits",
    registry.Boolean(False, """Include API unit explanations after the answer sections. --units lists them directly."""),
)
