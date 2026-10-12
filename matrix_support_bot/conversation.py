"""Small, expiring user-only context. Never treat earlier AI output as evidence."""
from collections import OrderedDict
import time

from response_guidance import retrieval_question


class ConversationMemory:
    def __init__(self, *, ttl=1800, capacity=1000, turns=4, clock=time.monotonic):
        self.ttl, self.capacity, self.turns, self.clock = ttl, capacity, turns, clock
        self._threads = OrderedDict()

    def resolve(self, room, sender, domain, question):
        now = self.clock()
        # Expire idle conversations, even if the next request is from another user.
        for key, (updated, _) in list(self._threads.items()):
            if now - updated >= self.ttl:
                del self._threads[key]
        key = (room, sender, domain)
        _, history = self._threads.pop(key, (now, []))
        previous = "\n".join(history)[-2800:]
        effective = retrieval_question(question, previous)
        inherited = effective != question
        history = (history if inherited else []) + [question[:1200]]
        self._threads[key] = (now, history[-self.turns:])
        while len(self._threads) > self.capacity:
            self._threads.popitem(last=False)
        return effective, previous if inherited else ""

    def clear(self, room, sender):
        for key in list(self._threads):
            if key[:2] == (room, sender):
                del self._threads[key]
