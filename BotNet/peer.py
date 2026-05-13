import time

class Peer:
    def __init__(self, sock=None, address=None):
        self.sock = sock
        self.address = address

        self.bot_name = None
        self.pubkey_signing = None
        self.encryption_manager = None

        self.authenticated = False
        self.connected = False
        self.last_pong = time.time()
        self.last_seen = time.time()
        
        # Reconnection tracking
        self.reconnect_pending = False
        self.reconnect_attempts = 0

    def __repr__(self):
        return f"<Peer {self.bot_name} ({self.address[0]}:{self.address[1]})>"
    
    @property
    def is_alive(self):
        """Check if peer is still alive based on last PONG"""
        return time.time() - self.last_pong < 90
