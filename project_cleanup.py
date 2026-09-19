"""回收站项目在启动时清理，复用手动删除校验与日志。"""
from datetime import datetime, timedelta, timezone
import projects

def cleanup_old_projects(app, days=30):
    cutoff=datetime.now(timezone.utc)-timedelta(days=days)
    with projects.catalog(app) as c:
        rows=c.execute("SELECT * FROM projects WHERE deleted=1 AND deleted_at IS NOT NULL AND id!='legacy'").fetchall()
    count=0
    for row in rows:
        try:
            date=datetime.fromisoformat(row['deleted_at'])
            if date.tzinfo is None:date=date.astimezone()
            if date>=cutoff:continue
            projects.manage(app,dict(id=row['id'],action='purge',confirm=row['name']),'系统自动清理')
            count+=1
        except (OSError,ValueError) as e:
            print(f"清理项目 {row['id']} 失败：{e}",flush=True)
    return count
