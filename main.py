import ctypes
import time
from ctypes import wintypes
import configparser
from pathlib import Path
from SimConnect import SimConnect, Event
import sys

# ============================================================
# 读取配置
# ============================================================

GAMEPAD_INDEX = 0
POLL_HZ = 120

TAP_COUNT = 3
HOLD_SEND_HZ = 24
HOLD_DELAY = 0.28

ENABLE_LB_FINE_THROTTLE = True
FINE_TAP_COUNT = 2

# ============================================================
# X 键渐进刹车参数（直接在这里调整）
#
# 所有“强度/速度”均按百分比理解：
#   0.0   = 0%
#   100.0 = 100%
# ============================================================

ENABLE_X_BRAKE = True
X_BRAKE_TAP_INCREASE = 12.0            # 轻按一次增加多少 %
X_BRAKE_HOLD_INCREASE_PER_SEC = 0.0   # 长按时每秒增加多少 %
X_BRAKE_DECAY_DELAY = 0.20             # 松开后等待多久才开始衰减（秒）
X_BRAKE_DECAY_PER_SEC = 100.0           # 衰减时每秒降低多少 %
_X_BRAKE_HOLD_DELAY = 0.28             # 长按判定时间


def load_config():
    global GAMEPAD_INDEX, POLL_HZ, TAP_COUNT, HOLD_SEND_HZ, HOLD_DELAY, ENABLE_LB_FINE_THROTTLE, FINE_TAP_COUNT
    config = configparser.ConfigParser()
    if getattr(sys, "frozen", False):
        config_path = Path(sys.executable).with_name("config.ini")
    else:
        config_path = Path(__file__).with_name("config.ini")
    config.read(config_path, encoding="utf-8")
    GAMEPAD_INDEX = config.getint("gamepad", "index", fallback=GAMEPAD_INDEX)
    POLL_HZ = config.getfloat("gamepad", "poll_hz")
    TAP_COUNT = config.getint("throttle", "tap_count")
    HOLD_SEND_HZ = config.getfloat("throttle", "hold_send_hz")
    HOLD_DELAY = config.getfloat("throttle", "hold_delay")
    ENABLE_LB_FINE_THROTTLE = config.getboolean("fine_throttle", "enabled")
    FINE_TAP_COUNT = config.getint("fine_throttle", "fine_tap_count")

    global ENABLE_X_BRAKE, X_BRAKE_TAP_INCREASE, X_BRAKE_HOLD_INCREASE_PER_SEC, X_BRAKE_DECAY_DELAY, X_BRAKE_DECAY_PER_SEC
    
    ENABLE_X_BRAKE = config.getboolean("brake", "enabled", fallback=ENABLE_X_BRAKE)
    X_BRAKE_TAP_INCREASE = config.getfloat("brake", "tap_increase", fallback=X_BRAKE_TAP_INCREASE)
    X_BRAKE_HOLD_INCREASE_PER_SEC = config.getfloat(
        "brake",
        "hold_increase_per_sec",
        fallback=X_BRAKE_HOLD_INCREASE_PER_SEC
    )
    X_BRAKE_DECAY_DELAY = config.getfloat(
        "brake", "decay_delay", fallback=X_BRAKE_DECAY_DELAY
    )
    X_BRAKE_DECAY_PER_SEC = config.getfloat(
        "brake", "decay_per_sec", fallback=X_BRAKE_DECAY_PER_SEC
    )


# ============================================================
# XInput 定义
# ============================================================

ERROR_SUCCESS = 0

XINPUT_GAMEPAD_DPAD_UP = 0x0001
XINPUT_GAMEPAD_DPAD_DOWN = 0x0002
XINPUT_GAMEPAD_DPAD_LEFT = 0x0004
XINPUT_GAMEPAD_DPAD_RIGHT = 0x0008

XINPUT_GAMEPAD_START = 0x0010
XINPUT_GAMEPAD_BACK = 0x0020

XINPUT_GAMEPAD_LEFT_THUMB = 0x0040
XINPUT_GAMEPAD_RIGHT_THUMB = 0x0080

XINPUT_GAMEPAD_LEFT_SHOULDER = 0x0100
XINPUT_GAMEPAD_RIGHT_SHOULDER = 0x0200

