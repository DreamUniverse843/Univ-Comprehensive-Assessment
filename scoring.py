"""2022 年本科生测评办法；仅计算已确认记录，不推断缺失事实。"""
import re
import hashlib
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

CATEGORIES = ['专业学术', '文体科技实践活动', '志愿服务', '社会实践', '学生干部', '荣誉表彰', '其他', '专业论文', '非专业作品', '无偿献血', '处罚分']
LEVELS = ['国家级', '省市级', '区县级', '校级', '院系级']
ACADEMIC = [[4,3.5,3,2,1],[3,2.5,2,1,.8],[2.5,2,1.5,.8,.6],[2,1.5,1,.6,.4],[1,.7,.5,.3,.2]]
CULTURE = [[2,1.5,1.2,.8,.5],[1.6,1.2,.8,.6,.4],[1.2,.9,.6,.4,.3],[.8,.6,.4,.3,.2],[.5,.4,.3,.2,.1]]
CAPS = {'专业学术':4,'文体科技实践活动':3,'志愿服务':2,'荣誉表彰':3,'学生干部':2,'非专业作品':1,'无偿献血':.4,'处罚分':10}
RULES = [
 {'title':'专业学术竞赛','category':'专业学术','cap':'4 分','page':7,'text':'按级别和奖项计算。相同项目跨级别、同一比赛不同奖项取最高分。集体项目按 1/2 计分。','table':ACADEMIC},
 {'title':'文体科技实践活动','category':'文体科技实践活动','cap':'3 分','page':8,'text':'同一赛事取最高分。裁判、表演（含主持）、体育队活动经理各限 2 次。主持统一按文体参与分计算。集体项目按 1/2 计分。','table':CULTURE},
 {'title':'志愿服务','category':'志愿服务','cap':'200 小时 / 2 分','page':12,'text':'已认定时长 × 0.01。年度汇总与活动明细可能重叠时须核实。单项志愿活动内的志愿者荣誉不另加分。'},
 {'title':'学生干部','category':'学生干部','cap':'最高职务，至多 2 分','page':11,'text':'多职务不累加；标准是上限，采用组织测评后的分值。任职不足半年、获得工作学分或勤工助学补助者不计。组织限定的校级及以上个人荣誉不另加分。'},
 {'title':'荣誉表彰','category':'荣誉表彰','cap':'3 分','page':10,'text':'个人荣誉依类型、级别计分；集体荣誉按对应分值的 1/2 计分。军训标兵、军训优秀副班长为 0.2 分。'},
 {'title':'社会实践','category':'社会实践','cap':'同一假期取一项','page':13,'text':'暑期参与 0.4；校/市/国家优秀团队每人 0.6/0.8/1；先进个人 0.8/1/1.2。同一假期不得累加。寒假按审批分值、最高 0.4。暑期分数归下一学年，导入按提交表的测评学年，需核对归属。'},
 {'title':'论文、作品与献血','category':'其他','cap':'分类计算','page':9,'text':'专业论文按刊物与作者顺序计分（不并入学术竞赛 4 分上限）。非专业作品年度至多 1 分。献血每次 0.2、每年最多 2 次。未覆盖项目需提供审批依据。'},
 {'title':'总分与处罚','category':'处罚分','cap':'奖励 10 / 处罚 10','page':14,'text':'奖励、处罚分别封顶 10 分，附加分 = 奖励 − 处罚。警告 3、严重警告 4、记过 6、留校察看 8；校级通报每次 1、院级每次 0.5。总分 = 教师×10% + 学生×10% + 智育百分制加权均分×70% + 体育×10% + 附加分。'}
]

def dec(v): return Decimal(str(v))
def rounded(v): return float(dec(v).quantize(Decimal('.0001'), rounding=ROUND_HALF_UP))
def clean(v): return re.sub(r'\s+', '', str(v or '')).replace('（','(').replace('）',')')
def number(v):
    try:
        n=dec(v)
        return float(n) if n.is_finite() else None
    except Exception: return None

def level(v):
    v=clean(v)
    if '国际' in v or '国家' in v or '全国' in v: return 0
    if '省' in v or '市' in v: return 1
    if '区' in v or '县' in v: return 2
    if '校' in v: return 3
    if '院' in v or '系' in v: return 4
    return None

def award(v):
    for i,words in enumerate([['一等奖','金奖','冠军'],['二等奖','银奖','亚军'],['三等奖','铜奖','季军'],['优秀奖','单项奖'],['参与','参加']]):
        if any(w in v for w in words): return i
    return None

