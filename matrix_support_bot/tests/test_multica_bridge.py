import asyncio,json,tempfile,unittest
from pathlib import Path
from multica_bridge import MulticaBridge,BridgeError,history_key,answer_for_task,AGENTS

class FakeBridge(MulticaBridge):
    def __init__(self,p):
        c=p/'c.json';c.write_text(json.dumps({'server_url':'https://api.multica.ai','workspace_id':'8ce41896-3a62-4065-baf1-94695dfac01d','token':'synthetic-test-credential'}));super().__init__(c,p/'state.sqlite3',timeout=1);self.calls=[]
    async def api(self,http,method,path,data=None):
        self.calls.append((method,path,data))
        if path=='/api/chat/sessions/':return {'id':'session-'+str(len(self.calls))}
        if method=='POST':return {'task_id':'task-123'}
        return [{'role':'assistant','task_id':'task-old','content':'{"answer":"WRONG"}'},{'role':'assistant','task_id':'task-123','content':'{"answer":"Ответ [S1]"}'}]

class BridgeTests(unittest.TestCase):
    def test_history_isolated_by_worker_room_and_specialist(self):
        base=history_key('r','u','legal')
        self.assertNotEqual(base,history_key('r','v','legal'))
        self.assertNotEqual(base,history_key('s','u','legal'))
        self.assertNotEqual(base,history_key('r','u','finance'))
        with self.assertRaises(BridgeError):history_key('r','u','admin')
    def test_replies_match_exact_task(self):
        self.assertIsNone(answer_for_task([{'role':'assistant','task_id':'other','content':'{"answer":"old"}'}],'new'))
        self.assertEqual(answer_for_task([{'role':'assistant','task_id':'new','content':'```json\n{"answer":"answer [S1]"}\n```'}],'new'),'answer [S1]')
    def test_failures_and_nonstructured_answers_rejected(self):
        for row in [{'role':'assistant','task_id':'x','failure_reason':'failed','content':'text'},{'role':'assistant','task_id':'x','content':'plain text'},{'role':'assistant','task_id':'x','content':'{"answer":false}'}]:
            with self.assertRaises(BridgeError):answer_for_task([row],'x')
    def test_server_and_workspace_allowlisted(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);c=p/'c'
            for server,workspace in [('https://evil.invalid','8ce41896-3a62-4065-baf1-94695dfac01d'),('https://api.multica.ai','other')]:
                c.write_text(json.dumps({'server_url':server,'workspace_id':workspace,'token':'fake'}))
                with self.assertRaises(BridgeError):MulticaBridge(c,p/'db')
    def test_same_event_not_submitted_twice_and_agent_selected(self):
        with tempfile.TemporaryDirectory() as d:
            b=FakeBridge(Path(d));e=[{'id':'doc:p1:1','text':'source'}]
            async def run():
                a=await b.ask(None,'legal','r','u','$event','question',e)
                again=await b.ask(None,'legal','r','u','$event','question',e)
                return a,again
            self.assertEqual(asyncio.run(run()),('Ответ [S1]','Ответ [S1]'))
            self.assertEqual(len([c for c in b.calls if c[0]=='POST' and c[1].endswith('/messages')]),1)
            self.assertEqual(b.calls[0][2]['agent_id'],AGENTS['legal'])
            prompt=json.loads(b.calls[1][2]['content'].split('\n')[-1]);self.assertEqual(prompt['evidence'][0]['id'],'S1')
            self.assertNotIn('token',prompt)
    def test_uncertain_submission_not_duplicated(self):
        with tempfile.TemporaryDirectory() as d:
            b=FakeBridge(Path(d));k=__import__('hashlib').sha256((history_key('r','u','legal')+'|'+'e').encode()).hexdigest()
            b.db.execute('INSERT INTO requests VALUES(?,?,NULL,?,NULL)',(k,'sid','uncertain'));b.db.commit()
            with self.assertRaises(BridgeError):asyncio.run(b.ask(None,'legal','r','u','e','q',[]))
            self.assertEqual(b.calls,[])
if __name__=='__main__':unittest.main()
