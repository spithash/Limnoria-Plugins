# DeepseekAI

Query DeepSeek from Limnoria with `@ai <question>`, recent conversation history,
and flood protection. HTTP requests run in threads so the bot remains responsive.

## Setup

Requires Python 3, Limnoria, and `httpx` in the bot's Python environment. The
package declares these dependencies in `pyproject.toml`.

Load the plugin and set the private API key **in a private message to the bot**:

```text
@load DeepseekAI
@config plugins.DeepseekAI.apiKey YOUR_DEEPSEEK_API_KEY
```

In each channel you want to use, run this as a bot owner:

```text
@DeepseekAI enable
```

GroqAI also defines `ai`. If both plugins are loaded, route the command explicitly:

```text
@defaultplugin ai DeepseekAI
```

Then ask a question:

```text
@ai Explain what an IRC bouncer does.
```

`@` is Limnoria's command prefix, configured through
`supybot.reply.whenAddressedBy.chars`. Include `@` there if your bot uses another
prefix. The plugin registers the `ai` command and follows normal Limnoria command
routing; it does not intercept arbitrary channel traffic.

## Questions addressed to the bot's nick

As owner, enable nick addressing so normal commands such as `botnick: help`
work. You can send this privately to the bot (using your configured prefix):

```text
@config reply.whenAddressedBy.nick True
```

Then run these in each desired channel, on the desired network:

```text
@DeepseekAI enable
@DeepseekAI nickreply on
@DeepseekAI status
```

Now `botnick: explain IRC bouncers` asks DeepSeek, while `botnick: help` runs the
normal command. `botnick ai explain IRC bouncers` still works too. Use your bot's
current nick.
Recognized commands take priority, including their syntax and
permission errors; use explicit `ai` if a question starts with a command name.

An empty `reply.whenAddressedBy.chars` disables character prefixes in that
channel; nick addressing is controlled separately. Keep it empty in any
channel where you want nick-only addressing. Other channels can keep
`@`. Plain channel conversation and unknown `@commands` do not trigger AI.
The fallback is off by default and requires both `enabled` and `replyToNick`.
It shares `ai` history, cooldowns, usage limits, and API settings.

Both `enabled` and `replyToNick` are read for the current network and channel.
The owner commands `DeepseekAI enable` and `DeepseekAI disable` change only
that network/channel pair; `status` checks the same scope. Limnoria can fall
back to older channel-only entries, so set those to False when migrating to
network-specific enablement. Its `config channel` command also writes a
channel-only compatibility value; reset it with, for example,
`config plugins.DeepseekAI.enabled.<channel> False` after enabling a specific
network. Apply the same cleanup to `replyToNick` for strict network isolation.

Nick-addressed text that does not start with a recognized command is sent as
literal prompt text: quotes, square brackets, backslashes, and pipes are
preserved, including unmatched quotes or brackets. Text inside brackets is
not executed as a nested command. Recognized commands (including explicit
`ai` and `aiweb`) still follow Limnoria's normal parsing rules.

`nickreply` changes only the current network/channel's `replyToNick` setting.
Use `@DeepseekAI nickreply off` to turn nick prompts off. This command does not
enable AI or change Limnoria's nick-addressing setting. `status`
shows the network, channel, AI enablement, nick replies, nick addressing, and
whether the effective personality differs from the global default.

## Personalities

`plugins.DeepseekAI.systemPrompt` contains the instructions sent with each
question. Its global value remains the default for channels without an
override. The built-in default in `config.py` is unchanged: concise, helpful
answers in the user's language, including Greek or English.

To customize just the current network/channel, run this as owner in that channel:

```text
@DeepseekAI prompt Be a helpful technical assistant. Answer only in English, concisely and with dry wit.
@flush
```

`@DeepseekAI prompt` without instructions shows the effective prompt privately.
Use `@DeepseekAI prompt <channel>` to view another channel's prompt on the same
network, including from a private message. You can also set it remotely with
`@DeepseekAI prompt <channel> <instructions>`. The bot must be in that channel.
To manage a channel on another network, send the command to the bot there.
The command writes the existing `systemPrompt` setting only for the current
network and selected channel; it does not change the global or channel-only defaults.
Instructions follow normal Limnoria command quoting and nesting rules.

For prompts longer than one IRC message, send explicit chunks in order:

