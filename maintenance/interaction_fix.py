"""Interaction fixes and the worker using an independent Enter sender."""

async def click_game_target(capture,hwnd,region,box,stopped,verify):
    left,top,right,bottom=box
    x=region['left']+round((left+right)*region['width']/2560)
    y=region['top']+round((top+bottom)*region['height']/1440)

    def fail(reason):
        getattr(verify,'__func__',verify).failure=reason
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
    state=getattr(reader,'loss_enter_sender',None)
    pending=0
    if state is not None:
        count=state['count']
        pending=count-state['reported']
        state['reported']=count
        if state['thread'].is_alive():
            return pending

    state={'count':0,'reported':0,'cancel':threading.Event()}
    reader.loss_enter_sender=state
    hwnd,region=window

    def pump():
        reason='завершение партии'
        started=time.monotonic()
        try:
            # MSS capture objects are local to the thread that uses them.
            with mss.mss() as stream_capture:
                next_check=0.
                while not stopped.is_set() and not state['cancel'].is_set():
                    if not reader.loss_enter_allowed():
                        reason='режим или партия изменились'
                        break
                    if not can_type(hwnd,stopped):
                        reason='потеря фокуса'
                        break
                    now=time.monotonic()
                    if now>=next_check:
                        if game_window()!=window:
                            reason='окно переместилось'
                            break
                        status=dict(left=region['left']+round(region['width']*1221/1280),
                                    top=region['top']+round(region['height']*12/720),
                                    width=max(1,round(region['width']*43/1280)),
                                    height=max(1,round(region['height']*43/720)))
                        frame=np.asarray(stream_capture.grab(status))[:,:,:3]
                        b,g,r=frame.astype('int16').transpose(2,0,1)
                        if ((r>155)&(r>g+60)&(r>b+45)).mean()<.55:
                            break
                        next_check=time.monotonic()+.05
                    with reader.loss_input_lock:
                        if (stopped.is_set() or state['cancel'].is_set()
                                or not reader.loss_enter_allowed() or not can_type(hwnd,stopped)):
                            break
                        down=INPUT(type=1,ki=KEYBDINPUT(0x0d,0x1c,0,0,0))
                        up=INPUT(type=1,ki=KEYBDINPUT(0x0d,0x1c,2,0,0))
                        if user32.SendInput(1,C.byref(down),C.sizeof(INPUT))!=1:
                            raise RuntimeError('Windows отклонила нажатие Enter')
                        try:
                            time.sleep(.018)
                        finally:
                            if user32.SendInput(1,C.byref(up),C.sizeof(INPUT))!=1:
                                raise RuntimeError('Windows не подтвердила отпускание Enter')
                        state['count']+=1
                    time.sleep(.006)
        except (OSError,RuntimeError) as exc:
            reason=str(exc)
        finally:
            elapsed=max(.001,time.monotonic()-started)
            reader.loss_enter_notify(
                f"Проигрыш: поток Enter ×{state['count']}, "
                f"{state['count']/elapsed:.0f} нажатий/с; {reason}.")

    state['thread']=threading.Thread(target=pump,name='loss-enter',daemon=True)
    state['thread'].start()
    return pending


async def letter_choices(self,image,row=None,confirmed_title=None):
        if image.size!=(1280,720):
            image=image.resize((1280,720),Image.Resampling.LANCZOS)
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
        header=crop_relative(image,(.20,.055,.80,.18),2)
        for sample in (header,ImageOps.autocontrast(ImageOps.grayscale(header))):
            text=await self.text(sample,self.ru)
            words=''.join(c for c in text.casefold() if c.isalpha())
            if (any(prompt in words for prompt in ('выбери','выбири'))
                    and 'перв' in words and 'букв' in words):
                self.opening_confirmation=(now,opening_title_mask(image))
                return row
        return []


