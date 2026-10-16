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

from collections import OrderedDict
from decimal import Decimal, InvalidOperation
from threading import RLock
from time import monotonic
from urllib.parse import urlsplit
import builtins
import re
import xml.etree.ElementTree as ElementTree

import supybot.callbacks as callbacks
import supybot.conf as conf
from supybot.commands import getopts, wrap
import supybot.ircutils as ircutils
import supybot.utils as utils


class WolframAlpha(callbacks.Plugin):
    """Ask Wolfram|Alpha questions with wolframalpha, or convert bitcoin with btc.

    Try wolframalpha --shortest 2+2 or wolframalpha --usemetric 10 miles.
    """

    threaded = True
    _endpoint = "https://api.wolframalpha.com/v2/query"
    _response_limit = 2 * 1024 * 1024

    @staticmethod
    def _clean(text):
        text = ircutils.stripFormatting(text or "")
        text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
        return " ".join(text.split())

    def __init__(self, irc):
        super().__init__(irc)
        self._cache = OrderedDict()
        self._cache_lock = RLock()

    def _style(self, text, role, channel):
        if self.registryValue("disableANSI", channel):
            return text
        color = self.registryValue(role + "Color", channel)
        if color != "default":
            text = ircutils.mircColor(text, color)
        if role == "answer":
            text = ircutils.bold(text)
        return text

    def _reply(self, irc, text, query=None, link=False):
        # Pass complete text to Limnoria. Its standard more handling owns
        # wrapping, byte limits, storage, and continuation messages.
        if query and link:
            text += " | https://www.wolframalpha.com/input?" + utils.web.urlencode({"i": query})
        irc.reply(text)

    @staticmethod
    def _record(label, text, role="detail", separator=" :: "):
        return {"label": label, "text": text, "role": role,
                "separator": separator}

    def _pod_record(self, pod, main):
        return self._record(pod["title"], pod["text"], "answer" if pod is main else "detail")

    def _render(self, record, channel):
        label = self._style(record["label"], "label", channel) + record["separator"] if record["label"] else ""
        return label + self._style(record["text"], record["role"], channel)

    def _section_count(self, channel, options=None):
        options = options or {}
        ceiling = self.registryValue("outputLimit")
        count = ceiling if "fulloutput" in options else options.get("num", self.registryValue("maxOutput", channel))
        return max(1, min(count, ceiling))

    def _summary_order(self, pods, channel):
        main = self._answer(pods)
        preferred = [item.casefold() for item in self.registryValue("preferredSections", channel)]
        def rank(pod):
            names = (pod["title"].casefold(), pod["id"].casefold())
            preference = next((n for n, name in enumerate(preferred) if builtins.any(name in candidate for candidate in names)), len(preferred))
            dense = len(pod["text"].encode()) > 350
            return (pod["id"] == "Input", preference, not pod["primary"], dense)
        return [main] + sorted((pod for pod in pods if pod is not main), key=rank)

    @staticmethod
    def _take(text, budget):
        """Keep complete rows/items where possible, falling back to words."""
        if len(text.encode("utf-8")) <= budget:
            return text, ""
        prefix = text.encode("utf-8")[:budget].decode("utf-8", "ignore")
        separator = " ; " if " ; " in text else " | " if " | " in text else None
        if separator:
            index = prefix.rfind(separator)
            if index > 0:
                return text[:index].rstrip(), text[index + len(separator):].lstrip()
        index = prefix.rfind(" ")
        if index > 0:
            return text[:index].rstrip(), text[index + 1:].lstrip()
        return prefix, text[len(prefix):]

    def _labeled_lines(self, record, channel, budget):
        record = dict(record)
        # Leave room for a value even when an API title is unusually long.
        label_budget = min(80, budget // 3)
        if len(record["label"].encode()) > label_budget:
            record["label"] = self._take(record["label"], label_budget)[0]
        overhead = len(self._render(dict(record, text=""), channel).encode())
        if budget - overhead < 4:
            record["label"] = ""
            overhead = len(self._render(dict(record, text=""), channel).encode())
        available = max(4, budget - overhead)
        text = record["text"]
        while text:
            head, text = self._take(text, available)
            yield self._render(dict(record, text=head), channel)

    def _save_mores(self, irc, msg, lines):
        # Misc.more expects a reversed list of ready-to-send IrcMsgs. Use the
        # same reply builder and buffer as Limnoria's automatic wrapping so
        # standard more, more <nick>, routing, and private replies keep working.
        messages = [callbacks._makeReply(irc, msg, line) for line in lines]
        messages.reverse()
        mask = msg.prefix.split("!", 1)[1] if "!" in msg.prefix and "@" in msg.prefix else msg.nick
        private = not msg.channel
        if messages:
            private = not irc.isChannel(messages[0].args[0])
        with self._cache_lock:
            buffer = callbacks.ReplyIrcProxy._mores
            buffer[mask] = messages
            buffer[msg.nick] = (private, messages)

    def _send_result(self, irc, msg, records, query=None, force_link=False, count=None):
        if isinstance(irc, callbacks.NestedCommandsIrcProxy) and not irc.finalEvaled:
            # Nested commands must return one value to their enclosing command.
            self._reply(irc, " | ".join(self._render(record, msg.channel) for record in records))
            return
        count = count or self._section_count(msg.channel)
        network = irc.network
        length = conf.get(conf.supybot.reply.mores.length, channel=msg.channel, network=network)
        wire_length = 512 - irc._replyOverhead(msg)
        # The suffix uses the remaining space. Every saved line stays below
        # the normal framework/IRC limits and carries its own section label.
        budget = max(24, min(350, min(length or wire_length, wire_length) - 32))
        sections = [list(self._labeled_lines(record, msg.channel, budget)) for record in records]
        sections = [lines for lines in sections if lines]
        previews = [lines[0] for lines in sections[:count]]
        tails = [line for lines in sections[:count] for line in lines[1:]]
        lines = previews + tails + [line for section in sections[count:] for line in section]
        if not lines:
            return
        link = query and (force_link or (self.registryValue("showLink") and len(lines) > count))
        url = "https://www.wolframalpha.com/input?" + utils.web.urlencode({"i": query}) if link else ""
        if url and len(url.encode()) <= budget:
            if len((lines[-1] + " | " + url).encode()) <= budget:
                lines[-1] += " | " + url
            else:
                lines.append(url)
        maximum = conf.get(conf.supybot.reply.mores.maximum, channel=msg.channel, network=network)
        if len(lines) > maximum:
            lines = lines[:max(0, maximum - 1)] + ["Further output exceeds the bot's more limit." + (" " + url if url and len(url.encode()) < budget - 50 else "")]
        initial = min(count, len(lines))
        total = len(lines)
        def with_hint(index, line):
            left = total - index - 1
            if left:
                line += " " + ircutils.bold("(%d more %s)" % (left, "message" if left == 1 else "messages"))
            return line
        pending = [with_hint(index, line) for index, line in enumerate(lines[initial:], initial)]
        # noLengthCheck avoids creating a second automatic continuation for
        # these already-sized lines. Only the final initial line needs a hint.
        for index, line in enumerate(lines[:initial]):
            if index == initial - 1:
                line = with_hint(index, line)
            irc.reply(line, noLengthCheck=True)
        self._save_mores(irc, msg, pending)

    @staticmethod
    def _safe_url(url):
        try:
            parsed = urlsplit(url)
            return bool(parsed.scheme in ("http", "https") and parsed.hostname
                        and not parsed.username and not parsed.password
                        and not re.search(r"[\x00-\x20\x7f]", url))
        except ValueError:
            return False

    def _url_text(self, label, url):
        text = label + " :: " + url
        if len(text.encode()) > 350:
            return label + " (link too long for IRC)"
        return text

    def _information(self, document, units=False, empty_message=True):
        records = []
        seen = set()
        for pod in document.findall("pod"):
            title = self._clean(pod.get("title")) or "Result"
            for info in pod.findall("infos/info"):
                texts = []
                if units:
                    texts.extend(self._clean(unit.get("short")) + " = " + self._clean(unit.get("long"))
                                 for unit in info.findall("units/unit") if unit.get("short") and unit.get("long"))
                else:
                    if info.get("text"):
                        texts.append(self._clean(info.get("text")))
                    for link in info.findall("link"):
                        if self._safe_url(link.get("url", "")):
                            texts.append(self._url_text(self._clean(link.get("text")) or "Reference", link.get("url")))
                for text in texts:
                    text = ("Units" if units else "Info") + " — " + title + " :: " + text
                    if text not in seen:
                        records.append(self._record("", text))
                        seen.add(text)
        if records or not empty_message:
            return records
        return [self._record("", "No unit explanations were supplied." if units else "No additional notes or reference links were supplied.")]

    def _request(self, irc, query, metric=False, reinterpret=False, cache_allowed=True, assumption=None, podstate=None, location="", images=False, channel=None):
        api_key = self.registryValue("apiKey").strip()
        if not api_key or api_key == "Not set":
            irc.reply("WolframAlpha API key is not set. Set plugins.WolframAlpha.apiKey in a private message to the bot.")
            return None
        params = {
            "input": query, "appid": api_key, "format": "plaintext,image" if images else "plaintext",
            "units": "metric" if metric or not self.registryValue("useImperial", channel) else "nonmetric",
            "reinterpret": "true" if reinterpret or self.registryValue("reinterpretInput") else "false",
        }
        if location:
            params["location"] = location
        if assumption:
            params["assumption"] = assumption
        if podstate:
            params["podstate"] = podstate
        ttl = self.registryValue("cacheSeconds")
        cache_key = tuple(sorted(params.items()))
        now = monotonic()
        with self._cache_lock:
            # Never persist queries or keys to disk. Disabling also clears memory.
            for key, (created, _) in list(self._cache.items()):
                if not ttl or now - created >= ttl:
                    del self._cache[key]
            if cache_allowed and ttl and cache_key in self._cache:
                return self._cache[cache_key][1]
        url = self._endpoint + "?" + utils.web.urlencode(params)
        try:
            page = utils.web.getUrl(url, timeout=self.registryValue("timeout"),
                                    size=self._response_limit + 1)
        except Exception:
            # Exceptions may contain the URL and private AppID.
            self.log.warning("WolframAlpha API request failed.")
            irc.reply("Could not reach WolframAlpha. Please try again later.")
            return None
        if len(page) > self._response_limit:
            irc.reply("WolframAlpha returned too much data. Try a more specific question.")
            return None
        try:
            document = ElementTree.fromstring(page)
            if document.tag != "queryresult":
                raise ValueError("Unexpected response root")
        except (ElementTree.ParseError, ValueError):
            self.log.warning("WolframAlpha returned an invalid response.")
            irc.reply("WolframAlpha returned an unreadable response. Please try again later.")
            return None
        if document.get("error") == "true" or document.find("error") is not None:
            code = document.findtext("error/code", "")
            if code in ("1", "2"):
                irc.reply("WolframAlpha rejected the API key. Ask the bot owner to check the AppID and API access.")
            else:
                irc.reply("WolframAlpha could not process the request. Please try again later.")
            return None
        if cache_allowed and ttl and document.get("success") == "true" and document.find("pod") is not None and not self._partial(document):
            with self._cache_lock:
                self._cache[cache_key] = (monotonic(), document)
                self._cache.move_to_end(cache_key)
                while len(self._cache) > 16:
                    self._cache.popitem(last=False)
        return document

    def _pods(self, document):
        pods = []
        for index, pod in enumerate(document.findall("pod")):
            if pod.get("error") == "true":
                continue
            texts = []
            for subpod in pod.findall("subpod"):
                rows = [self._clean(row) for row in (subpod.findtext("plaintext") or "").splitlines()]
                text = " ; ".join(row for row in rows if row)
                subtitle = self._clean(subpod.get("title"))
                if text and subtitle and subtitle.casefold() != self._clean(pod.get("title")).casefold():
                    text = subtitle + ": " + text
                if text and text not in texts:
                    texts.append(text)
            if not texts:
                continue
            try:
                position = Decimal(pod.get("position", str(index)))
                if not position.is_finite():
                    position = Decimal(index)
            except InvalidOperation:
                position = Decimal(index)
            pods.append({
                "position": position,
                "title": self._clean(pod.get("title")) or "Result",
                "id": pod.get("id", ""),
                "primary": pod.get("primary") == "true",
                "text": " | ".join(texts),
            })
        return sorted(pods, key=lambda pod: pod["position"])

    @staticmethod
    def _answer(pods):
        answers = [pod for pod in pods if pod["id"] != "Input"]
        return next((pod for pod in answers if pod["primary"]),
                    next((pod for pod in answers if pod["id"] == "Result"),
                         answers[0] if answers else pods[0]))

    @staticmethod
    def _partial(document):
        return (builtins.any(document.get(name, "").lower() not in ("", "false", "0")
                    for name in ("timedout", "timedoutpods", "parsetimedout"))
                or builtins.any(pod.get("async", "").lower() not in ("", "false", "0") for pod in document.findall("pod")))

    def _assumptions(self, document):
        choices = []
        for assumption in document.findall("assumptions/assumption"):
            word = self._clean(assumption.get("word")) or self._clean(assumption.get("type")) or "Interpretation"
            for index, value in enumerate(assumption.findall("value")):
                token = value.get("input")
                desc = self._clean(value.get("desc")) or self._clean(value.get("name"))
                if token and desc:
                    choices.append((word + " → " + desc + (" (current)" if index == 0 else ""), token))
        return choices

    def _states(self, document):
        choices = []
        seen = set()
        for pod in document.findall("pod"):
            title = self._clean(pod.get("title")) or "Result"
            for state in pod.findall(".//state"):
                token = state.get("input")
                name = self._clean(state.get("name"))
                if token and name and token not in seen:
                    choices.append((title + " → " + name, token))
                    seen.add(token)
        return choices

    def _notes(self, document, channel):
        notes = []
        if self._partial(document):
            notes.append("Partial result: some calculations are unfinished.")
        if self.registryValue("showWarnings", channel):
            for warning in document.findall("warnings/*"):
                if warning.tag == "spellcheck":
                    word, suggestion = self._clean(warning.get("word")), self._clean(warning.get("suggestion"))
                    text = "Corrected " + word + " → " + suggestion if word and suggestion else self._clean(warning.get("text"))
                elif warning.tag == "reinterpret":
                    text = "Interpreted as " + self._clean(warning.get("new")) if warning.get("new") else self._clean(warning.get("text"))
                elif warning.tag == "translation":
                    text = "Translated to " + self._clean(warning.get("trans")) if warning.get("trans") else self._clean(warning.get("text"))
                else:
                    text = self._clean(warning.get("text")) or self._clean(warning.text)
                if text and text not in notes:
                    notes.append(text)
        if self.registryValue("showAssumptions", channel):
            for label, _ in self._assumptions(document):
                if label.endswith(" (current)"):
                    notes.append("Assuming " + label[:-10])
                    break
        return notes[:4]

    def _context(self, text, document, channel):
        notes = self._notes(document, channel)
        if notes:
            text = self._style(" / ".join(notes), "detail", channel) + " | " + text
        return text

    def _inspect(self, irc, msg, document, options):
        lines = []
        if "assumptions" in options:
            choices = self._assumptions(document)
            lines.extend("Interpretation " + str(n) + " :: " + label for n, (label, _) in enumerate(choices, 1))
            if not choices:
                lines.append("No alternative interpretations were supplied.")
        if "states" in options:
            choices = self._states(document)
            lines.extend("View " + str(n) + " :: " + label for n, (label, _) in enumerate(choices, 1))
            if not choices:
                lines.append("No alternative result views were supplied.")
        if "units" in options:
            lines.extend(record["text"] for record in self._information(document, units=True))
        if "info" in options:
            lines.extend(record["text"] for record in self._information(document, units=False))
        if "images" in options:
            images = []
            for pod in document.findall("pod"):
                title = self._clean(pod.get("title")) or "Image"
                for image in pod.findall("subpod/img"):
                    url = image.get("src", "")
                    if self._safe_url(url):
                        images.append(self._url_text(title, url))
            lines.extend(dict.fromkeys(images or ["No image links were supplied for this query."]))
        if "sources" in options:
            sources = []
            for source in document.findall("sources/source"):
                name = self._clean(source.get("text")) or self._clean(source.text) or "Source"
                url = source.get("url", "")
                try:
                    parsed = urlsplit(url)
                    valid = (parsed.scheme in ("http", "https") and parsed.hostname
                             and not parsed.username and not parsed.password
                             and not re.search(r"[\x00-\x20\x7f]", url))
                except ValueError:
                    valid = False
                if not valid:
                    continue
                limit = 350
                text = name + " :: " + url
                # Never turn a reference URL into a broken, truncated link.
                if len(text.encode()) > limit:
                    text = name + " (source link too long for IRC)"
                if text not in sources:
                    sources.append(text)
            lines.extend(sources or ["No source links were supplied for this query."])
        records = [self._record("", line, "detail") for line in lines]
        self._send_result(irc, msg, records)

    def _no_result(self, irc, document, channel=None):
        suggestions = [self._clean(node.text) for node in document.findall("didyoumeans/didyoumean")]
        suggestions = [text for text in suggestions if text]
        if suggestions:
            text = "No result. Did you mean: " + " or ".join(suggestions[:3]) + "?"
        else:
            tips = [self._clean(node.get("text")) for node in document.findall("tips/tip")]
            tips += [self._clean(node.get("msg")) for node in document.findall("futuretopic")]
            tips += [self._clean(node.text) for node in document.findall("languagemsg/*")]
            tip = next((text for text in tips if text), "Try a more specific question.")
            text = "No text result from WolframAlpha. " + tip
        self._reply(irc, self._context(text, document, channel))

    def wolframalpha(self, irc, msg, args, optlist, query):
        """[options] <query>

        Ask Wolfram|Alpha a question. --shortest shows the main answer;
        --fulloutput sends up to outputLimit lines before requiring more.
        --num overrides the initial line count, subject to outputLimit.
        --usemetric requests metric units; --reinterpret allows a revised query.
        --section matches section titles or IDs; quote titles containing spaces.
        --link includes a link to the full result when it fits in the reply.
        --sources, --assumptions and --states list reference links, interpretations
        and alternative result views instead of answers. --assume <number> and
        --state <number> apply a numbered choice from those lists to this query.
        --units and --info list unit explanations and notes; --images lists image
        links. --location <place> overrides the channel's default location.
        Use Limnoria's standard more command to read long replies.
        """
        options = dict(optlist)
        if options.get("num", 1) < 1:
            irc.error("--num must be at least 1.")
            return
        if "shortest" in options and "fulloutput" in options:
            irc.error("Choose either --shortest or --fulloutput.")
            return
        for name in ("assume", "state"):
            if name in options and options[name] < 1:
                irc.error("--" + name + " must be at least 1.")
                return
        inspect = builtins.any(name in options for name in ("sources", "assumptions", "states", "units", "info", "images"))
        if inspect and builtins.any(name in options for name in ("shortest", "fulloutput", "section", "num", "link")):
            irc.error("Use listing options separately from answer display options.")
            return
        request_options = {
            "metric": "usemetric" in options, "reinterpret": "reinterpret" in options,
            "location": options.get("location", self.registryValue("defaultLocation", msg.channel)),
            "images": "images" in options, "channel": msg.channel,
        }
        document = self._request(irc, query, **request_options)
        if document is None:
            return
        for name, selector, parameter in (("assume", self._assumptions, "assumption"),
                                          ("state", self._states, "podstate")):
            if name not in options:
                continue
            choices = selector(document)
            index = options[name] - 1
            if index >= len(choices):
                irc.error("That choice is unavailable. Use --" + ("assumptions" if name == "assume" else "states") + " with the same query to list choices.")
                return
            request_options[parameter] = choices[index][1]
            document = self._request(irc, query, **request_options)
            if document is None:
                return
        if inspect:
            self._inspect(irc, msg, document, options)
            return
        pods = self._pods(document)
        if not pods:
            self._no_result(irc, document, msg.channel)
            return
        if "section" in options:
            section = options["section"].strip().casefold()
            if not section:
                irc.error("--section needs a nonempty title or ID.")
                return
            exact = [pod for pod in pods if section in (pod["title"].casefold(), pod["id"].casefold())]
            matches = exact or [pod for pod in pods if section in pod["title"].casefold() or section in pod["id"].casefold()]
            if not matches:
                self._reply(irc, "No matching section. Available: " + ", ".join(dict.fromkeys(pod["title"] for pod in pods)))
                return
            pods = matches
        mode = self.registryValue("outputStyle", msg.channel)
        if "shortest" in options:
            mode = "shortest"
        elif "fulloutput" in options or "num" in options or "section" in options:
            mode = "detailed"
        count = self._section_count(msg.channel, options)
        records = []
        notes = self._notes(document, msg.channel)
        if mode == "shortest":
            answer = self._answer(pods)
            question = self._clean(next((pod["text"] for pod in pods if pod["id"] == "Input"), query))
            text = (" / ".join(notes) + " | " if notes else "") + answer["text"]
            records = [self._record(question, text, "answer")]
            count = 1
        else:
            ordered = self._summary_order(pods, msg.channel) if mode == "summary" else pods
            main = self._answer(pods)
            if notes:
                records.append(self._record("Notes", " / ".join(notes), "detail"))
            records.extend(self._pod_record(pod, main) for pod in ordered)
        if self.registryValue("showUnits", msg.channel):
            records.extend(self._information(document, units=True, empty_message=False))
        self._send_result(irc, msg, records, query, force_link="link" in options, count=count)

    wolframalpha = wrap(wolframalpha, [getopts({
        "num": "int", "reinterpret": "", "usemetric": "",
        "shortest": "", "fulloutput": "", "section": "something", "link": "",
        "sources": "", "assumptions": "", "states": "",
        "assume": "int", "state": "int", "location": "something",
        "units": "", "info": "", "images": "",
    }), "text"])

    def btc(self, irc, msg, args, amount, currency):
        """<amount> <currency>

        Convert a nonnegative bitcoin amount to a currency, for example btc 0.001 EUR.
        Values come from Wolfram|Alpha and may not be current market quotes.
        """
        try:
            value = Decimal(amount)
            if not value.is_finite() or value < 0 or len(amount) > 64 or abs(value.adjusted()) > 100:
                raise ValueError("Invalid amount")
        except (InvalidOperation, ValueError):
            irc.error("Use a finite, nonnegative bitcoin amount, such as 0.001.")
            return
        amount = f"{value:f}"
        document = self._request(irc, amount + " BTC to " + currency, cache_allowed=False,
                                 channel=msg.channel, location=self.registryValue("defaultLocation", msg.channel))
        if document is None:
            return
        pods = [pod for pod in self._pods(document) if pod["id"] != "Input"]
        if not pods:
            self._no_result(irc, document, msg.channel)
            return
        answer = self._answer(pods)["text"]
        if "not compatible" in answer.lower():
            self._reply(irc, "Conversion from BTC to " + self._clean(currency) + " is not available.")
            return
        notes = self._notes(document, msg.channel)
        text = (" / ".join(notes) + " | " if notes else "") + answer
        self._send_result(irc, msg, [self._record(amount + " BTC =", text, "answer", separator=" ")],
                          amount + " BTC to " + currency, count=1)

    btc = wrap(btc, ["something", "text"])


Class = WolframAlpha