XINPUT_GAMEPAD_A = 0x1000
XINPUT_GAMEPAD_B = 0x2000
XINPUT_GAMEPAD_X = 0x4000
XINPUT_GAMEPAD_Y = 0x8000


class XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [
        ("wButtons", wintypes.WORD),
        ("bLeftTrigger", wintypes.BYTE),
        ("bRightTrigger", wintypes.BYTE),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]


class XINPUT_STATE(ctypes.Structure):
    _fields_ = [
        ("dwPacketNumber", wintypes.DWORD),
        ("Gamepad", XINPUT_GAMEPAD),
    ]


def load_xinput():
    """
    加载 Windows XInput。
    Windows 10/11 通常使用 xinput1_4.dll。
    """

    dll_names = (
        "xinput1_4.dll",
        "xinput1_3.dll",
        "xinput9_1_0.dll",
    )

    for name in dll_names:
        try:
            dll = ctypes.WinDLL(name)

            fn = dll.XInputGetState

            fn.argtypes = [
                wintypes.DWORD,
                ctypes.POINTER(XINPUT_STATE),
            ]

            fn.restype = wintypes.DWORD

            return fn

        except (OSError, AttributeError):
            pass

    raise RuntimeError(
        "无法加载 XInput DLL。"
        "本程序需要 Windows + XInput 兼容手柄。"
    )


XInputGetState = load_xinput()


def read_gamepad(index):
    """
    读取指定 XInput 手柄。
    """

    state = XINPUT_STATE()

    result = XInputGetState(
        index,
        ctypes.byref(state)
    )

    if result != ERROR_SUCCESS:
        return None

    return state.Gamepad


# ============================================================
# 判断 A/B 当前属于哪种模式
# ============================================================

MODE_NORMAL = "normal"
MODE_FINE = "fine"


def get_press_mode(gamepad, target_button):
    """
    判断当前 A/B 按键是否合法，以及属于：

        normal = 普通油门调整
        fine   = LB + A/B 微调
        None   = 非法组合键

    规则：

    A / B
        -> normal

    LB + A / LB + B
        -> fine
           仅 ENABLE_LB_FINE_THROTTLE=True 时有效

    RB + A/B
        -> 永远禁止

    A + B
        -> 永远禁止
    """

    buttons = gamepad.wButtons

    # --------------------------------------------------------
    # 目标按钮本身必须按下
    # --------------------------------------------------------

    if not (buttons & target_button):
        return None


    # --------------------------------------------------------
    # A + B 同时按永远禁止
    # --------------------------------------------------------

    if target_button == XINPUT_GAMEPAD_A:
        if buttons & XINPUT_GAMEPAD_B:
            return None

    if target_button == XINPUT_GAMEPAD_B:
        if buttons & XINPUT_GAMEPAD_A:
            return None


    # --------------------------------------------------------
    # RB + A/B 永远禁止
    # --------------------------------------------------------

    if buttons & XINPUT_GAMEPAD_RIGHT_SHOULDER:
        return None


    # --------------------------------------------------------
    # LB + A/B
    # --------------------------------------------------------

    if buttons & XINPUT_GAMEPAD_LEFT_SHOULDER:

        if ENABLE_LB_FINE_THROTTLE:
            return MODE_FINE

        # 微调功能关闭时：
        # 保持原行为，LB + A/B 作废
        return None


    # --------------------------------------------------------
    # 普通 A/B
    # --------------------------------------------------------

    return MODE_NORMAL


# ============================================================
# 一个 A/B 按键对应的油门控制状态机
# ============================================================

