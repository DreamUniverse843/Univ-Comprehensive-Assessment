"""独立项目库和学院公共名册；删除进入可恢复的回收站。"""
import hashlib
import json
import re
import secrets
import sqlite3
from pathlib import Path

TABLES=['records','files','bases','meta','departments','classes','roster','audit','archives']

def catalog(app):
    path=app.DB.parent/'projects-catalog.sqlite3'
    app.initialize(path)
    c=app.connect(path)
    c.execute('CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,created TEXT NOT NULL,deleted INTEGER NOT NULL DEFAULT 0)')
    c.execute('BEGIN IMMEDIATE')
    if not c.execute("SELECT 1 FROM projects WHERE id='legacy'").fetchone():
        with app.connect(app.DB) as old:
            meta=dict(old.execute('SELECT key,value FROM meta'))
            c.execute('INSERT INTO projects VALUES(?,?,?,0)',('legacy',meta.get('year','')+' '+meta.get('college','')+'测评',app.now()))
            for table in ['departments','classes','roster']:
                rows=[dict(r) for r in old.execute('SELECT * FROM '+table)]
                for row in rows:
                    c.execute('INSERT OR IGNORE INTO '+table+' ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',tuple(row.values()))
    app.registry.seed_dictionary(c);c.commit()
    return c

def path_for(app,ident):
    if ident=='legacy':return app.DB
    if not re.fullmatch(r'[a-f0-9]{32}',ident):raise ValueError('项目编号无效')
    return app.DB.parent/'projects'/(ident+'.sqlite3')

def resolve(app,ident):
    with catalog(app) as c:
        row=c.execute('SELECT * FROM projects WHERE id=? AND deleted=0',(ident,)).fetchone()
    if not row:raise ValueError('项目不存在或已删除，请到项目管理切换或恢复')
    path=path_for(app,ident)
    if not path.exists():raise ValueError('项目数据文件缺失')
    return path

def listing(app):
    result=[]
    with catalog(app) as c:rows=c.execute('SELECT * FROM projects ORDER BY created DESC,id').fetchall()
    for row in rows:
        item=dict(row)
        with app.connect(path_for(app,item['id'])) as c:
            meta=dict(c.execute('SELECT key,value FROM meta'))
            item.update(year=meta.get('year'),college=meta.get('college'),locked=meta.get('locked')=='true',records=c.execute('SELECT COUNT(*) FROM records').fetchone()[0],students=c.execute('SELECT COUNT(*) FROM roster').fetchone()[0])
        result.append(item)
    return result

def copy_roster(app,src,dst,college,actor):
    master=app.registry.get(src);added=0;conflicts=[]
    departments={d['id']:d for d in master['departments'] if d['active'] and (college=='全部学院' or d['name']==college)}
    for cls in master['classes']:
        if not cls['active'] or cls['department_id'] not in departments:continue
        dep=departments[cls['department_id']]
        dst.execute('INSERT OR IGNORE INTO departments(name,code) VALUES(?,?)',(dep['name'],dep['code']))
        target=dst.execute('SELECT id,active FROM departments WHERE name=?',(dep['name'],)).fetchone()
        if not target or not target['active']:continue
        dst.execute('INSERT OR IGNORE INTO classes(department_id,name) VALUES(?,?)',(target['id'],cls['name']))
        target_class=dst.execute('SELECT id,active FROM classes WHERE department_id=? AND name=?',(target['id'],cls['name'])).fetchone()
        if not target_class['active']:continue
        for student in [s for s in master['students'] if s['class_id']==cls['id'] and s['active']]:
            old=dst.execute('SELECT * FROM roster WHERE student_id=?',(student['student_id'],)).fetchone()
            if old:
                if old['name']!=student['name'] or old['class_id']!=target_class['id']:conflicts.append(student['student_id'])
                continue
            ident,before,after=app.registry.save(dst,dict(kind='student',name=student['name'],student_id=student['student_id'],class_id=target_class['id']))
            app.log(dst,actor,'从学院名册加入项目',ident,before,after);added+=1
    return {'added':added,'conflicts':conflicts}

