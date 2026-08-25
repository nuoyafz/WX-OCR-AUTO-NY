"""
识别阈值统一源（tuning 收敛）
================================================================
把散落在 ocr_engine.py / message_parser.py 里的魔法数字收敛到此处，
做到：改一个值全链路生效、微信升级/DPI 变化时只需校准这里。
所有默认值与原实现保持一致，**不要随意改动**，否则会改变识别行为。
写在每个常量旁的 "*" 表示其被哪些模块使用。
"""
import logging

logger = logging.getLogger(__name__)

# ============ 气泡文本行合并（ocr_engine._merge_bubble_lines）============
BUBBLE_MERGE_Y_GAP_FACTOR = 1.35        # 同气泡合并的 Y 间距 = 行高中位数 × 该系数
BUBBLE_MERGE_X_OVERLAP = 0.3            # X 重叠度阈值兜底
BUBBLE_MERGE_X_ALIGN_EPS = 36           # 左右边界"对齐"容忍像素（px）
BUBBLE_MERGE_X_MIN_OVERLAP = -24        # 允许的最小 X 重叠（负=容忍 1 字符宽的错位）

# ============ 消息解析分组（message_parser._group_by_bubble）============
# 同一条"消息气泡"内多条 OCR 行合并时的最大 Y 间距（px）。
# 不同来源过小时会把相邻两条消息误并成一条；过大则把多行消息切碎。
BUBBLE_GROUP_GAP_PX = 40.0

# ============ 发送者位置判定（ocr_engine.identify_senders）V3 ============
SENDER_LEFT_RATIO = 0.35        # left_x < w*ratio 且 center_x < w*0.45 => other
SENDER_LEFT_CENTER_RATIO = 0.45
SENDER_RIGHT_RATIO = 0.65        # right_x > w*ratio 且 center_x > w*0.55 => me
SENDER_RIGHT_CENTER_RATIO = 0.55
SENDER_FALLBACK_OTHER_RATIO = 0.48   # center_x < w*ratio => other
SENDER_FALLBACK_ME_RATIO = 0.52      # center_x > w*ratio => me

# ============ 发送者位置强化（ocr_engine.identify_senders_v4）V4 ============
SENDER_V4_RIGHT_STRONG_RATIO = 0.68   # right_ratio > ratio 且 center>w*0.60 => 强 me
SENDER_V4_RIGHT_STRONG_CENTER = 0.60
SENDER_V4_RIGHT_HARD_RATIO = 0.72     # right_ratio > ratio => 强制 me（未采到绿时兜底）
SENDER_V4_LEFT_STRONG_RATIO = 0.30    # left_ratio < ratio 且 center<w*0.42 => 强 other
SENDER_V4_LEFT_STRONG_CENTER = 0.42
SENDER_V4_GREEN_CONF_POS = 0.92       # 绿气泡 + 位置强 me 的置信度
SENDER_V4_GREEN_CONF = 0.85           # 仅绿气泡的置信度

# ============ 发送者历史一致性（identify_senders_v4 的 V4.5）============
SENDER_HISTORY_EXPIRE_FRAMES = 8       # 超过 N 帧未更新的位置桶视为失效
SENDER_HISTORY_CONFIRM_FRAMES = 2      # 同一位置连续 >=N 帧一致才采信
SENDER_HISTORY_MAX_BUCKETS = 400       # 桶数上限，超出截尾
SENDER_HISTORY_KEEP_BUCKETS = 200      # 截尾时保留的桶数

# ============ 群聊成员昵称匹配（ocr_engine._group_detect_and_strip_member_name）============
GROUP_NICK_GAP_FACTOR_MIN = 0.6        # 昵称在气泡上方最小间距 = 行高 × 系数
GROUP_NICK_GAP_FACTOR_MAX = 3.2        # 昵称在气泡上方最大间距
GROUP_NICK_GAP_ABS_MIN = 3             # 最小间距下限（px）
GROUP_NICK_GAP_ABS_MAX = 14            # 最大间距下限（px）
GROUP_NICK_X_RATIO = 0.6               # 昵称 x 中心不得超过 气泡左缘 + 气泡宽 × ratio
GROUP_NICK_MAX_LEN = 12                # 昵称最大长度

# ============ 群聊投票权重（ocr_engine.recognize_with_group_enhance）============
GROUP_VOTE_TITLE_GROUP = 0.85          # 标题判群
GROUP_VOTE_TITLE_PERSONAL = 0.75       # 标题判私
GROUP_VOTE_STRUCT_SCALE = 0.55          # 成员命中基础分（+0.08/成员）
GROUP_VOTE_STRUCT_PER_MEMBER = 0.08
GROUP_VOTE_STRUCT_CAP = 0.95           # 成员命中封顶
GROUP_VOTE_DECIDE_THRESHOLD = 0.40     # 低于该票数视为 unknown


def load_tuning(config=None):
    """
    若 config.yaml 提供 [ocr.tuning] 或 [wechat.tuning] 段，可覆盖以上默认阈值。
    约定：键名与上面的常量同名。未提供的项保持默认。
    返回 dict（只读覆盖表），供需要动态读配置的模块使用。
    """
    overrides = {}
    if not config:
        return overrides
    try:
        sec = (config.get("ocr") or {}).get("tuning") or \
              (config.get("wechat") or {}).get("tuning") or {}
        for k, v in sec.items():
            if k in globals() and isinstance(v, (int, float)):
                overrides[k] = v
    except Exception as e:
        logger.warning(f"[tuning] 加载配置覆盖失败: {e}")
    if overrides:
        logger.info(f"[tuning] 应用配置覆盖率: {list(overrides)}")
    return overrides


# 供模块用当前生效值（load_tuning 后可替换，默认即为常量本身）
def build_tuning_map():
    """返回一份完整的 {常量名: 值}，供 load_tuning 覆盖。"""
    keys = [
        "BUBBLE_GROUP_GAP_PX",
        "SENDER_LEFT_RATIO", "SENDER_LEFT_CENTER_RATIO",
        "SENDER_RIGHT_RATIO", "SENDER_RIGHT_CENTER_RATIO",
        "SENDER_FALLBACK_OTHER_RATIO", "SENDER_FALLBACK_ME_RATIO",
    ]
    return {k: globals()[k] for k in keys}