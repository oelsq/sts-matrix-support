import React, { useEffect, useState } from 'react';

function TicketList({ onSelect, refreshFlag }) {
  const [tickets, setTickets] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    const fetchTickets = async () => {
      setLoading(true);
      setError(null);
      try {
        const resp = await fetch('/api/tickets/');
        if (!resp.ok) throw new Error('Failed to fetch tickets');
        const data = await resp.json();
        setTickets(data);
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    };
    fetchTickets();
  }, [refreshFlag]);

  if (loading) return <p>Loading tickets...</p>;
  if (error) return <p style={{ color: 'red' }}>{error}</p>;
  if (tickets.length === 0) return <p>No tickets found.</p>;

  return (
    <section>
      <h2>Tickets</h2>
      <ul>
        {tickets.map((t) => (
          <li key={t.id} style={{ marginBottom: '0.5rem' }}>
            <strong>{t.title}</strong> – {t.status}
            <button
              style={{ marginLeft: '1rem' }}
              onClick={() => onSelect(t)}
            >
              View
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default TicketList;