def infer(r):
    cat=r['category']; a=clean(r.get('award')); lv=level(r.get('level')); group=clean(r.get('kind'))
    score=r.get('original_score'); issues=[]; role=''; page=13; formula='需按审批依据确认'
    factor=.5 if group=='集体' else 1
    if r.get('ineligible'):
        return 0,cat,'不符合任职或活动计分条件',12,role,[]
    if cat in ['专业学术','文体科技实践活动']:
        page=7 if cat=='专业学术' else 8
        if any(x in a for x in ['主持','表演','演员','参演','裁判','活动经理']):
            cat='文体科技实践活动'; page=9
            role='裁判' if '裁判' in a else '活动经理' if '活动经理' in a else '表演（含主持）'
            ix=4
        else: ix=award(a)
        if ix is None or lv is None:
            return None,cat,'奖项或级别无法直接匹配；名次需确认前六/前八名赛制',page,role,['奖项或级别需确认']
        if group not in ['个人','集体']: issues.append('缺少个人/集体信息')
        if '参与' in a and not role and r.get('source')=='手工录入' and not r.get('participation_confirmed'):
            issues.append('参与奖需确认赛制与入围条件')
        base=(ACADEMIC if cat=='专业学术' else CULTURE)[lv][ix]
        val=rounded(dec(base)*dec(factor)); formula=f'{LEVELS[lv]}标准 {base} × '+('集体系数 1/2' if factor==.5 else '个人系数 1')
    elif cat=='志愿服务':
        page=12
        match=re.fullmatch(r'(\d+(?:\.\d+)?)\s*(?:h|H|小时|时)?',str(r.get('award','')).strip())
        if not match: return None,cat,'需确认有效志愿时长',page,role,['志愿时长无法识别']
        hours=dec(match[1]); val=rounded(hours*dec('.01')); formula=f'{hours} 小时 × 0.01（年度最多 200 小时）'
    elif cat=='学生干部':
        page=11
        # 岗位名称经常不能区分组织，保留测评分值，不擅自按上限替换。
        if score is None or score<0 or score>2:
            return None,cat,'组织测评分值应为 0～2，需核实岗位上限',page,role,['干部测评分值需确认']
        val=score; formula='采用组织测评分值；多个职务只取最高分'
        upper=None; context=a+clean(r.get('event'))
        if '优秀社团' in a and '部员' in a: upper=.4
        elif '优秀社团' in a and '部长' in a: upper=.8
        elif '优秀社团' in a and '负责人' in a: upper=1.3
        elif '社团' in a and '负责人' in a: upper=1
        elif any(x in a for x in ['部员','干事','班委','团支部委员']): upper=1
        elif any(x in a for x in ['团支部书记','班长']): upper=1.3
        elif '部长' in a:
            upper=1.3 if lv==4 else 1.6 if any(x in context for x in ['校团委','校学生会','志愿服务总队','红十字会','中外学生艺术团']) else None
        if upper is not None and score>upper: issues.append(f'组织测评分值超过可识别岗位上限 {upper}')
        if r.get('source')=='手工录入' and not r.get('eligibility_confirmed'): issues.append('需确认任期、补助/学分和岗位上限')
    elif cat=='荣誉表彰':
        page=10
        if any(x in a for x in ['军训标兵','军训优秀副班长']): val=.2; factor=1
        elif lv is None: return None,cat,formula,page,role,['荣誉级别需确认']
        elif any(x in a for x in ['志愿者','优秀学员']): val=[3,2,1.5,1,.5][lv]
        elif any(x in a for x in ['党支部','班集体','团支部','宿舍','基层党组织']): val=[2,1,.9,.8,.4][lv]
        elif any(x in a for x in ['优秀党员','优秀共产党员','三好学生','优秀学生干部','优秀共青团员','优秀团干部','优秀共青团干部']): val=[4,3,2.5,2,1][lv]
        else: return None,cat,formula,page,role,['荣誉类型需确认']
        val=rounded(dec(val)*dec(factor)); formula='荣誉类型和级别对应分值'+(' × 集体系数 1/2' if factor==.5 else '')
        if group not in ['个人','集体']: issues.append('缺少个人/集体信息')
    elif cat=='社会实践':
        page=13; event=r.get('event','')
        if '寒假' in event or '千人千校' in event+a:
            role='寒假'; val=score if score is not None and 0<=score<=.4 else None; formula='寒假采用审批分值，最高 0.4'
        elif '暑期' in event or '暑假' in event:
            role='暑假'
            if '优秀团队' in a and lv in [0,1,3]: val={0:1,1:.8,3:.6}[lv]
            elif '先进个人' in a and lv in [0,1,3]: val={0:1.2,1:1,3:.8}[lv]
            elif '参与' in a: val=score if score is not None and 0<=score<=.4 else .4
            else: val=None
            formula='暑期社会实践每人标准，同一假期取最高一项'
        else: val=None; issues.append('需确认寒暑假及学年归属')
        if val is None: issues.append('社会实践审批标准需确认')
    elif cat=='无偿献血': val=.2; role='献血'; formula='每次 0.2，年度最多两次'
    elif cat=='处罚分':
        page=14; val=None
        for key,n in [('留校察看',8),('记过',6),('严重警告',4),('警告',3),('校级通报',1),('院级通报',.5),('院系级通报',.5)]:
            if key in a: val=n; break
        formula='按处罚类型逐条扣分，年度最高 10'
    elif cat=='非专业作品':
        page=10; val=.2 if '市级' in a else .05 if '校内' in a else None; formula='市级以上公开报刊 0.2 / 校内报刊 0.05，每篇'
    elif cat=='专业论文':
        page=9; val=None
        if '校内' in a: val=.15
        else:
            tier=next((k for k in ['A','B','C'] if k+'类' in a.upper()),None)
            if '特类' in a: tier='A'
            author=next((i for i,k in enumerate(['独立','第一','第二','其他']) if k in a),None)
            if tier and author is not None: val={'A':[3,2,1,.4],'B':[2,1,.5,.3],'C':[1,.8,.4,.2]}[tier][author]
        formula='刊物类别与作者顺序对应分值；需学术委员会认定'
    else:
        val=None; issues.append('其他项目需审批依据')
    if val is None and not issues: issues.append('无法匹配明确计分标准')
    return val,cat,formula,page,role,issues

def calculate(records, bases=None, college='文学院', roster=None):
    bases=bases or {}; roster=roster or []; master={clean(r['student_id']):r for r in roster if r['active']}
    result=[]; name_sets=defaultdict(set); id_sets=defaultdict(set)
    for r in records:
        sid=clean(r.get('student_id')); name=clean(r.get('name'))
        if sid and name: name_sets[sid].add(name); id_sets[name].add(sid)
    for raw in records:
        r=dict(raw); r['student_id']=clean(r.get('student_id')); r['name']=clean(r.get('name'))
        person=master.get(r['student_id'])
        if person:
            r['source_college']=r.get('college');r['source_class_name']=r.get('class_name')
            r['college']=person['college'];r['class_name']=person['class_name']
        r['student_key']=r['student_id']+'|'+r['name']
        suggested,cat,formula,page,role,issues=infer(r)
        r.update(suggested=suggested,effective_category=cat,formula=formula,page=page,role=role,issues=list(issues),credited=0,notes=[])
        structural=[]
        if not clean(r.get('college')): structural.append('缺少院系，需确认归属')
        if not clean(r.get('class_name')): structural.append('缺少班级信息')
        if person and clean(person['name'])!=r['name']: structural.append('申报姓名与学生名册不一致：名册为'+person['name'])
        if not r['student_id'] or not r['name']: structural.append('缺少学号或姓名，不能归入学生')
        if r['student_id'] and not re.fullmatch(r'\d{10,12}',r['student_id']): structural.append('学号格式异常')
        if len(name_sets[r['student_id']])>1: structural.append('同一学号对应多个姓名')
        if len(id_sets[r['name']])>1: structural.append('同一姓名对应多个学号，需核对身份')
        r['issues']+=structural
        if r.get('original_score') is not None and r['original_score']<0: r['issues'].append('原表分值为负，请核对处罚类别')
        if suggested is not None and r.get('original_score') is not None and abs(suggested-r['original_score'])>.00001:
            r['issues'].append('原表与规则分值不一致')
        if not r.get('event'): r['issues'].append('项目名称缺失')
        if r.get('import_issue'): r['issues'].append(r['import_issue'])
        grad=bool(re.search(r'研究生|\(研\)|博物馆|现当代文学|古代文学|文字文献|古文中国史|比较文学',r.get('class_name','')) or re.match(r'^20\d{2}2\d{7}$',r['student_id']))
        r['scope_reason']='其他学院' if college!='全部学院' and clean(r.get('college')) and clean(r.get('college'))!=clean(college) else '研究生（按班级/学号识别）' if grad else ''
        review=r.get('review','auto')
        if r['scope_reason']: r['status']='outside'; r['value']=0
        elif review=='exclude': r['status']='excluded'; r['value']=0; r['notes'].append('人工排除：'+r.get('review_note',''))
        elif not r['student_id'] or not r['name'] or not clean(r.get('college')) or not clean(r.get('class_name')) or not re.fullmatch(r'\d{10,12}',r['student_id']): r['status']='pending'; r['value']=0
        elif structural and not r.get('identity_confirmed'): r['status']='pending'; r['value']=0
        elif review in ['original','rule','custom']:
            val={'original':r.get('original_score'),'rule':suggested,'custom':r.get('custom_score')}.get(review)
            r['value']=val if val is not None and val>=0 else 0
            r['status']='confirmed' if val is not None and val>=0 else 'pending'
            r['notes'].append('人工确认：'+r.get('review_note',''))
        elif r['issues']: r['status']='pending'; r['value']=0
        else: r['status']='confirmed'; r['value']=suggested or 0
        result.append(r)
    # 年度时长汇总与明细可能重叠，不自动相加。
    vol=defaultdict(list)
    for r in result:
        if r['effective_category']=='志愿服务' and not r['scope_reason'] and r.get('review')!='exclude': vol[r['student_key']].append(r)
    for group in vol.values():
        annual=[r for r in group if re.search(r'20\d{2}[-—至]20?\d{2}.*志愿服务活动',r['event']) or r['event']=='2025-2026志愿服务活动']
        if annual and len(group)>len(annual):
            for r in group:
                r['issues'].append('年度志愿汇总可能包含活动明细，需核对重叠')
                if r.get('review','auto')=='auto': r['status']='pending'; r['value']=0
    # 完全相同申报跨文件去重；排除的行不会挡住仍有效的记录。
    seen={}; duplicate_buckets=defaultdict(list)
    for r in sorted(result,key=lambda x:(x['status']!='confirmed',str(x['id']))):
        if r['status'] in ['outside','excluded']: continue
        key=tuple(clean(r.get(k)) for k in ['student_id','name','category','event','event_date','event_group','award','level','kind'])+(r.get('original_score'),)
        duplicate_buckets[key].append(r)
        if key in seen:
            r['status']='duplicate'; r['value']=0; r['duplicate_of']=seen[key]; r['notes'].append('完全重复申报，保留 '+str(seen[key]))
        else: seen[key]=r['id']
    students=defaultdict(list)
    for r in result:
        if not r['scope_reason'] and r['student_id'] and r['name']: students[r['student_key']].append(r)
    summaries=[]
    for key,rows in students.items():
        active=[r for r in rows if r['status']=='confirmed']; groups=defaultdict(list)
        for r in active:
            cat=r['effective_category']; event=clean(r.get('event_group') or r['event'])
            if cat in ['专业学术','文体科技实践活动']: bucket=(cat,event,r['role'])
            elif cat=='学生干部': bucket=(cat,'年度最高职务')
            elif cat=='社会实践': bucket=(cat,r['role'] or event)
            else: bucket=(cat,str(r['id']))
            groups[bucket].append(r)
        winners=[]
        for group in groups.values():
            ranked=sorted(group,key=lambda r:(-r['value'],str(r['id'])))
            winners.append(ranked[0])
            for r in ranked[1:]: r['notes'].append('同一比赛/同假期/多职务取最高，本条不叠加')
        roles=defaultdict(list); eligible=[]
        for r in winners:
            if r['role'] in ['裁判','表演（含主持）','活动经理','献血']: roles[r['role']].append(r)
            else: eligible.append(r)
        for group in roles.values():
            ranked=sorted(group,key=lambda r:(-r['value'],str(r['id'])))
            eligible.extend(ranked[:2])
            for r in ranked[2:]: r['notes'].append('该类别年度只计最高两次（选取高分为试算口径）')
        totals=defaultdict(lambda:Decimal('0'))
        for r in sorted(eligible,key=lambda r:(-r['value'],str(r['id']))):
            cat=r['effective_category']; amount=dec(r['value']); cap=dec(CAPS.get(cat,100000))
            credited=max(Decimal('0'),min(amount,cap-totals[cat])); totals[cat]+=credited
            r['credited']=rounded(credited)
            if credited<amount: r['notes'].append(f'{cat}年度上限 {cap} 分，本条计 {credited}')
        before=sum(v for k,v in totals.items() if k!='处罚分'); reward=min(before,dec(10)); penalty=min(totals['处罚分'],dec(10)); net=reward-penalty
        pending=sum(r['status']=='pending' for r in rows)
        base=bases.get(key,{})
        ready=all(number(base.get(k)) is not None and 0<=number(base[k])<=100 for k in ['teacher','peer','academic','sport'])
        foundation=(dec(base['teacher'])+dec(base['peer']))*dec('.1')+dec(base['academic'])*dec('.7')+dec(base['sport'])*dec('.1') if ready else None
        summaries.append({'key':key,'student_id':rows[0]['student_id'],'name':rows[0]['name'],'class_name':rows[0].get('class_name',''),'classes':sorted(set(r.get('class_name','') for r in rows)), 'count':len(rows),'pending':pending,'reward_before_cap':rounded(before),'reward':rounded(reward),'penalty':rounded(penalty),'net':rounded(net),'categories':{k:rounded(v) for k,v in totals.items()},'base':base,'foundation':rounded(foundation) if ready else None,'total':rounded(foundation+net) if ready else None,'complete':pending==0})
    existing={s['key'] for s in summaries}
    for person in master.values():
        key=clean(person['student_id'])+'|'+clean(person['name'])
        if key in existing or (college!='全部学院' and clean(person['college'])!=clean(college)): continue
        if re.match(r'^20\d{2}2\d{7}$',clean(person['student_id'])) or '研究生' in person['class_name']: continue
        base=bases.get(key,{})
        ready=all(number(base.get(k)) is not None and 0<=number(base[k])<=100 for k in ['teacher','peer','academic','sport'])
        foundation=rounded((dec(base['teacher'])+dec(base['peer']))*dec('.1')+dec(base['academic'])*dec('.7')+dec(base['sport'])*dec('.1')) if ready else None
        summaries.append({'key':key,'student_id':person['student_id'],'name':person['name'],'class_name':person['class_name'],'classes':[person['class_name']],'count':0,'pending':0,'reward_before_cap':0,'reward':0,'penalty':0,'net':0,'categories':{},'base':base,'foundation':foundation,'total':foundation,'complete':True})
    summaries.sort(key=lambda s:(-s['net'],s['student_id'],s['name']))
    # 对照界面也包含同项目取最高的全部来源，不能只展示完全重复的排除行。
    display_buckets=defaultdict(list)
    exact_keys={r['id']:tuple(clean(r.get(k)) for k in ['student_id','name','category','event','event_date','event_group','award','level','kind'])+(r.get('original_score'),) for r in result if not r['scope_reason']}
    for r in result:
        if r['id'] not in exact_keys: continue
        if r['effective_category'] in ['专业学术','文体科技实践活动'] and r.get('event'):
            key=('同项目',r['student_key'],r['effective_category'],r['role'],clean(r.get('event_group') or r['event']))
        else: key=('完全重复',exact_keys[r['id']])
        display_buckets[key].append(r)
    duplicate_groups=[]
    for key,members in display_buckets.items():
        if len(members)<2: continue
        group_id=hashlib.sha256(repr(key).encode()).hexdigest()[:16]
        candidates=[r for r in members if r['status']=='confirmed'] or [r for r in members if r['status'] not in ['duplicate','excluded']] or members
        keeper=sorted(candidates,key=lambda r:(-r['value'],str(r['id'])))[0]
        match_type='完全重复' if len({exact_keys[r['id']] for r in members})==1 else '同项目取最高'
        for r in members:
            r['duplicate_group']=group_id
            r['duplicate_count']=len(members)
            r['duplicate_kept']=r['id']==keeper['id']
            r['duplicate_disposition']='已作废（留存原记录）' if r['status']=='excluded' else '待审核，暂不计分' if r['status']=='pending' else '保留参与核算' if r['duplicate_kept'] else '不重复计入' if r['status']=='duplicate' else '同项目不叠加'
        members.sort(key=lambda r:(r['id']!=keeper['id'],str(r['id'])))
        resolved=all(r.get('review','auto')!='auto' and r['status']!='pending' for r in members)
        duplicate_groups.append({'id':group_id,'resolved':resolved,'match_type':match_type,'student_id':keeper['student_id'],'name':keeper['name'],'category':keeper['category'],'event':keeper['event'],'award':keeper['award'],'original_score':keeper.get('original_score'),'keeper_id':keeper['id'],'member_ids':[r['id'] for r in members],'file_count':len({r['source'] for r in members}),'count':len(members)})
    duplicate_groups.sort(key=lambda g:(g['student_id'],g['event']))
    return {'records':result,'students':summaries,'rules':RULES,'categories':CATEGORIES,'caps':CAPS,'duplicate_groups':duplicate_groups}
