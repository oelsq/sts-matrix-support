import React, { useEffect, useState } from 'react';

function TicketDetail({ ticket, onClose, onUpdated }) {
  const [messages, setMessages] = useState([]);
  const [newMessage, setNewMessage] = useState('');
  const [status, setStatus] = useState(ticket.status);
  const [loading, setLoading] = useState({ messages: false, send: false, status: false });
  const [error, setError] = useState(null);

  const loadMessages = async () => {
    setLoading((l) => ({ ...l, messages: true }));
    try {
      const resp = await fetch(`/api/tickets/${ticket.id}`);
      if (!resp.ok) throw new Error('Failed to load messages');
      const data = await resp.json();
      setMessages(data.messages || []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading((l) => ({ ...l, messages: false }));
    }
  };

  useEffect(() => {
    loadMessages();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSend = async () => {
    if (!newMessage.trim()) return;
    setLoading((l) => ({ ...l, send: true }));
    try {
      const resp = await fetch(`/api/tickets/${ticket.id}/messages/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: newMessage }),
      });
      if (!resp.ok) throw new Error('Failed to send message');
      setNewMessage('');
      await loadMessages();
      onUpdated();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading((l) => ({ ...l, send: false }));
    }
  };

  const handleStatusChange = async (e) => {
    const newStatus = e.target.value;
    setLoading((l) => ({ ...l, status: true }));
    try {
      const resp = await fetch(`/api/tickets/${ticket.id}/status/`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: newStatus }),
      });
      if (!resp.ok) throw new Error('Failed to change status');
      setStatus(newStatus);
      onUpdated();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading((l) => ({ ...l, status: false }));
    }
  };

  return (
    <section>
      <button onClick={onClose}>← Back to list</button>
      <h2>{ticket.title}</h2>
      <p>Status: 
        <select value={status} onChange={handleStatusChange} disabled={loading.status}>
          <option value="open">Open</option>
          <option value="pending">Pending</option>
          <option value="closed">Closed</option>
        </select>
      </p>
      <h3>Conversation</h3>
      {loading.messages ? (
        <p>Loading messages...</p>
      ) : (
        <ul>
          {messages.map((m) => (
            <li key={m.id}>
              <strong>{m.role}:</strong> {m.content}
            </li>
          ))}
        </ul>
      )}
      <div>
        <textarea
          rows="3"
          placeholder="Type a message..."
          value={newMessage}
          onChange={(e) => setNewMessage(e.target.value)}
        />
        <br />
        <button onClick={handleSend} disabled={loading.send}>
          {loading.send ? 'Sending...' : 'Send'}
        </button>
      </div>
      {error && <p style={{ color: 'red' }}>{error}</p>}
    </section>
  );
}

export default TicketDetail;
