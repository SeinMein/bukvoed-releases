"""Opening-card mouse input and rapid loss submissions. Runtime symbols come from bot."""

async def click_game_target(capture,hwnd,region,box,stopped,verify):
    left,top,right,bottom=box
    x=region['left']+round((left+right)*region['width']/2560)
    y=region['top']+round((top+bottom)*region['height']/1440)

    def fail(reason):
        verify.failure=reason
        return False

    def can_click():
        if stopped.is_set() or game_window()!=(hwnd,region):return False
        under_cursor=user32.WindowFromPoint(W.POINT(x,y))
        return bool(under_cursor and user32.GetAncestor(under_cursor,2)==hwnd)

    origin=W.POINT()
    if not can_click() or not user32.GetCursorPos(C.byref(origin)):return fail('target_or_focus')
    desktop_x,desktop_y,desktop_w,desktop_h=(user32.GetSystemMetrics(i) for i in (76,77,78,79))
    if desktop_w<2 or desktop_h<2:return fail('desktop_size')
    for step in range(1,13):
        if stopped.is_set() or user32.GetForegroundWindow()!=hwnd:return fail('focus_during_move')
        fraction=step/12
        eased=fraction*fraction*(3-2*fraction)
        px=origin.x+(x-origin.x)*eased
        py=origin.y+(y-origin.y)*eased
        dx=max(0,min(65535,round((px-desktop_x)*65535/(desktop_w-1))))
        dy=max(0,min(65535,round((py-desktop_y)*65535/(desktop_h-1))))
        move=INPUT(type=0,mi=MOUSEINPUT(dx,dy,0,0xC001,0,0))  # Absolute virtual desktop move.
        if user32.SendInput(1,C.byref(move),C.sizeof(INPUT))!=1:return fail('mouse_move_rejected')
        if stopped.wait(.015):return fail('stopped')
    if stopped.wait(.10) or not can_click():return fail('focus_after_move')
    pointer=W.POINT()
    if not user32.GetCursorPos(C.byref(pointer)):return fail('cursor_unavailable')
    if abs(pointer.x-x)>3 or abs(pointer.y-y)>3:
        if not user32.SetCursorPos(x,y) or stopped.wait(.05):return fail('cursor_correction')
    shot=capture.grab(region)
    image=Image.frombytes('RGB',shot.size,shot.rgb)
    if not await verify(image):return fail('screen_changed')
    if not can_click():return fail('focus_before_click')
    dx=max(0,min(65535,round((x-desktop_x)*65535/(desktop_w-1))))
    dy=max(0,min(65535,round((y-desktop_y)*65535/(desktop_h-1))))
    # Position and button event must arrive together if the game recenters
    # the cursor while its TextBox or camera owns the mouse.
    down=INPUT(type=0,mi=MOUSEINPUT(dx,dy,0,0xC003,0,0))
    up=INPUT(type=0,mi=MOUSEINPUT(dx,dy,0,0xC005,0,0))
    if user32.SendInput(1,C.byref(down),C.sizeof(INPUT))!=1:return fail('mouse_down_rejected')
    try:
        stopped.wait(.11)
    finally:
        if game_window()!=(hwnd,region):
            up.mi.dx=up.mi.dy=0
            up.mi.dwFlags=0x0004
        released=user32.SendInput(1,C.byref(up),C.sizeof(INPUT))
    if released==1:
        stopped.wait(.12)
        move_cursor_off_cards(hwnd,region,[box],stopped)
    return released==1 or fail('mouse_up_rejected')


async def click_letter_card(reader,capture,hwnd,region,box,stopped,confirmed_image):
    confirmed_title=opening_title_mask(confirmed_image)

    async def verify(image):
        left,top,right,bottom=box
        cx,cy=(left+right)/2,(top+bottom)/2
        fresh=await reader.letter_choices(image,row=(),confirmed_title=confirmed_title)
        return any(l<=cx<=r and t<=cy<=b for l,t,r,b in fresh)
    clicked=await click_game_target(capture,hwnd,region,box,stopped,verify)
    if not clicked:
        try:
            folder=APP_DIR/'logs'
            details=dict(reason=getattr(verify,'failure','unknown'),box=box,region=region)
            (folder/'latest_choice_click.json').write_text(json.dumps(details),encoding='utf-8')
            if game_window()==(hwnd,region):
                shot=capture.grab(region)
                Image.frombytes('RGB',shot.size,shot.rgb).save(folder/'latest_choice_click.png')
        except OSError:
            pass
    return clicked


def repeat_loss_enter(reader,capture,window,stopped,turbo=False):
    hwnd,region=window
    sent=0
    deadline=time.monotonic()+.25
    next_check=0.
    while time.monotonic()<deadline:
        if not can_type(hwnd,stopped) or game_window()!=window:
            break
        now=time.monotonic()
        if now>=next_check:
            shot=capture.grab(region)
            image=Image.frombytes('RGB',shot.size,shot.rgb)
            if not reader.turn_indicator(image)['present'] or not can_type(hwnd,stopped):
                break
            next_check=time.monotonic()+.075
        press(0x1c,.006,stopped,hwnd)
        sent+=1
        if stopped.wait(.004):break
    return sent


async def letter_choices(self,image,row=None,confirmed_title=None):
        if row is None:
            row=self.last.get('card_row',[])
        # Preserve the four-card layout across window shapes and UI scales.
        def opening_row(boxes):
            if len(boxes)!=4:return False
            widths=[r-l for l,t,r,b in boxes]
            centers=[(l+r)/2 for l,t,r,b in boxes]
            pitch=(centers[-1]-centers[0])/3
            return (abs((centers[0]+centers[-1])/2-640)<=36 and 42<=pitch<=118
                    and all(38<=width<=112 and pitch*.70<=width<=pitch*1.16 for width in widths)
                    and all(265<=(t+b)/2<=400 and 36<=b-t<=120 for l,t,r,b in boxes)
                    and max((t+b)/2 for l,t,r,b in boxes)-min((t+b)/2 for l,t,r,b in boxes)<=18
                    and all(abs(right-left-pitch)<=max(8,pitch*.18)
                            for left,right in zip(centers,centers[1:])))

        if not opening_row(row):
            source=np.asarray(image.resize((1280,720),Image.Resampling.BILINEAR).convert('RGB'))
            isolated=np.zeros((720,1280,3),dtype=np.uint8)
            isolated[220:445,395:885]=source[220:445,395:885,::-1]
            row=find_card_row(isolated)
        if not opening_row(row):
            return []
        now=time.monotonic()
        previous=getattr(self,'opening_confirmation',None)
        if confirmed_title is None and previous is not None and now-previous[0]<=1:
            confirmed_title=previous[1]
        if confirmed_title is not None:
            current_title=opening_title_mask(image)
            overlap=np.count_nonzero(current_title & confirmed_title)
            total=np.count_nonzero(current_title)+np.count_nonzero(confirmed_title)
            if overlap>=80 and 2*overlap>=.82*total:
                self.opening_confirmation=(now,current_title)
                return row
        header=crop_relative(image,(.12,.045,.88,.21),2)
        for sample in (header,ImageOps.autocontrast(ImageOps.grayscale(header))):
            text=await self.text(sample,self.ru)
            words=''.join(c for c in text.casefold() if c.isalpha())
            if ('выбер' in words or 'выбир' in words) and 'перв' in words and 'букв' in words:
                self.opening_confirmation=(now,opening_title_mask(image))
                return row
        return []
