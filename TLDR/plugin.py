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

    def sanitize_command(self, command):
        """Sanitize the command to remove unwanted switches."""
        valid_switches = set([
            '-h', '--help', '-v', '--version', '-u', '--update_cache',
            '-p', '--platform', '-l', '--list', '-s', '--search',
            '-s', '--source', '-c', '--color', '-r', '--render',
            '-L', '--language', '-m', '--markdown', '--print-completion'
        ])

        # Split command by spaces to handle arguments
        parts = command.split()
        
        # Check if any part of the command is an invalid switch
        for part in parts:
            if part in valid_switches:
                return True  # Indicates invalid switch used

        return False

    def tldr(self, irc, msg, args, command):
        """<command>
        Shows a TLDR summary of the given command.
        """
        try:
            # Check for invalid switches
            if self.sanitize_command(command):
                irc.reply("Error: use of switches is not allowed.")
                return
            
            # Sanitize the command to remove unwanted switches
            sanitized_command = ' '.join(part for part in command.split() if part not in self.sanitize_command(command))
            
            # Execute the tldr command with -c switch to enforce color output
            result = subprocess.run(['tldr', '-c'] + sanitized_command.split(), capture_output=True, text=True)
            if result.returncode != 0:
                # Error message handling
                error_message = result.stderr.strip()
                for line in error_message.splitlines():
                    irc.reply(f"Error: {line}")
                return

            # Print raw ANSI codes to IRC without additional text
            output = result.stdout
            for line in output.splitlines():
                if line.strip():  # Skip empty lines
                    irc.reply(line)

        except Exception as e:
            irc.reply(f"An error occurred: {str(e)}")

    tldr = wrap(tldr, ['text'])

Class = TLDR

# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:

