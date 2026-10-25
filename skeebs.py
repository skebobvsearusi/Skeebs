import sys, os, json, time, threading, subprocess, shutil, re, copy, ctypes, wave
from pathlib import Path
if sys.stderr is None:
    diagnostic=Path(os.environ.get('LOCALAPPDATA','.'))/'Skeebs'
    diagnostic.mkdir(parents=True,exist_ok=True)
    sys.stderr=open(diagnostic/'application.log','a',encoding='utf-8',buffering=1)
if sys.stdout is None:sys.stdout=sys.stderr
import cv2
import mss
import soundcard as sc
import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QRectF
from PySide6.QtGui import QImage, QPainter, QColor, QFont, QPixmap, QPen, QIcon
from PySide6.QtWidgets import *

ROOT = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
DATA = Path(os.environ.get('LOCALAPPDATA', str(ROOT))) / 'Skeebs'
DATA.mkdir(parents=True, exist_ok=True)
FFMPEG = str(ROOT / 'ffmpeg.exe') if (ROOT / 'ffmpeg.exe').exists() else shutil.which('ffmpeg')
HIDE = 0x08000000

class CursorInfo(ctypes.Structure):
    _fields_=[('size',ctypes.c_uint32),('flags',ctypes.c_uint32),('handle',ctypes.c_void_p),('x',ctypes.c_long),('y',ctypes.c_long)]
class IconInfo(ctypes.Structure):
    _fields_=[('icon',ctypes.c_int),('hotx',ctypes.c_uint32),('hoty',ctypes.c_uint32),('mask',ctypes.c_void_p),('color',ctypes.c_void_p)]
class NativeBitmap(ctypes.Structure):
    _fields_=[('type',ctypes.c_long),('width',ctypes.c_long),('height',ctypes.c_long),('stride',ctypes.c_long),('planes',ctypes.c_ushort),('bits',ctypes.c_ushort),('data',ctypes.c_void_p)]

def cursor_patch(picture,bounds,cursor_info=None):
    """Draw the actual Windows cursor over its underlying pixels, including XOR cursors."""
    import struct
    u=ctypes.windll.user32;g=ctypes.windll.gdi32;ptr=ctypes.c_void_p
    u.GetCursorInfo.argtypes=[ctypes.POINTER(CursorInfo)]
    u.GetIconInfo.argtypes=[ptr,ctypes.POINTER(IconInfo)]
    g.GetObjectW.argtypes=[ptr,ctypes.c_int,ptr]
    g.CreateCompatibleDC.argtypes=[ptr];g.CreateCompatibleDC.restype=ptr
    g.CreateDIBSection.argtypes=[ptr,ptr,ctypes.c_uint,ctypes.POINTER(ptr),ptr,ctypes.c_uint32];g.CreateDIBSection.restype=ptr
    g.SelectObject.argtypes=[ptr,ptr];g.SelectObject.restype=ptr
    g.DeleteObject.argtypes=[ptr];g.DeleteDC.argtypes=[ptr]
    u.DrawIconEx.argtypes=[ptr,ctypes.c_int,ctypes.c_int,ptr,ctypes.c_int,ctypes.c_int,ctypes.c_uint,ptr,ctypes.c_uint]
    ci=cursor_info or CursorInfo();ci.size=ctypes.sizeof(ci)
    if cursor_info is None and not u.GetCursorInfo(ctypes.byref(ci)):return None
    if not ci.flags&1:return None
    ii=IconInfo()
    if not u.GetIconInfo(ci.handle,ctypes.byref(ii)):return None
    dc=dib=old=None
    try:
        bm=NativeBitmap()
        if not g.GetObjectW(ii.color or ii.mask,ctypes.sizeof(bm),ctypes.byref(bm)):return None
        cw=bm.width;ch=bm.height if ii.color else bm.height//2
        cx=ci.x-bounds['left']-ii.hotx;cy=ci.y-bounds['top']-ii.hoty
        x=max(0,cx);y=max(0,cy);w=min(picture.width(),cx+cw)-x;h=min(picture.height(),cy+ch)-y
        if w<=0 or h<=0:return None
        base=picture.copy(x,y,w,h).convertToFormat(QImage.Format.Format_RGB32)
        header=ctypes.create_string_buffer(struct.pack('<IiiHHIIiiII',40,w,-h,1,32,0,w*h*4,0,0,0,0))
        dc=g.CreateCompatibleDC(None);bits=ptr()
        dib=g.CreateDIBSection(dc,header,0,ctypes.byref(bits),None,0)
        if not dib:return None
        old=g.SelectObject(dc,dib);ctypes.memmove(bits,bytes(base.constBits()),w*h*4)
        if not u.DrawIconEx(dc,cx-x,cy-y,ci.handle,cw,ch,0,None,3):return None
        data=ctypes.string_at(bits,w*h*4)
        patch=QImage(data,w,h,w*4,QImage.Format.Format_RGB32).copy()
        return patch,x,y
    finally:
        if old:g.SelectObject(dc,old)
        if dib:g.DeleteObject(dib)
        if dc:g.DeleteDC(dc)
        if ii.mask:g.DeleteObject(ii.mask)
        if ii.color:g.DeleteObject(ii.color)

def source(kind, name, **kw):
    return dict(kind=kind, name=name, x=0., y=0., w=1., h=1., visible=True, opacity=100, **kw)

class Events(QObject):
    frame = Signal(QImage)
    error = Signal(str)
    stopped = Signal(str)

class Engine:
    def __init__(self):
        self.events = Events(); self.layers = []; self.size = (1280,720); self.fps = 30
        self.preview_fps=30; self.latest_frame=None; self.captured=0; self.repeated=0
        self.desktop=None;self.capture_backend='GDI'
        self.running = True; self.recording = False; self.preview = True; self.proc = None
        self.stop_request = False; self.frames = 0; self.late = 0; self.cameras = {}; self.images = {}
        self.thread = threading.Thread(target=self.loop, daemon=True); self.thread.start()

    def start(self, path, audio, quality, encoder):
        w,h = self.size
        cmd = [FFMPEG, '-hide_banner','-y','-thread_queue_size','128','-f','rawvideo','-pixel_format','bgra','-video_size',f'{w}x{h}','-framerate',str(self.fps),'-i','pipe:0']
        cmd += ['-map','0:v:0']
        if encoder == 'Intel Quick Sync': cmd += ['-c:v','h264_qsv','-preset','veryfast','-global_quality',str(quality)]
        else: cmd += ['-c:v','libx264','-preset','ultrafast','-crf',str(quality),'-threads','2']
        cmd += ['-pix_fmt','nv12' if encoder == 'Intel Quick Sync' else 'yuv420p']
        if str(path).endswith('.mp4'): cmd += ['-movflags','+faststart']
        cmd += [str(path)]
        self.log = open(DATA/'encoder.log','w',encoding='utf-8')
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=self.log, creationflags=HIDE)
        self.path = str(path); self.frames = self.late = self.captured = self.repeated = 0; self.started = time.monotonic()
        self.audio_stop=threading.Event(); self.audio_error=''; self.audio_thread=None
        if audio:
            self.audio_path=DATA/('audio_'+str(time.time_ns())+'.wav')
            self.audio_thread=threading.Thread(target=self.capture_audio,args=(audio,),daemon=True); self.audio_thread.start()
        self.stop_request = False; self.recording = True

    def capture_audio(self,device):
        try:
            mic=sc.get_microphone(id=device['id'],include_loopback=True)
            with wave.open(str(self.audio_path),'wb') as wav:
                wav.setnchannels(2); wav.setsampwidth(2); wav.setframerate(48000)
                with mic.recorder(samplerate=48000,channels=2,blocksize=2048) as recorder:
                    next_chunk=time.monotonic()
                    while not self.audio_stop.is_set():
                        samples=recorder.record(numframes=2048)
                        wav.writeframes((np.clip(samples,-1,1)*32767).astype('<i2').tobytes())
                        next_chunk+=len(samples)/48000
                        self.audio_stop.wait(max(0,next_chunk-time.monotonic()))
        except Exception as e:self.audio_error=str(e)

    def compose(self, grab):
        w,h = self.size
        active={s.get('device',0) for s in self.layers if s['kind']=='Камера' and s['visible']}
        for idx in list(self.cameras):
            if idx not in active:self.cameras.pop(idx).release()
        img = QImage(w,h,QImage.Format.Format_RGB32); img.fill(QColor('#101114'))
        p = QPainter(img); p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        screens={}
        for s in copy.deepcopy(self.layers):
            if not s['visible']: continue
            r = QRectF(s['x']*w,s['y']*h,s['w']*w,s['h']*h)
            p.setOpacity(s['opacity']/100)
            try:
                k = s['kind']; picture = None
                if k == 'Область экрана':
                    bounds=s['region'];frame=None
                    primary=next((m for m in grab.monitors[1:] if m.get('is_primary')),grab.monitors[1])
                    inside=bounds['left']>=primary['left'] and bounds['top']>=primary['top'] and bounds['left']+bounds['width']<=primary['left']+primary['width'] and bounds['top']+bounds['height']<=primary['top']+primary['height']
                    if self.desktop is not None and inside:
                        try:
                            frame=self.desktop.grab(new_frame_only=False)
                            if frame is not None:
                                x=bounds['left']-primary['left'];y=bounds['top']-primary['top']
                                frame=np.ascontiguousarray(frame[y:y+bounds['height'],x:x+bounds['width']])
                        except Exception:frame=None
                    if frame is not None:picture=QImage(frame.data,frame.shape[1],frame.shape[0],frame.strides[0],QImage.Format.Format_RGB32)
                    else:
                        shot=grab.grab(bounds);picture=QImage(shot.raw,shot.width,shot.height,QImage.Format.Format_RGB32)
                elif k == 'Экран':
                    idx = min(s.get('monitor',1),len(grab.monitors)-1)
                    if idx not in screens:
                        frame=None
                        if self.desktop is not None and idx>0 and grab.monitors[idx].get('is_primary',idx==1):
                            try:frame=self.desktop.grab(new_frame_only=False)
                            except Exception:self.capture_backend='GDI (резерв)'
                        if frame is not None:screens[idx]=frame
                        else:screens[idx]=grab.grab(grab.monitors[idx])
                    shot=screens[idx]
                    if isinstance(shot,np.ndarray):picture=QImage(shot.data,shot.shape[1],shot.shape[0],shot.strides[0],QImage.Format.Format_RGB32)
                    else:picture = QImage(shot.raw,shot.width,shot.height,QImage.Format.Format_RGB32)
                elif k == 'Камера':
                    idx = s.get('device',0)
                    if idx not in self.cameras:
                        cap = cv2.VideoCapture(idx,cv2.CAP_DSHOW); cap.set(cv2.CAP_PROP_FRAME_WIDTH,640); cap.set(cv2.CAP_PROP_FRAME_HEIGHT,480); self.cameras[idx]=cap
                    ok, frame = self.cameras[idx].read()
                    if ok:
                        frame = cv2.cvtColor(frame,cv2.COLOR_BGR2BGRA)
                        picture = QImage(frame.data,frame.shape[1],frame.shape[0],frame.strides[0],QImage.Format.Format_RGB32).copy()
                    else:
                        p.fillRect(r,QColor('#302b3a')); p.setPen(QColor('#dddddd')); p.drawText(r,Qt.AlignmentFlag.AlignCenter,'Камера недоступна')
                elif k == 'Изображение':
                    path = s['path']
                    if path not in self.images: self.images[path] = QImage(path)
                    picture = self.images[path]
                elif k == 'Цвет': p.fillRect(r,QColor(s.get('color','#7657ff')))
                elif k == 'Текст':
                    p.setPen(QColor(s.get('color','#ffffff'))); p.setFont(QFont('Segoe UI',max(8,int(s.get('font',36)*h/720))))
                    p.drawText(r,Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap,s.get('text','Skeebs'))
                if picture is not None and not picture.isNull():
                    fit = picture.size().scaled(int(r.width()),int(r.height()),Qt.AspectRatioMode.KeepAspectRatio)
                    dest = QRectF(r.x()+(r.width()-fit.width())/2,r.y()+(r.height()-fit.height())/2,fit.width(),fit.height())
                    p.drawImage(dest,picture)
                    if k in ('Экран','Область экрана'):
                        capture_bounds=s['region'] if k=='Область экрана' else grab.monitors[idx]
                        cursor=cursor_patch(picture,capture_bounds)
                        if cursor:
                            patch,cx,cy=cursor;sx=dest.width()/picture.width();sy=dest.height()/picture.height()
                            p.drawImage(QRectF(dest.x()+cx*sx,dest.y()+cy*sy,patch.width()*sx,patch.height()*sy),patch)
            except Exception: pass
        p.end(); return img

    def finish(self):
        proc = self.proc
        try:
            proc.stdin.close(); self.audio_stop.set(); code = proc.wait(timeout=30)
            if self.audio_thread:
                self.audio_thread.join(timeout=8)
                if self.audio_thread.is_alive():self.audio_error='Аудиоустройство не завершило запись.'
            self.log.close()
            if code: self.events.error.emit('Кодировщик завершился с ошибкой. Попробуйте CPU / ultrafast.\n'+(DATA/'encoder.log').read_text(encoding='utf-8',errors='replace')[-1800:])
            elif self.audio_error:self.events.error.emit('Видео сохранено без звука. Ошибка аудиоустройства: '+self.audio_error)
            else:
                if self.audio_thread:
                    target=Path(self.path); mixed=target.with_name(target.stem+'.mix'+target.suffix)
                    result=subprocess.run([FFMPEG,'-hide_banner','-y','-i',self.path,'-i',str(self.audio_path),'-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-b:a','160k','-af','apad','-shortest',str(mixed)],capture_output=True,creationflags=HIDE,timeout=120)
                    if result.returncode:raise RuntimeError('Видео сохранено, но звук не присоединён: '+result.stderr.decode('utf-8',errors='replace')[-900:])
                    mixed.replace(target); self.audio_path.unlink(missing_ok=True)
                self.events.stopped.emit(self.path)
        except Exception as e:
            proc.kill(); self.events.error.emit(str(e))
        finally: self.proc = None; self.recording = False; self.stop_request = False

    def loop(self):
        try:
            import dxcam
            self.desktop=dxcam.create(output_color='BGRA',max_buffer_len=2)
            self.capture_backend='DirectX'
        except Exception:self.desktop=None
        with mss.MSS() as grab:
            deadline=time.monotonic()
            while self.running:
                t = time.monotonic()
                if self.recording and self.stop_request: self.finish(); continue
                if not self.recording and not self.preview: time.sleep(.05); deadline=time.monotonic(); continue
                try:
                    img = self.compose(grab)
                    if self.recording:
                        self.captured += 1
                        target = int((time.monotonic()-self.started)*self.fps)+1
                        count=max(1,target-self.frames)
                        self.repeated += count-1
                        for _ in range(count):
                            self.proc.stdin.write(img.constBits())
                            self.frames += 1
                    if self.preview:self.latest_frame=img
                except Exception as e:
                    print('Capture loop:',repr(e),file=sys.stderr)
                    if self.recording: self.finish()
                    else: time.sleep(.3)
                interval = 1/self.fps if self.recording else 1/self.preview_fps
                elapsed = time.monotonic()-t
                if self.recording and elapsed > interval: self.late += 1
                deadline+=interval
                now=time.monotonic()
                if deadline<now:deadline=now
                time.sleep(max(.001,deadline-now))
        for cap in self.cameras.values(): cap.release()
        if self.desktop is not None:self.desktop.release()

class Preview(QWidget):
    moved = Signal(float,float)
    def __init__(self):
        super().__init__(); self.img=None; self.selected=None; self.box=QRectF(); self.drag=None
        self.setMinimumSize(400,225)
    def paintEvent(self,event):
        p=QPainter(self); p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform); p.fillRect(self.rect(),QColor('#111215'))
        if self.img:
            sz=self.img.size().scaled(self.size(),Qt.AspectRatioMode.KeepAspectRatio)
            self.box=QRectF((self.width()-sz.width())/2,(self.height()-sz.height())/2,sz.width(),sz.height())
            p.drawImage(self.box,self.img)
            if self.selected:
                s=self.selected; p.setPen(QPen(QColor('#ac9aff'),2))
                p.drawRect(QRectF(self.box.x()+s['x']*sz.width(),self.box.y()+s['y']*sz.height(),s['w']*sz.width(),s['h']*sz.height()))
        else:
            p.setPen(QColor('#888b95')); p.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter,'SKEEBS  /  ПРЕДПРОСМОТР')
    def mousePressEvent(self,e):
        if self.selected: self.drag=(e.position(),self.selected['x'],self.selected['y'])
    def mouseMoveEvent(self,e):
        if self.drag and self.box.width():
            origin,x,y=self.drag; d=e.position()-origin
            self.moved.emit(max(-.9,min(.99,x+d.x()/self.box.width())),max(-.9,min(.99,y+d.y()/self.box.height())))
    def mouseReleaseEvent(self,e): self.drag=None

