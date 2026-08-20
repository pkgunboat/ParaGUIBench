import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import pandas as pd
from datetime import datetime
from typing import List, Dict, Any, Optional
import sys
import os

# 添加父目录到路径以便导入 record_template
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dataviewer.record_template import RecordTemplate, Agent, Device

# 页面配置
st.set_page_config(
    page_title="Multi-Agent Timeline Visualizer",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 自定义 CSS
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: bold;
        color: #1f77b4;
        text-align: center;
        margin-bottom: 1rem;
    }
    .metric-card {
        background-color: #f0f2f6;
        padding: 1rem;
        border-radius: 0.5rem;
        text-align: center;
    }
    .level-indicator {
        font-size: 1.2rem;
        font-weight: bold;
        padding: 0.5rem;
        border-radius: 0.3rem;
        margin-bottom: 1rem;
    }
    .level-1 { background-color: #e1bee7; color: #4a148c; }
    .level-2 { background-color: #bbdefb; color: #0d47a1; }
    .level-3 { background-color: #fff9c4; color: #f57f17; }
</style>
""", unsafe_allow_html=True)

def load_record(file_path: str) -> RecordTemplate:
    """
    加载记录文件。
    
    Args:
        file_path (str): JSON 文件路径。
        
    Returns:
        RecordTemplate: 记录模板实例。
    """
    return RecordTemplate.load_from_file(file_path)

def get_agent_color(agent_type: str) -> str:
    """
    根据智能体类型返回颜色。
    
    Args:
        agent_type (str): 智能体类型。
        
    Returns:
        str: 颜色代码。
    """
    colors = {
        "planner": "#9c27b0",  # 紫色
        "gui": "#2196f3",      # 蓝色
        "code": "#ff9800"      # 橙色
    }
    return colors.get(agent_type, "#757575")

def get_agent_time_range(agent: Agent) -> tuple:
    """
    从 agent 的 rounds 中计算实际的时间范围。
    
    Args:
        agent (Agent): 智能体实例。
        
    Returns:
        tuple: (start_timestamp, end_timestamp, duration)
    """
    if not agent.rounds:
        if agent.recording:
            return (agent.recording.start_timestamp, agent.recording.end_timestamp, agent.recording.duration)
        return (0, 0, 0)
    
    # 直接从 rounds 中取最早开始和最晚结束
    min_start = min(r.model_prediction.start_timestamp for r in agent.rounds)
    max_end = max(
        r.action_execution.end_timestamp if r.action_execution and r.action_execution.end_timestamp > 0 
        else r.model_prediction.end_timestamp 
        for r in agent.rounds
    )
    
    return (min_start, max_end, max_end - min_start)


def create_level1_timeline(record: RecordTemplate) -> go.Figure:
    """
    创建第1级时间轴：总任务级视图（按设备分组）。
    
    Args:
        record (RecordTemplate): 记录模板实例。
        
    Returns:
        go.Figure: Plotly 图表对象。
    """
    fig = go.Figure()
    
    # 添加协调者（Control Center）底层背景条
    fig.add_trace(go.Bar(
        name="coordinator_bg",
        x=[record.metadata.duration],
        y=["Control Center"],
        base=[0],
        orientation='h',
        marker=dict(
            color='rgba(156, 39, 176, 0.2)',  # 淡紫色背景
            line=dict(color='#9c27b0', width=2)
        ),
        hovertemplate=(
            "<b>Coordinator</b><br>"
            f"Model: {record.coordinator.model_name}<br>"
            f"Duration: {record.metadata.duration:.2f}s<br>"
            f"Rounds: {len(record.coordinator.rounds)}<br>"
            "<extra></extra>"
        ),
        showlegend=False
    ))
    
    # 在 Control Center 条上叠加 Plan Agent 的各轮时间段
    plan_think_color = '#7b1fa2'  # 深紫色 - 思考
    plan_action_color = '#ce93d8'  # 浅紫色 - 执行
    
    for round_data in record.coordinator.rounds:
        mp = round_data.model_prediction
        ae = round_data.action_execution
        
        # Plan Agent 思考阶段
        think_start = mp.start_timestamp - record.metadata.start_timestamp
        fig.add_trace(go.Bar(
            name=f"Plan R{round_data.round_id} Think",
            x=[mp.duration],
            y=["Control Center"],
            base=[think_start],
            orientation='h',
            marker=dict(
                color=plan_think_color,
                line=dict(color='white', width=1)
            ),
            hovertemplate=(
                f"<b>Plan Agent Round {round_data.round_id} - Think</b><br>"
                f"Duration: {mp.duration:.2f}s<br>"
                "<extra></extra>"
            ),
            showlegend=False
        ))
        
        # Plan Agent 执行阶段（dispatch/等待子agent）
        if ae:
            action_start = ae.start_timestamp - record.metadata.start_timestamp
            fig.add_trace(go.Bar(
                name=f"Plan R{round_data.round_id} Action",
                x=[ae.duration],
                y=["Control Center"],
                base=[action_start],
                orientation='h',
                marker=dict(
                    color=plan_action_color,
                    line=dict(color='white', width=1)
                ),
                hovertemplate=(
                    f"<b>Plan Agent Round {round_data.round_id} - Dispatch</b><br>"
                    f"Duration: {ae.duration:.2f}s<br>"
                    "<extra></extra>"
                ),
                showlegend=False
            ))
    
    # 添加各设备上的智能体（按设备合并显示）
    for device in record.devices:
        if not device.agents:
            continue
        
        # 计算该设备上所有 agent 的时间范围
        all_starts = []
        all_ends = []
        for agent in device.agents:
            start_ts, end_ts, duration = get_agent_time_range(agent)
            if duration > 0:
                all_starts.append(start_ts)
                all_ends.append(end_ts)
        
        if all_starts and all_ends:
            device_start = min(all_starts)
            device_end = max(all_ends)
            device_duration = device_end - device_start
            base_offset = device_start - record.metadata.start_timestamp
            
            # 统计该设备上的 agent 数量（按 agent_id 去重）
            unique_agents = set(a.agent_id for a in device.agents)
            total_calls = len(device.agents)
            
            fig.add_trace(go.Bar(
                name=device.device_id,
                x=[device_duration],
                y=[device.device_id],
                base=[base_offset],
                orientation='h',
                marker=dict(
                    color=get_agent_color("code"),
                    line=dict(color='white', width=2)
                ),
                hovertemplate=(
                    f"<b>{device.device_id}</b><br>"
                    f"Agents: {len(unique_agents)} ({total_calls} calls)<br>"
                    f"Duration: {device_duration:.2f}s<br>"
                    f"Start: {base_offset:.2f}s<br>"
                    "<extra></extra>"
                ),
                showlegend=False
            ))
    
    fig.update_layout(
        title="Level 1: Global Task Timeline (Grouped by Device)",
        xaxis_title="Time (seconds from start)",
        yaxis_title="Device",
        height=max(400, (len(record.devices) + 1) * 80),
        barmode='overlay',
        hovermode='closest',
        plot_bgcolor='rgba(240,240,240,0.5)'
    )
    
    return fig

def create_level2_timeline(record: RecordTemplate, device_id: str) -> go.Figure:
    """
    创建第2级时间轴：单设备任务级视图。
    
    Args:
        record (RecordTemplate): 记录模板实例。
        device_id (str): 设备 ID。
        
    Returns:
        go.Figure: Plotly 图表对象。
    """
    fig = go.Figure()
    
    # 找到对应的设备
    device = None
    for d in record.devices:
        if d.device_id == device_id:
            device = d
            break
    
    if not device or not device.agents:
        st.warning(f"No agents found on device: {device_id}")
        return fig
    
    # 按 agent_id 分组，同一个 agent 的多次调用显示在同一行
    from collections import OrderedDict
    agent_groups = OrderedDict()  # agent_id -> list of agent objects
    for agent in device.agents:
        aid = agent.agent_id
        if aid not in agent_groups:
            agent_groups[aid] = []
        agent_groups[aid].append(agent)
    
    # 先添加 Plan Agent 时间线（显示在顶部）
    plan_think_color = '#7b1fa2'  # 深紫色 - 思考
    plan_action_color = '#ce93d8'  # 浅紫色 - 执行
    
    for round_data in record.coordinator.rounds:
        mp = round_data.model_prediction
        ae = round_data.action_execution
        
        # Plan Agent 思考阶段
        think_start = mp.start_timestamp - record.metadata.start_timestamp
        fig.add_trace(go.Bar(
            name=f"Plan R{round_data.round_id} Think",
            x=[mp.duration],
            y=["Plan Agent"],
            base=[think_start],
            orientation='h',
            marker=dict(
                color=plan_think_color,
                line=dict(color='white', width=1)
            ),
            hovertemplate=(
                f"<b>Plan Agent Round {round_data.round_id} - Think</b><br>"
                f"Duration: {mp.duration:.2f}s<br>"
                "<extra></extra>"
            ),
            showlegend=False
        ))
        
        # Plan Agent 执行阶段
        if ae:
            action_start = ae.start_timestamp - record.metadata.start_timestamp
            fig.add_trace(go.Bar(
                name=f"Plan R{round_data.round_id} Action",
                x=[ae.duration],
                y=["Plan Agent"],
                base=[action_start],
                orientation='h',
                marker=dict(
                    color=plan_action_color,
                    line=dict(color='white', width=1)
                ),
                hovertemplate=(
                    f"<b>Plan Agent Round {round_data.round_id} - Dispatch</b><br>"
                    f"Duration: {ae.duration:.2f}s<br>"
                    "<extra></extra>"
                ),
                showlegend=False
            ))
    
    # 创建甘特图，每个 agent_id 一行，多次调用显示为多个条形
    for agent_id, agents_list in agent_groups.items():
        for agent in agents_list:
            start_ts, end_ts, duration = get_agent_time_range(agent)
            if duration > 0:
                color = get_agent_color(agent.type)
                base_offset = start_ts - record.metadata.start_timestamp
                call_info = f" (call #{agent.call_id})" if agent.call_id else ""
                fig.add_trace(go.Bar(
                    name=f"{agent_id}{call_info}",
                    x=[duration],
                    y=[agent_id],  # Y 轴使用原始 agent_id，同一 agent 的调用显示在同一行
                    base=[base_offset],
                    orientation='h',
                    marker=dict(
                        color=color,
                        line=dict(color='white', width=2)
                    ),
                    hovertemplate=(
                        f"<b>{agent_id}{call_info}</b><br>"
                        f"Task: {agent.task[:50]}...<br>"
                        f"Model: {agent.model_name}<br>"
                        f"Duration: {duration:.2f}s<br>"
                        f"Rounds: {len(agent.rounds)}<br>"
                        "<extra></extra>"
                    ),
                    showlegend=False
                ))
    
    # 计算唯一 agent 数量（不是调用次数）+ Plan Agent 行
    unique_agent_count = len(agent_groups) + 1  # +1 for Plan Agent
    
    fig.update_layout(
        title=f"Level 2: Device Task Timeline - {device_id}",
        xaxis_title="Time (seconds from start)",
        yaxis_title="Agent",
        height=max(300, unique_agent_count * 80),
        barmode='overlay',
        hovermode='closest',
        plot_bgcolor='rgba(240,240,240,0.5)'
    )
    
    return fig

def create_level3_timeline(agent: Agent, start_offset: float) -> go.Figure:
    """
    创建第3级时间轴：单个 Agent 的轮次级视图。
    
    Args:
        agent (Agent): 智能体实例。
        start_offset (float): 起始时间偏移量。
        
    Returns:
        go.Figure: Plotly 图表对象。
    """
    fig = make_subplots(
        rows=2, cols=1,
        row_heights=[0.7, 0.3],
        subplot_titles=("Round Timeline", "Model vs Action Time"),
        vertical_spacing=0.15
    )
    
    # 轨道1: Model Prediction (思考)
    # 轨道2: Action Execution (执行)
    
    for round_data in agent.rounds:
        round_id = round_data.round_id
        mp = round_data.model_prediction
        ae = round_data.action_execution
        
        # Model Prediction 轨道
        model_start = mp.start_timestamp - start_offset
        fig.add_trace(go.Bar(
            name=f"Round {round_id} - Model",
            x=[mp.duration],
            y=["Model Thinking"],
            base=[model_start],
            orientation='h',
            marker=dict(color='#4caf50', line=dict(color='white', width=1)),
            hovertemplate=(
                f"<b>Round {round_id} - Model</b><br>"
                f"Duration: {mp.duration:.3f}s<br>"
                f"Actions: {len(mp.actions)}<br>"
                "<extra></extra>"
            ),
            showlegend=False
        ), row=1, col=1)
        
        # Action Execution 轨道
        if ae:
            action_start = ae.start_timestamp - start_offset
            # 设置最小显示宽度（秒），确保很短的 action 也能看见
            MIN_ACTION_DISPLAY_WIDTH = 2.0  # 最小显示 2 秒宽度
            display_duration = max(ae.duration, MIN_ACTION_DISPLAY_WIDTH)
            
            fig.add_trace(go.Bar(
                name=f"Round {round_id} - Action",
                x=[display_duration],
                y=["Action Execution"],
                base=[action_start],
                orientation='h',
                marker=dict(color='#ff5722', line=dict(color='white', width=1)),
                hovertemplate=(
                    f"<b>Round {round_id} - Action</b><br>"
                    f"Actual Duration: {ae.duration:.3f}s<br>"
                    f"Status: {ae.result.status if ae.result else 'N/A'}<br>"
                    "<extra></extra>"
                ),
                showlegend=False
            ), row=1, col=1)
    
    # 第二行：柱状图对比每轮的 Model 和 Action 时间
    rounds_data = []
    for round_data in agent.rounds:
        rounds_data.append({
            "Round": f"R{round_data.round_id}",
            "Model Time": round_data.model_prediction.duration,
            "Action Time": round_data.action_execution.duration if round_data.action_execution else 0
        })
    
    df = pd.DataFrame(rounds_data)
    
    fig.add_trace(go.Bar(
        name="Model Time",
        x=df["Round"],
        y=df["Model Time"],
        marker=dict(color='#4caf50'),
        showlegend=True
    ), row=2, col=1)
    
    fig.add_trace(go.Bar(
        name="Action Time",
        x=df["Round"],
        y=df["Action Time"],
        marker=dict(color='#ff5722'),
        showlegend=True
    ), row=2, col=1)
    
    fig.update_xaxes(title_text="Time (seconds from agent start)", row=1, col=1)
    fig.update_yaxes(title_text="Track", row=1, col=1)
    fig.update_xaxes(title_text="Round", row=2, col=1)
    fig.update_yaxes(title_text="Duration (s)", row=2, col=1)
    
    # 构建标题，如果有 call_id 则显示
    call_info = f" (call #{agent.call_id})" if agent.call_id else ""
    fig.update_layout(
        title=f"Level 3: Agent Detail - {agent.agent_id}{call_info} (Model: {agent.model_name})",
        height=700,
        hovermode='closest',
        barmode='group'
    )
    
    return fig

def display_round_details(agent: Agent, round_id: int, use_expander: bool = True):
    """
    显示某一轮的详细信息。
    
    Args:
        agent (Agent): 智能体实例。
        round_id (int): 轮次 ID。
        use_expander (bool): 是否使用 expander 显示详情（防止嵌套）
    """
    # Use unique_id if available, otherwise fall back to agent_id
    agent_key = getattr(agent, 'unique_id', agent.agent_id)
    
    round_data = None
    for r in agent.rounds:
        if r.round_id == round_id:
            round_data = r
            break
    
    if not round_data:
        st.error(f"Round {round_id} not found")
        return
    
    st.markdown(f"### 📋 Round {round_id} Details")
    
    # Display messages if available
    if hasattr(round_data.model_prediction, 'messages') and round_data.model_prediction.messages:
        st.markdown("#### 💬 Messages")
        with st.expander("View Conversation History", expanded=False):
            messages = round_data.model_prediction.messages
            for i, msg in enumerate(messages):
                role = msg.get('role', 'unknown')
                content = msg.get('content', '')
                
                # Display role badge
                if role == 'system':
                    st.markdown(f"**🔧 System Message** (message {i})")
                elif role == 'user':
                    st.markdown(f"**👤 User** (message {i})")
                elif role == 'assistant':
                    st.markdown(f"**🤖 Assistant** (message {i})")
                else:
                    st.markdown(f"**{role}** (message {i})")
                
                # Display content (handle both string and array content)
                if isinstance(content, str):
                    st.text_area("", content, height=min(200, max(50, len(content) // 3)), 
                               key=f"msg_{agent_key}_{round_id}_{i}", label_visibility="collapsed")
                elif isinstance(content, list):
                    # Handle multimodal content (text + images)
                    for j, item in enumerate(content):
                        if isinstance(item, dict):
                            if item.get('type') == 'text':
                                st.text_area("", item.get('text', ''), height=100,
                                           key=f"msg_{agent_key}_{round_id}_{i}_{j}_text", label_visibility="collapsed")
                            elif item.get('type') == 'image_url':
                                # 显示 base64 编码的图片
                                image_url = item.get('image_url', {})
                                if isinstance(image_url, dict):
                                    url = image_url.get('url', '')
                                elif isinstance(image_url, str):
                                    url = image_url
                                else:
                                    url = ''
                                
                                # 检查是否是 base64 数据
                                if url.startswith('data:image'):
                                    try:
                                        import base64
                                        import io
                                        from PIL import Image
                                        
                                        # 提取 base64 数据
                                        # 格式: data:image/png;base64,<base64_data>
                                        if ';base64,' in url:
                                            base64_data = url.split(';base64,')[1]
                                            image_bytes = base64.b64decode(base64_data)
                                            image = Image.open(io.BytesIO(image_bytes))
                                            
                                            # 显示图片（缩小以适应界面）
                                            st.image(image, caption="📸 Screenshot", use_container_width=True)
                                        else:
                                            st.caption("🖼️ Image (base64 encoded, format not recognized)")
                                    except Exception as e:
                                        st.caption(f"🖼️ Image (failed to decode: {str(e)[:50]})")
                                else:
                                    st.caption("🖼️ Image (base64 encoded, not displayed)")
                st.markdown("---")
    
    # Display screenshot_url if available (for GUI agents)
    if hasattr(round_data.model_prediction, 'screenshot_url') and round_data.model_prediction.screenshot_url:
        st.markdown(f"#### 📸 Screenshot")
        st.info(f"Screenshot URL: `{round_data.model_prediction.screenshot_url}`")
    
    # Display three time metrics
    st.markdown("#### ⏱️ Time Breakdown")
    
    # 检查是否有详细的timing信息（GUI agent的三段时间）
    has_detailed_timing = (
        hasattr(round_data.model_prediction, 'timing') and 
        round_data.model_prediction.timing and 
        isinstance(round_data.model_prediction.timing, dict)
    )
    
    if has_detailed_timing:
        # GUI agent的详细时间分解：准备 + API + Action = Total
        timing = round_data.model_prediction.timing
        time_col1, time_col2, time_col3, time_col4 = st.columns(4)
        
        with time_col1:
            prep_time = timing.get("preparation_time", 0.0)
            st.metric("📷 Preparation", f"{prep_time:.3f}s", help="Screenshot preparation time")
        
        with time_col2:
            api_time = timing.get("api_call_time", 0.0)
            st.metric("🧠 API Call", f"{api_time:.3f}s", help="Model API response time")
        
        with time_col3:
            # Action时间 = 总时间 - 准备时间 - API时间
            total_time = timing.get("total_round_time", 0.0)
            action_time = total_time - prep_time - api_time
            st.metric("⚙️ Action Time", f"{action_time:.3f}s", help="Parsing and execution time")
        
        with time_col4:
            st.metric("📊 Total", f"{total_time:.3f}s", help="Total round time")
    else:
        # 普通的时间显示（2+1段时间）
        time_col1, time_col2, time_col3 = st.columns(3)
        
        with time_col1:
            st.metric("🧠 Model Time", f"{round_data.model_prediction.duration:.3f}s")
        
        with time_col2:
            action_duration = round_data.action_execution.duration if round_data.action_execution else 0.0
            st.metric("⚙️ Action Time", f"{action_duration:.3f}s")
        
        with time_col3:
            total_duration = round_data.total_duration if hasattr(round_data, 'total_duration') else 0.0
            st.metric("📊 Total Time", f"{total_duration:.3f}s")
    
    st.markdown("---")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.markdown("#### 🧠 Model Prediction")
        st.text(f"Actions: {len(round_data.model_prediction.actions)}")
        if use_expander:
            with st.expander("View Response"):
                st.text_area("Response", round_data.model_prediction.response, height=200, key=f"response_{agent_key}_{round_id}", label_visibility="collapsed")
        else:
            st.markdown("**Response:**")
            st.text_area("Response", round_data.model_prediction.response, height=150, key=f"response_{agent_key}_{round_id}", label_visibility="collapsed")
    
    with col2:
        st.markdown("#### ⚙️ Action Execution")
        if round_data.action_execution:
            st.text(f"Duration: {round_data.action_execution.duration:.3f}s")
            if round_data.action_execution.result:
                st.text(f"Status: {round_data.action_execution.result.status}")
                st.text(f"Return Code: {round_data.action_execution.result.returncode}")
            if round_data.action_execution.code:
                if use_expander:
                    with st.expander("View Code"):
                        st.text_area("Code", round_data.action_execution.code, height=200, key=f"code_{agent_key}_{round_id}", label_visibility="collapsed")
                else:
                    st.markdown("**Code:**")
                    st.text_area("Code", round_data.action_execution.code, height=150, key=f"code_{agent_key}_{round_id}", label_visibility="collapsed")
        else:
            st.info("No action execution (terminal round)")
    
    with col3:
        st.markdown("#### 📊 Execution Result")
        if round_data.action_execution and round_data.action_execution.result:
            result = round_data.action_execution.result
            if result.output:
                if use_expander:
                    with st.expander("View Output"):
                        st.text_area("Output", result.output, height=200, key=f"output_{agent_key}_{round_id}", label_visibility="collapsed")
                else:
                    st.markdown("**Output:**")
                    st.text_area("Output", result.output, height=150, key=f"output_{agent_key}_{round_id}", label_visibility="collapsed")
            if result.error:
                if use_expander:
                    with st.expander("View Error", expanded=True):
                        st.text_area("Error", result.error, height=100, key=f"error_{agent_key}_{round_id}", label_visibility="collapsed")
                else:
                    st.markdown("**Error:**")
                    st.text_area("Error", result.error, height=100, key=f"error_{agent_key}_{round_id}", label_visibility="collapsed")
        else:
            st.info("No result available")

def main():
    """
    主函数：构建 Streamlit 应用。
    """
    st.markdown('<div class="main-header">🚀 Multi-Agent Timeline Visualizer</div>', unsafe_allow_html=True)
    
    # 侧边栏：文件上传和导航
    with st.sidebar:
        st.header("📁 Data Source")
        
        # 获取脚本所在目录的父目录（parallel_benchmark）
        import os
        script_dir = os.path.dirname(os.path.abspath(__file__))
        parent_dir = os.path.dirname(script_dir)
        default_log_path = os.path.join(parent_dir, "logs", "execution_record.json")
        
        # 文件选择
        data_file = st.text_input(
            "JSON File Path",
            value=default_log_path,
            help="Enter the path to your record JSON file"
        )
        
        if st.button("🔄 Load Data"):
            st.session_state['data_loaded'] = False
        
        if not st.session_state.get('data_loaded', False):
            try:
                record = load_record(data_file)
                st.session_state['record'] = record
                st.session_state['data_loaded'] = True
                st.success("✅ Data loaded successfully!")
            except Exception as e:
                st.error(f"❌ Error loading file: {e}")
                return
        
        if st.session_state.get('data_loaded', False):
            st.markdown("---")
            st.header("🎯 Navigation")
            
            # 级别选择
            level = st.radio(
                "Select View Level",
                ["Level 1: Global Task", "Level 2: Device Task", "Level 3: Agent Detail"],
                index=0
            )
            
            st.session_state['level'] = level
    
    # 主内容区
    if not st.session_state.get('data_loaded', False):
        st.info("👈 Please load a data file from the sidebar")
        return
    
    record = st.session_state['record']
    
    # 显示全局信息
    st.markdown("### 📊 Global Summary")
    col1, col2, col3, col4, col5 = st.columns(5)
    
    with col1:
        st.metric("Task ID", record.task_id)
    with col2:
        st.metric("Total Duration", f"{record.summary.total_duration:.2f}s")
    with col3:
        st.metric("Devices", record.summary.devices_count)
    with col4:
        st.metric("Total Agents", record.summary.total_agents_count)
    with col5:
        status_emoji = "✅" if record.summary.success else "❌"
        st.metric("Status", status_emoji)
    
    st.markdown(f"**Instruction:** {record.instruction}")
    st.markdown(f"**Coordinator Model:** {record.coordinator.model_name}")
    
    # Display coordinator system_prompt if available
    if hasattr(record.coordinator, 'system_prompt') and record.coordinator.system_prompt:
        with st.expander("🔧 Coordinator System Prompt", expanded=False):
            st.text_area("", record.coordinator.system_prompt, height=200, 
                       key="coord_sys_prompt", label_visibility="collapsed")
    
    st.markdown("---")
    
    # 根据选择的级别显示不同的视图
    level = st.session_state.get('level', "Level 1: Global Task")
    
    if level == "Level 1: Global Task":
        st.markdown('<div class="level-indicator level-1">📍 Level 1: Global Task Timeline</div>', unsafe_allow_html=True)
        fig = create_level1_timeline(record)
        st.plotly_chart(fig, use_container_width=True)
        
        st.markdown("---")
        st.markdown("### 🔽 Drill Down to Level 2")
        
        # 设备选择器
        device_ids = ["Control Center"] + [d.device_id for d in record.devices]
        selected_device = st.selectbox("Select Device to View", device_ids, key="level1_device_select")
        
        if st.button("📊 Go to Level 2: Device View", key="level1_goto_level2"):
            if selected_device == "Control Center":
                st.info("Control Center only has the Coordinator. Select a device to view agents.")
            else:
                st.session_state['level'] = "Level 2: Device Task"
                st.session_state['selected_device'] = selected_device
                st.rerun()
    
    elif level == "Level 2: Device Task":
        st.markdown('<div class="level-indicator level-2">📍 Level 2: Device Task Timeline</div>', unsafe_allow_html=True)
        
        # 设备选择器（在当前层级可以切换）
        device_ids = [d.device_id for d in record.devices]
        col1, col2 = st.columns([3, 1])
        with col1:
            current_device = st.session_state.get('selected_device', device_ids[0] if device_ids else '')
            selected_device = st.selectbox(
                "Current Device",
                device_ids,
                index=device_ids.index(current_device) if current_device in device_ids else 0,
                key="level2_device_select"
            )
            st.session_state['selected_device'] = selected_device
        with col2:
            if st.button("⬅️ Back to Level 1", key="level2_back"):
                st.session_state['level'] = "Level 1: Global Task"
                st.rerun()
        
        fig = create_level2_timeline(record, selected_device)
        st.plotly_chart(fig, use_container_width=True)
        
        # 找到该设备
        device = next((d for d in record.devices if d.device_id == selected_device), None)
        
        if device and device.agents:
            st.markdown("---")
            st.markdown("### 🔽 Drill Down to Level 3")
            # 按 agent_id 分组，选择的是原始 agent_id
            from collections import OrderedDict
            agent_groups = OrderedDict()
            for a in device.agents:
                if a.agent_id not in agent_groups:
                    agent_groups[a.agent_id] = []
                agent_groups[a.agent_id].append(a)
            
            agent_ids = list(agent_groups.keys())
            # 显示每个 agent 的调用次数
            agent_labels = [f"{aid} ({len(agent_groups[aid])} calls)" for aid in agent_ids]
            selected_idx = st.selectbox("Select Agent to View", range(len(agent_ids)), 
                                       format_func=lambda i: agent_labels[i], key="level2_agent_select")
            selected_agent_id = agent_ids[selected_idx]
            if st.button("📊 Go to Level 3: Agent Detail", key="level2_goto_level3"):
                st.session_state['level'] = "Level 3: Agent Detail"
                st.session_state['selected_agent_id'] = selected_agent_id  # 存储原始 agent_id
                st.session_state['selected_device'] = selected_device
                st.rerun()
    
    elif level == "Level 3: Agent Detail":
        st.markdown('<div class="level-indicator level-3">📍 Level 3: Agent Detail Timeline</div>', unsafe_allow_html=True)
        
        # 收集所有 agents（按 unique_id）
        from collections import OrderedDict
        all_agents = OrderedDict()  # unique_id -> (agent, device_id)
        for device in record.devices:
            for agent in device.agents:
                uid = agent.unique_id or f"{agent.agent_id}_unknown"
                all_agents[uid] = (agent, device.device_id)
        
        # Agent 选择器（按 unique_id 选择每个具体的调用）
        col1, col2 = st.columns([3, 1])
        with col1:
            unique_ids = list(all_agents.keys())
            # 显示每个调用的详细信息：unique_id (device, rounds数)
            agent_labels = []
            for uid in unique_ids:
                agent, device_id = all_agents[uid]
                rounds_count = len(agent.rounds)
                agent_labels.append(f"{uid} ({device_id}, {rounds_count} rounds)")
            
            current_unique_id = st.session_state.get('selected_unique_id', unique_ids[0] if unique_ids else '')
            # 找到当前选中的索引
            current_idx = unique_ids.index(current_unique_id) if current_unique_id in unique_ids else 0
            selected_idx = st.selectbox(
                "Current Agent",
                range(len(unique_ids)),
                index=current_idx,
                format_func=lambda i: agent_labels[i],
                key="level3_agent_select"
            )
            selected_unique_id = unique_ids[selected_idx]
            st.session_state['selected_unique_id'] = selected_unique_id
        with col2:
            if st.button("⬅️ Back to Level 2", key="level3_back"):
                st.session_state['level'] = "Level 2: Device Task"
                st.rerun()
        
        # 获取选中的 agent
        selected_agent, selected_device_id = all_agents.get(selected_unique_id, (None, None))
        
        if selected_agent:
            # 显示基本信息
            st.markdown(f"**Unique ID:** `{selected_unique_id}`")
            st.markdown(f"**Agent ID:** `{selected_agent.agent_id}`")
            st.markdown(f"**Device:** `{selected_device_id}`")
            st.markdown(f"**Model:** {selected_agent.model_name}")
            st.markdown(f"**Type:** {selected_agent.type}")
            st.markdown(f"**Task:** {selected_agent.task}")
            st.markdown(f"**Total Rounds:** {len(selected_agent.rounds)}")
            
            # Display system_prompt if available
            if hasattr(selected_agent, 'system_prompt') and selected_agent.system_prompt:
                with st.expander("🔧 System Prompt", expanded=False):
                    st.text_area("", selected_agent.system_prompt, height=200, 
                               key=f"sys_prompt_{selected_unique_id}", label_visibility="collapsed")
            
            if selected_agent.recording:
                st.markdown(f"**Duration:** {selected_agent.recording.duration:.2f}s")
            
            # 创建时间线图
            st.markdown("---")
            st.markdown("### 📊 Round Timeline")
            
            # 使用该 agent 的起始时间作为基准
            start_offset = selected_agent.rounds[0].model_prediction.start_timestamp if selected_agent.rounds else 0
            
            # 创建时间线图（使用原来的样式：Model Thinking 和 Action Execution 两条轨道）
            fig = make_subplots(
                rows=2, cols=1,
                row_heights=[0.7, 0.3],
                subplot_titles=("Round Timeline", "Model vs Action Time"),
                vertical_spacing=0.15
            )
            
            round_data_list = []
            
            for round_obj in selected_agent.rounds:
                round_id = round_obj.round_id
                mp = round_obj.model_prediction
                ae = round_obj.action_execution
                
                # Model Prediction 轨道
                model_start = mp.start_timestamp - start_offset
                fig.add_trace(go.Bar(
                    name=f"Round {round_id} - Model",
                    x=[mp.duration],
                    y=["Model Thinking"],
                    base=[model_start],
                    orientation='h',
                    marker=dict(color='#4caf50', line=dict(color='white', width=1)),
                    hovertemplate=(
                        f"<b>Round {round_id} - Model</b><br>"
                        f"Duration: {mp.duration:.3f}s<br>"
                        f"Actions: {len(mp.actions)}<br>"
                        "<extra></extra>"
                    ),
                    showlegend=False
                ), row=1, col=1)
                
                # Action Execution 轨道
                if ae:
                    action_start = ae.start_timestamp - start_offset
                    # 设置最小显示宽度（秒），确保很短的 action 也能看见
                    MIN_ACTION_DISPLAY_WIDTH = 2.0
                    display_duration = max(ae.duration, MIN_ACTION_DISPLAY_WIDTH)
                    
                    fig.add_trace(go.Bar(
                        name=f"Round {round_id} - Action",
                        x=[display_duration],
                        y=["Action Execution"],
                        base=[action_start],
                        orientation='h',
                        marker=dict(color='#ff5722', line=dict(color='white', width=1)),
                        hovertemplate=(
                            f"<b>Round {round_id} - Action</b><br>"
                            f"Actual Duration: {ae.duration:.3f}s<br>"
                            f"Status: {ae.result.status if ae.result else 'N/A'}<br>"
                            "<extra></extra>"
                        ),
                        showlegend=False
                    ), row=1, col=1)
                    
                    round_data_list.append({
                        "Round": f"R{round_id}",
                        "Model Time": float(mp.duration),
                        "Action Time": float(ae.duration)
                    })
                else:
                    round_data_list.append({
                        "Round": f"R{round_id}",
                        "Model Time": float(mp.duration),
                        "Action Time": 0.0
                    })
            
            # 第二行：柱状图对比每轮的 Model 和 Action 时间
            if round_data_list:
                # 直接从列表提取数据，避免 DataFrame 的类型推断问题
                rounds = [str(item["Round"]) for item in round_data_list]
                model_times = [float(item["Model Time"]) for item in round_data_list]
                action_times = [float(item["Action Time"]) for item in round_data_list]
                
                fig.add_trace(go.Bar(
                    name="Model Time",
                    x=rounds,
                    y=model_times,
                    marker=dict(color='#4caf50'),
                    showlegend=True
                ), row=2, col=1)
                
                fig.add_trace(go.Bar(
                    name="Action Time",
                    x=rounds,
                    y=action_times,
                    marker=dict(color='#ff5722'),
                    showlegend=True
                ), row=2, col=1)
            
            fig.update_xaxes(title_text="Time (seconds from first call start)", row=1, col=1)
            fig.update_yaxes(title_text="Track", row=1, col=1)
            fig.update_xaxes(title_text="Round", row=2, col=1)
            fig.update_yaxes(title_text="Duration (s)", row=2, col=1)
            
            fig.update_layout(
                title=f"Level 3: Agent Detail - {selected_unique_id} ({len(selected_agent.rounds)} rounds)",
                height=700,
                hovermode='closest',
                barmode='overlay'
            )
            
            st.plotly_chart(fig, use_container_width=True)
            
            # 显示轮次详情
            st.markdown("---")
            st.markdown("### 📋 Round Details")
            
            if selected_agent.rounds:
                round_id = st.selectbox(
                    "Select Round for Details", 
                    range(len(selected_agent.rounds)), 
                    key=f"level3_round_select_{selected_unique_id}"
                )
                # 显示选中轮次的详细信息
                display_round_details(selected_agent, round_id, use_expander=False)
        else:
            st.error(f"Agent {selected_unique_id} not found")

if __name__ == "__main__":
    main()