class ThrottleButton:

    def __init__(
        self,
        target_button,
        normal_event,
        fine_event,
        label,
    ):

        self.target_button = target_button

        # 普通事件
        self.normal_event = normal_event

        # LB 微调事件
        self.fine_event = fine_event

        self.label = label

        # 上一次循环时物理按键是否按着
        self.prev_physical_down = False

        # 当前这一“次”按键是否被允许
        self.active_gesture = False

        # 当前手势模式：
        #
        # normal
        # fine
        # None
        #
        self.active_mode = None

        # 当前手势实际使用的 SimConnect Event
        self.active_event = None

        # 开始按下时间
        self.press_time = 0.0

        # 是否已经进入长按状态
        self.hold_started = False

        # 上一次发送 SimConnect 的时间
        self.last_send_time = 0.0


    def cancel_until_release(self):
        """
        作废当前这次按键。

        例如：

            A 按下
            -> 普通模式

            A 还按着时再按 LB
            -> 当前 A 作废

        即使随后松开 LB，只要 A 还没有松开，
        这次 A 都不会重新生效。

        同理：

            LB + A
            -> 微调模式

            A 还按着时松开 LB
            -> 当前 A 作废

        必须松开 A 后重新开始下一次按键。
        """

        self.active_gesture = False
        self.active_mode = None
        self.active_event = None
        self.hold_started = False


    def send_events(self, count=1):
        """
        使用当前手势选择的事件发送指定次数。
        """

        if self.active_event is None:
            return

        for _ in range(max(0, int(count))):
            self.active_event()


    def update(self, gamepad, now):

        physical_down = bool(
            gamepad.wButtons & self.target_button
        )

        current_mode = get_press_mode(
            gamepad,
            self.target_button
        )


        # ====================================================
        # 1. 检测新的物理按下
        # ====================================================

        if physical_down and not self.prev_physical_down:

            # ------------------------------------------------
            # 必须在“刚按下”的这一刻就属于合法模式
            # ------------------------------------------------

            if current_mode is not None:

                self.active_gesture = True

                # 锁定本次手势模式
                self.active_mode = current_mode

                # 根据模式选择 SimConnect Event
                if current_mode == MODE_FINE:
                    self.active_event = self.fine_event
                else:
                    self.active_event = self.normal_event

                self.press_time = now

                self.hold_started = False
                self.last_send_time = now

                # --------------------------------------------
                # 短按立即响应
                #
                # 普通：
                #   THROTTLE_INCR / DECR
                #
                # 微调：
                #   THROTTLE_INCR_SMALL / DECR_SMALL
                # --------------------------------------------

                if current_mode == MODE_FINE:
                    self.send_events(FINE_TAP_COUNT)
                else:
                    self.send_events(TAP_COUNT)

            else:
                # 一开始就是非法组合键
                self.cancel_until_release()


        # ====================================================
        # 2. 按着过程中组合方式发生变化
        # ====================================================

        if (
            physical_down
            and self.active_gesture
        ):

            # ------------------------------------------------
            # 非常重要：
            #
            # 本次手势的模式必须始终与刚按下时相同。
            #
            # 例如：
            #
            # A
            # -> normal
            #
            # 然后不松 A 再按 LB
            # -> current_mode 变 fine
            # -> 作废
            #
            #
            # LB+A
            # -> fine
            #
            # 然后不松 A 先松 LB
            # -> current_mode 变 normal
            # -> 作废
            #
            #
            # RB 加入
            # -> current_mode=None
            # -> 作废
            # ------------------------------------------------

            if current_mode != self.active_mode:
                self.cancel_until_release()


        # ====================================================
        # 3. 长按
        # ====================================================

        if (
            physical_down
            and self.active_gesture
            and current_mode == self.active_mode
            and self.active_mode == MODE_NORMAL
        ):

            held = now - self.press_time

            if held >= HOLD_DELAY:

                # 第一次跨过 HOLD_DELAY
                if not self.hold_started:
                    self.hold_started = True
                    self.last_send_time = now

                else:
                    send_interval = 1.0 / HOLD_SEND_HZ
                    if now - self.last_send_time >= send_interval:
                        self.send_events(1)
                        self.last_send_time = now


        # ====================================================
        # 4. 松开
        # ====================================================

        if not physical_down:
            self.cancel_until_release()

        self.prev_physical_down = physical_down


# ============================================================
# X 键渐进刹车状态机
# ============================================================