```text
@DeepseekAI prompt <channel> First part of the instructions.
@DeepseekAI promptappend <channel> Next part of the instructions.
@DeepseekAI promptappend <channel> Final part of the instructions.
@DeepseekAI prompt <channel>
@flush
```

Each append joins the new chunk with a space and updates the same configuration
value. Wait for each success reply before sending the next chunk. `prompt`
replaces the old text; `promptappend` extends the effective text, including the
default if no override exists yet. Omit `<channel>` when working in the target
channel. Visual wrapping in your IRC client does not split a message, but actual
new messages or pasted lines are not automatically combined: every continuation
needs its own `promptappend` command. Long prompt replies use Limnoria's normal
pagination; ask `@more` privately for the remaining text when indicated.

You can also use `config` directly. First run `@DeepseekAI status` in the target
channel to register its configuration entries, then set the fully qualified
entry, replacing both placeholders:

```text
@config plugins.DeepseekAI.systemPrompt.:<network>.<channel> Be a helpful technical assistant. Answer only in English, concisely and with dry wit.
@flush
```

Use a channel name including its `#` prefix. This direct setting changes only
that network/channel pair. It avoids the channel-only compatibility entry
written by `config channel`. Set `plugins.DeepseekAI.systemPrompt` without the
suffix to edit the global default instead. Unconfigured channels inherit it.

Limnoria saves the global prompt and explicit overrides in the bot's `.conf`
file when it saves configuration, including on `@flush`. They survive reloads
and restarts. No channel name or custom personality is hardcoded in the plugin.
For long prompts that exceed your IRC server's message limit, run `@flush`,
edit the corresponding `.conf` entry, then use `@config reload` to read it back.
The same personality applies to `ai`, nick prompts, and `aiweb`; formatting and
web-source instructions are appended as needed. Existing conversation history
is retained when a prompt changes; use `@DeepseekAI forget` for a fresh start.