class RegionPicker(QDialog):
    def __init__(self):
        super().__init__();self.setWindowFlags(Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground);self.setCursor(Qt.CursorShape.CrossCursor)
        self.setGeometry(QApplication.primaryScreen().virtualGeometry());self.origin=None;self.selection=QRectF();self.region=None
    def paintEvent(self,event):
        p=QPainter(self);p.fillRect(self.rect(),QColor(10,12,18,100))
        if not self.selection.isNull():
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear);p.fillRect(self.selection,QColor(0,0,0,0));p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            p.setPen(QPen(QColor('#bba8ff'),2));p.drawRect(self.selection)
        p.setPen(QColor('white'));p.setFont(QFont('Segoe UI',16));p.drawText(30,45,'Выделите область мышью · Esc — отмена')
    def mousePressEvent(self,event):self.origin=event.position();self.selection=QRectF(self.origin,self.origin);self.update()
    def mouseMoveEvent(self,event):
        if self.origin is not None:self.selection=QRectF(self.origin,event.position()).normalized();self.update()
    def mouseReleaseEvent(self,event):
        if self.origin is None:return
        self.selection=QRectF(self.origin,event.position()).normalized()
        if self.selection.width()<16 or self.selection.height()<16:self.reject();return
        # Qt uses device-independent coordinates; map both corners through their monitor.
        def physical(point):
            global_point=self.mapToGlobal(point.toPoint());screen=QApplication.screenAt(global_point) or QApplication.primaryScreen();geo=screen.geometry();ratio=screen.devicePixelRatio()
            with mss.MSS() as capture:
                monitors=capture.monitors[1:];screens=QApplication.screens();index=screens.index(screen)
                monitor=monitors[min(index,len(monitors)-1)]
            return (monitor['left']+round((global_point.x()-geo.x())*ratio),monitor['top']+round((global_point.y()-geo.y())*ratio))
        left,top=physical(self.selection.topLeft());right,bottom=physical(self.selection.bottomRight())
        width=(right-left)//2*2;height=(bottom-top)//2*2
        if width<16 or height<16:self.reject();return
        self.region=dict(left=left,top=top,width=width,height=height);self.accept()

