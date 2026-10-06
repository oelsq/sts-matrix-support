import React, { useEffect, useState } from 'react';
import TicketForm from './TicketForm';
import TicketList from './TicketList';
import TicketDetail from './TicketDetail';

function App() {
  const [selectedTicket, setSelectedTicket] = useState(null);
  const [refreshFlag, setRefreshFlag] = useState(0);

  const refresh = () => setRefreshFlag((f) => f + 1);

  return (
    <div className="container">
      <h1>Support Operator Interface</h1>
      <TicketForm onCreated={refresh} />
      <hr />
      {selectedTicket ? (
        <TicketDetail ticket={selectedTicket} onClose={() => setSelectedTicket(null)} onUpdated={refresh} />
      ) : (
        <TicketList onSelect={setSelectedTicket} refreshFlag={refreshFlag} />
      )}
    </div>
  );
}

export default App;
