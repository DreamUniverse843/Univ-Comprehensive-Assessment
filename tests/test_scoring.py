import unittest
from scoring import calculate, infer

def record(n=1,**kwargs):
    value=dict(id=str(n),source='测试表.xlsx',sheet='测试',row=n,student_id='202411680001',name='测试甲',college='文学院',class_name='24中文1',category='专业学术',event='比赛'+str(n),award='一等奖',level='校级',kind='个人',original_score=2,review='auto',review_note='')
    value.update(kwargs);return value

class ScoringTests(unittest.TestCase):
    def summary(self,rows,**kwargs):return calculate(rows,**kwargs)['students'][0]
    def test_academic_cap(self):self.assertEqual(self.summary([record(i) for i in range(3)])['reward'],4)
    def test_team_halved(self):self.assertEqual(infer(record(kind='集体'))[0],1)
    def test_same_event_best_not_sum(self):
        r=[record(1,event='竞赛'),record(2,event='竞赛',level='国家级',original_score=4)]
        self.assertEqual(self.summary(r)['net'],4)
        self.assertEqual(calculate(r)['records'][0]['credited'],0)
    def test_distinct_project_group(self):
        r=[record(1,event='校选赛',event_group='统一项目'),record(2,event='国赛',event_group='统一项目',level='国家级',original_score=4)]
        self.assertEqual(self.summary(r)['net'],4)
    def test_cadre_highest_only(self):
        r=[record(1,category='学生干部',award='部员',original_score=1),record(2,category='学生干部',award='部长',level='院级',original_score=1.3)]
        self.assertEqual(self.summary(r)['net'],1.3)
    def test_cadre_above_role_limit_pending(self):
        r=record(category='学生干部',award='部员',original_score=2)
        self.assertEqual(self.summary([r])['pending'],1)
    def test_volunteer_200_hour_cap(self):
        rows=[record(i,category='志愿服务',award='125.5h',original_score=1.255) for i in range(2)]
        self.assertEqual(self.summary(rows)['net'],2)
    def test_fractional_hours_preserved(self):
        self.assertEqual(infer(record(category='志愿服务',award='20.5',original_score=.205))[0],.205)
    def test_volunteer_aggregate_overlap(self):
        rows=[record(1,category='志愿服务',event='2025-2026志愿服务活动',award='60',original_score=.6),record(2,category='志愿服务',event='单项志愿活动',award='4h',original_score=.04)]
        self.assertEqual(self.summary(rows)['pending'],2)
        self.assertEqual(self.summary(rows)['net'],0)
    def test_performance_two_best_across_host_and_actor(self):
        rows=[record(i,category='文体科技实践活动',award=a,level='校级',original_score=.2) for i,a in enumerate(['主持人','演员','表演'])]
        self.assertEqual(self.summary(rows)['net'],.4)
    def test_host_academic_reclassified(self):
        result=infer(record(award='主持人',original_score=.2))
        self.assertEqual(result[0],.2);self.assertEqual(result[1],'文体科技实践活动')
    def test_honors_collective(self):
        self.assertEqual(infer(record(category='荣誉表彰',award='优秀宿舍',kind='集体'))[0],.4)
    def test_practice_per_member_and_holiday(self):
        rows=[record(1,category='社会实践',event='暑期实践甲',award='参与',original_score=.4,kind='集体'),record(2,category='社会实践',event='暑期实践乙',award='校级优秀团队',original_score=.6,kind='集体'),record(3,category='社会实践',event='寒假实践',award='千人千校',original_score=.3)]
        self.assertEqual(self.summary(rows)['net'],.9)
    def test_separate_reward_penalty_cap(self):
        rows=[record(i,category='其他',original_score=4,review='original',review_note='审批') for i in range(4)]
        rows += [record(i+5,category='处罚分',award='记过',original_score=6) for i in range(2)]
        s=self.summary(rows);self.assertEqual((s['reward'],s['penalty'],s['net']),(10,10,0))
    def test_unknown_and_mismatch_stay_pending(self):
        self.assertEqual(self.summary([record(original_score=.8)])['pending'],1)
        self.assertEqual(self.summary([record(award='第五名')])['pending'],1)
    def test_review_original_and_custom(self):
        self.assertEqual(self.summary([record(original_score=.8,review='original')])['net'],.8)
        self.assertEqual(self.summary([record(review='custom',custom_score=.3)])['net'],.3)
    def test_duplicate_import(self):
        rows=[record(1,event='相同项目'),record(2,event='相同项目',source='另一个文件.xlsx')]
        self.assertEqual(sum(r['status']=='duplicate' for r in calculate(rows)['records']),1)
    def test_duplicate_group_includes_kept_and_all_sources(self):
        rows=[record(1,event='相同项目',source='校级汇总.xlsx'),record(2,event='相同项目',source='院级汇总.xlsx'),record(3,event='相同项目',source='补表.xlsx')]
        result=calculate(rows);group=result['duplicate_groups'][0]
        self.assertEqual(group['count'],3);self.assertEqual(group['file_count'],3)
        self.assertEqual(set(group['member_ids']),{'1','2','3'})
        self.assertIn(group['keeper_id'],group['member_ids'])
        self.assertEqual(sum(r['duplicate_kept'] for r in result['records']),1)
        self.assertTrue(all(r['duplicate_group']==group['id'] for r in result['records']))
        self.assertEqual(result['students'][0]['net'],2)
    def test_same_event_missing_date_still_compared(self):
        rows=[record(1,event='同一赛事',event_date='2026年4月',source='单独表.xlsx'),record(2,event='同一赛事',event_date='',source='总表.xlsx')]
        result=calculate(rows);group=result['duplicate_groups'][0]
        self.assertEqual(group['match_type'],'同项目取最高')
        self.assertEqual(group['file_count'],2)
        self.assertEqual({r['duplicate_disposition'] for r in result['records']},{'保留参与核算','同项目不叠加'})
        self.assertEqual(result['students'][0]['net'],2)
    def test_distinct_date_not_duplicate(self):
        rows=[record(1,category='志愿服务',event='重复开展活动',event_date='2026-1-1',award='1h',original_score=.01),record(2,category='志愿服务',event='重复开展活动',event_date='2026-2-1',award='1h',original_score=.01)]
        self.assertEqual(self.summary(rows)['net'],.02)
    def test_identity_conflicts_require_confirmation(self):
        rows=[record(1,name='甲'),record(2,name='乙')]
        self.assertEqual(sum(r['status']=='pending' for r in calculate(rows)['records']),2)
    def test_missing_identity_cannot_be_bypassed(self):
        r=record(name='',review='original',identity_confirmed=True)
        self.assertEqual(calculate([r])['records'][0]['status'],'pending')
    def test_missing_college_not_silently_outside(self):
        r=record(college='',review='original',identity_confirmed=True)
        self.assertEqual(calculate([r])['records'][0]['status'],'pending')
    def test_grad_and_custom_college_scope(self):
        rows=[record(1,college='任意学院'),record(2,college='任意学院',student_id='202521100001')]
        self.assertEqual(len(calculate(rows,college='任意学院')['students']),1)
        self.assertEqual(len(calculate(rows,college='文学院')['students']),0)
    def test_zero_not_missing_and_weighted_total(self):
        base={'202411680001|测试甲':{'teacher':100,'peer':100,'academic':100,'sport':100}}
        self.assertEqual(self.summary([record()],bases=base)['total'],102)
        base['202411680001|测试甲']['teacher']=0
        self.assertEqual(self.summary([record()],bases=base)['total'],92)
        del base['202411680001|测试甲']['peer']
        self.assertIsNone(self.summary([record()],bases=base)['total'])
    def test_roster_unifies_class_without_changing_source(self):
        roster=[dict(student_id='202411680001',name='测试甲',college='自定义院系',class_name='标准班级',active=1)]
        s=calculate([record()],college='自定义院系',roster=roster)
        self.assertEqual(s['students'][0]['class_name'],'标准班级')
        self.assertEqual(s['records'][0]['source_college'],'文学院')
    def test_roster_student_without_awards(self):
        roster=[dict(student_id='202411680001',name='测试甲',college='文学院',class_name='班级',active=1)]
        self.assertEqual(self.summary([],roster=roster)['net'],0)