class Window(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle('Skeebs — студия записи'); self.setWindowIcon(QIcon(str(ROOT/'skeebs.ico'))); self.resize(1220,800)
        self.engine=Engine(); self.scenes=[{'name':'Основная сцена','layers':[source('Экран','Экран 1',monitor=1)]}]; self.current=0; self.loading=False
        try:
            saved=json.loads((DATA/'project.json').read_text(encoding='utf-8')); self.scenes=saved['scenes']
        except Exception: pass
        root=QWidget(); self.setCentralWidget(root); layout=QVBoxLayout(root); layout.setContentsMargins(18,14,18,12)
        bar=QHBoxLayout();mark=QLabel();mark.setPixmap(QPixmap(str(ROOT/'skeebs-icon.png')).scaled(36,36,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation));bar.addWidget(mark);logo=QLabel('SKEEBS'); logo.setStyleSheet('font-size:23px;font-weight:700;color:#b5a2ff'); bar.addWidget(logo)
        bar.addWidget(QLabel('   /   RECORDING STUDIO')); bar.addStretch(); self.badge=QLabel('●  ГОТОВ К ЗАПИСИ'); bar.addWidget(self.badge); layout.addLayout(bar)
        main=QSplitter(); layout.addWidget(main,1)
        center=QWidget(); cl=QVBoxLayout(center); cl.setContentsMargins(0,0,8,0)
        tools=QHBoxLayout(); tools.addWidget(QLabel('ПРОГРАММА')); tools.addStretch(); self.prev=QCheckBox('Предпросмотр'); self.prev.setChecked(True); self.prev.toggled.connect(self.preview_toggle); tools.addWidget(self.prev)
        self.preview_rate=QComboBox();self.preview_rate.addItems(['30 FPS','15 FPS','60 FPS']);self.preview_rate.currentTextChanged.connect(self.set_preview_rate);tools.addWidget(self.preview_rate);self.region_button=self.button(tools,'Область…',self.record_region);cl.addLayout(tools)
        self.preview=Preview(); cl.addWidget(self.preview,1); self.preview.moved.connect(self.move_source)
        cl.addWidget(QLabel('Перетащите выбранный слой • Размер и прозрачность — в свойствах справа'))
        main.addWidget(center)
        side=QWidget(); side.setMaximumWidth(310); side.setMinimumWidth(270); sl=QVBoxLayout(side)
        sl.addWidget(QLabel('НАСТРОЙКИ ЗАПИСИ')); form=QFormLayout(); sl.addLayout(form)
        self.res=QComboBox(); self.res.addItems(['1280 × 720','854 × 480','1920 × 1080']); form.addRow('Разрешение',self.res)
        self.fps=QComboBox(); self.fps.addItems(['30','25','15','60']); form.addRow('Кадров/с',self.fps)
        self.fmt=QComboBox(); self.fmt.addItems(['mkv','mp4']); form.addRow('Формат',self.fmt)
        self.enc=QComboBox(); self.enc.addItems(['CPU / ultrafast','Intel Quick Sync']); self.enc.setCurrentIndex(1); form.addRow('Кодировщик',self.enc)
        self.quality=QSpinBox(); self.quality.setRange(16,35); self.quality.setValue(20); form.addRow('CRF / качество',self.quality)
        self.audio=QComboBox(); self.audio.addItem('Без звука',''); form.addRow('Аудиовход',self.audio)
        refresh=QPushButton('Обновить аудиоустройства'); refresh.clicked.connect(self.devices); sl.addWidget(refresh)
        note=QLabel('720p / 30 FPS · качество 20. Для чёткого мелкого текста выберите разрешение вашего экрана. Меньше CRF — выше качество.\n\nЗвук Windows — выход динамиков.'); note.setWordWrap(True); note.setStyleSheet('color:#9699a5'); sl.addWidget(note)
        sl.addWidget(QLabel('СВОЙСТВА СЛОЯ')); props=QFormLayout(); sl.addLayout(props); self.props={}
        for key,label in [('x','X, %'),('y','Y, %'),('w','Ширина, %'),('h','Высота, %'),('opacity','Непрозрачность')]:
            spin=QDoubleSpinBox(); spin.setRange(-100 if key in ['x','y'] else 1,200 if key!='opacity' else 100); spin.setDecimals(1); spin.valueChanged.connect(self.edit_props); props.addRow(label,spin); self.props[key]=spin
        sl.addStretch(); main.addWidget(side)
        bottom=QHBoxLayout(); layout.addLayout(bottom)
        scenes_box=QGroupBox('СЦЕНЫ'); sb=QVBoxLayout(scenes_box); self.scene_list=QListWidget(); self.scene_list.setMaximumHeight(125); sb.addWidget(self.scene_list); self.scene_list.currentRowChanged.connect(self.select_scene)
        row=QHBoxLayout(); sb.addLayout(row); self.button(row,'+ Сцена',self.add_scene); self.button(row,'−',self.delete_scene); bottom.addWidget(scenes_box,1)
        layers_box=QGroupBox('ИСТОЧНИКИ / СЛОИ · верхний перекрывает нижний'); lb=QVBoxLayout(layers_box); self.layers=QListWidget(); self.layers.setMaximumHeight(125); lb.addWidget(self.layers); self.layers.currentRowChanged.connect(self.select_layer); self.layers.itemChanged.connect(self.visibility)
        self.layers.itemDoubleClicked.connect(self.edit_source)
        row=QHBoxLayout(); lb.addLayout(row); self.button(row,'+ Источник',self.add_source); self.button(row,'↑',lambda:self.reorder(1)); self.button(row,'↓',lambda:self.reorder(-1)); self.button(row,'−',self.remove_source); bottom.addWidget(layers_box,2)
        controls=QGroupBox('УПРАВЛЕНИЕ'); cb=QVBoxLayout(controls); self.record=QPushButton('●  Начать запись'); self.record.setMinimumHeight(46); self.record.setStyleSheet('background:#7854de;font-weight:700'); self.record.clicked.connect(self.toggle_record); cb.addWidget(self.record)
        self.folder=ROOT/'save video'; self.folder.mkdir(parents=True,exist_ok=True)
        self.button(cb,'Папка записей ↗',lambda:os.startfile(self.folder)); self.button(cb,'Выбрать папку…',self.choose_folder); bottom.addWidget(controls,1)
        self.status=QLabel('Skeebs 1.1  •  Область экрана / DirectX  •  MKV рекомендуется'); layout.addWidget(self.status)
        self.engine.events.frame.connect(self.frame); self.engine.events.error.connect(self.error); self.engine.events.stopped.connect(self.stopped)
        self.timer=QTimer(self); self.timer.timeout.connect(self.tick); self.timer.start(500)
        self.preview_timer=QTimer(self);self.preview_timer.timeout.connect(self.poll_preview);self.preview_timer.start(33)
        self.scene_list.addItems([s['name'] for s in self.scenes]); self.scene_list.setCurrentRow(0)
        self.res.currentTextChanged.connect(self.settings); self.fps.currentTextChanged.connect(self.settings)
        QTimer.singleShot(500,self.devices)

    def button(self,layout,text,fn):
        b=QPushButton(text); b.clicked.connect(fn); layout.addWidget(b); return b
    def settings(self):
        self.engine.size=tuple(map(int,self.res.currentText().split(' × '))); self.engine.fps=int(self.fps.currentText())
    def devices(self):
        self.audio.clear(); self.audio.addItem('Без звука','')
        try:
            for mic in sc.all_microphones(include_loopback=True):self.audio.addItem(('Звук Windows: ' if mic.isloopback else 'Вход: ')+mic.name,{'id':mic.id})
        except Exception: pass
    def scene(self): return self.scenes[self.current]['layers']
    def selected(self):
        i=self.layers.currentRow(); items=self.scene()
        return items[len(items)-1-i] if 0<=i<len(items) else None
    def sync(self): self.engine.layers=copy.deepcopy(self.scene()); self.save()
    def save(self):
        temp=DATA/'project.tmp'; temp.write_text(json.dumps({'scenes':self.scenes},ensure_ascii=False,indent=2),encoding='utf-8'); temp.replace(DATA/'project.json')
    def select_scene(self,i):
        if i<0:return
        self.current=i; self.reload(); self.sync()
    def reload(self):
        self.layers.blockSignals(True); self.layers.clear()
        for s in reversed(self.scene()):
            item=QListWidgetItem(s['name']); item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable); item.setCheckState(Qt.CheckState.Checked if s['visible'] else Qt.CheckState.Unchecked); self.layers.addItem(item)
        self.layers.blockSignals(False); self.layers.setCurrentRow(0); self.select_layer()
    def select_layer(self,*args):
        s=self.selected(); self.preview.selected=s; self.loading=True
        for k,spin in self.props.items(): spin.setEnabled(s is not None); spin.setValue((s[k] if k=='opacity' else s[k]*100) if s else 0)
        self.loading=False; self.preview.update()
    def edit_props(self):
        if self.loading:return
        s=self.selected()
        if s:
            for k,spin in self.props.items():s[k]=spin.value() if k=='opacity' else spin.value()/100
            self.sync(); self.preview.update()
    def move_source(self,x,y):
        s=self.selected()
        if s:s['x']=x; s['y']=y; self.select_layer(); self.sync()
    def visibility(self,item):
        self.scene()[len(self.scene())-1-self.layers.row(item)]['visible']=item.checkState()==Qt.CheckState.Checked; self.sync()
    def add_scene(self):
        name,ok=QInputDialog.getText(self,'Новая сцена','Название:')
        if ok and name.strip():self.scenes.append({'name':name,'layers':[]}); self.scene_list.addItem(name); self.scene_list.setCurrentRow(len(self.scenes)-1)
    def record_region(self):
        if self.engine.recording:return
        self.hide();app=QApplication.instance();app.processEvents()
        picker=RegionPicker();ok=picker.exec();self.show();self.raise_()
        if ok!=QDialog.DialogCode.Accepted:return
        bounds=picker.region;name=f"Область {bounds['width']} × {bounds['height']}"
        self.scenes.append({'name':name,'layers':[source('Область экрана',name,region=bounds)]});self.scene_list.addItem(name);self.scene_list.setCurrentRow(len(self.scenes)-1)
        resolution=f"{bounds['width']} × {bounds['height']}"
        if self.res.findText(resolution)<0:self.res.addItem(resolution)
        self.res.setCurrentText(resolution);self.settings();self.status.setText('Область выбрана. Нажмите «Начать запись».')
    def delete_scene(self):
        if len(self.scenes)<2:return
        i=self.current; self.scene_list.blockSignals(True); self.scenes.pop(i); self.scene_list.takeItem(i); self.scene_list.blockSignals(False); self.scene_list.setCurrentRow(0); self.select_scene(0)
    def add_source(self):
        kind,ok=QInputDialog.getItem(self,'Добавить источник','Тип:',['Экран','Камера','Изображение','Текст','Цвет'],0,False)
        if not ok:return
        s=source(kind,kind)
        if kind=='Экран':
            with mss.mss() as grab: count=len(grab.monitors)-1
            n,ok=QInputDialog.getInt(self,'Монитор','Номер монитора (0 = все):',1,0,count)
            if not ok:return
            s['monitor']=n; s['name']=f'Экран {n}'
        elif kind=='Камера':
            n,ok=QInputDialog.getInt(self,'Камера','Номер устройства (обычно 0):',0,0,8)
            if not ok:return
            s.update(device=n,x=.70,y=.65,w=.28,h=.33); s['name']=f'Камера {n}'
        elif kind=='Изображение':
            path,_=QFileDialog.getOpenFileName(self,'Изображение','','Изображения (*.png *.jpg *.jpeg *.bmp *.webp)')
            if not path:return
            s['path']=path; s['name']=Path(path).name
        elif kind=='Текст':
            text,ok=QInputDialog.getMultiLineText(self,'Текст','Надпись:','Skeebs')
            if not ok:return
            s.update(text=text,font=36,color='#ffffff',x=.05,y=.8,w=.9,h=.15); s['name']=text[:30]
        elif kind=='Цвет':
            color=QColorDialog.getColor(QColor('#7854de'),self)
            if not color.isValid():return
            s['color']=color.name()
        self.scene().append(s); self.reload(); self.sync()
    def remove_source(self):
        s=self.selected()
        if s:self.scene().remove(s); self.reload(); self.sync()
    def edit_source(self,*args):
        s=self.selected()
        if not s:return
        if s['kind']=='Текст':
            value,ok=QInputDialog.getMultiLineText(self,'Изменить текст','Надпись:',s['text'])
            if ok:s['text']=value; s['name']=value[:30]
        else:
            value,ok=QInputDialog.getText(self,'Переименовать источник','Название:',text=s['name'])
            if ok and value.strip():s['name']=value
        self.reload();self.sync()
    def reorder(self,d):
        s=self.selected()
        if s:
            a=self.scene(); i=a.index(s); j=max(0,min(len(a)-1,i+d)); a[i],a[j]=a[j],a[i]; self.reload(); self.layers.setCurrentRow(len(a)-1-j); self.sync()
    def preview_toggle(self,on):self.engine.preview=on; self.preview.img=None; self.preview.update()
    def set_preview_rate(self,text):
        self.engine.preview_fps=int(text.split()[0])
        if hasattr(self,'preview_timer'):self.preview_timer.setInterval(round(1000/self.engine.preview_fps))
    def poll_preview(self):
        img=self.engine.latest_frame
        if self.engine.preview and img is not None:self.engine.latest_frame=None;self.frame(img)
    def frame(self,img):self.preview.img=img; self.preview.update()
    def choose_folder(self):
        path=QFileDialog.getExistingDirectory(self,'Папка записи',str(self.folder))
        if path:self.folder=Path(path)
    def toggle_record(self):
        if self.engine.recording:
            self.engine.stop_request=True; self.record.setEnabled(False); self.record.setText('Сохранение…'); return
        if not FFMPEG:QMessageBox.warning(self,'FFmpeg','ffmpeg.exe не найден.'); return
        self.settings(); path=self.folder/('Skeebs_'+time.strftime('%Y-%m-%d_%H-%M-%S')+'.'+self.fmt.currentText())
        try:self.engine.start(path,self.audio.currentData(),self.quality.value(),self.enc.currentText())
        except Exception as e:self.error(str(e)); return
        for w in [self.res,self.fps,self.fmt,self.enc,self.audio,self.quality,self.region_button]:w.setEnabled(False)
        self.record.setText('■  Остановить запись'); self.badge.setText('●  ЗАПИСЬ'); self.badge.setStyleSheet('color:#ff657d')
    def reset(self):
        self.record.setEnabled(True); self.record.setText('●  Начать запись'); self.badge.setText('●  ГОТОВ К ЗАПИСИ'); self.badge.setStyleSheet('')
        for w in [self.res,self.fps,self.fmt,self.enc,self.audio,self.quality,self.region_button]:w.setEnabled(True)
    def stopped(self,path):self.reset(); self.status.setText('Сохранено: '+path)
    def error(self,message):self.reset(); QMessageBox.warning(self,'Skeebs',message)
    def tick(self):
        if self.engine.recording:
            e=self.engine; elapsed=time.monotonic()-e.started; seconds=int(elapsed); actual=e.captured/max(.001,elapsed)
            self.status.setText(f'REC  {seconds//60:02}:{seconds%60:02}   •   {e.capture_backend}   •   {e.size[0]} × {e.size[1]}   •   Захват: {actual:.1f} / {e.fps} FPS   •   Повторов: {e.repeated}')
    def closeEvent(self,event):
        if self.engine.recording:
            self.engine.stop_request=True; self.record.setText('Сохранение…'); event.ignore(); return
        self.save(); self.engine.running=False; self.engine.thread.join(timeout=2); event.accept()

