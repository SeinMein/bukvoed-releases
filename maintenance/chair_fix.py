"""Visual chair return with bounded camera search. Runtime symbols come from bot."""

def chair_view(image):
    # Use the scenery on either side of the avatar. Jumping in place
    # changes the avatar even when no movement key was accepted.
    frame=image.resize((160,90),Image.Resampling.BILINEAR)
    gray=np.asarray(frame.convert('L'),dtype=np.float32)
    return np.concatenate((gray[36:69,32:67],gray[36:69,93:128]),axis=1)


def chair_grass_direction(image,excluded=()):
    frame=np.asarray(image.resize((1280,720),Image.Resampling.BILINEAR).convert('RGB'))
    # Prefer down toward the grass when the visible paths are comparable.
    regions=((0x1f,600,418,680,500),(0x1e,520,365,605,420),
             (0x20,675,365,760,420),(0x11,600,298,680,370))
    opposite={0x11:0x1f,0x1f:0x11,0x1e:0x20,0x20:0x1e}
    scores={}
    for key,left,top,right,bottom in regions:
        r,g,b=frame[top:bottom,left:right].astype('int16').transpose(2,0,1)
        grass=((g>95)&(g>r*1.45)&(g>b*1.15)).mean()
        if key not in excluded and grass>=.30:
            scores[key]=grass+(.10 if key==0x1f else 0)
    if not scores:return None
    direction=max(scores,key=scores.get)
    return direction,opposite[direction]


