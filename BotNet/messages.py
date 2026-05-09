import time
import threading
from collections import defaultdict

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

# Replay protection
MAX_MESSAGE_AGE = 300  # 5 minutes maximum age for messages
REPLAY_CACHE_SIZE = 10000  # Maximum number of seen messages to track


class ReplayProtectionManager:
    """Track seen messages to prevent replay attacks"""
    
    def __init__(self):
        self.seen_messages = defaultdict(set)  # sender_pubkey -> set of msg_id
        self.lock = threading.Lock()
    
    def is_replay(self, sender_pubkey, msg_id, timestamp):
        """
        Check if a message is a replay.
        Also checks if timestamp is too old.
        Returns True if it's a replay or too old, False if it's new and valid.
        """
        current_time = time.time()
        
        # Check timestamp age
        if current_time - timestamp > MAX_MESSAGE_AGE:
            return True  # Message too old
        
        # Check if we've seen this message before
        with self.lock:
            if msg_id in self.seen_messages[sender_pubkey]:
                return True  # Replay detected
            
            # Add to seen messages
            self.seen_messages[sender_pubkey].add(msg_id)
            
            # Clean up old entries periodically (simple size-based cleanup)
            if len(self.seen_messages[sender_pubkey]) > REPLAY_CACHE_SIZE:
                # Remove oldest 20% of entries (approximate by removing arbitrary items)
                items = list(self.seen_messages[sender_pubkey])
                to_remove = items[:REPLAY_CACHE_SIZE // 5]
                for old_msg_id in to_remove:
                    self.seen_messages[sender_pubkey].discard(old_msg_id)
        
        return False
    
    def cleanup_sender(self, sender_pubkey):
        """Remove all cached messages for a sender (when they disconnect)"""
        with self.lock:
            if sender_pubkey in self.seen_messages:
                del self.seen_messages[sender_pubkey]


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
