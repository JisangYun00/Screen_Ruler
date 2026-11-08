#!/usr/bin/env python3
"""떠 있는 화면 자 (X11 + tkinter). 실물(A4 등)로 가로·세로 피치를 따로 보정해 mm 로 잰다.

    python3 screen_ruler.py [--monitor DP-4] [--length 320] [--angle 0]

모든 기능은 자 위 버튼으로 할 수 있다 (? 버튼 = 도움말). 보정값은 모니터·축별로
~/.config/screen_ruler.json 에 저장되고, 보정 전에는 EDID 크기로 추정한다.
"""
import argparse
import ctypes
import json
import math
import re
import subprocess
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path

CONF = Path.home() / ".config" / "screen_ruler.json"
FONT = ("Sans", 12)
THICK = 140      # 자 폭 (px)
GRIP = 18        # 길이 조절 손잡이 (px)
UI0 = GRIP + 10  # 버튼·글자 시작 위치 (손잡이와 안 겹치게)
MARGIN = 2
MIN_MM = 150     # 버튼이 다 들어가는 최소 길이
AXIS = {"x": "가로", "y": "세로"}
SIDE = {"x": ("긴 변", 297), "y": ("짧은 변", 210)}  # A4 를 가로로 눕힌 채 각 축에 대는 변
HELP = """화면 자 사용법

[재기]
 · 눈금 쪽 가장자리가 재는 면, 0 = 눈금이 시작하는 끝
 · 첫 클릭 = A 고정. 그때부터 A → 커서 거리가 계속 표시됨
 · 그다음 클릭 = B. 다시 클릭하면 B 만 옮겨짐 (여러 번 고쳐 찍기), 방향키 = B 1 px 이동
 · 확정 (Enter) → 측정 끝, 이후 클릭은 무시. 새 측정 (m) / 우클릭 / Delete = 지우고 다시 시작
 · Esc = 찍던 측정 취소
 · 드래그 = 자 이동, 방향키 = 1 px 이동 (Shift = 10 px)

[자]
 · ↺90° / ↻90°: 90° 회전 (r)
 · 마우스 휠: 1° 회전 (Shift+휠 0.1°) · , . 키: 1°
 · 각도…(a): 자 위에서 바로 숫자 입력 → Enter (Esc 취소)
   회전은 0 눈금을 중심으로 돈다
 · ⇋좌우: 0 눈금을 반대쪽 끝으로 (h) · ⇅상하: 눈금을 반대쪽 가장자리로 (v)
 · 길이: 끝의 회색 손잡이 드래그, 또는 −1cm / +1cm ([ ])
 · 투명도: + / - 키

[보정] 보정하기 (c) 한 번 → 두 단계를 차례로 (한 번 하면 모니터별로 저장)
 A4 를 가로로 눕힌 채로, 자가 알아서 0° → 90° 로 돈다
 ① 가로 (자 0°): 종이 '긴 변 (297 mm)' 을 자의 눈금 쪽 가장자리에 붙이고
    파란 선 두 개를 종이 왼쪽·오른쪽 끝에 맞춘 뒤 저장 (Enter)
 ② 세로 (자 90°): 종이 '짧은 변 (210 mm)' 을 자의 눈금 쪽 가장자리에 붙이고
    파란 선 두 개를 종이 위·아래 끝에 맞춘 뒤 저장 (Enter)
 한 쪽만 다시 할 때는 다른 단계를 건너뛰기. 끝나면 원래 각도로 돌아감
 선 이동: 드래그, 또는 선 클릭 후 방향키로 1 px씩
 다른 물체를 쓸 때만 숫자키로 길이(mm) 수정
 저장하면 아래 상태줄이 초록 ✓ 로 바뀜
 비스듬한 각도에서는 가로·세로 보정값을 합성해 쓴다

[검증] 보정 뒤 확인용 (t)
 · 자에 초록 선이 210 mm 와 297 mm 에 그려짐 (점선 = ±1 mm)
 · A4 한쪽 끝을 0 눈금에 맞추고, 반대쪽 끝이 초록 선에 오면 OK
 · 가로는 긴 변 297, 세로는 짧은 변 210 으로 (다른 변로도 교차 확인 가능)
 · 얼마나 어긋났는지는 A → B 측정으로 장 수 있음

Esc = 측정·검증·보정 취소 / 프로그램 종료"""
MON_RE = re.compile(r"(\d+)/(\d+)x(\d+)/(\d+)\+(-?\d+)\+(-?\d+)\s+(\S+)\s*$")


def parse_monitors(text):
    """`xrandr --listmonitors` 출력 → [{name, w, h, x, y, wmm, hmm}]."""
    mons = []
    for line in text.splitlines():
        m = MON_RE.search(line)
        if not m:
            continue
        w, wmm, h, hmm, x, y = map(int, m.groups()[:6])
        if (w > h and wmm < hmm) or (w < h and wmm > hmm):  # 회전된 모니터: xrandr 는 mm 를 안 바꿔 준다
            wmm, hmm = hmm, wmm
        mons.append({"name": m.group(7), "w": w, "h": h, "x": x, "y": y, "wmm": wmm, "hmm": hmm})
    return mons


