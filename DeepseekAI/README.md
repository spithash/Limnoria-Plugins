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

As owner, enable nick addressing so normal commands such as `R2-D2: help`
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

Now `R2-D2: explain IRC bouncers` asks DeepSeek, while `R2-D2: help` runs the
normal command. `R2-D2 ai explain IRC bouncers` still works too. Use your bot's
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

To customize just one network/channel, first run `@DeepseekAI status` in that
channel. This registers its configuration entries. Then, as owner, set the
fully qualified entry, replacing both placeholders:

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
| `@ai <question>` | Ask DeepSeek with your recent context here. |
| `@aiweb <question>` | Search the web and answer with sources; question up to 128 characters. |
| `@DeepseekAI forget` | Clear your context in the current channel or private chat. |
| `@DeepseekAI aiusage` | Receive your daily usage and global totals privately. |
| `@DeepseekAI status` | Check whether the plugin is enabled here. |
| `@DeepseekAI nickreply on\|off` | Toggle nick prompts for this network/channel; owner only. |
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
| `maxResponseChars` | 800 | Maximum answer length sent to IRC. |
| `maxTokens` | 512 | Maximum output tokens per API call. |
| `requestTimeout` | 45 | HTTP timeout in seconds. |
| `webDailyLimit` | 15 | Shared daily web attempts across all users, channels, and networks; 0 disables web search. |
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

## Web search

`@aiweb latest Limnoria release` searches DeepSeek's web index using the same API
key, then sends up to three bounded result summaries to the chat model. Replies
include source links that fit within the response length limit. Retrieved text
is treated as untrusted reference material. Search improves freshness, but the
answer still needs to be checked against its sources.

The default allowance is **15 web attempts per day for the entire bot**. It is
saved with usage counters, survives reloads and restarts, and resets at midnight
in the bot's local timezone. Failed searches and failed subsequent completions
also consume an attempt. Web questions also count toward existing request and
token limits and share cooldowns and concurrency slots with `@ai`. No automatic
retries or full-page fetches are performed. `@DeepseekAI aiusage` shows the shared
web counter; the owner command `resetusage` also resets this counter.

At the published price of $0.75 per 1,000 successful searches, 15 searches daily
cost at most about $0.34 in search fees over 30 days, plus normal model input and
output charges. Search summaries add input tokens. See the
[Search & Fetch guide](https://api-docs.deepseek.com/guides/search/) for current
pricing. Ordinary `@ai` never searches and remains available when the web
allowance is exhausted, subject to the existing shared limits.

Change the allowance with `@config plugins.DeepseekAI.webDailyLimit 15`.

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

## Conversation history

`historyExchanges` defaults to **20 question/reply pairs per user per channel**;
`historyHours` defaults to **24 hours**. Context is isolated across channels,
networks, users, and private chats. Unlike the referenced Perl bot, private chats
do not combine channel histories. Only successful exchanges are retained, and
stored replies use the same bounded text sent to IRC. Expired context is removed
when a question is processed. Set `historyExchanges` to `0` to disable memory.

History is held in memory and is cleared by plugin reloads or bot restarts.
Daily usage counters are persisted separately. Your question and the included
history are sent to DeepSeek; previous ChatGPT conversations are not involved.
Customize the bot's personality with `plugins.DeepseekAI.systemPrompt`.

## Tests

Run from a temporary working directory with the bot's Python environment:

```sh
supybot-test --no-network --disable-multiprocessing /path/to/Limnoria-Plugins/DeepseekAI
```

Tests mock HTTP calls, so they need no API key or paid requests.
