"""Small, expiring user-only context. Never treat earlier AI output as evidence."""
from collections import OrderedDict
import time

from response_guidance import retrieval_question


class ConversationMemory:
    def __init__(self, *, ttl=1800, capacity=1000, turns=4, clock=time.monotonic, persistent_store=None):
        self.ttl, self.capacity, self.turns, self.clock = ttl, capacity, turns, clock
        self._threads = OrderedDict()
        self.persistent_store=persistent_store

    def resolve(self, room, sender, domain, question, *, request_id=None):
        if request_id is not None and self.persistent_store is not None:
            cached=self.persistent_store.prepared_context(request_id,domain)
            if cached is not None:return cached
        now = self.clock()
        # Expire idle conversations, even if the next request is from another user.
        for key, (updated, _) in list(self._threads.items()):
            if now - updated >= self.ttl:
                del self._threads[key]
        key = (room, sender, domain)
        _, history = self._threads.pop(key, (now, []))
        if not history and self.persistent_store is not None:
            history=self.persistent_store.load_history(room,sender,domain,self.ttl)
        previous = "\n".join(history)[-2800:]
        effective = retrieval_question(question, previous)
        inherited = effective != question
        history = (history if inherited else []) + [question[:1200]]
        self._threads[key] = (now, history[-self.turns:])
        if self.persistent_store is not None:
            self.persistent_store.save_history(room,sender,domain,history[-self.turns:],
                                               request_id=request_id,context=(effective,previous if inherited else ""))
        while len(self._threads) > self.capacity:
            self._threads.popitem(last=False)
        return effective, previous if inherited else ""

    def clear(self, room, sender):
        if self.persistent_store is not None:self.persistent_store.clear_history(room,sender)
        for key in list(self._threads):
            if key[:2] == (room, sender):
                del self._threads[key]