def monitors():
    try:
        out = subprocess.run(["xrandr", "--listmonitors"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_monitors(out)


def mon_at(mons, x, y):
    return next((m for m in mons if m["x"] <= x < m["x"] + m["w"] and m["y"] <= y < m["y"] + m["h"]), None)


def mon_key(m):
    # shortcut: 출력 이름+해상도+EDID 크기로 모니터를 구분, 같은 모델 두 대를 같은 포트에 번갈아 꽂으면 보정값을 공유함
    return f"{m['name']} {m['w']}x{m['h']} {m['wmm']}x{m['hmm']}mm" if m else "unknown"


def axis_pitch(m, cal, axis):
    """(mm/px, 출처). 보정값 > EDID > 96 dpi."""
    e = cal.get(mon_key(m), {}).get(axis)
    if e:
        return e["mm_per_px"], "보정"
    if m and m["wmm"] and m["hmm"]:
        return (m["wmm"] / m["w"] if axis == "x" else m["hmm"] / m["h"]), "EDID"
    return 25.4 / 96, "96dpi"


def along_pitch(px, py, theta):
    """θ 방향으로 1 px 움직일 때의 실제 길이 (mm)."""
    t = math.radians(theta)
    return math.hypot(px * math.cos(t), py * math.sin(t))


def fit_px(theta, mon):
    """θ 로 돌린 자가 모니터 안에 다 들어가는 최대 길이 (px)."""
    t = math.radians(theta)
    c, s = abs(math.cos(t)), abs(math.sin(t))
    lim = [(mon["w"] - 2 * MARGIN - THICK * s) / c if c > 1e-9 else math.inf,
           (mon["h"] - 2 * MARGIN - THICK * c) / s if s > 1e-9 else math.inf]
    return max(1, math.floor(min(lim)))


# ---- 기하: 몸통 좌표 (a = 길이 방향, b = 폭 방향, 회전 전) ↔ 창 좌표 ----
def make_frame(L, theta, fh, fv):
    t = math.radians(theta)
    c, s = math.cos(t), math.sin(t)
    pts = [(a * c - b * s, a * s + b * c) for a, b in ((0, 0), (L, 0), (L, THICK), (0, THICK))]
    mx, my = min(p[0] for p in pts), min(p[1] for p in pts)
    ox, oy = MARGIN - mx, MARGIN - my
    return {"L": L, "theta": theta, "c": c, "s": s, "ox": ox, "oy": oy, "fh": fh, "fv": fv,
            "W": math.ceil(max(p[0] for p in pts) - mx) + 2 * MARGIN,
            "H": math.ceil(max(p[1] for p in pts) - my) + 2 * MARGIN,
            "poly": [(ox + x, oy + y) for x, y in pts],
            "upside": c < -1e-9 or (abs(c) <= 1e-9 and s > 0)}  # 글자가 거꾸로 보일 방향


def fwd(f, a, b):
    return f["ox"] + a * f["c"] - b * f["s"], f["oy"] + a * f["s"] + b * f["c"]


def inv(f, x, y):
    dx, dy = x - f["ox"], y - f["oy"]
    return dx * f["c"] + dy * f["s"], -dx * f["s"] + dy * f["c"]


def ruler_ab(f, u, v):
    """눈금 좌표 (u = 0 눈금부터, v = 눈금 가장자리부터) ↔ 몸통 좌표. 자기 자신이 역함수."""
    return (f["L"] - u if f["fh"] else u), (THICK - v if f["fv"] else v)


def ui_ab(f, a, b):
    """글자·버튼 좌표 ↔ 몸통 좌표. 거꾸로 보일 각도면 180° 돌려서 늘 읽히게."""
    return (f["L"] - a, THICK - b) if f["upside"] else (a, b)


def row_spans(poly, h):
    """볼록 다각형 → 행별 [x, y, w, h] 사각형 (같은 구간이 이어지면 합침). X Shape 용."""
    out = []
    for y in range(h):
        yc = y + 0.5
        xs = [x0 + (yc - y0) * (x1 - x0) / (y1 - y0)
              for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1], strict=True) if (y0 <= yc < y1) or (y1 <= yc < y0)]
        if len(xs) < 2:
            continue
        x, w = math.floor(min(xs)), math.ceil(max(xs)) - math.floor(min(xs))
        if out and out[-1][0] == x and out[-1][2] == w and out[-1][1] + out[-1][3] == y:
            out[-1][3] += 1
        else:
            out.append([x, y, w, 1])
    return out


class _XRect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_short), ("y", ctypes.c_short), ("width", ctypes.c_ushort), ("height", ctypes.c_ushort)]


