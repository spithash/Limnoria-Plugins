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

    def _strip_www(self, url):
        if url.startswith("http://www."):
            return "http://" + url[len("http://www."):]
        if url.startswith("https://www."):
            return "https://" + url[len("https://www."):]
        return url

    def _search_multiple_terms(self, terms):
        results = []
        seen_ids = set()

        for term in terms:
            url = f"https://themealdb.com/api/json/v1/1/search.php?s={term}"
            try:
                data = requests.get(url, timeout=5).json()
            except:
                continue

            meals = data.get("meals") or []
            for m in meals:
                meal_id = m.get("idMeal")
                if meal_id not in seen_ids:
                    seen_ids.add(meal_id)
                    results.append(m)

        return results

    def _score_meal(self, meal, terms):
        name = (meal.get("strMeal") or "").lower()

        ingredients = []
        for i in range(1, 21):
            ing = meal.get(f"strIngredient{i}")
            if ing:
                ingredients.append(ing.lower())

        ingredient_text = " ".join(ingredients)

        anchor = terms[-1] if terms else ""
        modifiers = terms[:-1] if len(terms) > 1 else []

        score = 0

        if anchor in name:
            score += 5
        elif anchor in ingredient_text:
            score += 3
        else:
            return -999

        for t in modifiers:
            if t in name:
                score += 2
            elif t in ingredient_text:
                score += 1

        return score

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

        # ---- MEDIA MERGE LINE ----
        media_parts = []

        thumb = (meal.get("strMealThumb") or "").strip()
        if thumb:
            thumb = self._strip_www(thumb)
            media_parts.append(f"🖼️ {thumb}")

        youtube = (meal.get("strYoutube") or "").strip()
        if youtube:
            youtube = self._strip_www(youtube)
            media_parts.append(f"▶️ {youtube}")

        if media_parts:
            extra.append(f"{ircutils.bold('📎 Media:')} " + " | ".join(media_parts))

        return [line1, line2] + extra

    def recipe(self, irc, msg, args, query):
        """[<recipe name>|<number>]"""

        channel = msg.args[0]
        nick = msg.nick
        key = (channel, nick)

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
            irc.replies(self._formatMeal(meal), prefixNick=False)
            return

        if query:
            words = query.split()
            if len(words) > 3:
                irc.reply("❌ Please use up to 3 words max.")
                return

        is_random = False

        if not query or query.lower() in ("random", "rnd", "surprise"):
            url = "https://themealdb.com/api/json/v1/1/random.php"
            is_random = True
        else:
            url = f"https://themealdb.com/api/json/v1/1/search.php?s={query}"

        try:
            data = requests.get(url, timeout=5).json()
        except Exception as e:
            irc.reply(f"❌ Error: {e}")
            return

        meals = data.get("meals")

        if query and 2 <= len(query.split()) <= 3:
            terms = [t.lower() for t in query.split() if len(t) > 2]

            if terms:
                merged = self._search_multiple_terms(terms)

                if merged:
                    merged.sort(key=lambda m: self._score_meal(m, terms), reverse=True)
                    meals = merged

        if not meals:
            irc.reply("❌ No recipe found.")
            return

        if len(meals) == 1 or is_random:
            irc.replies(self._formatMeal(meals[0], is_random=is_random), prefixNick=False)
            return

        meals = meals[:10]
        self._last_results[key] = meals

        items = [f"{i}. {m.get('strMeal')}" for i, m in enumerate(meals, 1)]
        joined = " | ".join(items)

        if len(joined) > 350:
            joined = joined[:350] + "..."

        irc.reply(
            f"🔎 Found {len(meals)} recipes: {joined}  |  👉 Type: @recipe <number> to choose"
        )

    recipe = wrap(recipe, [optional('text')])


Class = TheMealDB

# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
