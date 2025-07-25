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
            headers = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:139.0) Gecko/20100101 Firefox/139.0'}
            response = requests.get(url, headers=headers)
            soup = BeautifulSoup(response.content, 'html.parser')

            for td in soup.find_all('td'):
                rtseg_div = td.find('div', class_='rtseg')
                pseg_div = td.find('div', class_='pseg')
                etyseg_div = td.find('div', class_='etyseg')
                runseg_div = td.find('div', class_='runseg')
                syntx_div = td.find('div', class_='syntx')

                if rtseg_div and etyseg_div:
                    # Remove "Share" and "Tweet"
                    for share in rtseg_div.find_all(string=lambda s: "Share" in s or "Tweet" in s):
                        share.extract()

                    # Format bold and italics in rtseg
                    for b in rtseg_div.find_all('b'):
                        b.string = f"\x02{b.get_text(strip=True)}\x02"
                    for i_tag in rtseg_div.find_all(['i', 'em']):
                        i_tag.string = f"\x0314{i_tag.get_text(strip=True)}\x0F"
                    rt_text = rtseg_div.get_text(" ", strip=True)

                    # Add pseg if available
                    if pseg_div:
                        for b in pseg_div.find_all('b'):
                            b.string = f"\x02{b.get_text(strip=True)}\x02"
                        for i_tag in pseg_div.find_all(['i', 'em']):
                            i_tag.string = f"\x0314{i_tag.get_text(strip=True)}\x0F"
                        pseg_text = pseg_div.get_text(" ", strip=True)
                        rt_text = f"{rt_text} {pseg_text}"

                    # Format etyseg
                    for b in etyseg_div.find_all('b'):
                        b.string = f"\x02{b.get_text(strip=True)}\x02"
                    for i_tag in etyseg_div.find_all(['i', 'em']):
                        i_tag.string = f"\x0314{i_tag.get_text(strip=True)}\x0F"
                    ety_text = etyseg_div.get_text(" ", strip=True)
                    ety_text = unescape(ety_text).strip()
                    if ety_text.startswith('[') and ety_text.endswith(']'):
                        ety_text = ety_text[1:-1].strip()

                    # Format runseg if available
                    run_text = ""
                    if runseg_div:
                        for b in runseg_div.find_all('b'):
                            b.string = f"\x02{b.get_text(strip=True)}\x02"
                        for i_tag in runseg_div.find_all(['i', 'em']):
                            i_tag.string = f"\x0314{i_tag.get_text(strip=True)}\x0F"
                        run_text = runseg_div.get_text(" ", strip=True)

                    # Format syntx if available
                    syntx_text = ""
                    if syntx_div:
                        for b in syntx_div.find_all('b'):
                            b.string = f"\x02{b.get_text(strip=True)}\x02"
                        for i_tag in syntx_div.find_all(['i', 'em']):
                            i_tag.string = f"\x0314{i_tag.get_text(strip=True)}\x0F"
                        syntx_text = syntx_div.get_text(" ", strip=True)

                    # Combine everything
                    full_entry = f"{rt_text} :: {ety_text}"
                    if run_text:
                        full_entry += f" {run_text}"
                    if syntx_text:
                        full_entry += f" {syntx_text}"

                    irc.reply(full_entry)
                    return  # Stop after the first valid result

            irc.reply("No etymology information found.")

        except Exception as e:
            irc.reply(f"Error fetching etymology: {e}")

Class = Etymology