The default model is `deepseek-flash`, with thinking disabled and at most 512
output tokens. Change `plugins.DeepseekAI.model` to another supported DeepSeek
chat model if needed. A DeepSeek API key and available API balance are required.
See the [DeepSeek API documentation](https://api-docs.deepseek.com/api/create-chat-completion/).

## Commands

| Command | Purpose |
| --- | --- |
| `@ai <question>` | Ask DeepSeek with your recent context; search automatically when needed. |
| `@aiweb <question>` | Always search the web. Ask for links explicitly to display URLs. |
| `@DeepseekAI history [channel] on\|off` | Enable/disable history in a target channel on this network; owner only. |
| `@DeepseekAI profiling [channel] on\|off` | Enable/disable profile learning/use in a target channel on this network; owner only. |
| `@DeepseekAI profile <channel> on\|off` | Alias for the owner-only channel profiling switch. |
| `@DeepseekAI forget [channel] [nick]` | Clear a user's exchanges and profile in the target channel; owner only; defaults to the current location and yourself. |
| `@DeepseekAI forgetchannel [channel]` | Clear a target channel's conversations and profiles; owner only. |
| `@DeepseekAI profile [show/reset/on/off/set/unset]` | Inspect or adjust your own profile privately; personal opt-in cannot override the channel switch. |
| `@DeepseekAI aiusage` | Receive your daily usage and global totals privately. |
| `@DeepseekAI balance` | Fetch the live DeepSeek account balance privately; owner only. |
| `@DeepseekAI responsechars [network] [channel] [limit]` | Set/show an exact network/channel reply limit from a debug channel or PM; owner only. |
| `@DeepseekAI status [channel]` | Show AI, nick replies/addressing, personality, history, profiling, and reply limit for a target channel or current location. |
| `@DeepseekAI nickreply on\|off` | Toggle nick prompts for this network/channel; owner only. |
| `@DeepseekAI prompt [channel] [instructions]` | Set a channel's personality on this network, or view it privately; owner only. |
| `@DeepseekAI promptappend [channel] <instructions>` | Append another chunk to the prompt; owner only. |
| `@DeepseekAI enable` | Enable this channel; owner only. |
| `@DeepseekAI disable` | Disable this channel; owner only. |
| `@DeepseekAI resetusage` | Reset daily counters when no calls are active; owner only. |

Private chat is disabled by default. Enable it with
`@config plugins.DeepseekAI.allowPrivate True` if wanted. Channel enablement and
private chat permission are independent.

## Flood protection and usage limits

All settings below are under `plugins.DeepseekAI`.

| Setting | Default | Meaning |
| --- | ---: | --- |
| `throttleEnabled` | True | Enforce the per-user cooldown. |
| `throttleSeconds` | 12 | Seconds between accepted attempts per user. |
| `localRpmLimit` | 30 | API attempts across all users/networks per rolling minute. |
| `maxConcurrentRequests` | 2 | Simultaneous API calls; excess requests are rejected. |
| `dailyLimitPerUser` | 25 | API attempts per user per day, including failed calls. |
| `globalDailyLimit` | 250 | API attempts across the bot per day, including failures. |
| `dailyTokensPerUser` | 0 | Optional per-user daily token threshold. |
| `globalDailyTokens` | 0 | Optional global daily token threshold. |
| `maxQuestionChars` | 1000 | Maximum question length. |
| `maxResponseChars` | 800 | Maximum complete answer, including citations and formatting; supports network/channel overrides. |
| `maxTokens` | 512 | Maximum output tokens per API call. |
| `requestTimeout` | 45 | HTTP timeout in seconds. |
| `webDailyLimit` | 15 | Shared daily web attempts across all users, channels, and networks; 0 disables web search. |
| `autoWeb` | True | Allow `@ai` and nick prompts to search when external evidence or current facts are needed. |
| `webShowUrls` | True | Allow up to two higher-authority source links when explicitly requested, one per IRC line. False disables links entirely. |
| `webSearchPrompt` | Research guidance | Configurable search decisions and evidence guidance, separate from personality; supports network/channel overrides. |
| `historyEnabled` | False | Use/record conversation history; network/channel overrides, owner-controlled. |
| `historyMode` | channel | Shared AI conversation or separate user histories; private messages always remain per user. |
| `profilesEnabled` | False | Use/learn requester profiles; independent network/channel switch, owner-controlled. |
| `historyExchanges` | 20 | Maximum retained exchanges per conversation. |
| `historyHours` | 24 | Retention for legacy/per-user conversations. |
| `sharedHistoryMinutes` | 60 | Shared channel conversation lifetime. |
| `historyMaxChars` | 12000 | Conversation input budget; whole exchanges are retained. |
| `profileReviewExchanges` | 8 | Review interval and minimum retained own excerpts (8–20). |
| `profileObservationDays` | 30 | Tentative observation lifetime. |
| `profileSampleDays` | 7 | Retention for up to 20 own question excerpts used in reviews. |
| `conversationPrompt` | Conversation guidance | Shared-context guidance; supports network/channel overrides. |
| `profilePrompt` | Personalization guidance | Profile-use guidance; supports network/channel overrides. |
| `profileLearningPrompt` | Pattern guidance | Global instructions for periodic communication-style reviews. |
| `formatting` | True | Convert Markdown emphasis to IRC formatting. |
| `greyItalics` | True | Render italic passages in grey; False uses native IRC italic. |

Set a daily limit, token threshold, or RPM limit to `0` to disable that limit.
The concurrency cap remains active. A user can have only one API request running
at a time, even if their cooldown is disabled. Limit checks and reservations use
a lock so concurrent calls cannot bypass request limits. Users are identified
by network and `user@host`, so changing nick does not reset their allowance;
changing host can change identity.

Daily counters reset at midnight in the bot process's local timezone. They are
saved atomically to `data/DeepseekAI/usage.json` and survive reloads/restarts.
If an attempt cannot be saved, the plugin does not call the API. Short-term
cooldowns, rolling RPM state, and server backoff reset on reload.

Token totals come from the API's `usage.total_tokens` in successful completion
responses, including search planning even if the subsequent search or answer fails.
One user question counts as one request attempt; a web answer can make two chat
completion calls and one search call. An eligible profile review adds one more
completion after the answer has been delivered. Request/RPM limits count user attempts,
while token totals include all completion calls. Token thresholds are checked
again before the final completion; an in-flight completion can exceed a threshold.
Attempts, successful replies, and web attempts are counted locally.
They describe this plugin's activity, rather than an account-wide billing report.
See [Token & Token Usage](https://api-docs.deepseek.com/quick_start/token_usage/).

As owner, use `@DeepseekAI balance` to fetch the account's current balance from
DeepSeek's [balance API](https://api-docs.deepseek.com/api/get-user-balance/).
The response is private and includes currency, total balance, granted credits,
topped-up balance, and whether funds are available for API calls. This is an
account balance, not cumulative daily usage; it does not replace local quota
counters or count as an AI attempt. No balance request is made automatically.

## Web search

`@ai latest Limnoria release` can now search automatically. The model receives the
current date/time and recent conversation, and can answer directly or request a
search. It is instructed to search for changing facts (news, election results,
prices, weather, officeholders, schedules, and latest releases), and to answer
stable explanations, translations, and rewriting directly. This decision is
model-driven and can occasionally miss; `@aiweb` always forces a search.

`systemPrompt` controls personality and reply language. `webSearchPrompt` supplies
the browsing guidance described above; change it globally or override it for a
network/channel. An empty value omits that extra guidance. The plugin still
supplies the current date, the applicable response limit, citation syntax, and
the requirement to treat retrieved material as untrusted data. It adds no fixed
answer-length target or extra personality instructions.

`maxResponseChars` is enforced on the entire formatted answer plus its source
footer. For example, to limit a particular network/channel to 215 characters:

```text
@DeepseekAI responsechars grnet #hackers 215
```

`responsechars` creates the exact scoped setting and reports its effective value.
It accepts an explicit connected network, works from a debug channel or PM, and
does not set a channel-only compatibility fallback. Omit the limit to read it;
omit the network/channel to use the current location. Valid limits are 80–2000.
Use `@flush` to save changes.

Plain `@config plugins.DeepseekAI.maxResponseChars.:network.#channel ...` only
works after that scoped registry entry exists. Limnoria's Config plugin checks
registered paths before creating anything, so a previously unused channel may
otherwise return “not a valid configuration variable.” Use `responsechars` to
create it instead of relying on a prior AI request to instantiate the entry.
The `config channel` command can create entries too, but also writes a legacy
channel-only fallback, so `responsechars` is preferred for strict network isolation.

Use the same scope syntax for `webSearchPrompt`. Unconfigured channels inherit
the network value or global default. Private replies use the network/global
fallback. Existing global limits and personality prompts survive plugin reloads.
The response limit covers the answer body, including formatting and citations;
Limnoria's nick prefix and IRC protocol framing are outside this limit.

The model creates a focused query, a language code matching the question, and an
optional publication cutoff for current events. The plugin enforces the query
length and adds DeepSeek's `lang:` and `after:` operators. Both commands accept
questions up to `maxQuestionChars`; only the generated search query must fit
the provider's 128-character limit.

Each search requests eight candidates with page content, then ranks by the
provider's relevance score and authority tier, selecting up to three distinct
hosts. Each selected source contributes at most 200 title characters, 1,000
summary characters, and 4,000 page-content characters, with publication dates
and quality metadata preserved. Missing page content falls back to summaries.
The model is instructed to prefer primary sources and established reporting,
distinguish forecasts from confirmed outcomes, and disclose stale, conflicting,
or insufficient evidence. Quality metadata and publisher reputation are signals,
not guarantees. Retrieved material is untrusted data, never instructions.

Replies omit URLs and numbered citations by default, including `aiweb` answers.
The plugin strips model-generated URLs, Markdown destinations, and bare domains
before formatting or truncation, even without a web search. Ask explicitly, for
example `give me the links` or `sources?`, to request source URLs for the current
answer; previous requests in conversation history do not enable links.

Requested links always come from retrieved results, never model-generated URLs.
Only cited results with provider authority 3 or 4 qualify; unknown or lower
ratings are omitted. The model must also prefer relevant official primary
sources and established reporting. This rating is a quality filter, not a
guarantee of accuracy. If no links qualify or fit, the answer says so.

At most two links are sent, each as a separate IRC message with its citation
number. Links occupy at most half of the complete response character budget.
Each must also fit intact within the IRC byte limit; longer URLs are omitted
instead of shortened, split, or replaced with a domain. Citations lost when the
answer is truncated have no link. `webShowUrls=False` disables links entirely.

The default allowance is **15 web attempts per day for the entire bot**. It is
saved with usage counters, survives reloads and restarts, and resets at midnight
in the bot's local timezone. Failed searches and failed subsequent completions
also consume an attempt. Web questions also count toward existing request and
token limits and share cooldowns and concurrency slots with `@ai`. There are at
most three searches per question; their results are combined, deduplicated by
host, and limited to three sources. The daily counter charges one web attempt
per question, regardless of how many searches it makes. Calls are not
automatically retried, and the plugin makes no additional fetch calls. Search
failures or exhausted allowances stop the web answer with a notice instead of
falling back to unverified model knowledge. `@DeepseekAI aiusage` shows the
shared web counter; the owner command `resetusage` also resets this counter.

Web search requires a configured DeepSeek API key, and API usage may incur
charges. Search results also add input tokens to model calls. Ordinary `@ai`
can still answer directly when the web allowance is exhausted, subject to the
existing shared limits. If it requests current evidence, it reports that
verification is unavailable.

Change the allowance with `@config plugins.DeepseekAI.webDailyLimit 15`.
Disable automatic searching with `@config plugins.DeepseekAI.autoWeb False`;
`@aiweb` remains available. Tool calling must be supported by the configured
model; the default `deepseek-flash` supports it with thinking disabled.

## IRC formatting

Both commands support `**bold**` and `*italic*` emphasis in model replies.
Bold becomes IRC bold; italic passages become darker grey (IRC colour 93,
`#656565`) by default. This shade requires extended IRC colour support. To use native
italic instead, run `@config plugins.DeepseekAI.greyItalics False`. To disable
formatting, run `@config plugins.DeepseekAI.formatting False`.

Rendering depends on the IRC client and theme, and channels that strip colours
or formatting may remove it. The plugin strips incoming control characters and
generates its own formatting codes, resets formatting at the end of each answer,
and stores plain text in conversation history. Formatting instructions are added
to requests automatically, including when you have customized `systemPrompt`.

Token thresholds are checked before sending a request using previously reported
usage. An in-flight call can exceed the threshold; these are not strict monetary
budgets. Reported token usage includes context sent again with each request.
HTTP 429 and transient server errors pause new requests (respecting `Retry-After`
up to one hour). Calls are never automatically retried. Rejected-request warnings
are private notices, limited to one per user every 15 seconds.

## Conversation history and profiles

Both `historyEnabled` and `profilesEnabled` default to **False**. With both off,
questions include no retained conversation or profile, successful requests store
neither, and no profile review calls are made. Each switch supports independent
network/channel overrides. Switching off stops use and recording/learning; it
leaves existing stored data to expire or be explicitly deleted.

To enable only in DALnet `#2600`, reload the plugin and run **in that channel on
DALnet**, while identified as a bot owner:

```text
@DeepseekAI history on
@DeepseekAI profiling on
@flush
```

Use `off` to disable either feature. These two commands and both forget commands
require the bot's **owner** capability, not merely channel operator status. The
switch commands update exactly the selected channel on the current network;
when no channel is supplied, they use the channel where you issued the command.
Confirmations include the target network/channel and stay in the issuing channel
or PM. `status [channel]` shows the selected channel's settings (or the current location when omitted), including history on/off and mode plus profiling on/off. Replies stay in the issuing channel or PM. Use `flush` to persist configuration changes. Equivalent configuration:

```text
supybot.plugins.DeepseekAI.historyEnabled: False
supybot.plugins.DeepseekAI.profilesEnabled: False
supybot.plugins.DeepseekAI.historyEnabled.\:DALnet.#2600: True
supybot.plugins.DeepseekAI.profilesEnabled.\:DALnet.#2600: True
```

From a debug channel or private message **on DALnet**, target `#2600` explicitly:

```text
@DeepseekAI history #2600 on
@DeepseekAI profile #2600 on
@flush
```

`profiling #2600 on` is equivalent to `profile #2600 on`. The explicit-channel
`profile` syntax is owner-only; `profile on/off` without a channel still controls
only the requester's personal opt-out. Remote switch commands and `forgetchannel`
require the bot to have joined the target channel. Cross-network changes still
use the fully scoped `@config` paths above. Commands do not send messages into
the target channel.

When editing configuration files directly, stop the bot first to avoid its
running registry overwriting the changes. The switches can also be changed with
owner-authorized `@config plugins.DeepseekAI.historyEnabled.:DALnet.#2600 True`
and the corresponding `profilesEnabled` setting.

When enabled, `historyMode channel` shares only **AI exchanges**, with speaker
nicknames, timestamps, questions, and the bot's actual replies including source
attribution. Normal channel chatter is not recorded. The current requester is
identified separately. No network/channel shares context with another, and
private messages remain per user. Shared channels allow one active AI request
at a time; overlapping requests receive a busy notice without being charged.
Different channels can still run concurrently within the global cap.

Shared history keeps at most 20 complete exchanges, no older than 60 minutes,
and fits the configured 12,000-character serialized context budget. Profiles and
speaker metadata are included in the shared context budget. Oldest complete
exchanges are dropped first. `historyMode user` retains the old per-user
behavior, bounded by `historyHours`, `historyExchanges`, and `historyMaxChars`.
Existing version-1 per-user history is preserved separately, never silently
merged into public channel history. Setting `historyExchanges` to zero clears
conversation history on the next prune; profiling has its own independent switch.

### User profiles

Profiles belong to a user identity in one network/channel (or their separate
private context), surviving nick changes while `user@host` stays the same. Only
the current requester's profile is supplied for personalization; other people's
profiles and internal hostmasks are not sent as conversation context.

Explicit preferences are user-controlled, not guessed by the model:

```text
@DeepseekAI profile
@DeepseekAI profile set reply_length short
@DeepseekAI profile set tone direct
@DeepseekAI profile set language en
@DeepseekAI profile unset tone
@DeepseekAI profile reset
@DeepseekAI profile off
```

`reply_length` accepts short/normal/detailed; `tone` accepts
neutral/playful/direct/formal; `language` accepts auto/en/el. Commands concern
only the requester's own profile and reply privately. `reset` clears preferences,
observations, and excerpts and restarts learning from new messages; it preserves
a personal opt-out. `on` starts fresh and opts back in, but cannot enable a
channel whose owner has disabled profiling. The channel personality and language
instructions take precedence over preferences.

After at least eight own exchanges and then periodically, the plugin can make
one extra completion to review up to 20 own question excerpts (at most 1,000
characters each, retained for seven days). These are kept separately from the
shorter shared conversation window so occasional users can build a pattern over
several visits. Reviews contain no other users' exchanges, bot replies, hostmasks,
or web results. No profile is inferred from one message.

A review can produce at most three observations from a fixed vocabulary:
upbeat, playful, direct, formal, frustrated, technical, concise, detailed.
Each requires evidence indexes from at least three distinct supplied excerpts.
The plugin fixes descriptions and labels them tentative; arbitrary personality
claims or extra profile fields are not accepted. Evidence checks validate the
shape and cited messages, not the truth of the model's interpretation. New
reviews replace old observations; observations expire after 30 days. Samples
expire after seven days. Explicit preferences and opt-outs persist until changed.
An inactive profile without preferences, observations, or an opt-out expires
after 30 days.

Reviews run after the answer is sent, still occupying that request's concurrency
slot. Their tokens count toward the requesting user's and global totals, and
reviews obey token thresholds and `maxTokens`. A failed review does not suppress
or duplicate the answer. Request/RPM counters count questions, not each internal
completion; reviews add input and output token costs when enabled.

### Forgetting and storage

As owner, use `@DeepseekAI forget Bob` in the relevant channel, or
`@DeepseekAI forget #2600 Bob` from a debug channel or PM on the same network,
to remove Bob's
retained exchanges and their corresponding bot replies, profile preferences,
observations, and learning excerpts. With no nickname it applies to yourself.
Known online nicks are resolved through IRC state; offline historical names must
identify exactly one retained user or the command refuses. This cannot erase
other people's quotations, already displayed IRC messages, or external logs.
`@DeepseekAI forgetchannel` clears all conversation and profile content for the
current network/channel; `@DeepseekAI forgetchannel #2600` targets that channel
from a debug channel or PM. Personal opt-outs survive both kinds of clearing. Clearing
waits until relevant in-flight requests finish so they cannot reintroduce context.
None of these commands changes channel personality, browsing guidance, or limits.

History and profiles are saved together in version 2 of
`data/DeepseekAI/history.json`, rather than separate files, so a forget operation
cannot persist only half its deletion. The JSON contains `conversations` (legacy
per-user history), `channels`, and `profiles` (including bounded review excerpts).
The bot restores it on reload/restart, validates the format, and prunes expired
entries. Writes use mode 640, fsync, and atomic replacement. A failed deletion
restores both in-memory history and profiles and reports an error. Daily usage
counters remain separate. Memory files and backups can contain user text; the
plugin does not erase external backup copies.

Your questions and the included memory are sent to DeepSeek. Previous ChatGPT
conversations are not involved. Customize personality with `systemPrompt`, and
research, conversation, and profile guidance with their separate prompt settings.

## Tests

Run from a temporary working directory with the bot's Python environment:

```sh
supybot-test --no-network --disable-multiprocessing /path/to/Limnoria-Plugins/DeepseekAI
```

Tests mock HTTP calls, so they need no API key or paid requests.