def create(app,body,actor):
    name=str(body.get('name','')).strip();year=str(body.get('year',''));college=str(body.get('college','')).strip()
    if not name or len(name)>100:raise ValueError('项目名称须为 1～100 字')
    if not re.fullmatch(r'20\d{2}-20\d{2}',year) or int(year[5:])!=int(year[:4])+1:raise ValueError('请填写连续学年，如 2025-2026')
    with catalog(app) as master:
        if college!='全部学院' and not master.execute('SELECT 1 FROM departments WHERE name=? AND active=1',(college,)).fetchone():raise ValueError('请选择有效学院')
        ident=secrets.token_hex(16);path=path_for(app,ident);app.initialize(path)
        with app.connect(path) as c:
            for department in app.registry.get(master)['departments']:
                c.execute('INSERT INTO departments(id,name,code,active) VALUES(?,?,?,?)',(department['id'],department['name'],department['code'],department['active']))
            c.execute("INSERT OR IGNORE INTO meta VALUES('department_dictionary_v1','1')")
            c.execute("UPDATE meta SET value=? WHERE key='year'",(year,));c.execute("UPDATE meta SET value=? WHERE key='college'",(college,))
            if body.get('use_roster',True):copy_roster(app,master,c,college,actor)
            app.log(c,actor,'创建测评项目',ident,after={'name':name,'year':year,'college':college})
        master.execute('INSERT INTO projects VALUES(?,?,?,0)',(ident,name,app.now()))
    return {'id':ident}

def export(app,ident):
    path=resolve(app,ident)
    with catalog(app) as master:name=master.execute('SELECT name FROM projects WHERE id=?',(ident,)).fetchone()[0]
    with app.connect(path) as c:
        c.execute('BEGIN')
        tables={t:[dict(r) for r in c.execute('SELECT * FROM '+t)] for t in TABLES}
    payload={'format':'assessment-project','version':1,'name':name,'tables':tables}
    payload['digest']=hashlib.sha256(app.dumps(payload).encode()).hexdigest()
    return payload

def import_project(app,body,actor):
    payload=body.get('package')
    if not isinstance(payload,dict) or payload.get('format')!='assessment-project' or payload.get('version')!=1:raise ValueError('不是支持的测评项目包')
    core={k:v for k,v in payload.items() if k!='digest'}
    if hashlib.sha256(app.dumps(core).encode()).hexdigest()!=payload.get('digest'):raise ValueError('项目包校验失败，文件可能不完整')
    tables=payload.get('tables',{})
    if set(tables)!=set(TABLES) or any(not isinstance(v,list) for v in tables.values()):raise ValueError('项目包缺少数据表')
    name=str(body.get('name') or payload.get('name','导入项目')).strip()
    if not name or len(name)>100:raise ValueError('项目名称须为 1～100 字')
    ident=secrets.token_hex(16);path=path_for(app,ident);app.initialize(path)
    with app.connect(path) as c:
        c.execute('BEGIN IMMEDIATE')
        for table in TABLES:
            columns=[r[1] for r in c.execute('PRAGMA table_info('+table+')')]
            if table=='meta':c.execute('DELETE FROM meta')
            for row in tables[table]:
                if not isinstance(row,dict) or set(row)!=set(columns):raise ValueError('项目表字段不完整：'+table)
                c.execute('INSERT INTO '+table+' ('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')',tuple(row[k] for k in columns))
        meta=dict(c.execute('SELECT key,value FROM meta'))
        if not {'college','year','locked'}<=meta.keys() or meta['locked'] not in ['true','false']:raise ValueError('项目设置无效')
        if c.execute('SELECT 1 FROM roster r LEFT JOIN classes c ON c.id=r.class_id WHERE c.id IS NULL').fetchone() or c.execute('SELECT 1 FROM classes c LEFT JOIN departments d ON d.id=c.department_id WHERE d.id IS NULL').fetchone():raise ValueError('名册关联缺失')
        for r in c.execute('SELECT original,current FROM records'):
            if not all(isinstance(json.loads(v),dict) for v in r):raise ValueError('申报数据无效')
        if meta['locked']=='true' and not tables['archives']:raise ValueError('归档快照缺失')
        app.state(c) # Validate the imported project with the same reader before publishing it.
        app.log(c,actor,'导入项目副本',ident,after={'name':name,'source_digest':payload['digest']})
    with catalog(app) as master:master.execute('INSERT INTO projects VALUES(?,?,?,0)',(ident,name,app.now()))
    return {'id':ident}

def manage(app,body,actor):
    ident=body.get('id');action=body.get('action')
    with catalog(app) as c:
        row=c.execute('SELECT * FROM projects WHERE id=?',(ident,)).fetchone()
        if not row:raise ValueError('项目不存在')
        if action=='rename':
            name=str(body.get('name','')).strip()
            if not name or len(name)>100:raise ValueError('项目名称须为 1～100 字')
            c.execute('UPDATE projects SET name=? WHERE id=?',(name,ident))
        elif action in ['delete','restore']:c.execute('UPDATE projects SET deleted=? WHERE id=?',(int(action=='delete'),ident))
        else:raise ValueError('项目操作无效')
        app.log(c,actor,'项目'+action,ident,dict(row),body)
    return {'ok':True}
