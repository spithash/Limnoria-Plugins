###
# Copyright (c) 2025, Stathis Xantinidis https://github.com/spithash
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

from supybot import utils, plugins, ircutils, callbacks
from supybot.commands import wrap
import requests
from bs4 import BeautifulSoup
from html import unescape

class Etymology(callbacks.Plugin):
    """Fetches etymology of a word from ahdictionary.com."""

    @wrap(['text'])
    def ety(self, irc, msg, args, word):
        """<word>
        Returns the etymology of the given word from ahdictionary.com.
        """
        try:
            url = f"https://www.ahdictionary.com/word/search.html?q={utils.web.urlquote(word)}"
            headers = {'User-Agent': 'Mozilla/5.0'}
            response = requests.get(url, headers=headers)
            soup = BeautifulSoup(response.content, 'html.parser')

            entries = []

            for td in soup.find_all('td'):
                rtseg_div = td.find('div', class_='rtseg')
                etyseg_div = td.find('div', class_='etyseg')

                if rtseg_div and etyseg_div:
                    # Remove any strings containing "Share" or "Tweet"
                    for share in rtseg_div.find_all(string=lambda s: "Share" in s or "Tweet" in s):
                        share.extract()

                    # Wrap bold text inside rtseg with IRC bold \x02
                    for b in rtseg_div.find_all('b'):
                        bold_text = b.get_text(strip=True)
                        b.string = f"\x02{bold_text}\x02"

                    rt_text = rtseg_div.get_text(" ", strip=True)

                    # Wrap bold text inside etyseg with IRC bold \x02
                    for b in etyseg_div.find_all('b'):
                        bold_text = b.get_text(strip=True)
                        b.string = f"\x02{bold_text}\x02"

                    ety_text = etyseg_div.get_text(" ", strip=True)

                    entry = f"{rt_text} :: {unescape(ety_text)}"
                    entries.append(entry)

            if entries:
                for entry in entries:
                    irc.reply(entry)
            else:
                irc.reply("No etymology information found.")

        except Exception as e:
            irc.reply(f"Error fetching etymology: {e}")

Class = Etymology