class Shaper:
    """창 모양을 기울어진 자 모양으로 잘라, 바깥 모서리는 아래 창이 보이고 클릭도 통과하게 한다 (libXext)."""

    def __init__(self):
        try:
            self.x11 = ctypes.cdll.LoadLibrary("libX11.so.6")
            self.ext = ctypes.cdll.LoadLibrary("libXext.so.6")
        except OSError:
            self.dpy = None
            return
        self.x11.XOpenDisplay.restype = ctypes.c_void_p
        self.x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.x11.XFlush.argtypes = [ctypes.c_void_p]
        self.x11.XFree.argtypes = [ctypes.c_void_p]
        self.x11.XQueryTree.argtypes = [ctypes.c_void_p, ctypes.c_ulong] + [ctypes.POINTER(ctypes.c_ulong)] * 2 + \
                                       [ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint)]
        self.ext.XShapeCombineRectangles.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                                                     ctypes.c_int, ctypes.POINTER(_XRect), ctypes.c_int,
                                                     ctypes.c_int, ctypes.c_int]
        self.dpy = self.x11.XOpenDisplay(None)

    def toplevel(self, win):
        """Tk 창 → 루트 바로 아래 래퍼 창 (Tk 는 안쪽 창 id 만 알려 준다)."""
        while True:
            root, parent, kids, n = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p(), ctypes.c_uint()
            if not self.x11.XQueryTree(self.dpy, win, ctypes.byref(root), ctypes.byref(parent),
                                       ctypes.byref(kids), ctypes.byref(n)):
                return win
            if kids:
                self.x11.XFree(kids)
            if parent.value in (0, root.value):
                return win
            win = parent.value

    def set(self, win, rects):
        if not self.dpy or not rects:
            return
        arr = (_XRect * len(rects))(*[_XRect(*r) for r in rects])
        for w in {win, self.toplevel(win)}:
            for kind in (0, 2):  # ShapeBounding, ShapeInput
                self.ext.XShapeCombineRectangles(self.dpy, w, kind, 0, 0, arr, len(rects), 0, 0)  # ShapeSet, Unsorted
        self.x11.XFlush(self.dpy)


