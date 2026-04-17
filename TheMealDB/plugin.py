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

import requests
from supybot import utils, plugins, ircutils, callbacks
from supybot.commands import *
from supybot.i18n import PluginInternationalization

_ = PluginInternationalization('TheMealDB')


class TheMealDB(callbacks.Plugin):
    """Fetches recipes from TheMealDB API"""

    def __init__(self, irc):
        self.__parent = super(TheMealDB, self)
        self.__parent.__init__(irc)
        self._last_results = {}  # (channel, nick) -> meals list

    def _formatMeal(self, meal, is_random=False):
        name = meal.get("strMeal", "Unknown")
        category = meal.get("strCategory", "Unknown")
        area = meal.get("strArea", "Unknown")

        instructions = (meal.get("strInstructions") or "").replace("\r\n", " ").strip()

        ingredients = []
        for i in range(1, 21):
            ing = meal.get(f"strIngredient{i}")
            meas = meal.get(f"strMeasure{i}")

            if ing and ing.strip():
                meas = (meas or "").strip()
                ingredients.append(f"{meas} {ing.strip()}".strip())

        title_text = name
        if is_random:
            title_text = f"🔁 (random) {title_text}"

        title = ircutils.bold(f"🍽️ {title_text}")
        meta = f"({category}, {area})"
        line1 = f"{title} {meta}"

        ing_label = ircutils.bold("🧂 Ingredients:")
        line2 = f"{ing_label} " + ", ".join(ingredients)

        extra = []

        if instructions:
            extra.append(f"{ircutils.bold('👨‍🍳 Instructions:')} {instructions}")

        thumb = (meal.get("strMealThumb") or "").strip()
        if thumb:
            extra.append(f"{ircutils.bold('🖼️ Image:')} {thumb}")

        youtube = (meal.get("strYoutube") or "").strip()
        if youtube:
            extra.append(f"{ircutils.bold('▶️ Video:')} {youtube}")

        return [line1, line2] + extra

    def recipe(self, irc, msg, args, query):
        """[<recipe name>|<number>]
        Fetch a recipe by name, random, or select from previous results.
        """

        channel = msg.args[0]
        nick = msg.nick
        key = (channel, nick)

        # ---- Selection mode ----
        if query and query.isdigit():
            if key not in self._last_results:
                irc.reply("❌ No active search. Try searching first.")
                return

            meals = self._last_results[key]
            index = int(query) - 1

            if index < 0 or index >= len(meals):
                irc.reply("❌ Invalid selection.")
                return

            meal = meals[index]
            lines = self._formatMeal(meal)
            irc.replies(lines, prefixNick=False)
            return

        # ---- Random or search ----
        is_random = False

        if not query or query.lower() in ("random", "rnd", "surprise"):
            url = "https://www.themealdb.com/api/json/v1/1/random.php"
            is_random = True
        else:
            url = f"https://www.themealdb.com/api/json/v1/1/search.php?s={query}"

        try:
            response = requests.get(url, timeout=5)
            data = response.json()
        except Exception as e:
            irc.reply(f"❌ Error: {e}")
            return

        meals = data.get("meals")
        if not meals:
            irc.reply("❌ No recipe found.")
            return

        # ---- Single result ----
        if len(meals) == 1 or is_random:
            lines = self._formatMeal(meals[0], is_random=is_random)
            irc.replies(lines, prefixNick=False)
            return

        # ---- Multiple results (SINGLE LINE OUTPUT) ----
        meals = meals[:6]  # limit to avoid spam
        self._last_results[key] = meals

        items = [f"{i}. {m.get('strMeal')}" for i, m in enumerate(meals, 1)]
        joined = " | ".join(items)

        # Optional: trim to avoid IRC length limits
        if len(joined) > 350:
            joined = joined[:350] + "..."

        irc.reply(f"🔎 Found {len(meals)} recipes: {joined} 👉 Type: @recipe <number> to choose")

    recipe = wrap(recipe, [optional('text')])


Class = TheMealDB

# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
