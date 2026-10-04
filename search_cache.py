"""Reusable contiguous visual index; only changed vectors are read from SQLite."""
import time
import numpy as np

class SearchCache:
    def __init__(self, path):
        self.path=path
        self.ids=[]
        self.signatures={}
        self.vectors=np.empty((0,512),dtype=np.float32)
        self.saved=0
        if path.exists():
            try:
                with np.load(path,allow_pickle=False) as data:
                    self.ids=data['ids'].tolist()
                    self.vectors=data['vectors']
                    self.signatures=dict(zip(self.ids,zip(data['mtime'].tolist(),data['size'].tolist())))
                if len(self.ids)!=len(self.vectors):
                    raise ValueError('Incomplete cache')
            except Exception:
                self.ids=[];self.signatures={};self.vectors=np.empty((0,512),dtype=np.float32)

    def refresh(self,c):
        current={r[0]:(r[1],r[2]) for r in c.execute('SELECT media_id,mtime,size FROM embeddings INDEXED BY embedding_cache_meta')}
        changed=[ident for ident,signature in current.items() if self.signatures.get(ident)!=signature]
        removed=set(self.signatures)-set(current)
        if not changed and not removed:
            return
        changed_set=set(changed)
        keep=[i for i,ident in enumerate(self.ids) if ident in current and ident not in changed_set]
        ids=[self.ids[i] for i in keep]
        matrices=[self.vectors[keep]]
        for offset in range(0,len(changed),500):
            batch=changed[offset:offset+500]
            rows=c.execute('SELECT media_id,vector FROM embeddings WHERE media_id IN ('+','.join('?' for _ in batch)+')',batch).fetchall()
            ids.extend(r[0] for r in rows)
            if rows:
                matrices.append(np.stack([np.frombuffer(r[1],dtype=np.float32) for r in rows]))
        self.ids=ids;self.vectors=np.concatenate(matrices);self.signatures={i:current[i] for i in ids}
        if time.monotonic()-self.saved>300 or not self.path.exists():
            temporary=self.path.with_suffix('.tmp')
            with temporary.open('wb') as output:
                np.savez(output,ids=np.asarray(ids),vectors=self.vectors,
                  mtime=np.asarray([current[i][0] for i in ids]),size=np.asarray([current[i][1] for i in ids]))
            temporary.replace(self.path)
            self.saved=time.monotonic()

    def rank(self,c,vector,clauses,args):
        self.refresh(c)
        allowed={r[0] for r in c.execute('SELECT id FROM media WHERE '+' AND '.join(clauses),args)}
        scores=np.einsum('ij,j->i',self.vectors,vector)
        eligible=np.array([i for i,ident in enumerate(self.ids) if ident in allowed],dtype=np.int64)
        best=eligible[np.argsort(scores[eligible])[-300:][::-1]]
        return [(self.ids[i],float(scores[i])) for i in best]
