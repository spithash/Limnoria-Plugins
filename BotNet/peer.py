class Peer:
    def __init__(self, sock=None, address=None):
        self.sock = sock
        self.address = address

        self.bot_name = None
        self.pubkey = None

        self.authenticated = False
        self.connected = False

    def __repr__(self):
        return f"<Peer {self.bot_name} {self.address}>"
