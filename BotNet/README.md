# Decentralized encrypted peer-to-peer botnet framework for Limnoria bots.

## Security Model

- Each bot has a local cryptographic identity (Ed25519 keypair)
- Keys are stored locally and must never be committed
- Peers are manually trusted via public key exchange
- No central server exists
- All connections are peer-to-peer

## Default Configuration

- Listener binds to 127.0.0.1 by default
- No external exposure unless explicitly configured