STYLE='''QWidget{background:#232429;color:#dedee6;font-family:Segoe UI;font-size:12px} QMainWindow{background:#232429} QGroupBox{border:1px solid #3b3c45;border-radius:6px;margin-top:12px;padding-top:12px;font-weight:600} QGroupBox::title{subcontrol-origin:margin;left:12px} QPushButton{background:#35363f;border:1px solid #464752;border-radius:5px;padding:7px 10px} QPushButton:hover{background:#494452;border-color:#9981eb} QPushButton:disabled{color:#73747c} QComboBox,QSpinBox,QDoubleSpinBox{background:#191a1f;border:1px solid #41424c;border-radius:4px;padding:5px} QListWidget{background:#191a1f;border:0;border-radius:4px;padding:4px} QListWidget::item{padding:6px} QListWidget::item:selected{background:#493a70;color:white} QSplitter::handle{background:#303138} QCheckBox{color:#aaaab8} QToolTip{background:#34313f;color:white}'''

if __name__=='__main__':
    app=QApplication(sys.argv); app.setWindowIcon(QIcon(str(ROOT/'skeebs.ico'))); app.setStyle('Fusion'); app.setStyleSheet(STYLE)
    window=Window()
    if '--verify' in sys.argv:
        target=Path(sys.argv[sys.argv.index('--verify')+1]).resolve()
        window.engine.size=(640,360);window.engine.layers=[source('Область экрана','Test',region=dict(left=100,top=100,width=640,height=360))]
        def verify_done(path):
            target.with_suffix('.json').write_text(json.dumps({'ok':True,'file':path,'backend':window.engine.capture_backend,'captured':window.engine.captured,'repeated':window.engine.repeated}),encoding='utf-8'); window.engine.running=False; app.quit()
        def verify_error(message):
            target.with_suffix('.json').write_text(json.dumps({'ok':False,'error':message}),encoding='utf-8'); window.engine.running=False; app.exit(1)
        window.engine.events.error.disconnect();window.engine.events.error.connect(verify_error)
        window.engine.events.stopped.connect(verify_done)
        QTimer.singleShot(2000,lambda:window.engine.start(target,{'id':sc.default_speaker().id},20,'Intel Quick Sync'))
        QTimer.singleShot(8000,lambda:setattr(window.engine,'stop_request',True))
        QTimer.singleShot(30000,lambda:app.exit(2))
    else:window.show()
    sys.exit(app.exec())

