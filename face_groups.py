"""Bounded, multi-example face matching and duplicate suggestions."""
import numpy as np
import json
from PIL import Image

def portrait(image, box):
    x,y,w,h = map(float, box[:4])
    side = min(max(w,h)*1.5, image.width, image.height)
    left = max(0, min(image.width-side, x+w/2-side/2))
    top = max(0, min(image.height-side, y+h/2-side/2))
    return image.crop((left,top,left+side,top+side)).resize((180,180), Image.Resampling.LANCZOS)

def representatives(c):
    # Up to eight recent examples plus the original; bounded even for huge groups.
    rows=c.execute('''SELECT person_id,feature FROM (
      SELECT person_id,feature,row_number() OVER(PARTITION BY person_id ORDER BY id DESC) n FROM faces
      ) WHERE n<=8''').fetchall()
    groups={r['id']:[np.frombuffer(r['feature'],dtype=np.float32)] for r in c.execute('SELECT id,feature FROM people')}
    for r in rows:
        groups.setdefault(r['person_id'],[]).append(np.frombuffer(r['feature'],dtype=np.float32))
    return groups

def match(feature, groups, excluded=()):
    ranked=[]
    for pid, vectors in groups.items():
        if pid in excluded:
            continue
        scores=np.asarray(vectors) @ feature
        # Two supporting examples allow pose variation without trusting one weak match.
        top=np.sort(scores)[-2:]
        score=float(top.mean()) if len(top)>1 else float(top[0])
        ranked.append((score,pid))
    ranked.sort(reverse=True)
    if not ranked or ranked[0][0]<.45:
        return None
    if len(ranked)>1 and ranked[0][0]-ranked[1][0]<.04:
        return None  # Ambiguous relatives are safer to review than silently combine.
    return ranked[0][1]

def suggestions(c, limit=40):
    groups=representatives(c)
    ids=list(groups)
    if len(ids)<2:
        return []
    centers=np.asarray([np.mean(groups[i],axis=0) for i in ids])
    centers/=np.maximum(np.linalg.norm(centers,axis=1,keepdims=True),1e-8)
    names={r['id']:r['name'] for r in c.execute('SELECT id,name FROM people')}
    # Never suggest combining two people seen in the same still photo.
    conflicts={tuple(sorted((r[0],r[1]))) for r in c.execute('''SELECT DISTINCT a.person_id,b.person_id
      FROM faces a JOIN faces b ON a.media_id=b.media_id AND a.person_id<b.person_id
      JOIN media m ON m.id=a.media_id WHERE m.kind='photo' ''')}
    candidates=[]
    for start in range(0,len(ids),128):
        scores=centers[start:start+128] @ centers.T
        for offset,row in enumerate(scores):
            i=start+offset
            for j in np.where(row[i+1:]>=.40)[0]+i+1:
                a,b=ids[i],ids[j]
                if tuple(sorted((a,b))) in conflicts or (names[a] and names[b] and names[a]!=names[b]):
                    continue
                candidates.append(dict(source=a,target=b,score=round(float(row[j]),3)))
    return sorted(candidates,key=lambda p:p['score'],reverse=True)[:limit]

def consolidate(c):
    """Merge only very close groups; save their previous membership for recovery."""
    c.execute('''CREATE TABLE IF NOT EXISTS face_merge_history (
      id INTEGER PRIMARY KEY, source INTEGER, target INTEGER, name TEXT, feature BLOB,
      face_ids TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP)''')
    touched=set()
    merged=0
    for pair in suggestions(c,160):
        if pair['score']<.80:
            break
        a,b=pair['source'],pair['target']
        if a in touched or b in touched:
            continue
        groups={r['id']:r for r in c.execute('''SELECT p.*,count(f.id) n FROM people p
          LEFT JOIN faces f ON f.person_id=p.id WHERE p.id IN (?,?) GROUP BY p.id''',(a,b))}
        # Keep a human name, otherwise the larger existing group.
        if (bool(groups[a]['name']),groups[a]['n'])>(bool(groups[b]['name']),groups[b]['n']):
            a,b=b,a
        members=[r[0] for r in c.execute('SELECT id FROM faces WHERE person_id=?',(a,))]
        c.execute('INSERT INTO face_merge_history(source,target,name,feature,face_ids) VALUES(?,?,?,?,?)',
          (a,b,groups[a]['name'],groups[a]['feature'],json.dumps(members)))
        c.execute('UPDATE faces SET person_id=? WHERE person_id=?',(b,a))
        c.execute('DELETE FROM people WHERE id=?',(a,))
        touched.update((a,b))
        merged+=1
    return merged
