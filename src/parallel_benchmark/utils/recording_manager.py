"""
Recording Manager - 管理多个 VM 的录屏
"""

import os
import time
import requests
from typing import Dict, Optional
from desktop_env.controllers.python import PythonController


class RecordingManager:
    """管理多个虚拟机的录屏"""
    
    def __init__(self, output_dir: str):
        """
        初始化录屏管理器
        
        Args:
            output_dir: 录屏文件输出目录
        """
        self.output_dir = output_dir
        self.recordings: Dict[str, Dict] = {}  # vm_id -> recording_info
        self.recording_start_time: Optional[float] = None
        
        # 确保输出目录存在
        os.makedirs(output_dir, exist_ok=True)
    
    def start_recording(self, vm_id: str, controller: PythonController) -> Dict:
        """
        在指定 VM 上开始录屏
        
        Args:
            vm_id: VM 标识符（如 "vm1", "vm2"）
            controller: 对应的 PythonController
            
        Returns:
            录屏信息字典
        """
        if self.recording_start_time is None:
            self.recording_start_time = time.time()
        
        # 设置录屏文件路径
        recording_path = os.path.join(self.output_dir, f"{vm_id}_recording.mp4")
        
        try:
            # 使用固定的、简单的 recording_id（基于 vm_id）
            # 这样每次启动时可以清理同一个 ID 的旧录屏
            recording_id = f"recording_{vm_id}"
            
            # 尝试先停止可能存在的旧录屏
            try:
                print(f"[INFO] Attempting to clean up old recording for {vm_id}...")
                stop_response = requests.post(
                    controller.http_server + "/end_recording",
                    json={"recording_id": recording_id},
                    timeout=3
                )
                if stop_response.status_code == 200:
                    print(f"[INFO] ✓ Cleaned up old recording: {recording_id}")
                else:
                    print(f"[INFO] No old recording to clean (status: {stop_response.status_code})")
            except Exception as e:
                print(f"[INFO] No old recording found or already cleaned")
            
            # 等待一下确保清理完成
            time.sleep(0.5)
            
            # 直接调用录屏 API（使用 JSON 格式，适配服务器端的要求）
            response = requests.post(
                controller.http_server + "/start_recording",
                json={"recording_id": recording_id}
            )
            
            print(f"[DEBUG] Start recording request sent with recording_id: {recording_id}")
            print(f"[DEBUG] Response status: {response.status_code}")
            
            if response.status_code == 200:
                recording_info = {
                    "vm_id": vm_id,
                    "controller": controller,
                    "recording_path": recording_path,
                    "recording_id": recording_id,  # 保存 recording_id
                    "start_timestamp": time.time(),
                    "status": "recording"
                }
                
                self.recordings[vm_id] = recording_info
                print(f"✓ Started recording on {vm_id}: {recording_path}")
                
                return recording_info
            else:
                response_text = response.text if hasattr(response, 'text') else 'N/A'
                raise Exception(f"Failed to start recording. Status code: {response.status_code}, Response: {response_text}")
            
        except Exception as e:
            print(f"✗ Failed to start recording on {vm_id}: {e}")
            recording_info = {
                "vm_id": vm_id,
                "controller": controller,
                "recording_path": None,
                "start_timestamp": time.time(),
                "status": "failed",
                "error": str(e)
            }
            self.recordings[vm_id] = recording_info
            return recording_info
    
    def stop_recording(self, vm_id: str) -> Optional[str]:
        """
        停止指定 VM 的录屏
        
        Args:
            vm_id: VM 标识符
            
        Returns:
            录屏文件路径，如果失败返回 None
        """
        if vm_id not in self.recordings:
            print(f"Warning: No recording found for {vm_id}")
            return None
        
        recording_info = self.recordings[vm_id]
        
        if recording_info["status"] != "recording":
            print(f"Warning: {vm_id} is not recording (status: {recording_info['status']})")
            return recording_info.get("recording_path")
        
        try:
            controller = recording_info["controller"]
            recording_path = recording_info["recording_path"]
            
            # 使用 start_recording 时保存的 recording_id
            recording_id = recording_info.get("recording_id")
            if not recording_id:
                raise Exception("No recording_id found in recording_info")
            
            print(f"[DEBUG] Stopping recording with recording_id: {recording_id}")
            
            # 直接调用录屏停止 API（使用 JSON 格式）
            response = requests.post(
                controller.http_server + "/end_recording",
                json={"recording_id": recording_id}
            )
            
            print(f"[DEBUG] Stop recording response status: {response.status_code}")
            
            if response.status_code == 200:
                # 保存录屏文件
                with open(recording_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                
                recording_info["status"] = "stopped"
                recording_info["end_timestamp"] = time.time()
                recording_info["duration"] = recording_info["end_timestamp"] - recording_info["start_timestamp"]
                
                print(f"✓ Stopped recording on {vm_id} (duration: {recording_info['duration']:.2f}s)")
                print(f"  Saved to: {recording_path}")
                
                return recording_path
            else:
                raise Exception(f"Failed to stop recording. Status code: {response.status_code}")
            
        except Exception as e:
            print(f"✗ Failed to stop recording on {vm_id}: {e}")
            recording_info["status"] = "error"
            recording_info["error"] = str(e)
            return None
    
    def stop_all_recordings(self) -> Dict[str, str]:
        """
        停止所有录屏
        
        Returns:
            vm_id -> recording_path 的字典
        """
        results = {}
        
        for vm_id in list(self.recordings.keys()):
            recording_path = self.stop_recording(vm_id)
            if recording_path:
                results[vm_id] = recording_path
        
        return results
    
    def get_recording_info(self, vm_id: str) -> Optional[Dict]:
        """获取录屏信息"""
        return self.recordings.get(vm_id)
    
    def get_all_recording_info(self) -> Dict[str, Dict]:
        """获取所有录屏信息"""
        return self.recordings.copy()
    
    def get_recording_start_timestamp(self) -> Optional[float]:
        """获取录屏开始的全局时间戳"""
        return self.recording_start_time
