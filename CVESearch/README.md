Search for CVE vulnerabilities on https://nvd.nist.gov/vuln

It also has a snarfer, when it detects a cve in the channel, it triggers. This can be enabled with something like:
```
@config channel supybot.plugins.CVESearch.cveSnarfer True
```

Usage trigger: @cve CVE-2024-7757
