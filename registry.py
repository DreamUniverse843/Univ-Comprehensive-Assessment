"""院系、班级、学生主数据维护，停用可恢复；归档由应用统一锁定。"""
from pathlib import Path
import json
import re
from collections import defaultdict
from scoring import clean

def initialize(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS departments(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, active INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS classes(id INTEGER PRIMARY KEY, department_id INTEGER NOT NULL, name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, UNIQUE(department_id,name));
    CREATE TABLE IF NOT EXISTS roster(id INTEGER PRIMARY KEY, class_id INTEGER NOT NULL, name TEXT NOT NULL, student_id TEXT NOT NULL UNIQUE, active INTEGER NOT NULL DEFAULT 1);
    ''')

    if 'code' not in {r[1] for r in c.execute('PRAGMA table_info(departments)')}:
        c.execute('ALTER TABLE departments ADD COLUMN code TEXT')
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS department_code ON departments(code) WHERE code IS NOT NULL AND code <> ''")

    if 'aliases' not in {r[1] for r in c.execute('PRAGMA table_info(classes)')}:
        c.execute('ALTER TABLE classes ADD COLUMN aliases TEXT')

    for table in ['classes','roster']:
        if 'deleted' not in {r[1] for r in c.execute('PRAGMA table_info('+table+')')}:
            c.execute('ALTER TABLE '+table+' ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0')

def department_dictionary():
    return sorted(json.loads(Path(__file__).with_name('departments.json').read_text()),key=lambda d:int(d['code']))

def seed_dictionary(c):
    for item in department_dictionary():
        existing=c.execute('SELECT id,code FROM departments WHERE name=?',(item['name'],)).fetchone()
        if existing:
            if not existing['code']: c.execute('UPDATE departments SET code=? WHERE id=?',(item['code'],existing['id']))
        else: c.execute('INSERT INTO departments(name,code) VALUES(?,?)',(item['name'],item['code']))
    c.execute("INSERT OR IGNORE INTO meta VALUES('department_dictionary_v1','1')")

def get(c):
    stored={r['name']:dict(r) for r in c.execute('SELECT * FROM departments')}
    departments=[dict(id=stored[d['name']]['id'],name=d['name'],code=d['code'],active=1) for d in department_dictionary() if d['name'] in stored]
    classes=[dict(r) for r in c.execute('SELECT classes.*,departments.name AS college FROM classes JOIN departments ON departments.id=classes.department_id WHERE (classes.deleted IS NULL OR classes.deleted=0) ORDER BY departments.name,classes.name')]
    students=[dict(r) for r in c.execute('SELECT roster.*,classes.name AS class_name,departments.name AS college FROM roster JOIN classes ON classes.id=roster.class_id JOIN departments ON departments.id=classes.department_id WHERE (roster.deleted IS NULL OR roster.deleted=0) ORDER BY roster.student_id')]
    return {'departments':departments,'classes':classes,'students':students}

def candidates(c):
    groups=defaultdict(list)
    for item in c.execute('SELECT current FROM records'):
        r=json.loads(item[0]);sid=clean(r.get('student_id'))
        if re.fullmatch(r'\d{10,12}',sid) and r.get('name'): groups[sid].append(r)
    current={r[0] for r in c.execute('SELECT student_id FROM roster')};items=[];conflicts=[]
    for sid,rows in groups.items():
        if sid in current: continue
        identities={(clean(r.get('name')),clean(r.get('college'))) for r in rows}
        if len(identities)!=1: conflicts.append(sid);continue
        r=dict(rows[0]);r['class_name']=canonical_class(c,r.get('college',''),r.get('class_name',''))
        if not r.get('college') or not r.get('class_name'): conflicts.append(sid);continue
        if re.match(r'^20\d{2}2\d{7}$',sid) or re.search(r'研究生|（研）|\(研\)|博物馆|现当代文学|古代文学|文字文献|古文中国史|比较文学',r['class_name']): continue
        items.append({'student_id':sid,'name':clean(r['name']),'college':clean(r['college']),'class_name':r['class_name']})
    return items,conflicts

def save(c,body):
    kind=body.get('kind');table={'department':'departments','class':'classes','student':'roster'}.get(kind)
    if not table: raise ValueError('基础信息类型无效')
    ident=body.get('id');old=c.execute(f'SELECT * FROM {table} WHERE id=?',(ident,)).fetchone() if ident else None
    if ident and (not old or ('deleted' in old.keys() and old['deleted'])): raise ValueError('信息不存在')
    name=str(body.get('name','')).strip()
    if not name or len(name)>80: raise ValueError('名称不能为空，且最多 80 字')
    active=0 if body.get('active') is False else 1
    if not active and kind=='department' and c.execute('SELECT 1 FROM classes WHERE department_id=? AND active=1',(ident,)).fetchone(): raise ValueError('请先转移或停用院系下的班级')
    if not active and kind=='class' and c.execute('SELECT 1 FROM roster WHERE class_id=? AND active=1',(ident,)).fetchone(): raise ValueError('请先转移或停用班级中的学生')
    values={'name':name,'active':active}
    if kind=='department':
        code=str(body.get('code',old['code'] if old else '') or '').strip()
        if code and not re.fullmatch(r'[0-9]{1,20}',code): raise ValueError('院系所代码须为 1～20 位数字')
        values['code']=code or None
        if old and old['name']!=name:
            c.execute("UPDATE meta SET value=? WHERE key='college' AND value=?",(name,old['name']))
    if kind=='class':
        parent=body.get('department_id')
        if not c.execute('SELECT 1 FROM departments WHERE id=? AND active=1',(parent,)).fetchone(): raise ValueError('请选择有效院系')
        values['department_id']=parent
        aliases=str(body.get('aliases',old['aliases'] if old else '') or '').strip()
        if len(aliases)>500: raise ValueError('别名总长度不得超过 500 字')
        names={name,*alias_names(aliases)}
        for other in c.execute('SELECT * FROM classes WHERE department_id=?',(parent,)):
            if other['id']!=ident and names.intersection({other['name'],*alias_names(other['aliases'])}):
                raise ValueError('同一院系的班级名称或别名重复（含回收站），请先核对')
        values['aliases']=','.join(sorted(names-{name})) or None
    if kind=='student':
        parent=body.get('class_id');sid=clean(body.get('student_id'))
        if not c.execute('SELECT 1 FROM classes WHERE id=? AND active=1 AND deleted=0',(parent,)).fetchone(): raise ValueError('请选择有效班级')
        if not re.fullmatch(r'\d{10,12}',sid): raise ValueError('学号须为 10～12 位数字')
        if old and old['student_id']!=sid:
            linked=any(clean(json.loads(row[0]).get('student_id'))==old['student_id'] for row in c.execute('SELECT current FROM records'))
            if linked: raise ValueError('此学号已有申报。请先在申报审核中更正学号，再修改名册，以免失去关联。')
        values.update(class_id=parent,student_id=sid)
    try:
        if ident:
            c.execute(f"UPDATE {table} SET "+','.join(k+'=?' for k in values)+' WHERE id=?',tuple(values.values())+(ident,))
        else:
            cur=c.execute(f"INSERT INTO {table} ("+','.join(values)+') VALUES ('+','.join('?' for _ in values)+')',tuple(values.values()));ident=cur.lastrowid
    except Exception as e:
        if 'UNIQUE constraint' in str(e): raise ValueError('该院系名称、代码、班级或学号已存在，请编辑已有信息') from e
        raise
    return str(ident),dict(old) if old else None,dict(c.execute(f'SELECT * FROM {table} WHERE id=?',(ident,)).fetchone())

def seed(c):
    items,conflicts=candidates(c)
    for r in items:
        c.execute('INSERT OR IGNORE INTO departments(name) VALUES(?)',(r['college'],))
        dep=c.execute('SELECT id,active FROM departments WHERE name=?',(r['college'],)).fetchone()
        if not dep['active']:continue
        c.execute('INSERT OR IGNORE INTO classes(department_id,name) VALUES(?,?)',(dep['id'],r['class_name']))
        cls=c.execute('SELECT id,active,deleted FROM classes WHERE department_id=? AND name=?',(dep['id'],r['class_name'])).fetchone()
        if not cls['active'] or cls['deleted']:continue
        c.execute('INSERT OR IGNORE INTO roster(class_id,name,student_id) VALUES(?,?,?)',(cls['id'],r['name'],r['student_id']))
    return {'added':len(items),'conflicts':conflicts}

def batch(c,body,log,actor):
    kind=body.get('kind');table={'student':'roster','class':'classes'}.get(kind)
    ids=body.get('ids',[]);operation=body.get('operation')
    if not table or not isinstance(ids,list) or not ids or len(ids)>20000 or any(type(i) is not int for i in ids):raise ValueError('请选择要维护的学生或班级')
    ids=sorted(set(ids));changed=0
    for ident in ids:
        row=c.execute('SELECT * FROM '+table+' WHERE id=?',(ident,)).fetchone()
        if not row or row['deleted']:raise ValueError('部分信息已变化，请刷新后重新选择')
        payload={**dict(row),'kind':kind,'active':bool(row['active'])}
        if operation in ['enable','disable']:payload['active']=operation=='enable'
        elif operation=='move' and kind=='student':payload['class_id']=body.get('class_id')
        elif operation=='delete':
            # 软删除：标记 deleted=1
            if kind=='student':
                linked=c.execute('SELECT 1 FROM records WHERE json_extract(current,"$.student_id")=? LIMIT 1',(row['student_id'],)).fetchone()
                if linked:raise ValueError(f'{row["name"]}（{row["student_id"]}）已有申报记录，不能删除')
            elif kind=='class':
                has_students=c.execute('SELECT 1 FROM roster WHERE class_id=? AND (deleted IS NULL OR deleted=0) LIMIT 1',(ident,)).fetchone()
                if has_students:raise ValueError(f'{row["name"]} 班级中仍有学生，请先转移或删除学生')
            c.execute('UPDATE '+table+' SET deleted=1 WHERE id=?',(ident,))
            log(c,actor,'批量删除'+('学生' if kind=='student' else '班级'),str(ident),dict(row),{'deleted':1})
            changed+=1
            continue
        else:raise ValueError('批量操作无效')
        key,before,after=save(c,payload)
        if before!=after:log(c,actor,'批量维护'+('学生' if kind=='student' else '班级'),key,before,after);changed+=1
    return {'changed':changed}

def get_recycle(c):
    """获取回收站中的班级和学生"""
    classes=[dict(r) for r in c.execute('SELECT classes.*,departments.name AS college FROM classes JOIN departments ON departments.id=classes.department_id WHERE classes.deleted=1 ORDER BY departments.name,classes.name')]
    students=[dict(r) for r in c.execute('SELECT roster.*,classes.name AS class_name,departments.name AS college FROM roster JOIN classes ON classes.id=roster.class_id JOIN departments ON departments.id=classes.department_id WHERE roster.deleted=1 ORDER BY roster.student_id')]
    return {'classes':classes,'students':students}

def restore(c,body,log,actor):
    """批量恢复已删除的班级或学生"""
    kind=body.get('kind');table={'student':'roster','class':'classes'}.get(kind)
    ids=body.get('ids',[])
    if not table or not isinstance(ids,list) or not ids or len(ids)>20000 or any(type(i) is not int for i in ids):raise ValueError('请选择要恢复的项')
    changed=0
    for ident in ids:
        row=c.execute('SELECT * FROM '+table+' WHERE id=? AND deleted=1',(ident,)).fetchone()
        if not row:continue
        if kind=='student' and not c.execute('SELECT 1 FROM classes WHERE id=? AND deleted=0 AND active=1',(row['class_id'],)).fetchone():
            raise ValueError('请先恢复并启用学生所属班级')
        c.execute('UPDATE '+table+' SET deleted=0 WHERE id=?',(ident,))
        log(c,actor,'恢复'+('学生' if kind=='student' else '班级'),str(ident),dict(row),{'deleted':0})
        changed+=1
    return {'changed':changed}

def purge(c,body,log,actor):
    """永久删除回收站中的班级或学生"""
    kind=body.get('kind');table={'student':'roster','class':'classes'}.get(kind)
    ids=body.get('ids',[])
    if not table or not isinstance(ids,list) or not ids or len(ids)>20000 or any(type(i) is not int for i in ids):raise ValueError('请选择要永久删除的项')
    changed=0
    for ident in ids:
        row=c.execute('SELECT * FROM '+table+' WHERE id=? AND deleted=1',(ident,)).fetchone()
        if not row:raise ValueError('只能永久删除回收站中的项')
        if kind=='class' and c.execute('SELECT 1 FROM roster WHERE class_id=?',(ident,)).fetchone():
            raise ValueError('班级仍有学生（含回收站），请先处理学生')
        if kind=='student' and c.execute("SELECT 1 FROM records WHERE json_extract(current,'$.student_id')=?",(row['student_id'],)).fetchone():
            raise ValueError('学生已有申报，不能永久删除')
        c.execute('DELETE FROM '+table+' WHERE id=?',(ident,))
        log(c,actor,'永久删除'+('学生' if kind=='student' else '班级'),str(ident),dict(row),{})
        changed+=1
    return {'changed':changed}


def alias_names(value):
    return [x.strip() for x in re.split(r'[,，;；\n]',value or '') if x.strip()]

def canonical_class(c,college,name):
    matches=[r['name'] for r in c.execute('SELECT classes.* FROM classes JOIN departments d ON d.id=department_id WHERE d.name=? AND classes.deleted=0',(college,)) if name==r['name'] or name in alias_names(r['aliases'])]
    if len(set(matches))>1:raise ValueError('班级别名匹配多个班级，请先维护别名')
    return matches[0] if matches else name

def normalize_records(c,rows):
    result=[]
    for row in rows:
        r=dict(row);name=canonical_class(c,r.get('college',''),r.get('class_name',''))
        if name!=r.get('class_name'):
            r['source_class_name']=r.get('class_name');r['class_name']=name
        result.append(r)
    return result
