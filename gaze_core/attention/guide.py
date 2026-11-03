"""Guide scheduling and action evidence, independent of Qt/browser timers.

The preregistered thresholds/layout/scoring windows remain in acceptance.py.
Only the operational script has been extended after the first real run.
"""
PHASES=('scroll','tab','app','exclude','pause')
SYSTEM_DIALOGS=('usernotificationcenter','notificationcenter','securityagent','coreauthui','authorizationhost')


def foreground_block(front):
    if front is None:return '无法确认前台应用，请回测试页后重试'
    text=(front.get('app_name','')+' '+front.get('bundle_id','')).lower()
    if any(name in text for name in SYSTEM_DIALOGS):return '请先处理系统弹窗；处理完成并回到测试页后自动开始，不消耗注视时间'
    return None


def browser_front(front):
    return front is not None and any(name in front.get('bundle_id','').lower() for name in ('chrome','safari','firefox','edgemac','brave','vivaldi','arc','orion'))


def phase_at(seconds,action_s=4):
    if seconds<16:return dict(kind='target',id=int(seconds//2)+1)
    index=int((seconds-16)//action_s)
    return dict(kind='end') if index>=len(PHASES) else dict(kind='phase',phase=PHASES[index])


def phase_start(phase,action_s=4):return 16+PHASES.index(phase)*action_s


def instructions(phase,excluded_app='备忘录',pause_key='Control+Option+S'):
    return dict(scroll='向下滚过至少半屏，看到下方编号内容后停住至少1.5秒',
                tab='按 ⌘⇧] 切到右边已准备的彩色测试标签，停住至少1.5秒；然后 ⌘⇧[ 返回',
                app=f'按 ⌘Tab 切到 Finder 等普通应用（不要选{excluded_app}），停住至少1.5秒，再返回',
                exclude=f'切到「{excluded_app}」，停住至少1.5秒，再回测试页（此步不截屏）',
                pause=f'按 {pause_key} 暂停注意力会话，等1秒，再按一次恢复')[phase]


def temporary_exclusions(configured,app,bundle):
    """Copy, never mutate settings or config/local.yaml."""
    values=list(configured);added=[v for v in (app,bundle) if v not in values]
    return tuple(values+added),added


def native_action(phase,front,browser_pid,excluded):
    if front is None or foreground_block(front):return False
    denied=front.get('app_name') in excluded or front.get('bundle_id') in excluded
    if phase=='exclude':return denied
    return phase=='app' and front.get('pid')!=browser_pid and not denied and not front.get('own_ui',False)
