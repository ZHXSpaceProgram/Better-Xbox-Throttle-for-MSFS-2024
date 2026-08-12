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


def load_config():
    global GAMEPAD_INDEX, POLL_HZ, TAP_COUNT, HOLD_SEND_HZ, HOLD_DELAY, ENABLE_LB_FINE_THROTTLE, FINE_TAP_COUNT
    config = configparser.ConfigParser()
    if getattr(sys, "frozen", False):
        config_path = Path(sys.executable).with_name("config.ini")
    else:
        config_path = Path(__file__).with_name("config.ini")
    config.read(
        config_path,
        encoding="utf-8"
    )
    GAMEPAD_INDEX = config.getint("gamepad", "index")
    POLL_HZ = config.getfloat("gamepad", "poll_hz")
    TAP_COUNT = config.getint("throttle", "tap_count")
    HOLD_SEND_HZ = config.getfloat("throttle", "hold_send_hz")
    HOLD_DELAY = config.getfloat("throttle", "hold_delay")
    ENABLE_LB_FINE_THROTTLE = config.getboolean("fine_throttle", "enabled")
    FINE_TAP_COUNT = config.getint("fine_throttle", "fine_tap_count")


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

            # ------------------------------------------------
            # 根据 POLL_HZ 设置扫描间隔
            # ------------------------------------------------

            time.sleep(1.0 / POLL_HZ)

    except KeyboardInterrupt:
        print("\n程序退出。")

    finally:
        sm.exit()


if __name__ == "__main__":
    main()