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
from dataviewer.record_template import RecordTemplate, GuiAgent, CodeAgent, PlanAgent

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
        "plan": "#9c27b0",  # 紫色
        "gui": "#2196f3",   # 蓝色
        "code": "#ff9800"   # 橙色
    }
    return colors.get(agent_type, "#757575")

def create_level1_timeline(record: RecordTemplate) -> go.Figure:
    """
    创建第1级时间轴：总任务级视图（按设备分组）。
    
    Args:
        record (RecordTemplate): 记录模板实例。
        
    Returns:
        go.Figure: Plotly 图表对象。
    """
    fig = go.Figure()
    
    # 收集所有智能体并按设备分组
    device_agents = {}
    
    # 添加 Plan Agent（通常没有设备）
    plan_device = "Control Center (No Device)"
    if plan_device not in device_agents:
        device_agents[plan_device] = []
    device_agents[plan_device].append({
        "agent_id": "plan_agent",
        "agent_type": "plan",
        "task": "Task Planning & Coordination",
        "start": record.agents.plan_agent.recording.start_timestamp,
        "end": record.agents.plan_agent.recording.end_timestamp,
        "duration": record.agents.plan_agent.recording.duration,
        "status": "success"
    })
    
    # 添加 GUI Agents
    for gui_agent in record.agents.gui_agents:
        # 使用第一个设备 ID，如果有多个设备则用逗号分隔
        device_id = gui_agent.device_id[0] if gui_agent.device_id and len(gui_agent.device_id) > 0 else "Unknown Device"
        if device_id not in device_agents:
            device_agents[device_id] = []
        device_agents[device_id].append({
            "agent_id": gui_agent.agent_id,
            "agent_type": "gui",
            "task": gui_agent.task,
            "start": gui_agent.recording.start_timestamp,
            "end": gui_agent.recording.end_timestamp,
            "duration": gui_agent.recording.duration,
            "status": gui_agent.summary.final_status or "success"
        })
    
    # 添加 Code Agents
    for code_agent in record.agents.code_agents:
        # 使用第一个设备 ID，如果有多个设备则用逗号分隔
        device_id = code_agent.device_id[0] if code_agent.device_id and len(code_agent.device_id) > 0 else "Unknown Device"
        if device_id not in device_agents:
            device_agents[device_id] = []
        device_agents[device_id].append({
            "agent_id": code_agent.agent_id,
            "agent_type": "code",
            "task": code_agent.task,
            "start": code_agent.recording.start_timestamp,
            "end": code_agent.recording.end_timestamp,
            "duration": code_agent.recording.duration,
            "status": code_agent.summary.final_status or "success"
        })
    
    # 创建泳道图
    y_position = 0
    y_labels = []
    
    for device_id, agents in device_agents.items():
        y_labels.append(device_id)
        for agent in agents:
            color = get_agent_color(agent["agent_type"])
            fig.add_trace(go.Bar(
                name=agent["agent_id"],
                x=[agent["duration"]],
                y=[device_id],
                base=[agent["start"] - record.metadata.start_timestamp],
                orientation='h',
                marker=dict(
                    color=color,
                    line=dict(color='white', width=2)
                ),
                hovertemplate=(
                    f"<b>{agent['agent_id']}</b><br>"
                    f"Task: {agent['task']}<br>"
                    f"Duration: {agent['duration']:.2f}s<br>"
                    f"Status: {agent['status']}<br>"
                    "<extra></extra>"
                ),
                showlegend=False
            ))
    
    fig.update_layout(
        title="Level 1: Global Task Timeline (Grouped by Device)",
        xaxis_title="Time (seconds from start)",
        yaxis_title="Device",
        height=max(400, len(y_labels) * 80),
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
    
    # 筛选该设备上的所有智能体
    agents_on_device = []
    
    for gui_agent in record.agents.gui_agents:
        agent_device = gui_agent.device_id[0] if gui_agent.device_id and len(gui_agent.device_id) > 0 else "Unknown Device"
        if agent_device == device_id:
            agents_on_device.append({
                "agent_id": gui_agent.agent_id,
                "agent_type": "gui",
                "task": gui_agent.task,
                "start": gui_agent.recording.start_timestamp,
                "end": gui_agent.recording.end_timestamp,
                "duration": gui_agent.recording.duration,
                "status": gui_agent.summary.final_status or "success",
                "rounds": len(gui_agent.rounds),
                "agent_obj": gui_agent
            })
    
    for code_agent in record.agents.code_agents:
        agent_device = code_agent.device_id[0] if code_agent.device_id and len(code_agent.device_id) > 0 else "Unknown Device"
        if agent_device == device_id:
            agents_on_device.append({
                "agent_id": code_agent.agent_id,
                "agent_type": "code",
                "task": code_agent.task,
                "start": code_agent.recording.start_timestamp,
                "end": code_agent.recording.end_timestamp,
                "duration": code_agent.recording.duration,
                "status": code_agent.summary.final_status or "success",
                "rounds": len(code_agent.rounds),
                "agent_obj": code_agent
            })
    
    if not agents_on_device:
        st.warning(f"No agents found on device: {device_id}")
        return fig
    
    # 创建甘特图
    for agent in agents_on_device:
        color = get_agent_color(agent["agent_type"])
        fig.add_trace(go.Bar(
            name=agent["agent_id"],
            x=[agent["duration"]],
            y=[agent["agent_id"]],
            base=[agent["start"] - record.metadata.start_timestamp],
            orientation='h',
            marker=dict(
                color=color,
                line=dict(color='white', width=2)
            ),
            hovertemplate=(
                f"<b>{agent['agent_id']}</b><br>"
                f"Task: {agent['task']}<br>"
                f"Duration: {agent['duration']:.2f}s<br>"
                f"Rounds: {agent['rounds']}<br>"
                f"Status: {agent['status']}<br>"
                "<extra></extra>"
            ),
            showlegend=False
        ))
    
    fig.update_layout(
        title=f"Level 2: Device Task Timeline - {device_id}",
        xaxis_title="Time (seconds from start)",
        yaxis_title="Agent",
        height=max(300, len(agents_on_device) * 60),
        barmode='overlay',
        hovermode='closest',
        plot_bgcolor='rgba(240,240,240,0.5)'
    )
    
    return fig

def create_level3_timeline(agent, agent_type: str, start_offset: float) -> go.Figure:
    """
    创建第3级时间轴：单个 Agent 的轮次级视图。
    
    Args:
        agent: 智能体实例（GuiAgent 或 CodeAgent）。
        agent_type (str): 智能体类型。
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
            fig.add_trace(go.Bar(
                name=f"Round {round_id} - Action",
                x=[ae.duration],
                y=["Action Execution"],
                base=[action_start],
                orientation='h',
                marker=dict(color='#ff5722', line=dict(color='white', width=1)),
                hovertemplate=(
                    f"<b>Round {round_id} - Action</b><br>"
                    f"Duration: {ae.duration:.3f}s<br>"
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
    
    fig.update_layout(
        title=f"Level 3: Agent Detail - {agent.agent_id}",
        height=700,
        hovermode='closest',
        barmode='group'
    )
    
    return fig

def display_round_details(agent, round_id: int):
    """
    显示某一轮的详细信息。
    
    Args:
        agent: 智能体实例。
        round_id (int): 轮次 ID。
    """
    round_data = None
    for r in agent.rounds:
        if r.round_id == round_id:
            round_data = r
            break
    
    if not round_data:
        st.error(f"Round {round_id} not found")
        return
    
    st.markdown(f"### 📋 Round {round_id} Details")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.markdown("#### 🧠 Model Prediction")
        st.text(f"Duration: {round_data.model_prediction.duration:.3f}s")
        st.text(f"Actions: {len(round_data.model_prediction.actions)}")
        with st.expander("View Response"):
            st.text_area("", round_data.model_prediction.response, height=200, key=f"response_{round_id}", label_visibility="collapsed")
    
    with col2:
        st.markdown("#### ⚙️ Action Execution")
        if round_data.action_execution:
            st.text(f"Duration: {round_data.action_execution.duration:.3f}s")
            if round_data.action_execution.result:
                st.text(f"Status: {round_data.action_execution.result.status}")
                st.text(f"Return Code: {round_data.action_execution.result.returncode}")
            if round_data.action_execution.code:
                with st.expander("View Code"):
                    st.text_area("", round_data.action_execution.code, height=200, key=f"code_{round_id}", label_visibility="collapsed")
        else:
            st.info("No action execution (terminal round)")
    
    with col3:
        st.markdown("#### 📊 Execution Result")
        if round_data.action_execution and round_data.action_execution.result:
            result = round_data.action_execution.result
            if result.output:
                with st.expander("View Output"):
                    st.text_area("", result.output, height=200, key=f"output_{round_id}", label_visibility="collapsed")
            if result.error:
                with st.expander("View Error", expanded=True):
                    st.text_area("", result.error, height=100, key=f"error_{round_id}", label_visibility="collapsed")
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
        
        # 文件选择
        data_file = st.text_input(
            "JSON File Path",
            value="dataviewer/record_templetae.json",
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
        st.metric("GUI Agents", record.summary.gui_agents_count)
    with col4:
        st.metric("Code Agents", record.summary.code_agents_count)
    with col5:
        status_emoji = "✅" if record.summary.success else "❌"
        st.metric("Status", status_emoji)
    
    st.markdown(f"**Instruction:** {record.instruction}")
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
        devices = set()
        devices.add("Control Center (No Device)")
        for gui_agent in record.agents.gui_agents:
            device_id = gui_agent.device_id[0] if gui_agent.device_id and len(gui_agent.device_id) > 0 else "Unknown Device"
            devices.add(device_id)
        for code_agent in record.agents.code_agents:
            device_id = code_agent.device_id[0] if code_agent.device_id and len(code_agent.device_id) > 0 else "Unknown Device"
            devices.add(device_id)
        
        selected_device = st.selectbox("Select Device to View", sorted(devices), key="level1_device_select")
        if st.button("📊 Go to Level 2: Device View", key="level1_goto_level2"):
            st.session_state['level'] = "Level 2: Device Task"
            st.session_state['selected_device'] = selected_device
            st.rerun()
    
    elif level == "Level 2: Device Task":
        st.markdown('<div class="level-indicator level-2">📍 Level 2: Device Task Timeline</div>', unsafe_allow_html=True)
        
        # 收集所有设备
        devices = set()
        devices.add("Control Center (No Device)")
        for gui_agent in record.agents.gui_agents:
            device_id = gui_agent.device_id[0] if gui_agent.device_id and len(gui_agent.device_id) > 0 else "Unknown Device"
            devices.add(device_id)
        for code_agent in record.agents.code_agents:
            device_id = code_agent.device_id[0] if code_agent.device_id and len(code_agent.device_id) > 0 else "Unknown Device"
            devices.add(device_id)
        
        # 设备选择器（在当前层级可以切换）
        col1, col2 = st.columns([3, 1])
        with col1:
            selected_device = st.selectbox(
                "Current Device", 
                sorted(devices),
                index=sorted(devices).index(st.session_state.get('selected_device', 'Unknown Device')) if st.session_state.get('selected_device', 'Unknown Device') in sorted(devices) else 0,
                key="level2_device_select"
            )
            st.session_state['selected_device'] = selected_device
        with col2:
            if st.button("⬅️ Back to Level 1", key="level2_back"):
                st.session_state['level'] = "Level 1: Global Task"
                st.rerun()
        
        fig = create_level2_timeline(record, selected_device)
        st.plotly_chart(fig, use_container_width=True)
        
        # Agent 选择器
        agents_on_device = []
        for gui_agent in record.agents.gui_agents:
            agent_device = gui_agent.device_id[0] if gui_agent.device_id and len(gui_agent.device_id) > 0 else "Unknown Device"
            if agent_device == selected_device:
                agents_on_device.append((gui_agent.agent_id, gui_agent, "gui"))
        for code_agent in record.agents.code_agents:
            agent_device = code_agent.device_id[0] if code_agent.device_id and len(code_agent.device_id) > 0 else "Unknown Device"
            if agent_device == selected_device:
                agents_on_device.append((code_agent.agent_id, code_agent, "code"))
        
        if agents_on_device:
            st.markdown("---")
            st.markdown("### 🔽 Drill Down to Level 3")
            selected_agent_id = st.selectbox("Select Agent to View", [a[0] for a in agents_on_device], key="level2_agent_select")
            if st.button("📊 Go to Level 3: Agent Detail", key="level2_goto_level3"):
                st.session_state['level'] = "Level 3: Agent Detail"
                st.session_state['selected_agent_id'] = selected_agent_id
                st.session_state['selected_agent_type'] = next(a[2] for a in agents_on_device if a[0] == selected_agent_id)
                st.rerun()
    
    elif level == "Level 3: Agent Detail":
        st.markdown('<div class="level-indicator level-3">📍 Level 3: Agent Detail Timeline</div>', unsafe_allow_html=True)
        
        # 收集所有 agents
        all_agents = []
        for gui_agent in record.agents.gui_agents:
            all_agents.append((gui_agent.agent_id, gui_agent, "gui"))
        for code_agent in record.agents.code_agents:
            all_agents.append((code_agent.agent_id, code_agent, "code"))
        
        # Agent 选择器（在当前层级可以切换）
        col1, col2 = st.columns([3, 1])
        with col1:
            agent_ids = [a[0] for a in all_agents]
            current_agent_id = st.session_state.get('selected_agent_id', agent_ids[0] if agent_ids else '')
            selected_agent_id = st.selectbox(
                "Current Agent",
                agent_ids,
                index=agent_ids.index(current_agent_id) if current_agent_id in agent_ids else 0,
                key="level3_agent_select"
            )
            st.session_state['selected_agent_id'] = selected_agent_id
            st.session_state['selected_agent_type'] = next(a[2] for a in all_agents if a[0] == selected_agent_id)
        with col2:
            if st.button("⬅️ Back to Level 2", key="level3_back"):
                st.session_state['level'] = "Level 2: Device Task"
                st.rerun()
        
        # 获取 agent 对象
        selected_agent_type = st.session_state.get('selected_agent_type', 'gui')
        agent = None
        if selected_agent_type == "gui":
            for gui_agent in record.agents.gui_agents:
                if gui_agent.agent_id == selected_agent_id:
                    agent = gui_agent
                    break
        elif selected_agent_type == "code":
            for code_agent in record.agents.code_agents:
                if code_agent.agent_id == selected_agent_id:
                    agent = code_agent
                    break
        
        if agent:
            st.markdown(f"**Task:** {agent.task}")
            st.markdown(f"**Total Rounds:** {agent.summary.total_rounds}")
            
            fig = create_level3_timeline(agent, selected_agent_type, record.metadata.start_timestamp)
            st.plotly_chart(fig, use_container_width=True)
            
            # 轮次详情
            st.markdown("---")
            round_id = st.selectbox("Select Round for Details", range(agent.summary.total_rounds), key="level3_round_select")
            display_round_details(agent, round_id)
        else:
            st.error(f"Agent {selected_agent_id} not found")

if __name__ == "__main__":
    main()

