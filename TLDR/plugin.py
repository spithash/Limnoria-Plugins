###
# Copyright (c) 2024, Stathis Xantinidis @spithash
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

import subprocess
import re
from supybot import utils, plugins, ircutils, callbacks
from supybot.commands import *
from supybot.i18n import PluginInternationalization

_ = PluginInternationalization('TLDR')

class TLDR(callbacks.Plugin):
    """TLDR, A simplified alternative to man pages for Limnoria (a.k.a supybot)"""
    threaded = True

    def tldr(self, irc, msg, args, command):
        """<command>
        Shows a TLDR summary of the given command.
        """
        try:
            # Execute the tldr command
            result = subprocess.run(['tldr', command], capture_output=True, text=True)
            if result.returncode != 0:
                # Error message handling
                error_message = result.stderr.strip()
                for line in error_message.splitlines():
                    irc.reply(f"Error: {line}")
                return

            # Clean up the output to handle ANSI colors for IRC
            output = result.stdout

            # Remove ANSI color codes
            ansi_escape = re.compile(r'\x1B[@-_][0-?]*[ -/]*[@-~]')
            clean_output = ansi_escape.sub('', output)

            # Process lines to apply formatting
            for line in clean_output.splitlines():
                if line.strip():  # Skip empty lines
                    # Check if the line is a comment (starts with "- " and ends with ":")
                    if re.match(r'^- .+:$', line.strip()):
                        # Make comments green
                        line = ircutils.mircColor(line, 'green')
                    else:
                        # Bold the matching words (command-related)
                        line = re.sub(rf'\b{re.escape(command)}\b', ircutils.bold(ircutils.mircColor(command, 'white')), line)

                    irc.reply(line)

        except Exception as e:
            irc.reply(f"An error occurred: {str(e)}")

    tldr = wrap(tldr, ['text'])

Class = TLDR

# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:

