"""编辑试算：只在内存替换草稿，复用正式计算规则。"""
import json
from scoring import calculate, CATEGORIES, number
import registry

FIELDS=['student_id','name','college','class_name','category','award','level','kind','event','event_group','review','review_note','custom_score','identity_confirmed','ineligible','eligibility_confirmed','participation_confirmed']

def calculate_draft(c,body):
    if c.execute("SELECT value FROM meta WHERE key='locked'").fetchone()[0]=='true':raise ValueError('历史归档只读，不进行编辑试算')
    records=[json.loads(r[0]) for r in c.execute('SELECT current FROM records ORDER BY id')]
    bases={r['student_key']:json.loads(r['data']) for r in c.execute('SELECT * FROM bases')}
    mode=body.get('mode','record');ident=body.get('id');draft=None
    if mode in ['record','manual']:
        if mode=='record':
            draft=next((r for r in records if r['id']==ident),None)
            if draft is None:raise ValueError('申报不存在')
        else:
            ident='draft-preview'
            draft={'id':ident,'source':'手工录入','sheet':'手工录入','row':0,'review':'auto','review_note':'','event_group':'','source_note':'','event_date':'','import_issue':'手工申报需审核','original_score':None}
            records.append(draft)
        changes=body.get('changes',{})
        for key in FIELDS+(['original_score'] if mode=='manual' else []):
            if key in changes:
                val=changes[key]
                if key in ['identity_confirmed','ineligible','eligibility_confirmed','participation_confirmed']:
                    if type(val) is not bool:raise ValueError('确认标记必须为布尔值')
                elif key in ['custom_score','original_score']:
                    val=number(val)
                    if val is not None and not 0<=val<=10:raise ValueError('分值须在 0～10 之间')
                elif not isinstance(val,str) or len(val)>2000:raise ValueError('字段格式无效')
                draft[key]=val
        if draft.get('category') not in CATEGORIES:raise ValueError('请选择活动类别')
        if draft.get('review') not in ['auto','rule','original','custom','exclude']:raise ValueError('审核方式无效')
    elif mode=='base':
        vals={}
        for key in ['teacher','peer','academic','sport']:
            raw=body.get('base',{}).get(key)
            value=number(raw)
            if raw not in [None,''] and (value is None or not 0<=value<=100):raise ValueError('基础成绩须为 0～100 分')
            if value is not None:vals[key]=value
        bases[body.get('key','')]=vals
    else:raise ValueError('试算类型无效')
    college=c.execute("SELECT value FROM meta WHERE key='college'").fetchone()[0]
    result=calculate(records,bases,college,registry.get(c)['students'])
    r=next((r for r in result['records'] if r['id']==ident),None) if draft else None
    key=r['student_key'] if r else body.get('key')
    student=next((s for s in result['students'] if s['key']==key),None)
    return {'record':r,'student':student,'unsaved':True}
