"""Optional offline dense retrieval. Verified local files only; never download at runtime."""
from pathlib import Path
from collections import OrderedDict
import hashlib,json,math,threading,os

# Dense dot products are small; avoid a large BLAS thread pool per worker.
for variable in ["OPENBLAS_NUM_THREADS","OMP_NUM_THREADS","MKL_NUM_THREADS"]:
    os.environ.setdefault(variable,"1")

MODEL_ID='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
MODEL_SHA='98a01d88b7de996cdea58c32ca71208c09968d143798814b2ea09d3439dc334f'


def passages_digest(passages):
    h=hashlib.sha256()
    for p in passages:h.update(json.dumps(p,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode());h.update(b'\n')
    return h.hexdigest()


class LocalEncoder:
    def __init__(self,directory,threads=2):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer
        self.np=np;root=Path(directory).resolve();meta=json.loads((root/'manifest.json').read_text())
        if meta.get('model_id')!=MODEL_ID or meta.get('dimensions')!=384 or meta['files'].get('model.onnx')!=MODEL_SHA:raise ValueError('Unrecognized embedding model')
        for name,sha in meta['files'].items():
            p=(root/name).resolve()
            if not p.is_relative_to(root) or hashlib.sha256(p.read_bytes()).hexdigest()!=sha:raise ValueError('Embedding artifact integrity failed')
        options=ort.SessionOptions();options.intra_op_num_threads=max(1,min(int(threads),4));options.inter_op_num_threads=1
        options.add_session_config_entry('session.intra_op.allow_spinning','0');options.add_session_config_entry('session.inter_op.allow_spinning','0')
        self.session=ort.InferenceSession(str(root/'model.onnx'),options,providers=['CPUExecutionProvider'])
        self.tokenizer=Tokenizer.from_file(str(root/'tokenizer.json'));self.tokenizer.enable_truncation(max_length=128);self.tokenizer.enable_padding(pad_id=0,pad_token='[PAD]')
        self.identity=hashlib.sha256(json.dumps(meta,sort_keys=True).encode()).hexdigest();self.lock=threading.Lock()

    def encode(self,texts):
        np=self.np
        with self.lock:
            tokens=self.tokenizer.encode_batch(texts)
            inputs={name:np.asarray([getattr(t,attr) for t in tokens],dtype=np.int64) for name,attr in [('input_ids','ids'),('attention_mask','attention_mask'),('token_type_ids','type_ids')]}
            hidden=self.session.run(['last_hidden_state'],inputs)[0];mask=inputs['attention_mask'][:,:,None]
            pooled=(hidden*mask).sum(axis=1)/np.maximum(mask.sum(axis=1),1)
            return (pooled/np.maximum(np.linalg.norm(pooled,axis=1,keepdims=True),1e-12)).astype(np.float32)


def passage_windows(p):
    # Bounded overlapping windows supplement, rather than replace, full-text search.
    text=' '.join(p['text'].split());prefix=p.get('section','')+' '
    return [prefix+text[start:start+450] for start in range(0,min(len(text),1050),350)] or [prefix]


def build_index(passages,encoder,directory,*,batch_size=32,progress=None):
    import numpy as np
    import os,tempfile
    root=Path(directory);root.mkdir(parents=True,exist_ok=True);root.chmod(0o700)
    vectors=np.empty((len(passages),384),dtype=np.float32)
    for start in range(0,len(passages),batch_size):
        batch=passages[start:start+batch_size];texts=[];lengths=[]
        for p in batch:
            windows=passage_windows(p);lengths.append(len(windows));texts.extend(windows)
        encoded=encoder.encode(texts);offset=0
        for i,count in enumerate(lengths):
            value=encoded[offset:offset+count].mean(axis=0);offset+=count;vectors[start+i]=value/max(float(np.linalg.norm(value)),1e-12)
        if progress and start%512==0:progress(min(start+len(batch),len(passages)),len(passages))
    fd,name=tempfile.mkstemp(dir=root,suffix='.npy')
    with os.fdopen(fd,'wb') as f:os.fchmod(f.fileno(),0o600);np.save(f,vectors,allow_pickle=False)
    path=root/'vectors.npy';os.replace(name,path)
    from evaluation_core import private_json
    private_json(root/'index.json',{'schema_version':1,'passages_sha256':passages_digest(passages),'model_sha256':encoder.identity,'ids':[p['id'] for p in passages],
        'vectors_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'shape':[len(passages),384],'windows':{'characters':450,'stride':350,'maximum':3}})
    return {'passages':len(passages),'dimensions':384}


class SemanticIndex:
    def __init__(self,passages,encoder,directory):
        import numpy as np
        root=Path(directory);meta=json.loads((root/'index.json').read_text());path=root/'vectors.npy'
        if meta.get('schema_version')!=1 or meta.get('passages_sha256')!=passages_digest(passages) or meta.get('model_sha256')!=encoder.identity:raise ValueError('Semantic index is stale or belongs to another corpus/model')
        if meta['ids']!=[p['id'] for p in passages] or len(set(meta['ids']))!=len(passages):raise ValueError('Semantic passage alignment failed')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=meta['vectors_sha256']:raise ValueError('Semantic vectors changed')
        self.vectors=np.load(path,mmap_mode='r',allow_pickle=False)
        if list(self.vectors.shape)!=[len(passages),384] or self.vectors.dtype!=np.float32 or not np.isfinite(self.vectors).all():raise ValueError('Invalid semantic vectors')
        self.encoder=encoder;self.passages=passages;self.ids=meta['ids'];self.cache=OrderedDict();self.lock=threading.Lock();self.metadata=meta

    def search(self,question,eligible,limit=8,minimum=.36):
        import numpy as np
        key=hashlib.sha256(question.encode()).digest()
        with self.lock:
            query=self.cache.get(key)
            if query is None:
                query=self.encoder.encode([question[:3000]])[0];self.cache[key]=query
                if len(self.cache)>128:self.cache.popitem(last=False)
        indexes=np.asarray([i for i,ident in enumerate(self.ids) if ident in eligible],dtype=np.int32)
        if not len(indexes):return []
        scores=self.vectors[indexes]@query;order=np.argsort(-scores,kind='stable')[:limit]
        return [self.passages[int(indexes[j])] for j in order if float(scores[j])>=minimum]


def merge_hits(lexical,dense,limit):
    if not dense:return lexical[:limit]
    # Protect leading lexical rules/exceptions; use at most two semantic-only hits.
    keep=min(5,max(1,limit-2));chosen=list(lexical[:keep]);seen={p['id'] for p in chosen}
    # Do not evict the only lexical reference to another legal act.
    documents={p.get('document_id') for p in chosen}
    for p in lexical[keep:]:
        if p.get('document_id') not in documents:
            chosen.append(p);seen.add(p['id']);documents.add(p.get('document_id'))
    if len(chosen)>=limit:return chosen[:limit]
    added=0
    for p in dense:
        if p['id'] not in seen:chosen.append(p);seen.add(p['id']);added+=1
        if len(chosen)>=limit or added>=2:break
    for p in lexical:
        if p['id'] not in seen:chosen.append(p);seen.add(p['id'])
        if len(chosen)>=limit:break
    return chosen[:limit]
