# BotNet Plugin for Limnoria

**Decentralized Encrypted Peer-to-Peer Botnet Framework for Limnoria IRC Bots**

BotNet transforms your Limnoria bot into a node in a secure, decentralized mesh network. Create private party lines, broadcast messages across botnets, and build trusted peer-to-peer communication channels between bots - all with end-to-end encryption.

## Features

- **🔐 End-to-End Encryption** - All peer communications encrypted using NaCl (X25519 + Ed25519)
- **🌐 Decentralized Mesh** - No central servers, pure peer-to-peer architecture
- **📡 Botnet Party Line** - Real-time messaging interface via private IRC queries
- **🔄 Flooding with Deduplication** - Messages propagate through the mesh with TTL and duplicate prevention
- **💾 Persistent State** - Keys and trusted peers survive plugin reloads and bot restarts
- **❤️ Heartbeat System** - Automatic PING/PONG keeps connections alive
- **🔄 Auto-Reconnection** - Automatically reconnects to trusted peers on startup
- **🏷️ Botnet Groups** - Organize peers into custom botnet names (Nest is mandatory)
- **📊 Mesh Topology** - Visualize the network with `.map` and `.who` commands

## Security Model

- Each bot generates a unique cryptographic identity (Ed25519 signing key + X25519 encryption key)
- **No central authority** - Trust is established manually via public key exchange
- All connections use mutual authentication and end-to-end encryption
- Peers must be explicitly trusted before communication is allowed

## Installation

### Prerequisites

- Limnoria (Supybot) IRC bot framework
- Python 3.7+
- PyNaCl (libsodium)

### Install Dependencies

```bash
pip install pynacl msgpack
