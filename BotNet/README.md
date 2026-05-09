# BotNet Plugin for Limnoria

Decentralized encrypted peer-to-peer mesh network for Limnoria IRC bots.

> **⚠️ DISCLAIMER**: This plugin was developed with vibecoding and has not been formally security-audited. Use at your own risk. While it implements strong cryptography, the implementation may contain bugs or oversights. Review the code before deploying in sensitive environments.

## Features

- X25519 encryption for all peer-to-peer communication
- Ed25519 signing keys for peer authentication and message signing
- Decentralized mesh network with flood routing and TTL-based propagation
- Message deduplication to prevent broadcast storms
- Replay attack protection with timestamp validation and per-sender message caching
- Partyline interface with simple commands: bwho, bmap, bcast, bquit, bhelp
- Automatic reconnection with exponential backoff
- PING/PONG heartbeat system
- Persistent storage in BotNet/ subfolder

## Commands

### Partyline Commands (requires `partyline` mode)
- `bwho` - Show online users in the mesh
- `bmap` - Show mesh topology tree
- `bcast <botnet> <message>` - Send signed broadcast to a botnet
- `bquit` - Exit partyline mode
- `bhelp` - Show help

### Core Commands
- `mykey` - Display full public signing key for sharing
- `status` - Show connection status and peer information
- `listen <port>` - Start encrypted listener
- `connect <host:port>` - Connect to a peer
- `partyline` - Enter partyline mode

### Botnet Management
- `trust <pubkey> [botnets]` - Trust a remote bot (always adds to Nest)
- `untrust <pubkey>` - Remove trust and disconnect
- `list_trusted` - List all trusted peers
- `joinnest` - Join Nest botnet
- `leavenest` - Leave Nest botnet

## Security

- All mesh traffic encrypted with NaCl Box (X25519)
- All broadcast messages signed with Ed25519 (prevents forgery)
- Replay attack protection with 5-minute timestamp window
- Rate limiting to prevent DoS (max 3 connections/IP/60s)
- Maximum message size limit (10MB)
- Nonce-based handshake to prevent replay attacks
