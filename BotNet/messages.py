import time
import threading

# Message types
HELLO = "hello"
HELLO_ACK = "hello_ack"
PING = "ping"
PONG = "pong"
BROADCAST = "broadcast"
PARTYLINE_CMD = "partyline_cmd"
PARTYLINE_MSG = "partyline_msg"
STATUS_QUERY = "status_query"
STATUS_RESPONSE = "status_response"

PROTOCOL_VERSION = 1
SUPPORTED_VERSIONS = [1]

# Partyline command types
CMD_BROADCAST = "broadcast"
CMD_WHO = "who"
CMD_MAP = "map"
CMD_QUIT = "quit"
CMD_HELP = "help"


class MessageIDGenerator:
    """Generates unique message IDs for flood prevention"""
    def __init__(self, bot_id):
        self.bot_id = bot_id[:16]
        self.counter = 0
        self.lock = threading.Lock()
    
    def next(self):
        with self.lock:
            self.counter += 1
            return f"{self.bot_id}_{int(time.time())}_{self.counter}"
