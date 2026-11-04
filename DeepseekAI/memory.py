"""Bounded public conversations and inspectable, tentative user profiles."""
import copy
import json
import math
import time


STYLES = {
    "upbeat": "Often uses upbeat language.",
    "playful": "Often uses humour or playful language.",
    "direct": "Often communicates directly.",
    "formal": "Often uses a formal communication style.",
    "frustrated": "Often expresses frustration when discussing problems.",
    "technical": "Often asks for technical detail.",
    "concise": "Often communicates briefly.",
    "detailed": "Often provides detailed explanations or questions.",
}
PREFERENCES = {
    "reply_length": ("short", "normal", "detailed"),
    "tone": ("neutral", "playful", "direct", "formal"),
    "language": ("auto", "en", "el"),
}


def timestamp(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= time.time() + 300


class ConversationMemory:
    def __init__(self):
        self.channels = {}
        self.profiles = {}

    def dump(self):
        return {
            "channels": [{"network": network, "target": target, "exchanges": entries}
                         for (network, target), entries in self.channels.items()],
            "profiles": [{"user": user, "target": target, **profile}
                         for (user, target), profile in self.profiles.items()],
        }

    @classmethod
    def restore(cls, data):
        memory = cls()
        for record in data.get("channels", []):
            network, target = record["network"], record["target"]
            if not all(isinstance(v, str) and v for v in (network, target)) or not target.startswith(("#", "&", "+", "!")):
                raise ValueError("Invalid shared scope")
            key = (network, target)
            if key in memory.channels or not isinstance(record["exchanges"], list):
                raise ValueError("Duplicate or invalid channel")
            entries = []
            for entry in record["exchanges"]:
                if (not isinstance(entry, dict) or not timestamp(entry["time"]) or
                        not all(isinstance(entry[k], str) and entry[k] for k in ("user", "nick", "question", "answer")) or
                        not entry["user"].startswith(network + ":")):
                    raise ValueError("Invalid shared exchange")
                entries.append({k: entry[k] for k in ("time", "user", "nick", "question", "answer")})
            memory.channels[key] = entries
        for record in data.get("profiles", []):
            key = (record["user"], record["target"])
            if (not all(isinstance(k, str) and k for k in key) or key in memory.profiles or
                    type(record["enabled"]) is not bool or
                    not isinstance(record["last_nick"], str) or
                    not timestamp(record["updated_at"]) or not timestamp(record["learn_after"]) or
                    type(record["pending"]) is not int or not 0 <= record["pending"] <= 1000000 or
                    not isinstance(record["preferences"], dict) or
                    not isinstance(record["observations"], list) or len(record["observations"]) > 3):
                raise ValueError("Invalid profile")
            for name, value in record["preferences"].items():
                if name not in PREFERENCES or value not in PREFERENCES[name]:
                    raise ValueError("Invalid explicit preference")
            samples = record.get("samples", [])
            if (not isinstance(samples, list) or len(samples) > 20 or any(
                    not isinstance(s, dict) or not timestamp(s["time"]) or
                    not isinstance(s["text"], str) or len(s["text"]) > 1000 for s in samples)):
                raise ValueError("Invalid profile samples")
            for observation in record["observations"]:
                if (observation["style"] not in STYLES or observation["confidence"] != "tentative" or
                        type(observation["supporting_exchanges"]) is not int or observation["supporting_exchanges"] < 3 or
                        not timestamp(observation["updated_at"]) or
                        type(observation["expires_at"]) not in (int, float) or
                        not math.isfinite(observation["expires_at"]) or
                        not observation["updated_at"] < observation["expires_at"]):
                    raise ValueError("Invalid observation")
            memory.profiles[key] = {k: copy.deepcopy(record[k]) for k in (
                "enabled", "last_nick", "preferences", "observations", "updated_at", "learn_after", "pending")}
            memory.profiles[key]["samples"] = copy.deepcopy(samples)
        return memory

    @staticmethod
    def public(entry):
        # Internal user@host identifiers never leave the plugin.
        return {"speaker": entry["nick"], "time": entry["time"],
                "question": entry["question"], "bot_reply": entry["answer"]}

    def prune(self, count, minutes, budget, sample_days=7):
        before = self.dump()
        now = time.time()
        for key, entries in list(self.channels.items()):
            kept = []
            for entry in reversed(entries):
                if now - entry["time"] >= minutes * 60:
                    continue
                candidate = [entry] + kept
                size = len(json.dumps([self.public(e) for e in candidate], ensure_ascii=False))
                if len(candidate) > count or size > budget:
                    break
                kept = candidate
            if kept:
                self.channels[key] = kept
            else:
                del self.channels[key]
        for key, profile in list(self.profiles.items()):
            profile["samples"] = [s for s in profile["samples"] if now - s["time"] < sample_days * 86400][-20:]
            profile["observations"] = [o for o in profile["observations"] if o["expires_at"] > now]
            # Keep explicit preferences and opt-outs; discard inactive inference state.
            if (profile["enabled"] and not profile["preferences"] and not profile["observations"] and
                    now - profile["updated_at"] > 30 * 86400):
                del self.profiles[key]
        return before != self.dump()

    def profile(self, key, nick=""):
        if key not in self.profiles:
            self.profiles[key] = {"enabled": True, "last_nick": nick, "preferences": {},
                                  "observations": [], "updated_at": time.time(),
                                  "learn_after": 0, "pending": 0, "samples": []}
        return self.profiles[key]

    def reset_profile(self, key, nick="", enabled=None):
        previous = self.profiles.get(key, {})
        self.profiles.pop(key, None)
        profile = self.profile(key, nick)
        profile["enabled"] = previous.get("enabled", True) if enabled is None else enabled
        profile["learn_after"] = time.time()
        return profile

    def forget_profile(self, key):
        previous = self.profiles.pop(key, None)
        # Retain only an opt-out marker, never a forgotten nickname or samples.
        if previous and not previous["enabled"]:
            self.reset_profile(key, enabled=False)

    def context(self, scope, key, nick, profiles_enabled, budget=None):
        profile = self.profiles.get(key)
        result = {"current_requester": nick,
                  "exchanges": [self.public(e) for e in self.channels.get(scope, [])]}
        if profiles_enabled and profile and profile["enabled"]:
            result["requester_profile"] = {"preferences": profile["preferences"],
                "observations": [{"description": STYLES[o["style"]], "confidence": "tentative"}
                                 for o in profile["observations"]]}
        if budget is not None:
            while result["exchanges"] and len(json.dumps(result, ensure_ascii=False)) > budget:
                result["exchanges"].pop(0)
        return result

    def review_samples(self, key, minimum):
        profile = self.profiles.get(key)
        if not profile or not profile["enabled"] or profile["pending"] < minimum:
            return []
        samples = [s["text"] for s in profile["samples"] if s["time"] >= profile["learn_after"]][-20:]
        return samples if len(samples) >= minimum else []

    def accept_review(self, key, result, sample_count, days):
        if not isinstance(result, dict) or not isinstance(result.get("observations"), list):
            raise ValueError("Invalid profile review")
        observations, seen = [], set()
        now = time.time()
        for item in result["observations"][:3]:
            style, evidence = item["style"], item["evidence"]
            if style not in STYLES or style in seen or not isinstance(evidence, list):
                raise ValueError("Invalid communication style")
            if any(type(i) is not int or not 0 <= i < sample_count for i in evidence) or len(set(evidence)) < 3:
                raise ValueError("Insufficient profile evidence")
            seen.add(style)
            observations.append({"style": style, "confidence": "tentative", "supporting_exchanges": len(set(evidence)),
                                 "updated_at": now, "expires_at": now + days * 86400})
        profile = self.profiles[key]
        profile["observations"] = observations
        profile["updated_at"] = now