def brake_percent_to_axis(brake_percent):
    """
    把 0~100% 的目标刹车强度转换为 AXIS_*_BRAKE_SET 的输入值。

    MSFS 2024 的 AXIS_LEFT_BRAKE_SET / AXIS_RIGHT_BRAKE_SET
    使用 -16383 ~ +16383，并且官方给出的响应是非线性的。
    这里依据官方公布的几个标定点做分段线性反算，让上面的
    “百分比参数”更接近实际刹车百分比，而不是直接操作原始轴值。
    """

    p = max(0.0, min(100.0, float(brake_percent)))

    calibration = (
        (0.0, -16383),
        (8.0, -8191),
        (27.0, 0),
        (53.0, 8191),
        (100.0, 16383),
    )

    for i in range(1, len(calibration)):
        p0, axis0 = calibration[i - 1]
        p1, axis1 = calibration[i]

        if p <= p1:
            ratio = (p - p0) / (p1 - p0)
            return int(round(axis0 + (axis1 - axis0) * ratio))

    return 16383


class XBrakeController:
    """
    X 键控制左右轮刹车。

    行为：
      0. X 必须单独按下；LB+X、RB+X 都无效。
      1. 刚按下 X：立即增加 X_BRAKE_TAP_INCREASE。
      2. 按住超过 _X_BRAKE_HOLD_DELAY：按
         X_BRAKE_HOLD_INCREASE_PER_SEC 连续增加。
      3. 松开 X：保持当前刹车 X_BRAKE_DECAY_DELAY 秒。
      4. 延迟结束后：按 X_BRAKE_DECAY_PER_SEC 连续衰减到 0。

    只有 X 功能真正使用过以后才会发送刹车轴事件；衰减到 0 后
    停止继续发送，从而尽量减少对其它刹车输入设备的持续覆盖。
    """

    def __init__(self, left_event, right_event):
        self.left_event = left_event
        self.right_event = right_event

        self.brake_percent = 0.0

        # 上一帧 X 的“有效按下”状态。
        # 有效按下 = X 按着，并且本次 X 手势没有被 LB/RB 作废。
        self.prev_down = False

        # 上一帧物理 X 是否按下，用来只在真正的 X 按下沿开始新手势。
        self.prev_physical_down = False

        # 当前这一次 X 手势是否有效。
        # 如果 X 按下时带有 LB/RB，或按住 X 期间加入 LB/RB，
        # 本次手势会一直作废，直到 X 完全松开后重新按。
        self.active_gesture = False

        self.press_time = 0.0
        self.hold_started = False

        self.decay_start_time = None
        self.last_update_time = None

        self.last_axis_value = None


    def _send_brake(self, force=False):
        axis_value = brake_percent_to_axis(self.brake_percent)

        if not force and axis_value == self.last_axis_value:
            return

        event_value = axis_value & 0xFFFFFFFF

        self.left_event(event_value)
        self.right_event(event_value)

        self.last_axis_value = axis_value


    def _set_brake_percent(self, value):
        value = max(0.0, min(100.0, float(value)))

        if abs(value - self.brake_percent) < 1e-9:
            return

        self.brake_percent = value
        self._send_brake()


    def release_immediately(self):
        """立即把本功能施加的刹车释放到 0%。"""

        had_control = (
            self.last_axis_value is not None
            or self.brake_percent > 0.0
        )

        self.brake_percent = 0.0
        self.prev_down = False
        self.prev_physical_down = False
        self.active_gesture = False
        self.press_time = 0.0
        self.hold_started = False
        self.decay_start_time = None
        self.last_update_time = None

        if had_control:
            self._send_brake(force=True)

        # 发送一次 0% 后立即放弃“所有权”。
        # 这样断开期间不会每 0.25 秒持续覆盖其它刹车输入。
        self.last_axis_value = None


    def update(self, gamepad, now):
        if not ENABLE_X_BRAKE:
            return

        buttons = gamepad.wButtons

        physical_down = bool(buttons & XINPUT_GAMEPAD_X)
        shoulder_down = bool(
            buttons
            & (
                XINPUT_GAMEPAD_LEFT_SHOULDER
                | XINPUT_GAMEPAD_RIGHT_SHOULDER
            )
        )

        # ----------------------------------------------------
        # 1. 只在“物理 X 刚按下”时决定本次手势是否合法
        #
        #    X       -> 有效
        #    LB + X  -> 无效
        #    RB + X  -> 无效
        #    LB+RB+X -> 无效
        #
        # 无效后，即使先松开 LB/RB，只要 X 还没松开，
        # 本次 X 都不会重新生效。
        # ----------------------------------------------------

        if physical_down and not self.prev_physical_down:
            if shoulder_down:
                self.active_gesture = False
            else:
                self.active_gesture = True
                self.press_time = now
                self.hold_started = False
                self.decay_start_time = None
                self.last_update_time = now

                # 刚按下 X：立即增加一次刹车。
                self._set_brake_percent(
                    self.brake_percent
                    + max(0.0, float(X_BRAKE_TAP_INCREASE))
                )

        # ----------------------------------------------------
        # 2. X 按住过程中一旦加入 LB 或 RB，本次 X 立即作废
        # ----------------------------------------------------

        if physical_down and self.active_gesture and shoulder_down:
            self.active_gesture = False

        # 逻辑上的“有效 X 按下”。
        down = physical_down and self.active_gesture

        # ----------------------------------------------------
        # 3. 按住：超过长按判定后按“每秒增加量”连续增加
        # ----------------------------------------------------

        if down:
            held = now - self.press_time

            if held >= _X_BRAKE_HOLD_DELAY:
                if not self.hold_started:
                    self.hold_started = True
                    self.last_update_time = now

                else:
                    dt = max(0.0, now - self.last_update_time)
                    self.last_update_time = now

                    if dt > 0.0:
                        self._set_brake_percent(
                            self.brake_percent
                            + max(0.0, float(X_BRAKE_HOLD_INCREASE_PER_SEC)) * dt
                        )

            else:
                self.last_update_time = now

        # ----------------------------------------------------
        # 4. 有效 X 刚结束：记录衰减开始时间
        #
        # 包括：
        #   - 松开 X
        #   - 按住 X 时加入 LB/RB，导致本次 X 被取消
        # ----------------------------------------------------

        if (not down) and self.prev_down:
            self.hold_started = False
            self.decay_start_time = (
                now + max(0.0, float(X_BRAKE_DECAY_DELAY))
            )
            self.last_update_time = now

        # ----------------------------------------------------
        # 5. 松开/作废后：延迟结束再连续衰减
        # ----------------------------------------------------

        if (
            not down
            and self.brake_percent > 0.0
            and self.decay_start_time is not None
            and now >= self.decay_start_time
        ):
            decay_from = max(
                self.last_update_time,
                self.decay_start_time
            )
            dt = max(0.0, now - decay_from)
            self.last_update_time = now

            if dt > 0.0:
                self._set_brake_percent(
                    self.brake_percent
                    - max(0.0, float(X_BRAKE_DECAY_PER_SEC)) * dt
                )

                if self.brake_percent <= 0.0:
                    self.decay_start_time = None

                    # 已经发送过一次 0%，之后停止持续占用刹车轴。
                    self.last_axis_value = None

        # X 完全松开后，本次手势结束；下一次按下才能重新判断。
        if not physical_down:
            self.active_gesture = False

        self.prev_down = down
        self.prev_physical_down = physical_down