class App:
    def run(self, nickname, delay, mode, bank, learning):
        def new_ledger():
            path=self.diagnostics.folder/('accepted_words_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.json')
            return WordLedger(path,learning)
        ledger=new_ledger()
        self.live_ledger=ledger
        tracker=TableTracker(Reader,ledger,self.diagnostics,self.notify,self.stopped)
        recorder=ScreenRecorder(self.diagnostics,self.stopped,self.notify,tracker.on_frame,
                                fps=lambda:50 if self.extra_mode==EXTRA_MODES[2] else 40)
        async def loop():
            nonlocal ledger
            reader, gate = Reader(), TurnGate()
            reader.loss_input_lock=threading.Lock()

            def reset_loss_sender():
                sender=getattr(reader,'loss_enter_sender',None)
                if sender is not None:
                    sender['cancel'].set()
                reader.loss_enter_sender=None
            timer=TurnTimer(BASE/'timer_digits.npz')
            pending=None
            cleanup_letter=None
            cleanup_urgent=False
            retry_letter=None
            last_order=None
            ending_revision=None
            ranked_endings=()
            last_hwnd = None
            next_focus_attempt=0.
            focus_status=None
            choice_frames=0
            next_choice_click=0.
            choice_click_pending=False
            choice_order=[]
            match_started=None
            loss_phase_announced=False
            lobby_frames=0
            next_lobby_check=0.
            next_restart=0.
            losing_letter=None
            loss_wrong=''
            loss_errors=loss_enters=0
            loss_progress=0.
            restart_attempts=0
            match_finished=False
            choices=[]
            reader.loss_enter_allowed=lambda: (
                not self.stopped.is_set() and not match_finished and not choices
                and (self.play_mode==PLAY_MODES[2]
                     or (self.play_mode==PLAY_MODES[1] and match_started is not None
                         and time.monotonic()>=match_started+LOSS_AFTER_SECONDS)))
            reader.loss_enter_notify=self.notify
            chair_blocked=set()
            saw_own_turn=False
            training_match=False
            next_opponent_check=0.
            with mss.mss() as capture:
                while not self.stopped.is_set():
                    window = game_window()
                    if window is None:
                        choice_frames=0
                        choice_order.clear()
                        next_choice_click=0.
                        now=time.monotonic()
                        if now>=next_focus_attempt:
                            next_focus_attempt=now+.35
                            outcome=focus_roblox(last_hwnd,self.stopped)
                            if outcome!=focus_status:
                                self.diagnostics.event('Фокус Roblox: '+outcome)
                                focus_status=outcome
                            if outcome=='active':continue
                        title = C.create_unicode_buffer(512)
                        user32.GetWindowTextW(user32.GetForegroundWindow(),title,512)
                        if self.diagnostics.frame({'state':'inactive','foreground':title.value}):
                            self.notify('Ожидание: окно Roblox не активно. Пытаюсь вернуть фокус.')
                        self.stopped.wait(.1)
                        continue
                    hwnd, region = window
                    focus_status=None
                    next_focus_attempt=0.
                    if hwnd != last_hwnd:
                        reset_loss_sender()
                        gate.previous, gate.count = None, 0
                        retry_letter=None
                        cleanup_letter=None
                        cleanup_urgent=False
                        pending=None
                        match_started=None
                        self.match_clock=None
                        saw_own_turn=training_match=False
                        next_opponent_check=0.
                        loss_phase_announced=False
                        match_finished=False
                        losing_letter=None
                        loss_wrong=''
                        restart_attempts=lobby_frames=0
                        next_restart=0.
                        chair_blocked.clear()
                        choice_frames=0
                        choice_order.clear()
                        next_choice_click=0.
                        choice_click_pending=False
                        last_hwnd = hwnd
                    shot = capture.grab(region)
                    image = Image.frombytes('RGB', shot.size, shot.rgb)
                    letter = await reader.read(image,nickname,check_rejection=pending is not None)
                    choices=await reader.letter_choices(image)
                    now=time.monotonic()
                    own_turn=reader.last['turn_icon']['present']
                    if (match_finished and (choices or (own_turn and letter))) or (choices and saw_own_turn):
                        reset_loss_sender()
                        losing_letter=None
                        loss_wrong=''
                        loss_enters=loss_errors=0
                        loss_progress=0.
                        chair_blocked.clear()
                        ledger=new_ledger()
                        tracker.begin_match(ledger)
                        self.live_ledger=ledger
                        restart_attempts=0
                        next_restart=0.
                        match_finished=False
                        match_started=None
                        loss_phase_announced=False
                        saw_own_turn=training_match=False
                        next_opponent_check=0.
                        self.notify('Началась новая партия. Сбросил использованные слова.')
                    if (own_turn or choices) and match_started is None:
                        match_started=now
                        training_match=self.restart_mode==RESTART_MODES[1]
                    saw_own_turn |= own_turn and not choices
                    if (not training_match and match_started is not None and not match_finished
                            and not own_turn and reader.last['card_row'] and now>=next_opponent_check):
                        next_opponent_check=now+1
                        opponent=await reader.text(crop_relative(image,(.32,.20,.68,.38),2),reader.ru)
                        training_match='буквоед' in ''.join(c for c in opponent.casefold() if c.isalpha())
                    if training_match and not match_finished:
                        ledger.training=self.extra_mode==EXTRA_MODES[2]
                        self.match_clock=(match_started,None)
                    elif not match_finished:
                        self.match_clock=None
                    if choices:
                        reset_loss_sender()
                        lobby_frames=0
                        pending=cleanup_letter=retry_letter=losing_letter=None
                        loss_wrong=''
                        cleanup_urgent=False
                        if choice_frames==0:
                            self.diagnostics.event('Экран выбора: подтверждены четыре карточки.')
                        choice_frames+=1
                        if now>=next_choice_click:
                            if not choice_order:
                                choice_order=[random.randrange(len(choices))]
                            next_choice_click=now+.65
                            index=choice_order[0]
                            if await click_letter_card(reader,capture,hwnd,region,choices[index],self.stopped,image):
                                choice_click_pending=True
                                self.notify(f'Выбор первой буквы: отправил клик по карточке {index+1} из 4.')
                                gate=TurnGate()
                            else:
                                self.diagnostics.event('Клик отменён: экран выбора или курсор не подтверждены.')
                        self.stopped.wait(.035)
                        continue
                    choice_frames=0
                    choice_order.clear()
                    next_choice_click=0.
                    turbo=self.pace_mode==PACE_MODES[2]
                    if cleanup_letter is not None:
                        cleaned=await recover_field(reader,capture,hwnd,cleanup_letter,self.stopped,delay,self.notify,
                                                    urgent=cleanup_urgent,fast=True,turbo=turbo)
                        if cleaned is None:
                            continue
                        if cleaned:
                            self.notify('Поле очищено до '+cleanup_letter.upper()+'. Выбираю другое слово.')
                            retry_letter=cleanup_letter
                        elif not self.stopped.is_set():
                            self.notify('Ход закончился во время очистки. Жду следующий.')
                        cleanup_letter=None
                        cleanup_urgent=False
                        gate=TurnGate()
                        continue
                    if own_turn:
                        lobby_frames=0
                    elif now>=next_lobby_check:
                        next_lobby_check=now+.45
                        in_lobby=await reader.lobby_visible(image)
                        lobby_frames=lobby_frames+1 if in_lobby else 0
                        if lobby_frames>=2:
                            if not match_finished:
                                reset_loss_sender()
                                self.diagnostics.event('Конец партии: лобби подтверждено. Перезапуск: '+self.restart_mode)
                                if training_match:
                                    self.match_clock=(match_started,now)
                            match_finished=True
                        if (match_finished and in_lobby and self.restart_mode!=RESTART_MODES[2]
                                and now>=next_restart):
                            next_restart=now+5
                            pending=losing_letter=retry_letter=None
                            gate=TurnGate()
                            if self.restart_mode==RESTART_MODES[1]:
                                restart_attempts+=1
                                clicked=await click_game_target(capture,hwnd,region,(32,435,105,500),
                                                               self.stopped,reader.lobby_visible)
                                self.notify('Перезапуск: нажал «Тренировка».' if clicked
                                            else 'Перезапуск: кнопка «Тренировка» не подтверждена.')
                            else:
                                if restart_attempts>=3:
                                    restart_attempts=0
                                    chair_blocked.clear()
                                restart_attempts+=1
                                outcome=await restart_at_chair(reader,capture,window,self.stopped,restart_attempts,
                                                               self.diagnostics,chair_blocked)
                                self.diagnostics.event('Перезапуск со стула: '+outcome)
                                if outcome in ('returned','transition'):
                                    next_restart=time.monotonic()+8
                                    self.notify('Перезапуск: жду подтверждения новой партии.')
                                elif outcome=='no_motion':
                                    next_restart=time.monotonic()+.4
                                    self.notify('Перезапуск: движения не видно. Перепроверю направление к траве.')
                                elif outcome=='grass_unconfirmed':
                                    self.notify('Перезапуск: не удалось определить сторону травы за стулом.')
                                elif outcome=='unconfirmed':
                                    self.notify('Перезапуск: возвращение к стулу не подтверждено. Попробую ещё.')
                                if restart_attempts>=3:
                                    next_restart=max(next_restart,time.monotonic()+8)
                            continue
                    move_cursor_off_cards(hwnd,region,reader.last.get('card_row',[]),self.stopped)
                    if choice_click_pending and letter:
                        self.diagnostics.event('После клика игра перешла к вводу слова.')
                        choice_click_pending=False
                    loss_deadline=(match_started+LOSS_AFTER_SECONDS
                                   if self.play_mode==PLAY_MODES[1] and match_started is not None else None)
                    fast_loss=self.play_mode==PLAY_MODES[2]
                    if not match_finished and (fast_loss or (loss_deadline is not None and now>=loss_deadline)):
                        loss_turbo=turbo or fast_loss
                        if not loss_phase_announced:
                            self.notify('Быстрый проигрыш. Действую на своём ходу.' if fast_loss else
                                        f'{LOSS_AFTER_SECONDS} секунд прошло. Проигрываю при первой возможности.')
                            loss_phase_announced=True
                        pending=None
                        if not own_turn:
                            if losing_letter and now-loss_progress>.9:
                                self.notify(f'Проигрыш: Enter ×{loss_enters}, ход завершён.')
                                losing_letter=None
                                loss_wrong=''
                        elif can_type(hwnd,self.stopped) and game_window()==window:
                            errors=sum(reader.last.get('error_slots',[]))
                            row=reader.last.get('card_row',[])
                            if letter:
                                first_attempt=losing_letter!=letter
                                if first_attempt:
                                    loss_enters=0
                                    loss_wrong=''
                                wrong='цщ'
                                with reader.loss_input_lock:
                                    sent=send_word(letter+wrong,hwnd,delay,self.stopped,mode,
                                                   submit=False,fast=True,turbo=loss_turbo)
                                    if sent:
                                        confirmed=await confirm_input(reader,capture,hwnd,self.stopped,letter+wrong)
                                    if not sent or not confirmed:
                                        reset_loss_sender()
                                        losing_letter=None
                                        cleanup_letter=letter
                                        cleanup_urgent=True
                                        sent=False
                                if sent:
                                    losing_letter=letter
                                    loss_wrong=wrong
                                    loss_progress=now
                                    loss_errors=errors
                                    self.diagnostics.event('Проигрыш: введено '+letter+wrong+
                                                           f'; поле подтверждено={confirmed}. Повторяю Enter.')
                            elif losing_letter and errors!=loss_errors:
                                loss_errors=errors
                                loss_progress=now
                            if losing_letter:
                                loss_enters+=repeat_loss_enter(reader,capture,window,self.stopped,turbo=loss_turbo)
                                if (not letter and len(row)==1+len(loss_wrong) and row_quality(row)['complete']
                                        and now-loss_progress>=.35 and can_type(hwnd,self.stopped)
                                        and game_window()==window):
                                    # Backspace only on the original two-card field,
                                    # after another fresh ownership check.
                                    shot=capture.grab(region)
                                    fresh=Image.frombytes('RGB',shot.size,shot.rgb)
                                    await reader.read(fresh)
                                    fresh_row=reader.last['card_row']
                                    if (reader.last['turn_icon']['present'] and len(fresh_row)==1+len(loss_wrong)
                                            and row_quality(fresh_row)['complete']):
                                        with reader.loss_input_lock:
                                            clear_suffix(len(loss_wrong),hwnd,delay,self.stopped,fast=True,turbo=loss_turbo)
                                        loss_progress=now
                        self.stopped.wait(.015 if loss_turbo else .035)
                        continue
                    if losing_letter:
                        cleanup_letter=losing_letter
                        cleanup_urgent=True
                        losing_letter=None
                        continue
                    if pending is not None and ledger.is_accepted(pending.word):pending=None
                    if pending is not None:
                        outcome=pending.observe(reader.last,time.monotonic())
                        if outcome=='turn_ended':pending=None
                        elif outcome=='rejected':
                            ledger.reject(pending.word,dict(error_slots=reader.last.get('error_slots'),
                                                          message=reader.last.get('rejection_text')))
                            self.notify('Ответ отклонён: '+pending.word+'. Очищаю остаток перед другой попыткой.')
                            cleanup_letter=pending.letter
                            cleanup_urgent=True
                            pending=None
                            continue
                        elif (not pending.resent and own_turn and time.monotonic()-pending.sent_at>=.40
                              and len(reader.last['card_row'])==len(pending.word)
                              and not reader.last['rejection_text']):
                            pending.resent=True
                            if await confirm_input(reader,capture,hwnd,self.stopped,pending.word):
                                press(0x1c,TURBO_HOLD if turbo else .028,self.stopped,hwnd)
                                self.notify('Поле осталось заполненным. Повторил Enter: '+pending.word)
                            continue
                    allowed = gate.observe(letter,time.monotonic())
                    diagnostic = dict(reader.last)
                    diagnostic.update(region=region,words=ledger.counts(),gate=(
                        'send' if allowed else 'already_sent' if letter and gate.sent==letter else
                        'confirming' if letter else 'waiting'), confirmations=gate.count)
                    if self.diagnostics.frame(diagnostic,image,reader.last_glyph):
                        state=diagnostic['state']
                        if state=='ready':
                            message = ('Ход подтверждён: ' if allowed else
                                       'Слово уже отправлено, жду смены хода: ' if gate.sent==letter else
                                       'Вижу значок времени и букву, подтверждаю: ')+letter.upper()
                        elif state=='no_prompt':
                            message='Ожидание: карточка с буквой не найдена.'
                        elif state=='waiting_turn_icon':
                            message='Ожидание: значка времени справа внизу нет.'
                        elif state=='letter_unreadable':
                            message='Значок времени найден, но буква пока не читается.'
                        elif state=='word_in_field':
                            message='В поле несколько букв. Жду принятия или подтверждённого отказа.'
                        else:
                            readings=[a['text'].replace('\n',' / ') for a in diagnostic['nick_attempts'] if a['text']]
                            message='Карточка есть, твой ник пока не подтверждён. OCR: '+(' | '.join(readings)[:160] or 'пусто')
                        self.notify(message)
                    if allowed:
                        with learning.lock:
                            ordering=(self.word_order,learning.revision)
                            if last_order!=ordering:
                                last_order=ordering
                                order_bank(bank,ordering[0])
                        # Table OCR runs independently and must never hold a turn
                        # hostage. Give it one short opportunity, then type.
                        await asyncio.to_thread(tracker.wait_idle,.15)
                        shot = capture.grab(region)
                        captured_at=time.monotonic()
                        fresh = Image.frombytes('RGB', shot.size, shot.rgb)
                        if await reader.read(fresh,nickname) != letter:
                            continue
                        # Confirm again immediately before typing.
                        current = game_window()
                        if current is None or current[0] != hwnd or current[1] != region:
                            self.notify('Отправка отменена: фокус или положение окна игры изменились.')
                            continue
                        retrying=letter==retry_letter
                        seconds=timer.seconds(fresh)
                        urgent=seconds is not None and seconds<=3
                        endings=()
                        if not urgent and self.extra_mode==EXTRA_MODES[2] and training_match:
                            endings=ledger.training_endings(bank,ALPHABET)
                        elif not urgent and self.extra_mode==EXTRA_MODES[1]:
                            with learning.lock:
                                if ending_revision!=learning.revision:
                                    ranked_endings=ending_priority(bank,ALPHABET)
                                    ending_revision=learning.revision
                                endings=ranked_endings
                        word = ledger.reserve_next(letter,bank,shortest_first=urgent or retrying or self.word_order==WORD_ORDERS[1],
                                                   preferred_endings=endings,longest_first=self.word_order==WORD_ORDERS[2])
                        retry_letter=None
                        gate.mark(letter)
                        if word is None:
                            self.notify('На «' + letter.upper() + '» слова закончились.')
                        else:
                            # Reserve before typing: partial/failed attempts are never repeated.
                            baseline=dict(reader.last)
                            self.notify(f'Ввожу: {word}; со второй буквы, с проверкой поля.')
                            remaining=None if seconds is None else seconds-(time.monotonic()-captured_at)
                            plan=None if turbo else paced_input(word,remaining,retrying or urgent)
                            sent=send_word(word,hwnd,delay,self.stopped,mode,submit=False,fast=True,plan=plan,turbo=turbo)
                            if sent:
                                sent=await confirm_input(reader,capture,hwnd,self.stopped,word)
                            target=3 if loss_deadline is not None else 6 if self.pace_mode==PACE_MODES[1] else None
                            if sent and target is not None and seconds is not None and seconds>target and not retrying:
                                sent=await wait_to_submit(timer,capture,hwnd,self.stopped,self.notify,target,loss_deadline)
                                if sent:
                                    sent=await confirm_input(reader,capture,hwnd,self.stopped,word)
                            if loss_deadline is not None and time.monotonic()>=loss_deadline:
                                sent=False
                            if sent and can_type(hwnd,self.stopped):
                                press(0x1c,TURBO_HOLD if turbo else .022,self.stopped,hwnd)
                            else:
                                sent=False
                            if sent:
                                pending=PendingAnswer(word,letter,baseline)
                                self.notify(f'Enter отправлен: {word} · исключено {ledger.counts()["blocked"]}; принято на столе {ledger.counts()["accepted"]}')
                            elif user32.GetAsyncKeyState(0x77)&0x8000:
                                self.stopped.set()
                            elif not self.stopped.is_set():
                                cleanup_letter=letter
                                cleanup_urgent=False
                                self.notify('Ввод прерван. Когда Roblox снова активен, проверю и очищу поле.')
                    self.stopped.wait(.015 if turbo else .055)
        try:
            asyncio.run(loop())
        except Exception as exc:
            self.diagnostics.logger.exception('Ошибка рабочего цикла')
            self.notify('Ошибка: ' + str(exc))
        finally:
            self.stopped.set()
            clock=self.match_clock
            if clock is not None and clock[1] is None:
                self.match_clock=(clock[0],time.monotonic())
            recorder.close()
            tracker.close()
            self.diagnostics.event('Рабочий цикл остановлен')
