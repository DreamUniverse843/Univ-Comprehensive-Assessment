import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import openpyxl
import app
import registry
from importer import parse_workbook
from test_scoring import record

def workbook(title='陌生工作表',meta='',sheet2=False):
    w=openpyxl.Workbook();s=w.active;s.title=title
    if meta:s.append([meta])
    s.append(['序号','院系','班级','学号','姓名','奖项/服务时长/职务','级别','个人/集体','分值','备注'])
    s.append([1,'文学院','24汉1','202411680001','测试甲','一等奖','校级','个人',2,'测试竞赛'])
    s.append([2])
    if sheet2:
        t=w.create_sheet('填写说明');t.append(['学号','姓名']);t.append(['202xxxxxxxxx','张三'])
    b=io.BytesIO();w.save(b);return b.getvalue()

class ImportTests(unittest.TestCase):
    def test_metadata_precedes_sheet_name(self):
        info,rows=parse_workbook(workbook('其他','活动性质：专业学术[ √ ];文体科技实践活动[ ]',True),'a.xlsx')
        self.assertEqual(rows[0]['category'],'专业学术');self.assertEqual(info['count'],1)
        self.assertEqual(rows[0]['row'],3);self.assertTrue(info['warnings'])
    def test_alias_match(self):
        info,rows=parse_workbook(workbook('文体科技实践'),'a.xlsx')
        self.assertEqual(rows[0]['category'],'文体科技实践活动');self.assertFalse(info['sheets'][0]['needs_mapping'])
    def test_unknown_mapping_required(self):
        raw=workbook();info,_=parse_workbook(raw,'a.xlsx')
        self.assertTrue(info['sheets'][0]['needs_mapping'])
        mapped,rows=parse_workbook(raw,'a.xlsx',{info['sheets'][0]['key']:'专业学术'})
        self.assertFalse(mapped['sheets'][0]['needs_mapping']);self.assertEqual(rows[0]['category'],'专业学术')
    def test_ambiguous_metadata_needs_mapping(self):
        info,_=parse_workbook(workbook('专业学术','活动性质：专业学术[√]；志愿服务[√]'),'a.xlsx')
        self.assertTrue(info['sheets'][0]['needs_mapping'])
    def test_invalid_workbook(self):
        with self.assertRaises(ValueError):parse_workbook(b'not xlsx','bad.xlsx')

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.patch=patch.object(app,'DB',Path(self.temp.name)/'test.sqlite3');self.patch.start();app.initialize()
    def tearDown(self):self.patch.stop();self.temp.cleanup()
    def run_action(self,path,**body):
        with app.connect() as c:return app.mutate(path,{'actor':'测试审核人',**body},c)
    def insert(self,r):
        with app.connect() as c:c.execute('INSERT INTO records VALUES(?,?,?)',(r['id'],app.dumps(r),app.dumps(r)))
    def test_year_validation_and_frozen_archive(self):
        self.run_action('/api/settings',college='文学院',year='2026-2027')
        with self.assertRaisesRegex(ValueError,'连续'):self.run_action('/api/settings',college='文学院',year='2026-2028')
        self.insert(record());self.run_action('/api/archive',title='学年测试')
        with self.assertRaisesRegex(ValueError,'归档锁定'):self.run_action('/api/settings',college='文学院',year='2027-2028')
        with app.connect() as c:self.assertEqual(app.state(c)['year'],'2026-2027')
    def test_department_dictionary_and_codes(self):
        with app.connect() as c:
            c.execute("INSERT INTO departments(name) VALUES('文学院')")
            app.registry.seed_dictionary(c);app.registry.seed_dictionary(c)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM departments').fetchone()[0],85)
            row=c.execute("SELECT * FROM departments WHERE name='文学院'").fetchone()
            self.assertEqual((row['id'],row['code']),(1,'302'))
        for body in [dict(name='新增单位',code='001'),dict(id=1,name='修改名称',active=False)]:
            with self.assertRaisesRegex(ValueError,'固定字典'):self.run_action('/api/registry',kind='department',**body)
    def test_department_numeric_order(self):
        with app.connect() as c:
            app.registry.seed_dictionary(c)
            c.execute("INSERT INTO departments(name,code) VALUES('旧自定义单位','001')")
            rows=app.registry.get(c)['departments']
            self.assertEqual(len(rows),85)
            self.assertEqual([int(d['code']) for d in rows],sorted(int(d['code']) for d in rows))
            self.assertFalse(any(d['name']=='旧自定义单位' for d in rows))
    def test_unmapped_never_imported(self):
        with self.assertRaises(ValueError):
            with app.connect() as c:app.import_files(c,[('test.xlsx',workbook())],'测试')
        with app.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM records').fetchone()[0],0)
    def test_duplicate_files_skipped(self):
        raw=workbook('专业学术')
        with app.connect() as c:
            app.import_files(c,[('test.xlsx',raw)],'测试')
            reports=app.import_files(c,[('renamed.xlsx',raw)],'测试')
            self.assertTrue(reports[0]['skipped']);self.assertEqual(c.execute('SELECT COUNT(*) FROM records').fetchone()[0],1)
    def test_archive_pending_rejected(self):
        self.insert(record(original_score=.8))
        with self.assertRaisesRegex(ValueError,'待审核'):self.run_action('/api/archive',title='第一版')
    def test_archive_locked_and_revision_preserves_snapshot(self):
        self.insert(record())
        archive=self.run_action('/api/archive',title='第一版')
        with self.assertRaisesRegex(ValueError,'归档锁定'):self.run_action('/api/settings',college='任意学院')
        with app.connect() as c:
            before=c.execute('SELECT data FROM archives').fetchone()[0]
            with self.assertRaises(sqlite3.IntegrityError):c.execute("UPDATE archives SET title='改名'")
            with self.assertRaises(sqlite3.IntegrityError):c.execute('DELETE FROM archives')
        self.run_action('/api/revise',reason='测试归档更正流程')
        self.run_action('/api/record',id='1',changes={'review':'custom','custom_score':1,'review_note':'更正原审批分值'})
        with app.connect() as c:
            self.assertEqual(c.execute('SELECT data FROM archives WHERE id=?',(archive['id'],)).fetchone()[0],before)
            self.assertEqual(app.state(c)['students'][0]['net'],1)
            self.assertEqual(json.loads(c.execute('SELECT original FROM records').fetchone()[0])['original_score'],2)
    def test_audit_immutable(self):
        self.run_action('/api/settings',college='自定义院系')
        with app.connect() as c:
            with self.assertRaises(sqlite3.IntegrityError):c.execute('DELETE FROM audit')
    def test_locked_workspace_uses_snapshot_not_new_rules(self):
        self.insert(record());self.run_action('/api/archive',title='原版规则结果')
        with patch.object(app,'calculate',side_effect=AssertionError('历史不得重算')):
            with app.connect() as c:self.assertEqual(app.state(c)['students'][0]['net'],2)
    def test_registry_hierarchy_and_unique_id(self):
        with app.connect() as c:c.execute("INSERT INTO departments(name) VALUES('文学院')")
        self.run_action('/api/registry',kind='class',name='一班',department_id=1)
        self.run_action('/api/registry',kind='student',name='学生甲',student_id='202411680001',class_id=1)
        with self.assertRaisesRegex(ValueError,'已存在'):self.run_action('/api/registry',kind='student',name='学生乙',student_id='202411680001',class_id=1)
        with self.assertRaisesRegex(ValueError,'停用'):self.run_action('/api/registry',kind='class',id=1,name='一班',department_id=1,active=False)
        self.run_action('/api/settings',college='文学院')
        with app.connect() as c:self.assertEqual(app.state(c)['students'][0]['name'],'学生甲')
    def test_base_rejects_missing_and_out_of_range(self):
        self.insert(record())
        with self.assertRaises(ValueError):self.run_action('/api/base',key='202411680001|测试甲',base={'teacher':120})
    def test_manual_then_review(self):
        result=self.run_action('/api/manual',record={k:v for k,v in record().items() if k in ['student_id','name','college','class_name','category','event','award','level','kind','original_score']})
        with app.connect() as c:self.assertEqual(app.state(c)['stats']['pending'],1)
        self.run_action('/api/record',id=result['id'],changes={'review':'rule','review_note':'已核对证书'})
        with app.connect() as c:self.assertEqual(app.state(c)['stats']['pending'],0)
    def test_backup_download_is_complete_sqlite(self):
        self.insert(record());self.run_action('/api/archive',title='备份验证')
        handler=app.Handler.__new__(app.Handler);handler.path='/api/backup';handler.headers={'Host':'127.0.0.1:8765'}
        handler.wfile=io.BytesIO();handler.send_response=lambda status:setattr(handler,'status',status)
        handler.send_header=lambda *args:None;handler.end_headers=lambda:None
        handler.do_GET();self.assertEqual(handler.status,200)
        backup=Path(self.temp.name)/'restored.sqlite3';backup.write_bytes(handler.wfile.getvalue())
        with sqlite3.connect(backup) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM archives').fetchone()[0],1)
            self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
    def test_duplicate_export_contains_kept_source(self):
        self.insert(record(1,event='相同项目',source='保留来源.xlsx'))
        self.insert(record(2,event='相同项目',source='重复来源.xlsx'))
        handler=app.Handler.__new__(app.Handler);handler.path='/api/export?mode=duplicates';handler.headers={'Host':'localhost:8765'}
        handler.wfile=io.BytesIO();handler.send_response=lambda status:setattr(handler,'status',status)
        handler.send_header=lambda *args:None;handler.end_headers=lambda:None
        handler.do_GET();out=handler.wfile.getvalue().decode('utf-8-sig')
        self.assertEqual(handler.status,200)
        for text in ['保留来源.xlsx','重复来源.xlsx','保留参与核算','不重复计入']:self.assertIn(text,out)
    def test_adopt_one_then_void_others_keeps_history(self):
        self.insert(record(1,event='同项目',source='甲表.xlsx'))
        self.insert(record(2,event='同项目',source='乙表.xlsx'))
        self.run_action('/api/record',id='2',changes={'review':'rule','review_note':'已核验证明，采纳乙表'})
        with app.connect() as c:group=app.state(c)['duplicate_groups'][0]
        result=self.run_action('/api/duplicate-resolve',group_id=group['id'],keep_id='2',member_ids=group['member_ids'],reason='同一申报跨文件重复，采纳乙表')
        self.assertEqual(result['voided'],1)
        with app.connect() as c:
            current=app.state(c);self.assertEqual(current['students'][0]['net'],2)
            by_id={r['id']:r for r in current['records']}
            self.assertEqual(by_id['1']['status'],'excluded');self.assertEqual(by_id['2']['status'],'confirmed')
            self.assertTrue(current['duplicate_groups'][0]['resolved'])
            self.assertEqual(current['duplicate_groups'][0]['keeper_id'],'2')
            self.assertEqual(len(current['duplicate_groups'][0]['member_ids']),2)
            self.assertEqual(json.loads(c.execute("SELECT original FROM records WHERE id='1'").fetchone()[0])['original_score'],2)
    def test_group_resolution_requires_adoption_and_current_members(self):
        self.insert(record(1,event='同项目'));self.insert(record(2,event='同项目'))
        with app.connect() as c:g=app.state(c)['duplicate_groups'][0]
        with self.assertRaisesRegex(ValueError,'先审核'):
            self.run_action('/api/duplicate-resolve',group_id=g['id'],keep_id='1',member_ids=g['member_ids'],reason='整组核实')
        with self.assertRaisesRegex(ValueError,'发生变化'):
            self.run_action('/api/duplicate-resolve',group_id=g['id'],keep_id='1',member_ids=['1'],reason='整组核实')

if __name__=='__main__':unittest.main()
