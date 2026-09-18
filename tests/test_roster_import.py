import base64
import io
import unittest
import openpyxl
import app
import registry
import roster_import
import test_workflow as workflow
from test_workflow import workbook
from test_scoring import record

class RosterImportTests(unittest.TestCase):
    tearDown=workflow.WorkflowTests.tearDown
    run_action=workflow.WorkflowTests.run_action
    insert=workflow.WorkflowTests.insert
    def setUp(self):
        workflow.WorkflowTests.setUp(self)
        with app.connect() as c:registry.seed_dictionary(c)
    def source(self,rows=None):
        if rows is None:raw=workbook()
        else:
            w=openpyxl.Workbook();w.active.append(['院系所代码','班级名称','学生学号','学生姓名'])
            for r in rows:w.active.append(r)
            out=io.BytesIO();w.save(out);raw=out.getvalue()
        return {'files':[{'name':'名册.xlsx','data':base64.b64encode(raw).decode()}]}
    def preview(self,source):
        with app.connect() as c:return roster_import.preview(c,source)
    def test_upload_aliases_code_duplicates_and_sources(self):
        source=self.source([['302','一班','202411680001','甲'],['302','一班','202411680001','甲']])
        report=self.preview(source);self.assertEqual(report['ready'],1);self.assertEqual(len(report['items'][0]['sources']),2)
        self.run_action('/api/roster-import',**source,fingerprint=report['fingerprint'],selected=['202411680001'])
        self.assertEqual(self.preview(source)['items'][0]['status'],'existing')
        with app.connect() as c:
            self.assertEqual(registry.get(c)['students'][0]['college'],'文学院')
            self.assertEqual(c.execute('SELECT COUNT(*) FROM records').fetchone()[0],0)
    def test_additional_score_sheet_requires_no_category(self):
        report=self.preview(self.source());self.assertEqual(report['ready'],1)
    def test_conflicts_missing_and_unknown_department(self):
        source=self.source([['302','一班','202411680001','甲'],['302','二班','202411680001','甲'],['404','一班','202411680002','乙'],['302','', '202411680003','丙'],['302','一班','','丁']])
        report=self.preview(source);self.assertEqual(report['ready'],0);self.assertEqual(report['items'][0]['status'],'conflict')
        with self.assertRaisesRegex(ValueError,'可新增'):self.run_action('/api/roster-import',**source,fingerprint=report['fingerprint'],selected=['202411680001'])
    def test_existing_records_preview_stale_and_archive_lock(self):
        self.insert(record());source={'from_existing':True};report=self.preview(source)
        self.assertEqual(report['ready'],1)
        self.run_action('/api/registry',kind='class',name='新增班级',department_id=next(d['id'] for d in self.get_master()['departments'] if d['name']=='文学院'))
        self.run_action('/api/roster-import',**source,fingerprint=report['fingerprint'],selected=[report['items'][0]['key']])
        with self.assertRaisesRegex(ValueError,'重新预览'):self.run_action('/api/roster-import',**source,fingerprint=report['fingerprint'],selected=[report['items'][0]['key']])
        self.run_action('/api/archive',title='测试')
        with self.assertRaisesRegex(ValueError,'归档锁定'):self.run_action('/api/roster-import',**source,fingerprint=report['fingerprint'],selected=[])
    def get_master(self):
        with app.connect() as c:return registry.get(c)