# ============================================================
# 主程序
# ============================================================

def main():
    # 读取配置
    try:
        load_config()
    except Exception as e:
        print("读取配置失败：")
        print(e)
        return

    print("正在连接 MSFS 2024 SimConnect...")

    # --------------------------------------------------------
    # SimConnect
    # --------------------------------------------------------

    try:
        sm = SimConnect()

    except Exception as e:
        print("SimConnect 连接失败：")
        print(e)
        time.sleep(2.0)
        return

    # --------------------------------------------------------
    # 普通油门事件
    # --------------------------------------------------------

    throttle_incr = Event(b"THROTTLE_INCR", sm)
    throttle_decr = Event(b"THROTTLE_DECR", sm)

    # --------------------------------------------------------
    # SMALL 微调油门事件
    # --------------------------------------------------------

    throttle_incr_small = Event(b"THROTTLE_INCR_SMALL", sm)

    throttle_decr_small = Event(b"THROTTLE_DECR_SMALL", sm)

    # --------------------------------------------------------
    # X 键渐进刹车事件
    #
    # 使用左右刹车轴 SET 事件，才能精确指定当前刹车强度。
    # --------------------------------------------------------

    x_brake = None

    if ENABLE_X_BRAKE:
        left_brake_set = Event(b"AXIS_LEFT_BRAKE_SET", sm)
        right_brake_set = Event(b"AXIS_RIGHT_BRAKE_SET", sm)

        x_brake = XBrakeController(
            left_brake_set,
            right_brake_set
        )

    # --------------------------------------------------------
    # A = 加油门
    # B = 减油门
    #
    # LB+A / LB+B = 微调
    # --------------------------------------------------------

    a_button = ThrottleButton(
        XINPUT_GAMEPAD_A,
        throttle_incr,
        throttle_incr_small,
        "A / THROTTLE_INCR"
    )

    b_button = ThrottleButton(
        XINPUT_GAMEPAD_B,
        throttle_decr,
        throttle_decr_small,
        "B / THROTTLE_DECR"
    )

    print("SimConnect 已连接。")
    print(f"XInput 手柄编号: {GAMEPAD_INDEX}")
    print()

    print("A：加大油门")
    print("B：减小油门")

    if ENABLE_LB_FINE_THROTTLE:
        print("LB + A：微调加大油门")
        print("LB + B：微调减小油门")
    else:
        print("LB 微调功能：关闭")

    print()

    print(f"普通短按 = {TAP_COUNT} 次事件")
    print(
        f"普通长按 = {HOLD_SEND_HZ} 次/秒"
        f"（延迟 {HOLD_DELAY:.2f}s 后开始）"
    )

    if ENABLE_LB_FINE_THROTTLE:
        print(f"微调短按 = {FINE_TAP_COUNT} 次 SMALL 事件")
        print("微调长按 = 不支持")

    print()

    if ENABLE_X_BRAKE:
        print("X：渐进刹车（LB/RB + X 无效）")
        print(f"X 轻按：+{X_BRAKE_TAP_INCREASE:.1f}%")
        print(
            f"X 长按：+{X_BRAKE_HOLD_INCREASE_PER_SEC:.1f}%/秒 "
            f"（按住 {_X_BRAKE_HOLD_DELAY:.2f}s 后开始连续增加）"
        )
        print(
            f"X 松开：等待 {X_BRAKE_DECAY_DELAY:.2f}s 后，"
            f"按 {X_BRAKE_DECAY_PER_SEC:.1f}%/秒衰减"
        )
    else:
        print("X 渐进刹车功能：关闭")

    print()
    print("Ctrl+C 退出")


    disconnected_reported = False

    try:
        while True:

            gamepad = read_gamepad(
                GAMEPAD_INDEX
            )

            # ------------------------------------------------
            # 手柄断开
            # ------------------------------------------------

            if gamepad is None:

                if not disconnected_reported:
                    print(
                        "手柄未找到/已断开，等待重新连接..."
                    )
                    disconnected_reported = True

                # 清空按键状态，
                # 避免重新连接后产生残留

                a_button.prev_physical_down = False
                b_button.prev_physical_down = False

                a_button.cancel_until_release()
                b_button.cancel_until_release()

                # 手柄断开时立即释放本功能施加的刹车，
                # 避免 X 在断开瞬间处于按下状态而导致刹车残留。
                if x_brake is not None:
                    x_brake.release_immediately()

                time.sleep(0.25)
                continue


            if disconnected_reported:
                print("手柄已重新连接。")
                disconnected_reported = False

            # ------------------------------------------------
            # 更新按钮
            # ------------------------------------------------

            now = time.perf_counter()

            a_button.update(gamepad, now)

            b_button.update(gamepad, now)

            if x_brake is not None:
                x_brake.update(gamepad, now)

            # ------------------------------------------------
            # 根据 POLL_HZ 设置扫描间隔
            # ------------------------------------------------

            time.sleep(1.0 / POLL_HZ)

    except KeyboardInterrupt:
        print("\n程序退出。")

    finally:
        # 退出程序时也确保不会留下本功能施加的刹车。
        if x_brake is not None:
            x_brake.release_immediately()

        sm.exit()


if __name__ == "__main__":
    main()