"""
OSWorld 任务浏览器
==================
展示 examples_zh 中的任务定义和 OSworld_screenshot 中的截图。

功能：
    - 按应用类型浏览任务
    - 搜索和过滤任务
    - 查看任务截图和详细配置
    - 分析并行化改造需求

运行方式：
    streamlit run dataviewer/osworld_viewer.py
"""

import streamlit as st
import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Any
import re

# ========================== 路径配置 ==========================
SCRIPT_DIR = Path(__file__).parent
EXAMPLES_ZH_DIR = SCRIPT_DIR / "OSWorld" / "examples_zh"
SCREENSHOT_DIR = SCRIPT_DIR / "OSWorld" / "OSworld_screenshot"
CLASSIFICATION_FILE = SCRIPT_DIR / "OSWorld" / "classification_results.json"

# ========================== 任务分类配置 ==========================
CATEGORY_INFO = {
    "information_search": {
        "name": "信息搜索类",
        "icon": "🔍",
        "color": "#3B82F6",
        "description": "从网上搜索、查询、浏览信息"
    },
    "settings": {
        "name": "设置类",
        "icon": "⚙️",
        "color": "#8B5CF6",
        "description": "修改软件、浏览器或系统中的设置"
    },
    "file_processing": {
        "name": "处理类",
        "icon": "📄",
        "color": "#10B981",
        "description": "对文件内容进行编辑、修改、创建"
    },
    "others": {
        "name": "其它类",
        "icon": "📦",
        "color": "#6B7280",
        "description": "其他未归类的任务"
    },
    "other": {
        "name": "其它类",
        "icon": "📦",
        "color": "#6B7280",
        "description": "其他未归类的任务"
    }
}

# ========================== 应用图标映射 ==========================
APP_ICONS = {
    "chrome": "🌐",
    "gimp": "🎨",
    "thunderbird": "📧",
    "libreoffice_calc": "📊",
    "libreoffice_writer": "📝",
    "libreoffice_impress": "📽️",
    "vs_code": "💻",
    "vlc": "🎬",
    "os": "🖥️",
    "multi_apps": "🔗"
}

# ========================== 颜色配置 ==========================
APP_COLORS = {
    "chrome": "#4285F4",
    "gimp": "#5C5543",
    "thunderbird": "#0A84FF",
    "libreoffice_calc": "#18A303",
    "libreoffice_writer": "#083FA1",
    "libreoffice_impress": "#D0410F",
    "vs_code": "#007ACC",
    "vlc": "#FF8800",
    "os": "#E95420",
    "multi_apps": "#9C27B0"
}

