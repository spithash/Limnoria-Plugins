# WolframAlpha for Limnoria

Ask Wolfram|Alpha questions from IRC: calculations, conversions, dates, science,
geography, and whatever else you want to look up.

```text
<you> @wolframalpha --shortest 2+2
<bot> 2+2 :: 4
```

Normal replies put the main answer first and favor concise facts over large
tables. Each line has its own section label. Four lines appear initially;
remaining lines are kept in Limnoria's standard `@more` buffer, without another
API request.

## Setup

You need Limnoria on Python 3 and a Wolfram|Alpha AppID with Full Results API
access. No additional Python packages are required.

1. Copy the `WolframAlpha` directory into one of your bot's plugin directories.
2. Get an AppID from the [Developer Portal](https://developer.wolframalpha.com/).
   Check your account's API access and query allowance there.
3. Load the plugin and set the key in a **private message**:

```text
/msg YourBot load WolframAlpha
/msg YourBot config plugins.WolframAlpha.apiKey YOUR_APP_ID
```

Replace `YourBot` with your bot's nickname. You need the usual permissions for
loading plugins and changing settings. Don't paste your AppID into a channel.
Changing the key does not require a reload.

After updating the files:

```text
/msg YourBot reload WolframAlpha
```

To get two lines per `more` request, set Limnoria's standard batch size:

```text
/msg YourBot config plugins.Misc.mores 2
```

This setting also applies to other plugins. To change it for just one channel,
use `config channel #yourchannel plugins.Misc.mores 2` instead.

Existing global settings still work. `maxOutput` and `useImperial` can now also
be set per channel. The plugin does not create or change command aliases; use
`wolframalpha` or an alias you have already configured, such as `wa`.

## Everyday use

Examples use `@` as the command prefix. Use your bot's configured prefix.

```text
@wolframalpha moons of Jupiter
@more
@wolframalpha 180 cm in feet
@wolframalpha days until Christmas
@wolframalpha solve x^2 - 5x + 6 = 0
@wolframalpha integrate x^2 from 0 to 10
@btc 0.00000001 USD
```

By default, four labeled lines appear immediately. Each selected section gets
a preview before its longer remainder, so a large table doesn't take up the
whole initial response. Every continuation line repeats the section label:

```text
Physical properties :: mass | total | ...
Orbital properties :: orbital period | median | ... (12 more messages)
```

The example uses `...` as a placeholder for values. The plugin breaks actual
text at complete table rows or list items where possible, then at word
boundaries. Only a single word or formula longer than an entire line has to be
split within a token. Returned text that doesn't fit is saved for `more`.

The last initial line reports how many lines remain. With `plugins.Misc.mores`
set to `2`, each `@more` sends the next two lines, or the final single line.
The usual `@more <nick>` behavior is available for public results too.

The plugin builds labeled lines in Limnoria's existing continuation buffer; it
does not replace the `more` command. Native reply routing, notices, private
results, and the standard continuation maximum are respected. An answer beyond
`supybot.reply.mores.maximum` is shortened with an explicit limit notice.

A list may already contain `... (total: 95)` in the API response. Those missing
members were not sent to the plugin, so `more` cannot recover them. Try
`--states` for an expanded view, if the API offers one, or ask a narrower question.

## Query options

| Option | Purpose |
| --- | --- |
| `--shortest` | Select only the interpreted question and main answer; long answers still use `more`. |
| `--num <lines>` | Send this many initial labeled lines in API order. Remaining lines stay available through `more`. Capped by `outputLimit`. |
| `--fulloutput` | Use API order and send up to `outputLimit` initial lines, saving the remainder for `more`. |
| `--section <title>` | Select sections by title or ID, ignoring case. Exact matches take priority, then partial matches. Quote names containing spaces. |
| `--usemetric` | Request metric units. |
| `--reinterpret` | Allow Wolfram|Alpha to try a revised interpretation of the question. |
| `--location <place>` | Override the default query location. Quote names containing spaces. |
| `--link` | Request a full-result website link even if the answer is short. |

```text
@wolframalpha --section population Greece
@wolframalpha --usemetric distance from Earth to Moon
@wolframalpha --location "Athens, Greece" sunrise today
@wolframalpha --shortest --link speed of light
```

`--shortest` and `--fulloutput` cannot be combined. `--section` restricts the
answer to matching sections; run a new query to retrieve other sections.
`--shortest` selects the main answer and starts with one labeled line, even
when combined with `--num`; long answers remain available through `more`.

The full-result website link appears once at the end of the result.
It opens a fresh website search, which may present the answer differently from
the API, including any location or interpretation choices made through the
plugin. With long results, the link may appear in a later `more` chunk.

`btc <amount> <currency>` preserves small bitcoin amounts instead of rounding
them to two decimal places. It always makes a fresh request. Values come from
Wolfram|Alpha and may include a date or other context; this is not a live
exchange feed. Long conversion results also use the standard `more` command.

## References, units, and alternative views

These options list information instead of ordinary answers. They can be
combined with each other, but not with answer display options such as
`--shortest`, `--num`, or `--section`. Long lists use the same standard `more` handling.

| Option | Purpose |
| --- | --- |
| `--sources` | Reference names and links supplied for the query. |
| `--units` | Unit abbreviations and their long names, when supplied. |
| `--info` | Explanatory notes and reference links attached to result sections. |
| `--images` | Links to API-rendered images, including plots when available. |
| `--assumptions` | Numbered interpretations; the current choice in each group is marked. |
| `--states` | Numbered alternative result views, such as more digits. |

```text
@wolframalpha --sources population of Greece
@wolframalpha --units distance from Earth to Jupiter
@wolframalpha --info speed of light
@wolframalpha --images plot sin(x)
@wolframalpha --assumptions mercury
@wolframalpha --states pi
```

Metadata varies by query; some results provide no units, sources, images, or
alternative views. Images are requested only with `--images`; the bot sends
links rather than downloading or uploading the files. Unusually long reference URLs are reported as too long for IRC.

Use a returned number to select an interpretation or view:

```text
@wolframalpha --assumptions mercury
@wolframalpha --assume 2 --shortest mercury
@wolframalpha --states pi
@wolframalpha --state 1 --shortest pi
```

The numbers above are examples; use the choices returned for your query. Keep
the same query, location, and unit/reinterpretation options. Choices are resolved
from the response to that query, never from a shared "last search." If the API
changes its choices, list them again.

One interpretation can be selected per request. If combining `--assume` and
`--state`, first list views with that interpretation applied, for example
`--assume 2 --states mercury`. The interpretation is applied before the view.
Selecting choices can require additional API requests; cached responses are
reused where possible.

## Channel preferences

Defaults can make normal queries useful without extra switches:

```text
/msg YourBot config channel #yourchannel plugins.WolframAlpha.outputStyle summary
/msg YourBot config channel #yourchannel plugins.WolframAlpha.maxOutput 3
/msg YourBot config channel #yourchannel plugins.WolframAlpha.defaultLocation Athens, Greece
/msg YourBot config channel #yourchannel plugins.WolframAlpha.useImperial False
/msg YourBot config channel #yourchannel plugins.WolframAlpha.preferredSections Basic information, Population
```

`summary` puts the main answer first, then preferred sections and concise facts.
`detailed` keeps the API's section order. `shortest` selects the main answer. Display flags override the default style for that query.

| Channel-capable setting | Default | Purpose |
| --- | --- | --- |
| `outputStyle` | `summary` | `summary`, `detailed`, or `shortest`. |
| `maxOutput` | `4` | Initial labeled lines before using `more`. |
| `preferredSections` | Empty | Comma-separated titles or IDs to prioritize after the main answer in summary mode. |
| `defaultLocation` | Empty | Location for queries such as sunrise or weather; empty uses the API default. |
| `useImperial` | `True` | Request nonmetric units; set to `False` for metric. |
| `showUnits` | `False` | Add available unit explanations after answer sections. |
| `disableANSI` | `False` | Disable all IRC color and bold. |
| `answerColor` | `default` | Main answer color; answers are also bold. |
| `labelColor` | `teal` | Section and question labels. |
| `detailColor` | `light grey` | Supporting information. |
| `showWarnings` | `True` | Show spelling corrections, reinterpretations, and API warnings. |
| `showAssumptions` | `True` | Show the first current interpretation. |

Settings can also be changed globally with
`/msg YourBot config plugins.WolframAlpha.<setting> <value>`.

Color names include `white`, `teal`, `light grey`, and `dark grey`. `default`
uses the reader's normal text color. Default-color bold works across light and
dark themes; explicit white is available if you prefer it. API plaintext does
not contain bold or italic markup: the plugin applies its own styling.

Corrections and partial-result notices are included in answers. Detailed and
summary layouts show notes as a separate entry. Partial results are always
flagged, even with `showWarnings` disabled. Sub-result titles are retained so
individual values remain identifiable.

## Global settings and caching

| Setting | Default | Purpose |
| --- | --- | --- |
| `apiKey` | Empty | Private Wolfram|Alpha AppID. |
| `outputLimit` | `10` | Maximum initial lines, including `--fulloutput`; remaining lines are saved for `more`. |
| `timeout` | `20` | Network timeout in seconds. |
| `cacheSeconds` | `60` | Successful-query cache lifetime; `0` disables it. |
| `showLink` | `True` | Add one website link when the result needs continuation. |
| `reinterpretInput` | `False` | Allow revised interpretations by default. |

Up to 16 successful API responses are cached in memory. Display choices can
reuse the same response; units, location, image format, interpretation choices,
result views, and AppID are part of the cache key. Failed or unfinished results
are not cached. Simultaneous requests may still make separate API calls.

Nothing is written to disk, and reloading clears the API cache. Setting
`cacheSeconds` to `0` clears cached responses on the next request. Continuations
belong to Limnoria's ordinary `more` buffer and follow its normal behavior when
another command is run.

## Standard reply settings

The plugin leaves the bot's reply settings alone. Set `plugins.Misc.mores` to
`2` for two lines per `more`. The initial line count comes from WolframAlpha's
`maxOutput` setting, while the native settings below control continuations:

| Setting | Purpose |
| --- | --- |
| `plugins.Misc.mores` | Lines returned by each `more` request; set to `2` for the four-then-two layout. |
| `supybot.reply.mores.length` | Native chunk length; labeled lines leave room for continuation hints and are capped at 350 bytes before the hint. |
| `supybot.reply.mores.maximum` | Maximum total labeled lines retained for a result. |

These settings affect other plugins too. Normal `more` behavior, including
requesting another user's public continuation, is supplied by Limnoria's Misc
plugin. No WolframAlpha-specific continuation settings are needed.

## Troubleshooting and tests

The plugin reports missing keys, API errors, connection problems, and unreadable
responses. Some answers only contain images; use `--images` or the website link.
Wolfram|Alpha may return an abbreviated list or unfinished calculation even
when other sections succeeded.

Requests use HTTPS. Request URLs and AppIDs are not included in the plugin's
own failure messages or log entries. Queries and any configured location are
sent to Wolfram|Alpha. See the [Full Results API documentation](https://products.wolframalpha.com/api/documentation)
for the upstream response formats and available parameters.

Tests use mocked responses; no AppID or network access is needed. From a scratch
working directory containing only the `WolframAlpha` plugin directory:

```sh
supybot-test --no-network WolframAlpha
```

The suite covers commands, safe errors, caching, labeled output through the real
`more` command, four initial lines, two-line continuations, single result links,
formatting, metadata, location preferences, and reloading over legacy command
imports. Live answers
and available choices depend on Wolfram|Alpha's interpretation and data.

## Credits and licensing

Based on the WolframAlpha plugins by spline and oddluck, with earlier work from
[Supyiel](https://github.com/ormanya/Supyiel/tree/master/WolframAlpha).
Original copyright and license notices are retained. `LICENSE.txt` contains the
original MIT notice; the Python files also carry BSD-style notices that must
be preserved when redistributing them.
