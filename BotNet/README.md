# BotNet Plugin for Limnoria

**Decentralized Encrypted Peer-to-Peer Botnet Framework for Limnoria IRC Bots**

BotNet transforms your Limnoria bot into a node in a secure, decentralized mesh network. Create private party lines, broadcast messages across botnets, and build trusted peer-to-peer communication channels between bots - all with end-to-end encryption.

> **⚠️ SECURITY NOTICE: This plugin is vibecoded and has not been formally reviewed for security vulnerabilities. Use at your own risk in production environments.**

## Features

- **🔐 End-to-End Encryption** - All peer communications encrypted using NaCl (X25519)
- **✍️ Message Signing** - Every broadcast is signed with Ed25519 to prevent forgery
- **🌐 Decentralized Mesh** - No central servers, pure peer-to-peer architecture
- **📡 Botnet Party Line** - Real-time messaging with `bcast`, `bwho`, `bmap`, `bquit`, `bhelp`
- **🔄 Flooding with Deduplication** - Messages propagate with TTL and duplicate prevention
- **💾 Persistent State** - Keys and trusted peers survive plugin reloads
- **❤️ Heartbeat System** - Automatic PING/PONG keeps connections alive
- **🔄 Auto-Reconnection** - Reconnects to trusted peers on startup with retry logic
- **🏷️ Botnet Groups** - Organize peers into custom botnet names (Nest is mandatory)
- **🛡️ DoS Protection** - Rate limiting, message size caps, and read deadlines

## Security Model

- Each bot generates Ed25519 signing keys + X25519 encryption keys
- **No central authority** - Trust established via manual public key exchange
- All messages are encrypted AND signed to prevent eavesdropping, forgery, and tampering
- Peers must be explicitly trusted before any communication is allowed
- Rate limiting prevents connection flood attacks

## Installation

### Prerequisites

- Limnoria (Supybot) IRC bot framework
- Python 3.7+
- PyNaCl (libsodium)

### Install Dependencies

```bash
pip install pynacl msgpack