class Ruler:
    def __init__(self, root, mons, length_mm, theta, x, y):
        self.root, self.mons = root, mons
        self.cal = json.loads(CONF.read_text()) if CONF.exists() else {}
        self.font = tkfont.Font(font=FONT)
        self.shaper = Shaper()
        self.length_mm, self.theta, self.fh, self.fv = max(MIN_MM, length_mm), theta % 360, False, False
        self.wx, self.wy = x, y
        self.f: dict = {}
        self.a = None        # 첫 번째 클릭 (u, px)
        self.b = None        # 두 번째 클릭
        self.done = False    # 확정되면 클릭으로 점이 바뀌지 않는다
        self.verify = False  # 검증 모드: A4 길이에 초록 선 표시
        self.hover = None
        self.calib = None    # 보정 중: [u0, u1]
        self.calib_steps: list = []  # 남은 보정 단계 ("x" → "y")
        self.calib_theta = 0.0       # 보정 끝나면 돌아갈 각도
        self.calib_msgs: list = []
        self.sel = 1
        self.ref_text = ""
        self.angle_text: str | None = None  # 각도 입력 중이면 입력한 글자
        self.press: tuple | None = None
        self.drawn_for = None
        self.flash = ("", 0.0)
        self.pending = {}    # after_idle 로 묶은 작업 (연속 이벤트는 마지막 한 번만 처리)
        self.rot_acc = 0.0
        self.grip_mm = 0.0
        self.widths = {}     # 버튼 글자 폭 캐시: font.measure 가 한글·기호 대체 글꼴 때문에 호출당 ~10 ms
        self.alpha = 0.85
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", self.alpha)
        self.c = tk.Canvas(root, bg="#fffbe6", highlightthickness=0, cursor="fleur")
        self.c.pack(fill="both", expand=True)
        self.c.bind("<Motion>", self.on_motion)
        self.c.bind("<Leave>", lambda e: self.set_hover(None))
        self.c.bind("<ButtonPress-1>", self.on_press)
        self.c.bind("<B1-Motion>", self.on_drag)
        self.c.bind("<ButtonRelease-1>", self.on_release)
        self.c.bind("<ButtonPress-3>", lambda e: self.set_a(None))
        self.c.bind("<Button-4>", lambda e: self.wheel(-0.1 if int(e.state) & 1 else -1))
        self.c.bind("<Button-5>", lambda e: self.wheel(0.1 if int(e.state) & 1 else 1))
        for tag, cur in (("btn", "hand2"), ("grip", "sizing")):
            self.c.tag_bind(tag, "<Enter>", lambda e, cur=cur: self.c.config(cursor=cur))
            self.c.tag_bind(tag, "<Leave>", lambda e: self.c.config(cursor=self.base_cursor()))
        root.bind("<Key>", self.on_key)
        self.update(keep="")

    # ---- 상태 ----
    def monitor(self):
        f = self.f
        return mon_at(self.mons, self.wx + f["W"] // 2, self.wy + f["H"] // 2) if f else mon_at(self.mons, self.wx, self.wy)

    def pitches(self):
        m = self.monitor()
        (px, sx), (py, sy) = axis_pitch(m, self.cal, "x"), axis_pitch(m, self.cal, "y")
        return along_pitch(px, py, self.theta), sx, sy, m

    def pitch(self):
        return self.pitches()[0]

    def calib_axis(self):
        t = self.theta % 180
        return "x" if t == 0 else "y" if t == 90 else None

    def P(self, u, v):  # 눈금 좌표 → 창 좌표
        return fwd(self.f, *ruler_ab(self.f, u, v))

    def U(self, a, b):  # 글자 좌표 → 창 좌표
        return fwd(self.f, *ui_ab(self.f, a, b))

    def u_of(self, e):  # 이벤트 → u (창 위치는 우리가 지정한 값 기준, 드래그 중 X 서버 지연과 무관)
        return ruler_ab(self.f, *inv(self.f, e.x_root - self.wx, e.y_root - self.wy))[0]

    def text_angle(self):
        return -self.theta + (180 if self.f["upside"] else 0)

    def update(self, keep="zero", **changes):
        """길이·각도·뒤집기를 바꾸고 창을 다시 배치. keep: 화면에서 제자리에 둘 점 (0 눈금 / 자 중심)."""
        def anchor():
            if keep == "zero":
                return self.P(0, 0)
            return fwd(self.f, self.f["L"] / 2, THICK / 2)
        old = None
        if keep and self.f:
            ax, ay = anchor()
            old = (self.wx + ax, self.wy + ay)
        for k, v in changes.items():
            setattr(self, k, v)
        self.theta = round(self.theta % 360, 1)
        mon = (mon_at(self.mons, *old) if old else None) or self.monitor()
        L = round(self.length_mm / self.pitch())
        if mon:  # 자 길이는 원래 값을 기억하고, 화면에 안 들어갈 때만 보이는 길이를 줄인다
            L = min(L, fit_px(self.theta, mon))
        self.f = make_frame(L, self.theta, self.fh, self.fv)
        if old:
            ax, ay = anchor()
            self.wx, self.wy = round(old[0] - ax), round(old[1] - ay)
        if mon:  # 모니터 밖으로 나가면 안쪽으로 밀어 넣는다
            self.wx = min(max(self.wx, mon["x"]), mon["x"] + mon["w"] - self.f["W"])
            self.wy = min(max(self.wy, mon["y"]), mon["y"] + mon["h"] - self.f["H"])
        self.root.geometry(f"{self.f['W']}x{self.f['H']}+{self.wx}+{self.wy}")
        self.root.update_idletasks()
        self.shaper.set(int(self.root.wm_frame(), 16), row_spans(self.f["poly"], self.f["H"]))
        self.redraw()

    def base_cursor(self):
        return "fleur" if self.done and self.a is not None and not self.calib else "crosshair"

    def later(self, key, fn):
        """같은 작업이 이벤트마다 쌓이지 않게, 이벤트가 다 처리된 뒤 한 번만 실행."""
        if key not in self.pending:
            self.pending[key] = self.root.after_idle(lambda: (self.pending.pop(key, None), fn()))

    def set_hover(self, v):
        self.hover = v
        self.later("dyn", self.redraw_dyn)

    def wheel(self, deg):
        self.rot_acc += deg
        self.later("rot", self.apply_wheel)

    def apply_wheel(self):
        deg, self.rot_acc = self.rot_acc, 0.0
        self.rotate(deg)
        self.redraw_dyn()

    def picking(self):  # A 는 찍었고 아직 확정 전
        return self.a is not None and not self.done

    def set_a(self, v):
        self.a, self.b, self.done = v, None, False
        self.c.config(cursor=self.base_cursor())
        self.redraw_dyn()

    def say(self, msg, sec=4):
        self.flash = (msg, time.time() + sec)
        self.root.after(int(sec * 1000) + 50, self.redraw_dyn)

    # ---- 그리기 ----
    def tick_line(self, u, v0, v1, **kw):
        self.c.create_line(*self.P(u, v0), *self.P(u, v1), **kw)

    def quad(self, pts, **kw):
        return self.c.create_polygon(*[k for p in pts for k in p], **kw)

    def text(self, a, b, s, **kw):
        kw.setdefault("anchor", "w")
        kw.setdefault("tags", "dyn")
        kw.setdefault("font", FONT)
        return self.c.create_text(*self.U(a, b), text=s, angle=self.text_angle(), **kw)

    def rows(self):
        """글자 좌표에서 버튼 줄 중심, 1·2 번째 글자 줄 중심 (눈금 반대쪽 띠)."""
        tick_b = ui_ab(self.f, 0, THICK if self.fv else 0)[1]
        b0 = 60 if tick_b == 0 else 0
        return b0 + 18, b0 + 48, b0 + 70

    def button(self, a, label, name, color="#fff"):
        if label not in self.widths:
            self.widths[label] = self.font.measure(label)
        w = self.widths[label] + 18
        rb = self.rows()[0]
        tags = ("dyn", "btn", f"btn:{name}")
        self.quad([self.U(a, rb - 14), self.U(a + w, rb - 14), self.U(a + w, rb + 14), self.U(a, rb + 14)],
                  fill=color, outline="#666", tags=tags)
        self.text(a + w / 2, rb, label, anchor="center", tags=tags)
        return a + w + 5

    def redraw(self):
        f, p = self.f, self.pitch()
        key = (f["L"], self.theta, self.fh, self.fv, p)
        if self.drawn_for == key:
            return
        self.drawn_for = key
        c, L, ta = self.c, f["L"], self.text_angle()
        c.delete("all")
        self.quad(f["poly"], fill="#fffbe6", outline="#888")
        for i in range(int((L - GRIP) * p) + 1):
            u, n = i / p, 34 if i % 10 == 0 else 22 if i % 5 == 0 else 12
            self.tick_line(u, 0, n)
            if i % 10 == 0 and i:
                c.create_text(*self.P(u, 46), text=f"{i // 10}", angle=ta, font=FONT)
        c.create_text(*self.P(14, 44), text="cm", angle=ta, font=("Sans", 9), fill="#666")
        self.quad([self.P(L - GRIP, 0), self.P(L, 0), self.P(L, THICK), self.P(L - GRIP, THICK)],
                  fill="#ccc", outline="#888", tags="grip")
        c.create_text(*self.P(L - GRIP / 2, THICK / 2), text="⋮", angle=-self.theta, font=("Sans", 14), tags="grip")
        self.redraw_dyn()

    def redraw_dyn(self):
        c = self.c
        c.delete("dyn")
        p, sx, sy, m = self.pitches()
        _, r1, r2 = self.rows()
        ax = self.calib_axis()
        bold = (FONT[0], FONT[1], "bold")
        if self.calib:
            for i, v in enumerate(self.calib):
                self.tick_line(v, 0, THICK, fill="red" if i == self.sel else "blue", width=2, tags="dyn")
            d = round(abs(self.calib[1] - self.calib[0]))
            a = self.button(UI0, "저장 (Enter)", "save", "#d8f5d0")
            a = self.button(a, "건너뛰기", "skip")
            self.button(a, "취소 (Esc)", "cancel")
            res = f" → {float(self.ref_text) / d:.5f} mm/px" if self.ref_text and d else ""
            ax = self.calib_steps[0] if self.calib_steps else ax
            side, ref0 = SIDE.get(ax or "x", SIDE["x"])
            ends = "왼쪽·오른쪽" if ax == "x" else "위·아래"
            step = 3 - len(self.calib_steps)  # ① 가로 / ② 세로
            msg, until = self.flash
            line1 = msg if time.time() < until else (
                f"[보정 {step}/2 · {AXIS.get(ax or '', '')}] A4 {side} ({ref0} mm) 을 눈금 쪽에 붙이고, 파란 선 두 개를 종이 {ends} 끝에 맞추기"
                f" (드래그 / 선 클릭 후 방향키 1 px)")
            self.text(UI0, r1, line1, fill="#036")
            self.text(UI0, r2, f"길이 {self.ref_text or '?'} mm (A4 아니면 숫자키로 수정) · 선 사이 {d} px{res}",
                      fill="#036", font=bold)
            return

        a = UI0
        for label, name in (("↺90°", "ccw"), ("↻90°", "cw"), ("각도…", "angle"), ("⇋좌우", "fliph"), ("⇅상하", "flipv")):
            a = self.button(a, label, name, "#d8f5d0" if name == "angle" and self.angle_text is not None else "#fff")
        calibrated = (sx if ax == "x" else sy) == "보정" if ax else sx == sy == "보정"
        if self.picking():
            a = self.button(a + 8, "확정 (Enter)", "confirm", "#d8f5d0" if self.b is not None else "#eee")
        else:
            a = self.button(a + 8, "새 측정 (m)", "measure")
        a = self.button(a, "보정" if calibrated else "보정하기", "calib", "#fff" if calibrated else "#ffd6d6")
        a = self.button(a, "검증 (t)", "verify", "#d8f5d0" if self.verify else "#fff")

        if self.verify:  # A4 두 변 길이에 초록 선, ±1 mm 점선
            for mm in (210, 297):
                u = mm / p
                if u > self.f["L"] - GRIP:
                    continue
                self.tick_line(u, 0, THICK, fill="#0a0", width=2, tags="dyn")
                for du in (-1 / p, 1 / p):
                    self.tick_line(u + du, 0, 60, fill="#0a0", dash=(3, 3), tags="dyn")
                c.create_text(*self.P(u + 6, 22), text=f"{mm}", anchor="w", angle=self.text_angle(),
                              fill="#0a0", font=(FONT[0], 11, "bold"), tags="dyn")
        for label, name in (("−1cm", "shorter"), ("+1cm", "longer"), ("?", "help"), ("✕", "quit")):
            a = self.button(a, label, name)

        bcol = "red" if self.done else "#f80"  # 확정 전 B 는 주황
        for v, col in ((self.a, "red"), (self.b, bcol)):
            if v is not None:
                self.tick_line(v, 0, THICK, fill=col, width=2, tags="dyn")
        if self.a is not None and self.b is not None:
            c.create_line(*self.P(self.a, 40), *self.P(self.b, 40), fill=bcol, width=3, arrow="both", tags="dyn")
        msg, until = self.flash
        if self.angle_text is not None:
            line1, col1 = f"각도 입력: {self.angle_text}▏°  (숫자키, - 가능 · Enter 적용 · Esc 취소)", "#036"
        elif time.time() < until:
            line1, col1 = msg, "#060"
        elif self.done and self.a is not None and self.b is not None:
            d = abs(self.b - self.a)
            line1, col1 = f"✓ A → B  {d * p:.2f} mm  ({d:.0f} px) 확정     새 측정: m / 우클릭", "#c00"
        elif self.picking():
            parts = []
            if self.b is not None:
                d = abs(self.b - self.a)
                parts.append(f"A → B  {d * p:.2f} mm ({d:.0f} px)")
            if self.hover is not None:
                self.tick_line(self.hover, 0, 60, fill="#06c", tags="dyn")
                parts.append(f"A → 커서 {abs(self.hover - self.a) * p:.2f} mm")
            parts.append("클릭: B 다시 찍기 · 방향키: B 1 px · Enter: 확정 · Esc: 취소" if self.b is not None
                         else "클릭해서 B 찍기 · Esc: 취소")
            line1, col1 = "     ".join(parts), "#c00" if self.b is not None else "#000"
        elif self.verify:
            main = {"x": "긴 변 297", "y": "짧은 변 210"}.get(ax or "", "변 297 / 210")
            line1, col1 = (f"[검증] A4 한쪽 끝을 0 눈금에, 반대쪽 끝이 초록 선({main} mm)에 오면 OK"
                           f" · 점선 = ±1 mm · 끝내기: t / Esc"), "#070"
        elif self.hover is not None and not self.done:
            self.tick_line(self.hover, 0, 60, fill="#06c", tags="dyn")
            line1, col1 = f"커서 {self.hover * p:.2f} mm", "#000"
            if self.a is not None:
                line1 += f"     A → 커서 {abs(self.hover - self.a) * p:.2f} mm ({abs(self.hover - self.a):.0f} px)     (클릭하면 B)"
            else:
                line1 += "     (클릭하면 A)"
        else:
            line1, col1 = "클릭: A → B 길이 · 드래그: 이동 · 휠: 회전 · 방향키: 1 px 이동 · 끝 손잡이: 길이", "#555"
        self.text(UI0, r1, line1, fill=col1)

        mark = {"보정": "✓"}
        if ax:
            other = "y" if ax == "x" else "x"
            so = sy if ax == "x" else sx
            if calibrated:
                ex = axis_pitch(m, {}, ax)[0]
                line2, col2 = f"✓ {AXIS[ax]} 보정됨 {p:.5f} mm/px (EDID 대비 {(p / ex - 1) * 100:+.2f}%)", "#060"
            else:
                line2, col2 = f"✗ {AXIS[ax]} 보정 안 됨 — {sx if ax == 'x' else sy} 추정 {p:.5f} mm/px", "#c00"
            line2 += f" · {AXIS[other]} 보정 {mark.get(so, '✗')}"
        else:
            line2 = f"{'✓' if calibrated else '✗'} 가로({mark.get(sx, '✗')})·세로({mark.get(sy, '✗')}) 합성 {p:.5f} mm/px"
            col2 = "#060" if calibrated else "#c00"
        flips = (" · 좌우반전" if self.fh else "") + (" · 상하반전" if self.fv else "")
        eff = self.f["L"] * p
        cut = " (화면에 맞춰 줄임)" if eff < self.length_mm - 1 else ""
        line2 += f" · 각도 {self.theta:g}°{flips} · 길이 {eff:.0f} mm{cut}"
        self.text(UI0, r2, line2, fill=col2, font=bold)

    # ---- 동작 ----
    def rotate(self, deg):
        if self.calib:
            return
        self.update(theta=self.theta + deg)

    def ask_angle(self):
        # 별도 창은 항상-위 자 뒤에 깔려서, 자 위에서 바로 입력받는다
        self.angle_text = None if self.angle_text is not None or self.calib else ""

    def angle_key(self, k):
        if k.isdigit() or k in ("period", "minus", "KP_Subtract"):
            self.angle_text = (self.angle_text or "") + (k if k.isdigit() else "." if k == "period" else "-")
        elif k == "BackSpace":
            self.angle_text = (self.angle_text or "")[:-1]
        elif k in ("Return", "KP_Enter"):
            try:
                v = float(self.angle_text or "")
            except ValueError:
                return self.say("각도를 숫자로 입력하세요")
            self.angle_text = None
            self.update(theta=v)
        elif k == "Escape":
            self.angle_text = None

    def flip(self, attr):
        if self.calib:  # 보정 중에는 각도·방향을 바꾸지 않는다
            return
        self.a, self.b = None, None
        self.update(keep="center", **{attr: not getattr(self, attr)})

    def resize(self, mm):
        self.update(length_mm=max(MIN_MM, mm))

    def toggle_calib(self):
        if self.calib:
            return self.end_calib()
        self.verify = False
        self.calib_steps, self.calib_theta, self.calib_msgs = ["x", "y"], self.theta, []
        self.calib_step()

    def calib_step(self):
        """보정 순서: ① 가로 (자 0°, A4 긴 변 297) → ② 세로 (자 90°, A4 짧은 변 210)."""
        ax = self.calib_steps[0]
        self.calib = None
        self.update(theta=0 if ax == "x" else 90)
        ref = SIDE[ax][1]
        if self.f["L"] * self.pitch() < ref + 20:  # 종이 변 전체가 자 위에 오도록
            self.resize(ref + 20)
        self.calib, self.ref_text, self.sel = [20, 20 + round(ref / self.pitch())], str(ref), 1

    def end_calib(self, msg=""):
        self.calib, self.calib_steps = None, []
        self.update(theta=self.calib_theta)  # 보정 전 각도로 되돌림
        if msg:
            self.say(msg, 12)

    def next_calib(self, msg):
        if not self.calib_steps:
            return
        self.calib_steps.pop(0)
        if msg:
            self.calib_msgs.append(msg)
        if self.calib_steps:
            self.say(f"✓ {msg} — 이제 ② 세로: 자가 90° 로 섰습니다" if msg else "② 세로 보정", 6)
            return self.calib_step()
        done = " · ".join(self.calib_msgs)
        self.end_calib(f"✓ 보정 완료 — {done}" if done else "보정을 건너뛰었습니다")

    def save_calib(self):
        ax = self.calib_steps[0] if self.calib_steps else None
        if not self.calib or not ax:
            return
        d = round(abs(self.calib[1] - self.calib[0]))
        try:
            ref = float(self.ref_text)
        except ValueError:
            return self.say("종이 길이를 숫자로 입력하세요")
        if d == 0 or ref <= 0:
            return self.say("선 두 개가 겹쳐 있습니다")
        e = self.cal.setdefault(mon_key(self.monitor()), {})
        e[ax] = {"mm_per_px": ref / d, "ref_mm": ref, "px": d, "date": time.strftime("%Y-%m-%d %H:%M")}
        CONF.parent.mkdir(parents=True, exist_ok=True)
        CONF.write_text(json.dumps(self.cal, indent=2, ensure_ascii=False))
        dev = (ref / d / axis_pitch(self.monitor(), {}, ax)[0] - 1) * 100
        warn = f" ⚠ EDID 와 차이가 큽니다: A4 {SIDE[ax][0]} ({SIDE[ax][1]} mm) 에 맞췄는지 확인" if abs(dev) > 5 else ""  # 보통 1% 안쪽
        self.next_calib(f"{AXIS[ax]} {ref / d:.5f} mm/px (EDID {dev:+.2f}%){warn}")

    def help(self):
        t = tk.Toplevel(self.root)
        t.title("화면 자 도움말")
        t.attributes("-topmost", True)
        tk.Label(t, text=HELP, justify="left", font=FONT, padx=16, pady=12).pack()
        tk.Button(t, text="닫기", command=t.destroy).pack(pady=(0, 12))

    def toggle_verify(self):
        self.verify = not self.verify and not self.calib
        if self.verify and self.f["L"] * self.pitch() < 297 + 15:  # 297 선이 자 위에 오도록
            self.resize(max(self.length_mm, 297 + 15))

    def confirm(self):
        if self.picking() and self.b is not None:
            self.done = True
            self.c.config(cursor=self.base_cursor())

    def act(self, name):
        {"cw": lambda: self.rotate(90), "ccw": lambda: self.rotate(-90), "angle": self.ask_angle,
         "fliph": lambda: self.flip("fh"), "flipv": lambda: self.flip("fv"),
         "calib": self.toggle_calib, "save": self.save_calib, "cancel": self.toggle_calib,
         "skip": lambda: self.next_calib(""),
         # 화면에 맞춰 줄어 있을 수 있으니 보이는 길이 기준으로 조절
         "shorter": lambda: self.resize(self.f["L"] * self.pitch() - 10),
         "longer": lambda: self.resize(self.f["L"] * self.pitch() + 10),
         "verify": self.toggle_verify, "measure": lambda: self.set_a(None), "confirm": self.confirm, "help": self.help, "quit": self.root.destroy}[name]()
        if name != "quit":
            self.redraw_dyn()

    # ---- 입력 ----
    def on_motion(self, e):
        self.set_hover(self.u_of(e))

    def on_press(self, e):
        self.root.focus_force()  # overrideredirect 창은 WM 이 포커스를 안 준다
        hit = self.c.find_withtag("current")
        tags = self.c.gettags(hit[0]) if hit else ()
        btn = next((t[4:] for t in tags if t.startswith("btn:")), None)
        if btn:
            self.press = None
            return self.act(btn)
        mode = "grip" if "grip" in tags else "calib" if self.calib else "move"
        self.press = (mode, e.x_root, e.y_root, self.wx, self.wy, False)
        if mode == "calib" and self.calib:
            u = self.u_of(e)
            self.sel = 0 if abs(u - self.calib[0]) < abs(u - self.calib[1]) else 1
            self.calib[self.sel] = round(u)
            self.redraw_dyn()

    def on_drag(self, e):
        if not self.press:
            return
        mode, x0, y0, wx, wy, moved = self.press
        if mode == "grip":
            self.grip_mm = self.u_of(e) * self.pitch()
            self.later("grip", lambda: self.resize(self.grip_mm))
        elif mode == "calib" and self.calib:
            self.calib[self.sel] = round(self.u_of(e))
            self.redraw_dyn()
        else:
            dx, dy = e.x_root - x0, e.y_root - y0
            if moved or abs(dx) + abs(dy) > 3:
                self.press = (mode, x0, y0, wx, wy, True)
                self.wx, self.wy = wx + dx, wy + dy
                self.root.geometry(f"+{self.wx}+{self.wy}")

    def on_release(self, e):
        if self.press and self.press[0] == "move":
            if self.press[5]:
                self.update()  # 다른 모니터로 옮겼으면 피치가 바뀐다
            elif self.a is None:
                self.set_a(self.u_of(e))
            elif not self.done:  # 확정 전에는 클릭할 때마다 B 만 다시 찍는다
                self.b = self.u_of(e)
                self.redraw_dyn()
        self.press = None

    def on_key(self, e):
        k, shift = e.keysym, bool(int(e.state) & 1)
        if self.angle_text is not None:
            self.angle_key(k)
            return self.redraw_dyn()
        step = {"Left": -1, "Up": -1, "Right": 1, "Down": 1}.get(k)
        if step:
            step *= 10 if shift else 1
            if self.calib:
                self.calib[self.sel] += step
            elif self.picking() and self.b is not None:
                self.b += step
            else:
                if k in ("Left", "Right"):
                    self.wx += step
                else:
                    self.wy += step
                self.root.geometry(f"+{self.wx}+{self.wy}")
        elif self.calib and (k.isdigit() or k == "period"):
            self.ref_text += "." if k == "period" else k
        elif self.calib and k == "BackSpace":
            self.ref_text = self.ref_text[:-1]
        elif self.calib and k == "Return":
            return self.act("save")
        elif k in ("Return", "KP_Enter"):
            return self.act("confirm")
        elif k in ("comma", "period", "less", "greater"):
            return self.rotate({"comma": -1, "period": 1, "less": -0.1, "greater": 0.1}[k])
        elif k in ("c", "m", "t", "r", "a", "h", "v", "bracketleft", "bracketright", "question"):
            return self.act({"c": "calib", "m": "measure", "t": "verify", "r": "cw", "a": "angle", "h": "fliph", "v": "flipv",
                             "bracketleft": "shorter", "bracketright": "longer", "question": "help"}[k])
        elif k in ("plus", "equal", "KP_Add", "minus", "KP_Subtract"):
            self.alpha = min(1.0, max(0.2, self.alpha + (-0.1 if "minus" in k or "Subtract" in k else 0.1)))
            self.root.attributes("-alpha", self.alpha)
        elif k == "Delete":
            self.set_a(None)
        elif k == "Escape":
            if self.picking():
                self.set_a(None)
            elif self.verify:
                self.verify = False
            elif self.calib:
                self.end_calib()
            else:
                return self.root.destroy()
        self.redraw_dyn()


def selftest():
    mons = parse_monitors(" 0: +*DP-4 3840/697x2160/392+0+214  DP-4\n 1: +DP-0 1440/553x2560/311+3840+0  DP-0\n")
    assert mons[0] == {"name": "DP-4", "w": 3840, "h": 2160, "x": 0, "y": 214, "wmm": 697, "hmm": 392}, mons[0]
    assert (mons[1]["wmm"], mons[1]["hmm"], mons[1]["x"]) == (311, 553, 3840), mons[1]  # 세로 회전 → mm 교환
    assert mon_at(mons, 3900, 10) is mons[1] and mon_at(mons, 100, 100) is None
    cal = {mon_key(mons[0]): {"y": {"mm_per_px": 0.2}}}
    assert axis_pitch(mons[0], cal, "y") == (0.2, "보정") and axis_pitch(mons[0], cal, "x") == (697 / 3840, "EDID")
    assert along_pitch(0.1, 0.2, 0) == 0.1 and abs(along_pitch(0.1, 0.2, 90) - 0.2) < 1e-12
    for theta in (0, 30, 90, 135, 200, 270):  # 눈금 좌표 → 창 좌표 → 눈금 좌표 왕복
        for fh in (False, True):
            for fv in (False, True):
                f = make_frame(1000, theta, fh, fv)
                x, y = fwd(f, *ruler_ab(f, 123, 45))
                u, v = ruler_ab(f, *inv(f, x, y))
                assert abs(u - 123) < 1e-9 and abs(v - 45) < 1e-9, (theta, fh, fv, u, v)
                assert all(-1e-9 <= px <= f["W"] and -1e-9 <= py <= f["H"] for px, py in f["poly"])
    f = make_frame(1000, 0, False, False)
    assert row_spans(f["poly"], f["H"]) == [[MARGIN, MARGIN, 1000, THICK]]
    for theta in (0, 30, 90, 135):  # 맞춘 길이로 만든 창은 모니터보다 크지 않다
        f = make_frame(fit_px(theta, mons[0]), theta, False, False)
        assert f["W"] <= mons[0]["w"] + 1 and f["H"] <= mons[0]["h"] + 1, (theta, f["W"], f["H"])
    assert fit_px(90, mons[0]) == 2160 - 2 * MARGIN
    assert make_frame(1000, 180, False, False)["upside"] and not make_frame(1000, 270, False, False)["upside"]
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser(description=f"{__doc__}\n{HELP}", formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--monitor", help="xrandr 출력 이름 (예: DP-4). 기본: 마우스가 있는 모니터")
    ap.add_argument("--length", type=float, default=320, help="자 길이 mm (기본 320)")
    ap.add_argument("--angle", type=float, default=0, help="처음 각도 (°, 시계 방향)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()

    root = tk.Tk()
    root.title("screen ruler")
    mons = monitors()
    m = next((m for m in mons if m["name"] == args.monitor), None) if args.monitor else mon_at(mons, *root.winfo_pointerxy())
    if args.monitor and not m:
        sys.exit(f"모니터 {args.monitor} 없음: {[m['name'] for m in mons]}")
    x, y = (m["x"] + 100, m["y"] + m["h"] // 3) if m else (100, 100)
    Ruler(root, mons, args.length, args.angle, x, y)
    root.mainloop()


if __name__ == "__main__":
    main()
