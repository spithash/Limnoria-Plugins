Look up CVEs using JSON from the [official CVE List on GitHub](https://github.com/CVEProject/cvelistV5).
No API key or local clone is required.

Usage: `@cve CVE-2024-7757` (or `@cve 2024-7757`).

Replies include a description, CVE Program publication/update timestamps, and a
link to the full record on CVE.org. When available, CVSS includes its score,
severity, version, and provider. CNA scores take precedence over ADP enrichment;
within either source group, newer CVSS versions take precedence.
Rejected records show their status and rejection reason instead.

Descriptions default to at most 500 characters. Adjust with:
```
@config supybot.plugins.CVESearch.maxDescriptionLength 500
```

Enable automatic lookup of CVE IDs mentioned in a channel with:
```
@config channel supybot.plugins.CVESearch.cveSnarfer True
```

The snarfer skips commands and messages containing URLs, removes duplicate IDs,
and looks up at most three distinct CVEs per message.

GitHub's copy is updated periodically, so a missing file can mean a reserved,
unpublished, invalid, or not-yet-synced ID. It does not prove that an ID does not
exist. Missing descriptions and request errors are reported explicitly.