if __name__=='__main__':unittest.main()


class ParticipationTests(unittest.TestCase):
    def rows(self,award,category='文体科技实践活动',review='auto'):
        score=.2 if category=='文体科技实践活动' else .4
        return [record(i,award=award,category=category,original_score=score,review=review,custom_score=score) for i in range(1,4)]
    def test_awards_do_not_use_two_participation_slots(self):
        for category in ['文体科技实践活动','专业学术']:
            for review in ['auto','original','rule','custom']:
                with self.subTest(category=category,review=review):
                    result=calculate(self.rows('参与奖',category,review))
                    self.assertEqual(sum(r['credited']>0 for r in result['records']),3)
                    self.assertFalse(any('最高两次' in note for r in result['records'] for note in r['notes']))
    def test_plain_participation_only_two_and_award_separate(self):
        for category in ['文体科技实践活动','专业学术']:
            rows=self.rows('参与',category)
            rows.append(record(4,award='参与奖',category=category,original_score=rows[0]['original_score']))
            result=calculate(rows)
            self.assertEqual(sum(r['credited']>0 for r in result['records'][:3]),2)
            self.assertGreater(result['records'][3]['credited'],0)
    def test_same_event_award_and_participation_do_not_stack(self):
        rows=self.rows('参与');rows[0]['event']='同一比赛'
        rows.append(record(4,award='参与奖',event='同一比赛',category='文体科技实践活动',original_score=.2))
        result=calculate(rows)
        self.assertEqual(sum(r['credited'] for r in result['records'] if r['event']=='同一比赛'),.2)
        self.assertEqual(result['students'][0]['net'],.6)
    def test_award_with_performance_description_not_role(self):
        r=record(award='表演参与奖',category='文体科技实践活动',original_score=.2)
        self.assertEqual(infer(r)[4],'')
    def test_manual_plain_and_award_eligibility(self):
        self.assertNotIn('参与奖需确认赛制与入围条件',infer(record(award='参与',source='手工录入'))[-1])
        self.assertIn('参与奖需确认赛制与入围条件',infer(record(award='参与奖',source='手工录入'))[-1])
