import unittest
import json
import app
import projects
import registry
import test_workflow as workflow
from test_scoring import record

class ProjectTests(unittest.TestCase):
    setUp=workflow.WorkflowTests.setUp
    tearDown=workflow.WorkflowTests.tearDown
    insert=workflow.WorkflowTests.insert
    run_action=workflow.WorkflowTests.run_action
    def new(self,**kwargs):return projects.create(app,dict(name='第二次测评',year='2026-2027',college='文学院',**kwargs),'测试')['id']
    def test_migration_creation_isolation_and_recycle(self):
        self.insert(record());items=projects.listing(app);self.assertEqual(items[0]['id'],'legacy')
        ident=self.new()
        with app.connect(projects.resolve(app,ident)) as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM records').fetchone()[0],0)
        with app.connect(app.DB) as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM records').fetchone()[0],1)
        projects.manage(app,{'id':ident,'action':'delete'},'测试')
        with self.assertRaises(ValueError):projects.resolve(app,ident)
        projects.manage(app,{'id':ident,'action':'restore'},'测试');self.assertTrue(projects.resolve(app,ident).exists())
    def test_package_roundtrip_preserves_archive_and_original(self):
        self.insert(record());self.run_action('/api/archive',title='第一版')
        pack=projects.export(app,'legacy');ident=projects.import_project(app,{'package':pack},'测试')['id']
        with app.connect(projects.resolve(app,ident)) as c:
            self.assertTrue(app.state(c)['locked'])
            self.assertEqual(app.state(c)['students'][0]['net'],2)
            self.assertEqual(json.loads(c.execute('SELECT original FROM records').fetchone()[0])['original_score'],2)
            self.assertEqual([dict(r) for r in c.execute('SELECT * FROM archives')],pack['tables']['archives'])
        pack['name']='tampered'
        with self.assertRaisesRegex(ValueError,'校验失败'):projects.import_project(app,{'package':pack},'测试')
    def test_shared_roster_copied_once_and_batch_rollback(self):
        with projects.catalog(app) as c:
            dep=c.execute("SELECT id FROM departments WHERE name='文学院'").fetchone()[0]
            registry.save(c,dict(kind='class',name='一班',department_id=dep))
            registry.save(c,dict(kind='class',name='二班',department_id=dep))
            registry.save(c,dict(kind='student',name='学生甲',student_id='202411680001',class_id=1))
        ident=self.new()
        with projects.catalog(app) as c:
            c.execute('BEGIN IMMEDIATE');registry.batch(c,dict(kind='student',ids=[1],operation='move',class_id=2),app.log,'测试')
        with app.connect(projects.resolve(app,ident)) as c:self.assertEqual(registry.get(c)['students'][0]['class_name'],'一班')
        with self.assertRaises(ValueError):
            with projects.catalog(app) as c:
                c.execute('BEGIN IMMEDIATE');registry.batch(c,dict(kind='class',ids=[1,2],operation='disable'),app.log,'测试')
        with projects.catalog(app) as c:self.assertEqual(c.execute('SELECT active FROM classes WHERE id=1').fetchone()[0],1)
    def test_request_scope_does_not_change_catalog_legacy(self):
        ident=self.new();scope=app.REQUEST_DB.set(projects.resolve(app,ident))
        try:
            with app.connect() as c:self.assertEqual(c.execute("SELECT value FROM meta WHERE key='year'").fetchone()[0],'2026-2027')
            self.assertEqual(next(p for p in projects.listing(app) if p['id']=='legacy')['year'],'2025-2026')
        finally:app.REQUEST_DB.reset(scope)