# ========================== 页面配置 ==========================
st.set_page_config(
    page_title="OSWorld 任务浏览器",
    page_icon="🌍",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ========================== 自定义样式 ==========================
st.markdown("""
<style>
    /* 主标题样式 */
    .main-header {
        font-family: 'Noto Sans SC', 'Source Han Sans CN', -apple-system, sans-serif;
        font-size: 2.2rem;
        font-weight: 700;
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        text-align: center;
        padding: 1rem 0;
        margin-bottom: 0.5rem;
    }
    
    /* 副标题 */
    .sub-header {
        text-align: center;
        color: #6B7280;
        font-size: 0.95rem;
        margin-bottom: 2rem;
    }
    
    /* 任务卡片 */
    .task-card {
        background: linear-gradient(145deg, #ffffff 0%, #f8fafc 100%);
        border-radius: 16px;
        padding: 1.5rem;
        margin: 1rem 0;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
        border: 1px solid #E5E7EB;
    }
    
    /* 指令文本 */
    .instruction-zh {
        font-size: 1.25rem;
        font-weight: 600;
        color: #1F2937;
        line-height: 1.6;
        margin-bottom: 0.75rem;
        padding: 1rem;
        background: linear-gradient(135deg, #EEF2FF 0%, #E0E7FF 100%);
        border-radius: 12px;
        border-left: 4px solid #6366F1;
    }
    
    .instruction-en {
        font-size: 0.95rem;
        color: #6B7280;
        font-style: italic;
        padding: 0.75rem 1rem;
        background: #F9FAFB;
        border-radius: 8px;
        margin-bottom: 1rem;
    }
    
    /* 应用标签 */
    .app-badge {
        display: inline-flex;
        align-items: center;
        padding: 0.375rem 0.75rem;
        border-radius: 20px;
        font-size: 0.85rem;
        font-weight: 500;
        margin-right: 0.5rem;
        margin-bottom: 0.5rem;
    }
    
    /* 统计卡片 */
    .stat-card {
        background: white;
        border-radius: 12px;
        padding: 1rem;
        text-align: center;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        border: 1px solid #E5E7EB;
    }
    
    .stat-number {
        font-size: 1.75rem;
        font-weight: 700;
        color: #4F46E5;
    }
    
    .stat-label {
        font-size: 0.85rem;
        color: #6B7280;
        margin-top: 0.25rem;
    }
    
    /* 截图容器 */
    .screenshot-container {
        border-radius: 12px;
        overflow: hidden;
        box-shadow: 0 10px 40px rgba(0,0,0,0.15);
        border: 1px solid #E5E7EB;
    }
    
    /* 配置块 */
    .config-block {
        background: #1E1E1E;
        color: #D4D4D4;
        border-radius: 8px;
        padding: 1rem;
        font-family: 'JetBrains Mono', 'Fira Code', monospace;
        font-size: 0.85rem;
        overflow-x: auto;
    }
    
    /* 侧边栏样式 */
    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #F8FAFC 0%, #EFF6FF 100%);
    }
    
    section[data-testid="stSidebar"] .stSelectbox label {
        font-weight: 600;
        color: #374151;
    }
    
    /* 任务列表项 */
    .task-list-item {
        padding: 0.75rem;
        border-radius: 8px;
        margin-bottom: 0.5rem;
        background: white;
        border: 1px solid #E5E7EB;
        cursor: pointer;
        transition: all 0.2s ease;
    }
    
    .task-list-item:hover {
        background: #EEF2FF;
        border-color: #6366F1;
    }
    
    /* 风险标签 */
    .risk-high {
        background: #FEE2E2;
        color: #B91C1C;
        padding: 0.25rem 0.5rem;
        border-radius: 4px;
        font-size: 0.75rem;
        font-weight: 600;
    }
    
    .risk-medium {
        background: #FEF3C7;
        color: #B45309;
        padding: 0.25rem 0.5rem;
        border-radius: 4px;
        font-size: 0.75rem;
        font-weight: 600;
    }
    
    .risk-low {
        background: #D1FAE5;
        color: #047857;
        padding: 0.25rem 0.5rem;
        border-radius: 4px;
        font-size: 0.75rem;
        font-weight: 600;
    }
    
    /* Expander 样式 */
    .streamlit-expanderHeader {
        font-weight: 600;
        color: #374151;
    }
    
    /* 隐藏默认 Streamlit 元素 */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    
    /* 分割线 */
    .divider {
        height: 1px;
        background: linear-gradient(90deg, transparent, #E5E7EB, transparent);
        margin: 1.5rem 0;
    }
</style>
""", unsafe_allow_html=True)


# ========================== 数据加载函数 ==========================

@st.cache_data
def load_classification_data() -> Dict[str, Dict]:
    """
    加载任务分类数据。
    
    Returns:
        Dict[str, Dict]: 以任务ID为键的分类结果字典
    """
    classification_by_id = {}
    
    if not CLASSIFICATION_FILE.exists():
        return classification_by_id
    
    try:
        with open(CLASSIFICATION_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        for result in data.get("results", []):
            task_id = result.get("task_id")
            if task_id:
                classification_by_id[task_id] = result
        
        return classification_by_id
    except Exception as e:
        st.warning(f"加载分类数据失败: {e}")
        return classification_by_id


@st.cache_data
def get_classification_stats() -> Dict:
    """
    获取分类统计信息。
    
    Returns:
        Dict: 分类统计数据
    """
    if not CLASSIFICATION_FILE.exists():
        return {}
    
    try:
        with open(CLASSIFICATION_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get("statistics", {})
    except:
        return {}


@st.cache_data
def load_all_tasks() -> Dict[str, List[Dict]]:
    """
    加载所有应用的任务数据。
    
    Returns:
        Dict[str, List[Dict]]: 按应用分类的任务字典，
            键为应用名称，值为该应用下的所有任务列表。
    """
    tasks_by_app = {}
    
    if not EXAMPLES_ZH_DIR.exists():
        st.error(f"目录不存在: {EXAMPLES_ZH_DIR}")
        return tasks_by_app
    
    for app_dir in EXAMPLES_ZH_DIR.iterdir():
        if app_dir.is_dir() and not app_dir.name.startswith('.'):
            app_name = app_dir.name
            tasks_by_app[app_name] = []
            
            for json_file in app_dir.glob("*.json"):
                try:
                    with open(json_file, 'r', encoding='utf-8') as f:
                        task_data = json.load(f)
                        task_data['_file_path'] = str(json_file)
                        tasks_by_app[app_name].append(task_data)
                except Exception as e:
                    st.warning(f"加载文件失败: {json_file}, 错误: {e}")
    
    return tasks_by_app


def get_screenshot_path(app_name: str, task_id: str) -> Optional[Path]:
    """
    获取任务对应的截图路径。
    
    Args:
        app_name (str): 应用名称
        task_id (str): 任务 ID
        
    Returns:
        Optional[Path]: 截图文件路径，如果不存在则返回 None
    """
    screenshot_path = SCREENSHOT_DIR / app_name / f"{task_id}.png"
    return screenshot_path if screenshot_path.exists() else None


def analyze_parallelization_risks(task: Dict) -> Dict[str, Any]:
    """
    分析任务的并行化改造风险。
    
    Args:
        task (Dict): 任务配置字典
        
    Returns:
        Dict[str, Any]: 风险分析结果，包含风险等级和具体问题列表
    """
    risks = {
        "level": "low",
        "issues": [],
        "path_remapping": [],
        "profile_risks": []
    }
    
    task_str = json.dumps(task, ensure_ascii=False)
    
    # 检查硬编码路径
    hardcoded_paths = re.findall(r'/home/user/[^\s"\']+', task_str)
    for path in hardcoded_paths:
        if '/shared/' not in path and '/mnt/shared/' not in path:
            risks["path_remapping"].append(path)
    
    # 检查 Profile 风险（Thunderbird, Chrome）
    app_name = task.get("snapshot", "").lower()
    if app_name in ["thunderbird", "chrome"]:
        if "profile" in task_str.lower():
            risks["profile_risks"].append(f"{app_name} Profile 加载存在锁冲突风险")
            risks["level"] = "high"
    
    # 检查评测器路径
    evaluator = task.get("evaluator", {})
    if isinstance(evaluator.get("result"), dict):
        result_path = evaluator["result"].get("path", "")
        if result_path and "/shared/" not in result_path:
            risks["issues"].append(f"评测结果路径需重映射: {result_path}")
    
    # 综合评估风险等级
    if risks["path_remapping"]:
        if risks["level"] != "high":
            risks["level"] = "medium"
        risks["issues"].append(f"发现 {len(risks['path_remapping'])} 个硬编码路径需重映射")
    
    if risks["profile_risks"]:
        risks["issues"].extend(risks["profile_risks"])
    
    return risks


# ========================== UI 组件函数 ==========================

def render_app_badge(app_name: str) -> str:
    """
    渲染应用标签 HTML。
    
    Args:
        app_name (str): 应用名称
        
    Returns:
        str: HTML 字符串
    """
    icon = APP_ICONS.get(app_name, "📦")
    color = APP_COLORS.get(app_name, "#6B7280")
    return f"""
    <span class="app-badge" style="background: {color}22; color: {color}; border: 1px solid {color}44;">
        {icon} {app_name}
    </span>
    """


def render_risk_badge(level: str) -> str:
    """
    渲染风险等级标签。
    
    Args:
        level (str): 风险等级 (high/medium/low)
        
    Returns:
        str: HTML 字符串
    """
    labels = {
        "high": ("🔴 高风险", "risk-high"),
        "medium": ("🟡 中风险", "risk-medium"),
        "low": ("🟢 低风险", "risk-low")
    }
    label, css_class = labels.get(level, ("❓ 未知", "risk-low"))
    return f'<span class="{css_class}">{label}</span>'


def render_category_badge(category: str) -> str:
    """
    渲染任务分类标签。
    
    Args:
        category (str): 分类名称
        
    Returns:
        str: HTML 字符串
    """
    info = CATEGORY_INFO.get(category, CATEGORY_INFO.get("other", {}))
    icon = info.get("icon", "📦")
    name = info.get("name", category)
    color = info.get("color", "#6B7280")
    
    return f'''
    <span style="
        display: inline-flex;
        align-items: center;
        padding: 0.25rem 0.6rem;
        border-radius: 16px;
        font-size: 0.8rem;
        font-weight: 500;
        background: {color}15;
        color: {color};
        border: 1px solid {color}40;
    ">
        {icon} {name}
    </span>
    '''


def get_category_icon(category: str) -> str:
    """
    获取分类图标。
    
    Args:
        category (str): 分类名称
        
    Returns:
        str: 分类图标
    """
    info = CATEGORY_INFO.get(category, {})
    return info.get("icon", "📦")


def render_task_detail(task: Dict, app_name: str, classification_data: Dict):
    """
    渲染任务详情页面。
    
    Args:
        task (Dict): 任务配置字典
        app_name (str): 应用名称
        classification_data (Dict): 分类数据字典
    """
    task_id = task.get("id", "未知")
    instruction_zh = task.get("instruction_zh", task.get("instruction", "无指令"))
    instruction_en = task.get("instruction", "")
    
    # 获取分类信息
    task_classification = classification_data.get(task_id, {})
    category = task_classification.get("category", "unknown")
    confidence = task_classification.get("confidence", 0)
    reason = task_classification.get("reason", "")
    
    # 并行化风险分析
    risks = analyze_parallelization_risks(task)
    
    # 主布局：左侧截图，右侧信息
    col_img, col_info = st.columns([1, 1])
    
    with col_img:
        st.markdown("#### 📸 任务截图")
        screenshot_path = get_screenshot_path(app_name, task_id)
        if screenshot_path:
            st.image(
                str(screenshot_path), 
                use_container_width=True,
                caption=f"Task ID: {task_id}"
            )
        else:
            st.info("🖼️ 暂无截图")
            st.markdown(f"*预期路径: `{SCREENSHOT_DIR / app_name / f'{task_id}.png'}`*")
    
    with col_info:
        # 任务指令
        st.markdown("#### 📋 任务指令")
        st.markdown(f'<div class="instruction-zh">{instruction_zh}</div>', unsafe_allow_html=True)
        if instruction_en and instruction_en != instruction_zh:
            st.markdown(f'<div class="instruction-en">🇬🇧 {instruction_en}</div>', unsafe_allow_html=True)
        
        # 元信息
        st.markdown("#### ℹ️ 任务信息")
        info_cols = st.columns(4)
        with info_cols[0]:
            st.markdown(f"**应用**")
            st.markdown(render_app_badge(app_name), unsafe_allow_html=True)
        with info_cols[1]:
            st.markdown(f"**任务分类**")
            st.markdown(render_category_badge(category), unsafe_allow_html=True)
        with info_cols[2]:
            st.markdown(f"**快照**")
            st.code(task.get("snapshot", "N/A"), language=None)
        with info_cols[3]:
            st.markdown(f"**并行化风险**")
            st.markdown(render_risk_badge(risks["level"]), unsafe_allow_html=True)
        
        # 分类详情（如果有）
        if category != "unknown" and reason:
            st.markdown(f"""
            <div style="
                background: linear-gradient(135deg, #F0FDF4 0%, #DCFCE7 100%);
                border-left: 4px solid #10B981;
                padding: 0.75rem 1rem;
                border-radius: 8px;
                margin-top: 0.5rem;
                font-size: 0.9rem;
            ">
                <strong>🤖 分类理由:</strong> {reason}
                <span style="color: #6B7280; margin-left: 0.5rem;">(置信度: {confidence:.0%})</span>
            </div>
            """, unsafe_allow_html=True)
        
        # 相关应用
        related_apps = task.get("related_apps", [])
        if related_apps:
            st.markdown("**相关应用:** " + " ".join([render_app_badge(app) for app in related_apps]), unsafe_allow_html=True)
    
    # 分割线
    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
    
    # 详细配置（可折叠）
    col1, col2 = st.columns(2)
    
    with col1:
        with st.expander("⚙️ 初始化配置 (Config)", expanded=False):
            config = task.get("config", [])
            if config:
                for i, step in enumerate(config):
                    step_type = step.get("type", "unknown")
                    st.markdown(f"**Step {i+1}: `{step_type}`**")
                    st.json(step.get("parameters", {}))
            else:
                st.info("无配置步骤")
    
    with col2:
        with st.expander("📊 评测器配置 (Evaluator)", expanded=False):
            evaluator = task.get("evaluator", {})
            if evaluator:
                st.json(evaluator)
            else:
                st.info("无评测器配置")
    
    # 并行化改造建议
    if risks["issues"]:
        with st.expander("⚠️ 并行化改造建议", expanded=True):
            for issue in risks["issues"]:
                st.warning(issue)
            
            if risks["path_remapping"]:
                st.markdown("**需要重映射的路径：**")
                for path in risks["path_remapping"]:
                    st.code(f"{path}  →  /home/user/shared/{task_id}/...")
    
    # 原始 JSON
    with st.expander("📄 原始 JSON", expanded=False):
        # 移除内部使用的字段
        display_task = {k: v for k, v in task.items() if not k.startswith('_')}
        st.json(display_task)


def render_task_list(tasks: List[Dict], app_name: str, classification_data: Dict):
    """
    渲染任务列表，点击任务时更新 session_state。
    
    Args:
        tasks (List[Dict]): 任务列表
        app_name (str): 应用名称
        classification_data (Dict): 分类数据字典
    """
    for task in tasks:
        task_id = task.get("id", "未知")
        instruction_zh = task.get("instruction_zh", task.get("instruction", "无指令"))
        
        # 截断过长的指令
        display_instruction = instruction_zh[:40] + "..." if len(instruction_zh) > 40 else instruction_zh
        
        # 获取分类图标
        task_classification = classification_data.get(task_id, {})
        category = task_classification.get("category", "unknown")
        category_icon = get_category_icon(category)
        
        # 检查是否有截图
        has_screenshot = get_screenshot_path(app_name, task_id) is not None
        screenshot_indicator = "📸" if has_screenshot else "  "
        
        # 检查是否是当前选中的任务
        is_selected = (
            st.session_state.get('selected_task_id') == task_id and 
            st.session_state.get('selected_app') == app_name
        )
        
        # 组合显示：分类图标 + 截图指示 + 指令
        button_text = f"{category_icon}{screenshot_indicator} {display_instruction}"
        
        if st.button(
            button_text,
            key=f"task_{app_name}_{task_id}",
            use_container_width=True,
            type="primary" if is_selected else "secondary"
        ):
            # 同时保存任务 ID 和所属应用
            st.session_state['selected_task_id'] = task_id
            st.session_state['selected_app'] = app_name
            st.rerun()


# ========================== 主函数 ==========================

def main():
    """
    主函数：构建 Streamlit 应用界面。
    """
    # 加载数据
    tasks_by_app = load_all_tasks()
    classification_data = load_classification_data()
    classification_stats = get_classification_stats()
    
    if not tasks_by_app:
        st.error("未找到任何任务数据，请检查目录配置。")
        return
    
    # ==================== 侧边栏 ====================
    with st.sidebar:
        st.markdown("## 🌍 OSWorld 任务浏览器")
        st.markdown("---")
        
        # 应用选择器
        app_names = sorted(tasks_by_app.keys())
        app_options = [f"{APP_ICONS.get(app, '📦')} {app}" for app in app_names]
        
        selected_option = st.selectbox(
            "📂 选择应用类型",
            app_options,
            index=0
        )
        selected_app = app_names[app_options.index(selected_option)]
        
        # 分类筛选器
        category_options = ["全部"] + [
            f"{info['icon']} {info['name']}" 
            for cat, info in CATEGORY_INFO.items() 
            if cat not in ["other"]  # 避免重复
        ]
        category_keys = ["all", "information_search", "settings", "file_processing", "others"]
        
        selected_category_option = st.selectbox(
            "🏷️ 筛选任务分类",
            category_options,
            index=0,
            help="按任务类型筛选"
        )
        selected_category = category_keys[category_options.index(selected_category_option)]
        
        # 搜索框
        search_query = st.text_input(
            "🔍 搜索任务",
            placeholder="输入关键词...",
            help="支持中英文指令搜索"
        )
        
        st.markdown("---")
        
        # 统计信息
        total_tasks = sum(len(tasks) for tasks in tasks_by_app.values())
        current_app_tasks = len(tasks_by_app.get(selected_app, []))
        
        st.markdown("### 📊 统计")
        stat_cols = st.columns(2)
        with stat_cols[0]:
            st.metric("总任务数", total_tasks)
        with stat_cols[1]:
            st.metric("当前应用", current_app_tasks)
        
        # 分类统计（如果有分类数据）
        if classification_stats:
            st.markdown("### 🏷️ 分类分布")
            for cat, count in classification_stats.items():
                info = CATEGORY_INFO.get(cat, CATEGORY_INFO.get("other", {}))
                icon = info.get("icon", "📦")
                name = info.get("name", cat)
                color = info.get("color", "#6B7280")
                percentage = count / total_tasks * 100 if total_tasks > 0 else 0
                
                st.markdown(f"""
                <div style="
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    padding: 0.3rem 0;
                    border-bottom: 1px solid #E5E7EB;
                ">
                    <span>{icon} {name}</span>
                    <span style="
                        background: {color}20;
                        color: {color};
                        padding: 0.15rem 0.5rem;
                        border-radius: 12px;
                        font-size: 0.8rem;
                        font-weight: 600;
                    ">{count} ({percentage:.1f}%)</span>
                </div>
                """, unsafe_allow_html=True)
        
        st.markdown("---")
        
        # 任务列表
        st.markdown(f"### 📋 {selected_app} 任务")
        
        # 过滤任务
        tasks = tasks_by_app.get(selected_app, [])
        
        # 按分类筛选
        if selected_category != "all":
            tasks = [
                t for t in tasks
                if classification_data.get(t.get("id", ""), {}).get("category") == selected_category
            ]
        
        # 按搜索词筛选
        if search_query:
            tasks = [
                t for t in tasks 
                if search_query.lower() in t.get("instruction_zh", "").lower() 
                or search_query.lower() in t.get("instruction", "").lower()
                or search_query.lower() in t.get("id", "").lower()
            ]
        
        if selected_category != "all" or search_query:
            st.caption(f"找到 {len(tasks)} 个匹配结果")
        
        # 渲染任务列表
        if tasks:
            render_task_list(tasks, selected_app, classification_data)
        else:
            st.info("没有找到匹配的任务")
    
    # ==================== 主内容区 ====================
    
    # 页面标题
    st.markdown('<h1 class="main-header">🌍 OSWorld 任务浏览器</h1>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">浏览、搜索和分析 OSWorld GUI 任务数据集</p>', unsafe_allow_html=True)
    
    # 获取选中的任务和对应的应用
    selected_task_id = st.session_state.get('selected_task_id')
    task_app = st.session_state.get('selected_app')
    
    if selected_task_id and task_app:
        # 查找选中的任务（在保存的应用中查找）
        selected_task = None
        for task in tasks_by_app.get(task_app, []):
            if task.get("id") == selected_task_id:
                selected_task = task
                break
        
        if selected_task:
            # 显示返回按钮
            col_back, col_title = st.columns([1, 5])
            with col_back:
                if st.button("⬅️ 返回列表"):
                    st.session_state['selected_task_id'] = None
                    st.session_state['selected_app'] = None
                    st.rerun()
            
            render_task_detail(selected_task, task_app, classification_data)
        else:
            st.warning(f"未找到任务: {selected_task_id}")
            if st.button("返回列表"):
                st.session_state['selected_task_id'] = None
                st.session_state['selected_app'] = None
                st.rerun()
    else:
        # 默认显示概览
        st.markdown("### 📊 数据集概览")
        
        # 应用统计卡片
        cols = st.columns(5)
        for i, (app_name, tasks) in enumerate(sorted(tasks_by_app.items())):
            with cols[i % 5]:
                icon = APP_ICONS.get(app_name, "📦")
                color = APP_COLORS.get(app_name, "#6B7280")
                st.markdown(f"""
                <div class="stat-card" style="border-left: 4px solid {color};">
                    <div style="font-size: 1.5rem;">{icon}</div>
                    <div class="stat-number">{len(tasks)}</div>
                    <div class="stat-label">{app_name}</div>
                </div>
                """, unsafe_allow_html=True)
        
        # 分类统计概览（如果有分类数据）
        if classification_stats:
            st.markdown("---")
            st.markdown("### 🏷️ 任务分类统计")
            
            cat_cols = st.columns(4)
            for i, (cat, count) in enumerate(classification_stats.items()):
                with cat_cols[i % 4]:
                    info = CATEGORY_INFO.get(cat, CATEGORY_INFO.get("other", {}))
                    icon = info.get("icon", "📦")
                    name = info.get("name", cat)
                    color = info.get("color", "#6B7280")
                    percentage = count / total_tasks * 100 if total_tasks > 0 else 0
                    
                    st.markdown(f"""
                    <div style="
                        background: linear-gradient(135deg, {color}10 0%, {color}20 100%);
                        border-radius: 12px;
                        padding: 1rem;
                        text-align: center;
                        border: 1px solid {color}30;
                    ">
                        <div style="font-size: 2rem;">{icon}</div>
                        <div style="font-size: 1.5rem; font-weight: 700; color: {color};">{count}</div>
                        <div style="font-size: 0.85rem; color: #6B7280;">{name}</div>
                        <div style="font-size: 0.75rem; color: #9CA3AF;">{percentage:.1f}%</div>
                    </div>
                    """, unsafe_allow_html=True)
        
        st.markdown("---")
        
        # 快速预览
        st.markdown("### 🚀 快速开始")
        st.info("👈 从左侧边栏选择应用类型和任务分类，然后点击任务查看详情。")
        
        # 显示当前应用的前几个任务预览
        st.markdown(f"### 📋 {selected_app} 任务预览")
        
        preview_tasks = tasks_by_app.get(selected_app, [])[:6]
        if preview_tasks:
            preview_cols = st.columns(3)
            for i, task in enumerate(preview_tasks):
                with preview_cols[i % 3]:
                    task_id = task.get("id", "")
                    instruction_zh = task.get("instruction_zh", task.get("instruction", ""))[:80]
                    screenshot_path = get_screenshot_path(selected_app, task_id)
                    
                    # 获取分类信息
                    task_classification = classification_data.get(task_id, {})
                    category = task_classification.get("category", "unknown")
                    category_icon = get_category_icon(category)
                    
                    with st.container():
                        if screenshot_path:
                            st.image(str(screenshot_path), use_container_width=True)
                        else:
                            st.markdown(f"""
                            <div style="background: #F3F4F6; border-radius: 8px; padding: 2rem; text-align: center; color: #9CA3AF;">
                                🖼️ 暂无截图
                            </div>
                            """, unsafe_allow_html=True)
                        
                        # 显示分类标签
                        if category != "unknown":
                            st.markdown(render_category_badge(category), unsafe_allow_html=True)
                        
                        st.markdown(f"**{instruction_zh}...**" if len(instruction_zh) == 80 else f"**{instruction_zh}**")
                        
                        if st.button("查看详情", key=f"preview_{selected_app}_{task_id}"):
                            st.session_state['selected_task_id'] = task_id
                            st.session_state['selected_app'] = selected_app
                            st.rerun()


if __name__ == "__main__":
    main()

