import unittest
import json
import app
import previews
import test_workflow as workflow
from test_scoring import record

class PreviewTests(unittest.TestCase):
    setUp=workflow.WorkflowTests.setUp
    tearDown=workflow.WorkflowTests.tearDown
    insert=workflow.WorkflowTests.insert
    run_action=workflow.WorkflowTests.run_action
    def test_category_kind_changes_and_no_writes(self):
        self.insert(record())
        with app.connect() as c:
            before=list(c.execute('SELECT original,current FROM records'))[0]
            result=previews.calculate_draft(c,{'id':'1','changes':{'category':'专业学术','kind':'集体','review':'rule'}})
            self.assertEqual(result['record']['suggested'],1)
            result=previews.calculate_draft(c,{'id':'1','changes':{'category':'志愿服务','award':'100小时','review':'rule'}})
            self.assertEqual(result['record']['suggested'],1)
            self.assertEqual(tuple(c.execute('SELECT original,current FROM records').fetchone()),tuple(before))
            self.assertEqual(c.execute('SELECT COUNT(*) FROM audit').fetchone()[0],0)
    def test_custom_invalid_and_base_incomplete(self):
        self.insert(record())
        with app.connect() as c:
            key=app.state(c)['students'][0]['key']
            result=previews.calculate_draft(c,{'mode':'base','key':key,'base':{'teacher':80,'peer':80,'academic':80,'sport':80}})
            self.assertEqual(result['student']['foundation'],80)
            result=previews.calculate_draft(c,{'mode':'base','key':key,'base':{'teacher':80}})
            self.assertIsNone(result['student']['foundation'])
            self.assertEqual(c.execute('SELECT COUNT(*) FROM bases').fetchone()[0],0)
            with self.assertRaises(ValueError):previews.calculate_draft(c,{'id':'1','changes':{'review':'custom','custom_score':99}})
    def test_manual_and_duplicate_context(self):
        self.insert(record())
        with app.connect() as c:
            result=previews.calculate_draft(c,{'mode':'manual','changes':{'student_id':'202411680002','name':'新生','college':'文学院','class_name':'一班','category':'无偿献血','award':'献血','event':'献血','original_score':.2}})
            self.assertEqual(result['record']['suggested'],.2)
            self.assertEqual(result['record']['status'],'pending')
            self.assertEqual(c.execute('SELECT COUNT(*) FROM records').fetchone()[0],1)