def move_chair_camera(window,stopped,dx=0,dy=0,zoom=0):
    hwnd,region=window
    center=W.POINT(region['left']+region['width']//2,region['top']+region['height']//2)
    target=user32.WindowFromPoint(center)
    if not target or user32.GetAncestor(target,2)!=hwnd:return False
    if game_window()!=window or not can_type(hwnd,stopped):return False
    if not user32.SetCursorPos(center.x,center.y):return False

    def mouse(flags,x=0,y=0,data=0):
        x=round(x*region['width']/1280)
        y=round(y*region['height']/720)
        event=INPUT(type=0,mi=MOUSEINPUT(x,y,data,flags,0,0))
        return user32.SendInput(1,C.byref(event),C.sizeof(INPUT))==1

    if game_window()!=window or not can_type(hwnd,stopped):
        return False
    if zoom:
        return mouse(0x0800,data=120*zoom)
    if not mouse(0x0008):
        return False
    try:
        if stopped.wait(.06):return False
        for _ in range(8):
            if not can_type(hwnd,stopped) or not mouse(0x0001,dx,dy) or stopped.wait(.025):
                return False
        return True
    finally:
        mouse(0x0010)


async def restart_at_chair(reader,capture,window,stopped,attempt,diagnostics,blocked):
    hwnd,region=window
    if game_window()!=window or not can_type(hwnd,stopped):return 'interrupted'
    shot=capture.grab(region)
    image=Image.frombytes('RGB',shot.size,shot.rgb)
    if not await reader.lobby_visible(image):return 'interrupted'
    point=W.POINT(region['left']+round(region['width']*.35),
                  region['top']+round(region['height']*.14))
    target=user32.WindowFromPoint(point)
    if not target or user32.GetAncestor(target,2)!=hwnd:return 'interrupted'
    if not user32.SetCursorPos(point.x,point.y):return 'interrupted'
    if game_window()!=window or not can_type(hwnd,stopped):return 'interrupted'
    down=INPUT(type=0,mi=MOUSEINPUT(0,0,0,2,0,0))
    up=INPUT(type=0,mi=MOUSEINPUT(0,0,0,4,0,0))
    try:
        if user32.SendInput(1,C.byref(down),C.sizeof(INPUT))!=1:return 'interrupted'
        if stopped.wait(.05):return 'interrupted'
    finally:
        user32.SendInput(1,C.byref(up),C.sizeof(INPUT))
    if stopped.wait(.12):return 'interrupted'
    diagnostics.event('Кресло: снял фокус ввода. Определяю направление по траве.')
    camera_changes=[]
    press._chair_input=True
    try:
        outcome=await attempt_chair_restart(reader,capture,window,stopped,attempt,diagnostics,blocked,camera_changes)
        if camera_changes and outcome in ('no_motion','grass_unconfirmed','unconfirmed','returned','interrupted'):
            if game_window()==window and can_type(hwnd,stopped):
                shot=capture.grab(region)
                frame=Image.frombytes('RGB',shot.size,shot.rgb)
                if await reader.lobby_visible(frame):
                    for dx,dy,zoom in reversed(camera_changes):
                        if not move_chair_camera(window,stopped,-dx,-dy,-zoom):break
                    else:
                        diagnostics.event('Кресло: завершил поиск, вернул камеру к исходному ракурсу.')
        return outcome
    finally:
        press._chair_input=False


async def attempt_chair_restart(reader,capture,window,stopped,attempt,diagnostics,blocked,camera_changes):
    hwnd,region=window

    def snapshot():
        if game_window()!=window or not can_type(hwnd,stopped):return None
        shot=capture.grab(region)
        return Image.frombytes('RGB',shot.size,shot.rgb)

    def move(key,hold,settle):
        if game_window()!=window or not can_type(hwnd,stopped):
            return False
        press(key,hold,stopped,hwnd)
        return not stopped.wait(settle) and game_window()==window and can_type(hwnd,stopped)

    def jump_then_move(key,hold,settle):
        diagnostics.event(f'Кресло: смещения нет, прыгаю и повторяю шаг {key:#x}.')
        return move(0x39,.12,0) and move(key,hold,settle)

    initial=snapshot()
    if initial is None or not await reader.lobby_visible(initial):return 'interrupted'
    if not move_chair_camera(window,stopped,0,8,0):return 'interrupted'
    camera_changes.append((0,8,0))
    diagnostics.event('Кресло: приподнял ракурс перед проверкой травы.')
    if stopped.wait(.20):return 'interrupted'
    initial=snapshot()
    if initial is None:return 'interrupted'
    if not await reader.lobby_visible(initial):return 'transition'
    direction=chair_grass_direction(initial,blocked) or chair_grass_direction(initial)

    if direction is None:
        camera_moves=((6,0,0),(-6,0,0),(0,-6,0))
        for dx,dy,zoom in camera_moves:
            if not move_chair_camera(window,stopped,dx,dy,zoom):return 'interrupted'
            camera_changes.append((dx,dy,zoom))
            if stopped.wait(.24):return 'interrupted'
            initial=snapshot()
            if initial is None or not await reader.lobby_visible(initial):
                return 'interrupted'
            direction=chair_grass_direction(initial,blocked) or chair_grass_direction(initial)
            diagnostics.event(f'Поиск кресла: камера dx={dx}, dy={dy}, колесо={zoom}, направление={direction}')
            if direction is not None:
                break
            if not move_chair_camera(window,stopped,-dx,-dy,-zoom):return 'interrupted'
            camera_changes.pop()
            if stopped.wait(.18):return 'interrupted'
        if direction is None:
            initial.save(diagnostics.folder/'latest_chair.png')
            return 'grass_unconfirmed'
    away_key,back_key=direction
    duration=(.46,.57,.68)[attempt-1]
    if not move(away_key,duration,.40):
        return 'interrupted'
    away=snapshot()
    if away is None:return 'interrupted'
    if not await reader.lobby_visible(away):return 'transition'
    initial_view,away_view=chair_view(initial),chair_view(away)
    displacement=float(np.mean(np.abs(away_view-initial_view)))
    diagnostics.event(f'Кресло: шаг к траве {away_key:#x}, смещение {displacement:.1f}')
    if displacement<3.5:
        if not jump_then_move(away_key,duration,.40):return 'interrupted'
        away=snapshot()
        if away is None:return 'interrupted'
        if not await reader.lobby_visible(away):return 'transition'
        away_view=chair_view(away)
        displacement=float(np.mean(np.abs(away_view-initial_view)))
        diagnostics.event(f'Кресло: смещение после прыжка {displacement:.1f}')
    if displacement<3.5:
        blocked.add(away_key)
        initial.save(diagnostics.folder/'latest_chair_before.png')
        away.save(diagnostics.folder/'latest_chair.png')
        return 'no_motion'
    sides=(0x1e,0x20) if away_key in (0x11,0x1f) else (0x11,0x1f)
    best=None
    previous_view=away_view
    stalled=0
    jumped_on_return=False
    for key,hold,reverse in ((back_key,duration+.04,away_key),(back_key,.14,away_key),
                             (sides[0],.16,sides[1]),(sides[1],.16,sides[0]),
                             (away_key,.11,back_key),(back_key,.11,away_key)):
        if not move(key,hold,.28):return 'interrupted'
        returned=snapshot()
        if returned is None:return 'interrupted'
        if not await reader.lobby_visible(returned):return 'transition'
        returned_view=chair_view(returned)
        distance=float(np.mean(np.abs(returned_view-initial_view)))
        motion=float(np.mean(np.abs(returned_view-previous_view)))
        previous_view=returned_view
        diagnostics.event(f'Кресло: шаг {key:#x}, отклонение от исходной сцены {distance:.1f}')
        if distance<=max(2.5,displacement*.48):
            return 'returned'
        stalled=stalled+1 if motion<2 else 0
        if stalled>=2:
            if jumped_on_return:
                blocked.add(away_key)
                returned.save(diagnostics.folder/'latest_chair.png')
                return 'no_motion'
            jumped_on_return=True
            stalled=0
            if not jump_then_move(key,hold,.28):return 'interrupted'
            returned=snapshot()
            if returned is None:return 'interrupted'
            if not await reader.lobby_visible(returned):return 'transition'
            previous_view=chair_view(returned)
            distance=float(np.mean(np.abs(previous_view-initial_view)))
            if distance<=max(2.5,displacement*.48):return 'returned'
        if best is not None and distance>best+1:
            if not move(reverse,hold,.28):return 'interrupted'
            corrected=snapshot()
            if corrected is None:return 'interrupted'
            if not await reader.lobby_visible(corrected):return 'transition'
            previous_view=chair_view(corrected)
        else:
            best=distance
    returned.save(diagnostics.folder/'latest_chair.png')
    return 'unconfirmed'
