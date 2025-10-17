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

# ANSI to IRC color mapping
x16colors = {
    "30": "1", "31": "4", "32": "3", "33": "11", 
    "34": "12", "35": "5", "36": "14", "37": "15",
    "90": "8", "91": "4", "92": "3", "93": "11",
    "94": "12", "95": "5", "96": "14", "97": "15",
}

class TLDR(callbacks.Plugin):
    """TLDR, A simplified alternative to man pages for Limnoria (a.k.a supybot)"""
    threaded = True

    def detect_invalid_switches(self, command):
        """Detect invalid switches and return a list of them."""
        valid_switches = set([
            '-h', '--help', '-v', '--version', '-u', '--update_cache',
            '-p', '--platform', '-l', '--list', '-s', '--search',
            '-s', '--source', '-c', '--color', '-r', '--render',
            '-L', '--language', '-m', '--markdown', '--print-completion'
        ])
        
        # Split command by spaces to handle arguments
        parts = command.split()
        
        # Find invalid switches
        invalid_switches = [part for part in parts if part in valid_switches]
        
        return invalid_switches

    def process_ansi(self, ansi):
        """Convert ANSI color codes to IRC color codes."""
        colors = []
        ansi = ansi.strip("\x1b[").strip("m").split(";")
        for code in ansi:
            if code == "0":
                colors.append("\x0F")  # Reset
            elif code in x16colors:
                colors.append(f"\x03{x16colors[code]}")  # Normal color
        return ''.join(colors)

    def tldr(self, irc, msg, args, command):
        """<command>
        Shows a TLDR summary of the given command.
        """
        try:
            # Detect invalid switches
            invalid_switches = self.detect_invalid_switches(command)
            if invalid_switches:
                irc.reply("Error: use of switches is not allowed.")
                return
            
            # Remove invalid switches from the command
            sanitized_command = ' '.join(part for part in command.split() if part not in invalid_switches)
            
            # Execute the tldr command with -c switch to enforce color output
            result = subprocess.run(['tldr', '--color'] + sanitized_command.split(), capture_output=True, text=True)
            if result.returncode != 0:
                # Error message handling
                error_message = result.stderr.strip()
                for line in error_message.splitlines():
                    irc.reply(f"Error: {line}")
                return

            # Process output for ANSI to IRC conversion
            output = result.stdout
            processed_output = ""
            current_color = "\x0F"  # Default to reset

            for line in output.splitlines():
                if line.strip():  # Skip empty lines
                    # Replace ANSI escape sequences
                    ansi_codes = re.findall(r'\x1B\[(\d+(;\d+)*)m', line)
                    for ansi_code in ansi_codes:
                        # Convert and append the corresponding IRC colors
                        irc_color = self.process_ansi(ansi_code[0])
                        if irc_color:
                            current_color = irc_color
                        line = line.replace(f"\x1B[{ansi_code[0]}m", current_color)

                    # Handle any remaining reset codes
                    line = line.replace("\x1B[0m", "\x0F")  # Reset

                    processed_output += line + '\n'

            # Send the processed output to the IRC channel
            for line in processed_output.splitlines():
                if line.strip():
                    irc.reply(line)

        except Exception as e:
            irc.reply(f"An error occurred: {str(e)}")

    tldr = wrap(tldr, ['text'])

Class = TLDR

# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:

