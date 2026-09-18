#!/usr/bin/env python3
"""本机单用户测评工作台。运行：bash start.sh；数据存于 .local/assessment.sqlite3。"""
import re
import argparse
import base64
import csv
import hashlib
import io
import json
import mimetypes
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote
from importer import parse_workbook
from scoring import calculate, number, CATEGORIES
import registry
import roster_import
import previews
import projects
import contextvars
import sys

ROOT=Path(__file__).resolve().parent
DB=Path(os.environ.get('ASSESSMENT_DB',ROOT/'.local/assessment.sqlite3'))
DEFAULT_SOURCES=Path.home()/'桌面/文学院25-26附加分大礼包'
POLICY=Path.home()/'桌面/北京语言大学本科生综合素质测评办法.pdf'
TOKEN=secrets.token_urlsafe(32)

def now(): return datetime.now().astimezone().isoformat(timespec='seconds')
def dumps(v): return json.dumps(v,ensure_ascii=False,allow_nan=False)
REQUEST_DB=contextvars.ContextVar('project_db',default=None)
def connect(path=None):
    con=sqlite3.connect(path or REQUEST_DB.get() or DB,timeout=30); con.row_factory=sqlite3.Row
    return con

def initialize(path=None):
    target=path or DB
    target.parent.mkdir(parents=True,exist_ok=True)
    with connect(target) as c:
        c.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, original TEXT NOT NULL, current TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS files(hash TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS bases(student_key TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, time TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT, before TEXT, after TEXT);
        CREATE TABLE IF NOT EXISTS archives(id INTEGER PRIMARY KEY, time TEXT NOT NULL, title TEXT NOT NULL, actor TEXT NOT NULL, digest TEXT NOT NULL, data TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS archive_no_update BEFORE UPDATE ON archives BEGIN SELECT RAISE(ABORT,'归档不可修改'); END;
        CREATE TRIGGER IF NOT EXISTS archive_no_delete BEFORE DELETE ON archives BEGIN SELECT RAISE(ABORT,'归档不可删除'); END;
        CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'日志不可修改'); END;
        CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'日志不可删除'); END;
        INSERT OR IGNORE INTO meta VALUES('locked','false');
        INSERT OR IGNORE INTO meta VALUES('year','2025-2026');
        INSERT OR IGNORE INTO meta VALUES('college','文学院');
        ''')
        registry.initialize(c)

def log(c,actor,action,target='',before=None,after=None):
    c.execute('INSERT INTO audit(time,actor,action,target,before,after) VALUES(?,?,?,?,?,?)',(now(),actor,action,target,dumps(before),dumps(after)))

def import_files(c,files,actor,mappings=None):
    reports=[]
    for filename,data in files:
        digest=hashlib.sha256(data).hexdigest()
        exists=c.execute('SELECT data FROM files WHERE hash=?',(digest,)).fetchone()
        if exists: reports.append({'name':filename,'skipped':True,'count':0}); continue
        info,records=parse_workbook(data,filename,mappings)
        unmatched=[s['name'] for s in info['sheets'] if s['needs_mapping']]
        if unmatched: raise ValueError('请先为以下工作表指定类别：'+filename+' / '+'、'.join(unmatched))
        for r in records: c.execute('INSERT INTO records VALUES(?,?,?)',(r['id'],dumps(r),dumps(r)))
        info['imported_at']=now()
        c.execute('INSERT INTO files VALUES(?,?)',(digest,dumps(info)))
        log(c,actor,'导入工作簿',filename,after={'count':len(records),'hash':digest})
        reports.append(info)
    return reports

def state(c):
    # 已锁定工作区也直接读快照，避免规则代码更新后悄悄重算历史分数。
    if c.execute("SELECT value FROM meta WHERE key='locked'").fetchone()[0]=='true':
        latest=c.execute('SELECT * FROM archives ORDER BY id DESC LIMIT 1').fetchone()
        if latest:
            frozen=json.loads(latest['data'])
            frozen['archives']=[dict(r) for r in c.execute('SELECT id,time,title,actor,digest FROM archives ORDER BY id DESC')]
            frozen['audit']=[dict(r) for r in c.execute('SELECT id,time,actor,action,target FROM audit ORDER BY id DESC LIMIT 100')]
            frozen['locked']=True
            return frozen
    college=c.execute("SELECT value FROM meta WHERE key='college'").fetchone()[0]
    master=registry.get(c)
    data=calculate([json.loads(r['current']) for r in c.execute('SELECT current FROM records ORDER BY id')],{r['student_key']:json.loads(r['data']) for r in c.execute('SELECT * FROM bases')},college,master['students'])
    data['registry']=master
    candidates,conflicts=registry.candidates(c)
    data['registry_candidates']={'count':len(candidates),'conflicts':len(conflicts)}
    data['college']=college
    data['colleges']=sorted(set(r['college'] for r in data['records'] if r.get('college'))|{r['name'] for r in master['departments'] if r['active']})
    data['files']=[json.loads(r['data']) for r in c.execute('SELECT data FROM files')]
    data['locked']=c.execute("SELECT value FROM meta WHERE key='locked'").fetchone()[0]=='true'
    data['year']=c.execute("SELECT value FROM meta WHERE key='year'").fetchone()[0]
    data['archives']=[dict(r) for r in c.execute('SELECT id,time,title,actor,digest FROM archives ORDER BY id DESC')]
    data['audit']=[dict(r) for r in c.execute('SELECT id,time,actor,action,target FROM audit ORDER BY id DESC LIMIT 100')]
    data['stats']={'records':len(data['records']),'students':len(data['students']),'pending':sum(r['status']=='pending' for r in data['records']),'confirmed':sum(r['status']=='confirmed' for r in data['records']),'duplicates':sum(r['status']=='duplicate' for r in data['records']),'outside':sum(r['status']=='outside' for r in data['records'])}
    return data

def actor_of(body):
    actor=str(body.get('actor','')).strip()
    if not actor or len(actor)>50: raise ValueError('请填写审核人姓名（最多 50 字）')
    return actor

def mutate(path,body,c):
    actor=actor_of(body)
    c.execute('BEGIN IMMEDIATE')
    locked=c.execute("SELECT value FROM meta WHERE key='locked'").fetchone()[0]=='true'
    if locked and path!='/api/revise': raise ValueError('此版本已经归档锁定，请先创建修订版')
    if path=='/api/duplicate-resolve':
        current=state(c)
        group=next((g for g in current['duplicate_groups'] if g['id']==body.get('group_id')),None)
        if not group or set(group['member_ids'])!=set(body.get('member_ids',[])): raise ValueError('这组申报已发生变化，请刷新后重新核对')
        keep_id=str(body.get('keep_id',''))
        if keep_id not in group['member_ids']: raise ValueError('采纳项不属于当前重复组')
        reason=str(body.get('reason','')).strip()
        if not reason or len(reason)>2000: raise ValueError('请填写整组处理依据')
        raw=[json.loads(r['current']) for r in c.execute('SELECT current FROM records')]
        original={r['id']:dict(r) for r in raw}
        keep=original[keep_id]
        if keep.get('review') not in ['original','rule','custom']: raise ValueError('请先审核并确认采纳其中一条')
        note=f"组内审核：采纳 {keep['source']} / {keep['sheet']} / 第 {keep['row']} 行，其余关联申报作废。依据：{reason}"
        others=set(group['member_ids'])-{keep_id}
        for r in raw:
            if r['id'] in others: r.update(review='exclude',review_note=note)
        college=current['college'];master=current['registry']['students']
        proposed=calculate(raw,{},college,master)
        selected=next(r for r in proposed['records'] if r['id']==keep_id)
        if selected['status']!='confirmed': raise ValueError('所采纳记录仍有身份、分值或范围问题，请先处理后再作废其他项')
        changed=0
        for r in raw:
            if r['id'] in others and original[r['id']].get('review')!='exclude':
                c.execute('UPDATE records SET current=? WHERE id=?',(dumps(r),r['id']))
                log(c,actor,'重复组作废其余申报',r['id'],original[r['id']],r);changed+=1
        log(c,actor,'完成重复组审核',group['id'],after={'adopted':keep_id,'voided':sorted(others),'reason':reason})
        return {'ok':True,'voided':changed}
    if path=='/api/registry-batch':
        return registry.batch(c,body,log,actor)
    if path=='/api/use-college-roster':
        with projects.catalog(sys.modules[__name__]) as master:
            return projects.copy_roster(sys.modules[__name__],master,c,c.execute("SELECT value FROM meta WHERE key='college'").fetchone()[0],actor)
    if path=='/api/roster-import':
        return roster_import.commit(c,body,log,actor)
    if path=='/api/registry':
        if body.get('kind')=='department':raise ValueError('院系所为固定字典，请修改 departments.json；页面不提供维护')
        ident,before,after=registry.save(c,body)
        log(c,actor,'维护'+{'department':'院系','class':'班级','student':'学生'}[body['kind']],ident,before,after)
        return {'ok':True}
    if path=='/api/registry-seed':
        report=registry.seed(c);log(c,actor,'从申报生成学生名册',after=report)
        return report
    if path=='/api/settings':
        college=str(body.get('college','')).strip()
        if not college or len(college)>80: raise ValueError('请填写学院名称（最多 80 字）')
        year=str(body.get('year',c.execute("SELECT value FROM meta WHERE key='year'").fetchone()[0])).strip()
        if not re.fullmatch(r'20[0-9]{2}-20[0-9]{2}',year) or int(year[5:])!=int(year[:4])+1:
            raise ValueError('学年请填写连续的两年，例如 2025-2026')
        previous_year=c.execute("SELECT value FROM meta WHERE key='year'").fetchone()[0]
        c.execute("UPDATE meta SET value=? WHERE key='year'",(year,))
        if year!=previous_year: log(c,actor,'调整测评学年',before=previous_year,after=year)
        old=c.execute("SELECT value FROM meta WHERE key='college'").fetchone()[0]
        c.execute("UPDATE meta SET value=? WHERE key='college'",(college,)); log(c,actor,'调整测评学院',before=old,after=college)
        return {'ok':True}
    if path=='/api/import':
        files=body.get('files',[])
        if len(files)>20: raise ValueError('每批最多 20 个文件')
        prepared=[]
        for f in files:
            if not str(f.get('name','')).lower().endswith('.xlsx'): raise ValueError('当前支持 .xlsx 文件，PDF 作为参考资料保留')
            prepared.append((f['name'],base64.b64decode(f['data'],validate=True)))
        return {'reports':import_files(c,prepared,actor,body.get('mappings',{}))}
    if path=='/api/record':
        ident=str(body.get('id','')); oldrow=c.execute('SELECT current FROM records WHERE id=?',(ident,)).fetchone()
        if not oldrow: raise ValueError('记录不存在')
        old=json.loads(oldrow[0]); updated=dict(old); changes=body.get('changes',{})
        fields=['student_id','name','college','class_name','category','award','level','kind','event','event_group','review','review_note','custom_score','identity_confirmed','ineligible','eligibility_confirmed','participation_confirmed']
        for key in fields:
            if key in changes: updated[key]=changes[key]
        if not str(updated.get('review_note','')).strip(): raise ValueError('请填写审核依据或更正原因')
        if updated['category'] not in CATEGORIES: raise ValueError('活动类别无效')
        if updated.get('review') not in ['auto','original','rule','custom','exclude']: raise ValueError('审核方式无效')
        for k in fields:
            if k in ['custom_score','identity_confirmed','ineligible','eligibility_confirmed','participation_confirmed']: continue
            if not isinstance(updated.get(k,''),str) or len(updated.get(k,''))>2000: raise ValueError('字段过长或类型错误')
        for k in ['identity_confirmed','ineligible','eligibility_confirmed','participation_confirmed']:
            if k in updated and type(updated[k]) is not bool: raise ValueError('确认标记必须为布尔值')
        if updated['review']=='custom':
            val=number(updated.get('custom_score'))
            if val is None or not 0<=val<=10: raise ValueError('手动分值须在 0～10 之间')
            updated['custom_score']=val
        c.execute('UPDATE records SET current=? WHERE id=?',(dumps(updated),ident))
        log(c,actor,'审核/更正记录',ident,old,updated)
        return {'ok':True}
    if path=='/api/manual':
        r=body.get('record',{}); ident='manual-'+secrets.token_hex(8)
        for k in ['student_id','name','category','event','award']:
            if not isinstance(r.get(k),str) or not r[k].strip(): raise ValueError('请填写学号、姓名、类别、项目和奖项/时长')
        if r['category'] not in CATEGORIES: raise ValueError('活动类别无效')
        val=number(r.get('original_score'))
        if val is None or not 0<=val<=10: raise ValueError('申报分值须在 0～10 之间')
        r={k:r.get(k,'') for k in ['student_id','name','college','class_name','category','event','award','level','kind']}
        r.update(id=ident,source='手工录入',sheet='手工录入',row=0,original_score=val,review='auto',review_note='',event_group='',source_note='',event_date='',import_issue='手工申报需审核')
        c.execute('INSERT INTO records VALUES(?,?,?)',(ident,dumps(r),dumps(r))); log(c,actor,'新增申报',ident,after=r)
        return {'ok':True,'id':ident}
    if path=='/api/base':
        key=str(body.get('key','')); vals=body.get('base',{})
        if key not in {s['key'] for s in state(c)['students']}: raise ValueError('学生不存在')
        base={}
        for k in ['teacher','peer','academic','sport']:
            v=number(vals.get(k))
            if v is None or not 0<=v<=100: raise ValueError('四项基础成绩均须填写 0～100 的百分制分数')
            base[k]=v
        old=c.execute('SELECT data FROM bases WHERE student_key=?',(key,)).fetchone()
        c.execute('INSERT OR REPLACE INTO bases VALUES(?,?)',(key,dumps(base))); log(c,actor,'填写基础成绩',key,json.loads(old[0]) if old else None,base)
        return {'ok':True}
    if path=='/api/archive':
        title=str(body.get('title','')).strip()
        if not title or len(title)>100: raise ValueError('请填写归档名称（最多 100 字）')
        snapshot=state(c)
        if not snapshot['students']: raise ValueError('尚无学生记录')
        if snapshot['stats']['pending']: raise ValueError(f"还有 {snapshot['stats']['pending']} 条待审核记录，请处理后归档")
        snapshot['original_records']=[json.loads(r[0]) for r in c.execute('SELECT original FROM records')]
        snapshot['full_audit']=[dict(r) for r in c.execute('SELECT * FROM audit ORDER BY id')]
        snapshot['archive_note']=str(body.get('note',''))
        snapshot['locked']=True
        encoded=dumps(snapshot); digest=hashlib.sha256(encoded.encode()).hexdigest()
        cur=c.execute('INSERT INTO archives(time,title,actor,digest,data) VALUES(?,?,?,?,?)',(now(),title,actor,digest,encoded))
        c.execute("UPDATE meta SET value='true' WHERE key='locked'")
        log(c,actor,'确认归档',str(cur.lastrowid),after={'title':title,'digest':digest})
        return {'ok':True,'id':cur.lastrowid}
    if path=='/api/revise':
        if not locked: raise ValueError('当前已是可编辑的临时库')
        reason=str(body.get('reason','')).strip()
        if len(reason)<5: raise ValueError('请具体填写更正原因（至少 5 字）')
        c.execute("UPDATE meta SET value='false' WHERE key='locked'")
        log(c,actor,'创建修订版',after={'reason':reason})
        return {'ok':True}
    raise ValueError('未知操作')

class Handler(BaseHTTPRequestHandler):
    def log_message(self,fmt,*args): pass
    def headers_ok(self):
        host=self.headers.get('Host','').split(':')[0]
        return host in ['127.0.0.1','localhost']
    def send(self,data,kind='application/json; charset=utf-8',code=200,filename=None):
        if not isinstance(data,bytes): data=(dumps(data) if kind.startswith('application/json') else str(data)).encode()
        self.send_response(code); self.send_header('Content-Type',kind); self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
        if filename: self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+quote(filename))
        self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        if not self.headers_ok(): return self.send({'error':'仅允许本机访问'},code=403)
        url=urlparse(self.path); path=url.path
        try:
            app=sys.modules[__name__]
            if path=='/api/projects':return self.send({'projects':projects.listing(app),'token':TOKEN})
            if path=='/api/college-registry':
                with projects.catalog(app) as c:return self.send(registry.get(c))
            ident=parse_qs(url.query).get('project',[self.headers.get('X-Project-Id','legacy')])[0]
            if path.startswith('/api/'):
                REQUEST_DB.set(projects.resolve(app,ident))
            if path=='/api/project-export':return self.send(projects.export(app,ident),filename='测评项目-'+ident+'.json')
            if path=='/api/college-registry':
                with projects.catalog(app) as c:return self.send(registry.get(c))
            if path=='/api/state':
                with connect() as c: data=state(c)
                data['token']=TOKEN
                data['project_id']=ident
                with projects.catalog(app) as catalog: data['project_name']=catalog.execute('SELECT name FROM projects WHERE id=?',(ident,)).fetchone()[0]
                return self.send(data)
            if path=='/api/original':
                with connect() as c: row=c.execute('SELECT original FROM records WHERE id=?',(parse_qs(url.query).get('id',[''])[0],)).fetchone()
                return self.send(json.loads(row[0]) if row else {},code=200 if row else 404)
            if path.startswith('/api/archive/'):
                ident=path.rsplit('/',1)[-1]
                with connect() as c: row=c.execute('SELECT * FROM archives WHERE id=?',(ident,)).fetchone()
                if not row: return self.send({'error':'归档不存在'},code=404)
                data=json.loads(row['data']); data['archive_meta']={k:row[k] for k in ['id','title','time','actor','digest']}
                return self.send(data)
            if path=='/api/export':
                q=parse_qs(url.query); archive=q.get('archive',[''])[0]; mode=q.get('mode',['summary'])[0]
                with connect() as c:
                    if archive:
                        row=c.execute('SELECT data FROM archives WHERE id=?',(archive,)).fetchone()
                        if not row: raise ValueError('归档不存在')
                        data=json.loads(row[0])
                    else: data=state(c)
                if mode=='json': return self.send(data,filename='测评数据备份.json')
                out=io.StringIO(); writer=csv.writer(out)
                def safe(v):
                    if isinstance(v,str) and v[:1] in ['=','+','-','@','\t','\r']: return "'"+v
                    return v
                if mode=='details':
                    fields=['student_id','name','class_name','category','event','award','level','kind','original_score','suggested','credited','status','source','sheet','row','review_note']
                    writer.writerow(['学号','姓名','班级','类别','项目','奖项/时长/职务','级别','个人/集体','原分值','规则分值','分类封顶后计入分值（总奖励另封顶10）','状态','来源文件','工作表','原行号','审核依据','计算说明'])
                    for r in data['records']: writer.writerow([safe(r.get(k,'')) for k in fields]+[safe('；'.join(r['issues']+r['notes']))])
                elif mode=='duplicates':
                    writer.writerow(['重复组','学生','学号','类别','项目','本组处理','源文件','工作表','行号','奖项/时长/职务','级别','个人/集体','原表分值','规则分值','分类封顶后计入','当前核算状态','说明'])
                    by_id={r['id']:r for r in data['records']}
                    for group in data.get('duplicate_groups',[]):
                        for ident in group['member_ids']:
                            r=by_id[ident]
                            writer.writerow([safe(v) for v in [group['id'],r['name'],r['student_id'],r['category'],r['event'],r.get('duplicate_disposition','保留参与核算' if ident==group['keeper_id'] else '不重复计入'),r['source'],r['sheet'],r['row'],r['award'],r['level'],r['kind'],r['original_score'],r['suggested'],r['credited'],r['status'],'；'.join(r['issues']+r['notes'])]])
                else:
                    writer.writerow(['学号','姓名','班级']+CATEGORIES+['分类封顶后奖励合计','奖励（封顶10）','处罚（封顶10）','附加分','待审核条数','教师评分','学生评分','智育百分制加权均分','体育百分制成绩','基础分','综合测评总分','状态'])
                    for s in data['students']:
                        writer.writerow([safe(s[k]) for k in ['student_id','name','class_name']]+[s['categories'].get(k,0) for k in CATEGORIES]+[s['reward_before_cap'],s['reward'],s['penalty'],s['net'],s['pending']]+[s['base'].get(k,'') for k in ['teacher','peer','academic','sport']]+[s['foundation'] if s['foundation'] is not None else '',s['total'] if s['total'] is not None else '', '待审核试算' if s['pending'] else '已归档' if data['locked'] else '临时库已核算'])
                return self.send(('\ufeff'+out.getvalue()).encode('utf-8'),'text/csv; charset=utf-8',filename=('历史库' if archive else '临时库')+'-'+({'details':'明细','duplicates':'重复对照'}.get(mode,'汇总'))+'.csv')
            if path=='/api/backup':
                with tempfile.TemporaryDirectory() as folder:
                    target=Path(folder)/'assessment.sqlite3'
                    with connect() as src, sqlite3.connect(target) as dst: src.backup(dst)
                    content=target.read_bytes()
                return self.send(content,'application/octet-stream',filename='综合测评-完整数据库备份.sqlite3')
            if path=='/policy.pdf':
                if POLICY.exists(): return self.send(POLICY.read_bytes(),'application/pdf')
                return self.send({'error':'请将测评办法 PDF 放回桌面原路径'},code=404)
            file=ROOT/'static'/('index.html' if path=='/' else path.lstrip('/'))
            if not file.resolve().is_relative_to((ROOT/'static').resolve()) or not file.is_file(): return self.send({'error':'页面不存在'},code=404)
            return self.send(file.read_bytes(),mimetypes.guess_type(file)[0] or 'application/octet-stream')
        except Exception as e: self.send({'error':str(e)},code=400)
        finally: REQUEST_DB.set(None)
    def do_POST(self):
        if not self.headers_ok() or self.headers.get('X-Assessment-Token')!=TOKEN: return self.send({'error':'请刷新页面后再操作'},code=403)
        try:
            size=int(self.headers.get('Content-Length',0))
            if not 0<size<=60*1024*1024: raise ValueError('请求大小超限')
            body=json.loads(self.rfile.read(size))
            if not isinstance(body,dict): raise ValueError('请求格式无效')
            app=sys.modules[__name__];route=urlparse(self.path).path
            if route in ['/api/project-create','/api/project-import','/api/project-manage']:
                actor=actor_of(body)
                operation={'/api/project-create':projects.create,'/api/project-import':projects.import_project,'/api/project-manage':projects.manage}[route]
                return self.send(operation(app,body,actor))
            if not route.startswith('/api/college-') or body.get('from_existing'):
                REQUEST_DB.set(projects.resolve(app,self.headers.get('X-Project-Id','legacy')))
            if route.startswith('/api/college-'):
                source_rows=None
                if body.get('from_existing'):
                    with connect() as project: source_rows=[json.loads(r[0]) for r in project.execute('SELECT current FROM records ORDER BY id')]
                with projects.catalog(app) as c:
                    if route=='/api/college-roster-preview':return self.send(roster_import.preview(c,body,source_rows))
                    actor=actor_of(body);c.execute('BEGIN IMMEDIATE')
                    if route=='/api/college-registry':
                        if body.get('kind')=='department':raise ValueError('院系所为固定字典，请修改 departments.json；页面不提供维护')
                        ident,before,after=registry.save(c,body);log(c,actor,'学院名册维护',ident,before,after);result={'ok':True}
                    elif route=='/api/college-registry-batch':result=registry.batch(c,body,log,actor)
                    elif route=='/api/college-roster-import':result=roster_import.commit(c,body,log,actor,source_rows)
                    else:raise ValueError('未知学院名册操作')
                return self.send(result)
            if urlparse(self.path).path=='/api/calculate-preview':
                with connect() as c: return self.send(previews.calculate_draft(c,body))
            if urlparse(self.path).path=='/api/roster-preview':
                with connect() as c: return self.send(roster_import.preview(c,body))
            if urlparse(self.path).path=='/api/import-preview':
                files=body.get('files',[])
                if not files or len(files)>20: raise ValueError('请选择 1～20 个工作簿')
                reports=[]
                with connect() as c:
                    for f in files:
                        if not f['name'].lower().endswith('.xlsx'): raise ValueError('当前支持 .xlsx 工作簿')
                        info,_=parse_workbook(base64.b64decode(f['data'],validate=True),f['name'])
                        info['skipped']=bool(c.execute('SELECT 1 FROM files WHERE hash=?',(info['hash'],)).fetchone())
                        reports.append(info)
                return self.send({'reports':reports})
            with connect() as c: result=mutate(urlparse(self.path).path,body,c)
            self.send(result)
        except Exception as e: self.send({'error':str(e)},code=400)
        finally: REQUEST_DB.set(None)

def main():
    global DB
    parser=argparse.ArgumentParser(); parser.add_argument('--port',type=int,default=8765); parser.add_argument('--no-seed',action='store_true'); parser.add_argument('--db',type=Path); args=parser.parse_args()
    if args.db: DB=args.db.resolve()
    initialize()
    with connect() as c:
        registry.seed_dictionary(c)
        if not args.no_seed and not c.execute('SELECT 1 FROM files LIMIT 1').fetchone() and DEFAULT_SOURCES.exists():
            for p in sorted(DEFAULT_SOURCES.rglob('*.xlsx')):
                if p.name.startswith('~$'): continue
                try: import_files(c,[(p.name,p.read_bytes())],'首次载入')
                except ValueError as e: print(f'自动载入跳过 {p.name}：{e}。请通过网页导入并指定类别。',flush=True)
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print(f'综合测评工作台：http://127.0.0.1:{args.port}',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__=='__main__': main()
