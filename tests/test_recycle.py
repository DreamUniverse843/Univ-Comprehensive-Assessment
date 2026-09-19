import io
import unittest
from unittest.mock import patch
from pathlib import Path
import openpyxl
import app, registry, projects, project_cleanup, roster_import
from importer import parse_workbook
import test_workflow as workflow

class RecycleTests(unittest.TestCase):
    setUp=workflow.WorkflowTests.setUp
    tearDown=workflow.WorkflowTests.tearDown
    run_action=workflow.WorkflowTests.run_action
    def setup_roster(self):
        with app.connect() as c:
            registry.seed_dictionary(c)
            dep=c.execute("SELECT id FROM departments WHERE name='文学院'").fetchone()[0]
            cls=int(registry.save(c,dict(kind='class',name='一班',aliases='甲班，A班',department_id=dep))[0])
            sid=int(registry.save(c,dict(kind='student',name='甲',student_id='202411680001',class_id=cls))[0])
        return cls,sid
    def test_delete_restore_parent_and_purge_guards(self):
        cls,sid=self.setup_roster()
        with self.assertRaises(ValueError):self.run_action('/api/registry-batch',kind='class',ids=[cls],operation='delete')
        self.run_action('/api/registry-batch',kind='student',ids=[sid],operation='delete')
        self.run_action('/api/registry-batch',kind='class',ids=[cls],operation='delete')
        with app.connect() as c:
            self.assertEqual(registry.get(c)['students'],[])
            self.assertEqual(len(registry.get_recycle(c)['students']),1)
        with self.assertRaises(ValueError):self.run_action('/api/registry-restore',kind='student',ids=[sid])
        with self.assertRaises(ValueError):self.run_action('/api/registry-purge',kind='class',ids=[cls])
        self.run_action('/api/registry-restore',kind='class',ids=[cls])
        self.run_action('/api/registry-restore',kind='student',ids=[sid])
        self.run_action('/api/registry-batch',kind='student',ids=[sid],operation='delete')
        self.run_action('/api/registry-purge',kind='student',ids=[sid])
        with app.connect() as c:self.assertEqual(registry.get_recycle(c)['students'],[])
    def test_batch_rollback(self):
        cls,sid=self.setup_roster()
        with self.assertRaises(ValueError):self.run_action('/api/registry-batch',kind='student',ids=[sid,999],operation='delete')
        with app.connect() as c:self.assertEqual(len(registry.get(c)['students']),1)
    def test_alias_scope_and_source_preserved(self):
        cls,sid=self.setup_roster()
        with app.connect() as c:
            rows=[dict(name='乙',student_id='202411680002',college='文学院',class_name='甲班')]
            report=roster_import.preview(c,{'from_existing':True},rows)
            self.assertEqual(report['items'][0]['class_name'],'一班')
            self.assertEqual(registry.normalize_records(c,rows)[0]['source_class_name'],'甲班')
            self.assertEqual(rows[0]['class_name'],'甲班')
            dep=c.execute('SELECT department_id FROM classes WHERE id=?',(cls,)).fetchone()[0]
            with self.assertRaises(ValueError):registry.save(c,dict(kind='class',name='二班',aliases='甲班',department_id=dep))
            self.assertEqual(registry.canonical_class(c,'别的学院','甲班'),'甲班')
    def test_project_purge_failure_retains_entry_and_cleanup(self):
        ident=projects.create(app,dict(name='测试项目',college='文学院',year='2026-2027'),'测试')['id']
        projects.manage(app,dict(id=ident,action='delete'),'测试')
        with patch.object(Path,'unlink',side_effect=PermissionError('blocked')):
            with self.assertRaises(PermissionError):projects.manage(app,dict(id=ident,action='purge',confirm='测试项目'),'测试')
        self.assertTrue(projects.path_for(app,ident).exists())
        with projects.catalog(app) as c:
            self.assertIsNotNone(c.execute('SELECT * FROM projects WHERE id=?',(ident,)).fetchone())
            c.execute("UPDATE projects SET deleted_at='2000-01-01T00:00:00+08:00' WHERE id=?",(ident,))
        self.assertEqual(project_cleanup.cleanup_old_projects(app),1)
        self.assertFalse(projects.path_for(app,ident).exists())
    def test_migration_old_schema(self):
        with app.connect() as c:
            c.execute('ALTER TABLE classes DROP COLUMN deleted')
            c.execute('ALTER TABLE roster DROP COLUMN deleted')
        app.initialize()
        with app.connect() as c:self.assertEqual(registry.get(c)['students'],[])
    def test_summary_rows_unknown_and_manual_override_and_skip_ids(self):
        w=openpyxl.Workbook();s=w.active;s.title='汇总'
        s.append(['学号','姓名','类别','分值']);s.append(['202411680001','甲','志愿服务',1]);s.append(['202411680002','乙','不认识',2])
        t=w.create_sheet('其他');t.append(['学号','姓名','分值']);t.append(['202411680003','丙',1])
        out=io.BytesIO();w.save(out);raw=out.getvalue()
        report,rows=parse_workbook(raw,'汇总.xlsx');key=report['sheets'][0]['key']
        self.assertFalse(report['sheets'][0]['needs_mapping'])
        self.assertIn('未识别活动性质',rows[1]['import_issue'])
        _,mapped=parse_workbook(raw,'汇总.xlsx',{key:'社会实践'})
        self.assertEqual(mapped[1]['category'],'社会实践')
        _,skipped=parse_workbook(raw,'汇总.xlsx',{key:'__SKIP__'})
        self.assertEqual(skipped[0]['id'],rows[2]['id'])

    def test_older_package_defaults_and_alias_copy(self):
        import hashlib
        self.setup_roster()
        pack=projects.export(app,'legacy')
        for row in pack['tables']['classes']:row.pop('deleted');row.pop('aliases')
        for row in pack['tables']['roster']:row.pop('deleted')
        pack['digest']=hashlib.sha256(app.dumps({k:v for k,v in pack.items() if k!='digest'}).encode()).hexdigest()
        ident=projects.import_project(app,{'package':pack},'测试')['id']
        with app.connect(projects.resolve(app,ident)) as c:self.assertEqual(len(registry.get(c)['students']),1)
        new=projects.create(app,dict(name='别名复制',college='文学院',year='2026-2027'),'测试')['id']
        with app.connect(projects.resolve(app,new)) as c:self.assertEqual(registry.canonical_class(c,'文学院','甲班'),'一班')

    def test_locked_recycle_mutation_rejected(self):
        cls,sid=self.setup_roster()
        with app.connect() as c:c.execute("UPDATE meta SET value='true' WHERE key='locked'")
        with self.assertRaises(ValueError):self.run_action('/api/registry-batch',kind='student',ids=[sid],operation='delete')
