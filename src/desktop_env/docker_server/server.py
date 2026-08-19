from flask import Flask, request, jsonify, send_file, abort  # , send_from_directory
import uuid
import logging
from desktop_env.providers.docker.provider import DockerProvider
from dataclasses import dataclass
from typing import Dict
import time
import os
logger = logging.getLogger("desktopenv.docker_server")
logger.setLevel(logging.INFO)

WAIT_TIME = 3
RETRY_INTERVAL = 1
LOCK_TIMEOUT = 10

app = Flask(__name__)

@app.route("/ping", methods=["GET"])
def read_root():
    return {"message": "Hello, World!"}

@dataclass
class Emulator:
    provider: DockerProvider
    emulator_id: str
    start_time: float = time.time()
    
    def stop_emulator(self):
        self.provider.stop_emulator(path_to_vm="./docker_vm_data/Ubuntu.qcow2")
    
    def duration(self) -> int:
        # convert to minutes
        return int((time.time() - self.start_time) / 60)
        
emulators: Dict[str, Emulator] = dict()

@app.route("/start_emulator", methods=["GET"])
def start_emulator():
    print("start_emulator")
    provider = DockerProvider(region="")
    
    provider.start_emulator(
        path_to_vm="./docker_vm_data/Ubuntu.qcow2",
        headless=True,
        os_type="linux"
    )
    # generate emulator id
    emulator_id = str(uuid.uuid4())
    print(f"emulator_id: {emulator_id}")
    emulators[emulator_id] = Emulator(
        provider=provider,
        emulator_id=emulator_id,
    )
    # execute remove_all.sh
    # os.system("bash scripts/remove_all.sh")
    return {
        "message": "Emulator started successfully",
        "code": 0,
        "data": {
            "emulator_id": emulator_id,
            "vnc_port": provider.vnc_port,
            "chromium_port": provider.chromium_port,
            "vlc_port": provider.vlc_port,
            "server_port": provider.server_port
        }
    }

@app.route("/stop_emulator", methods=["POST"])
def stop_emulator():
    emulator_id = request.json.get("emulator_id")
    print(emulators)
    if emulator_id not in emulators:
        return {"message": "Emulator not found", "code": 1}
    emulators[emulator_id].stop_emulator()
    del emulators[emulator_id]
    return {"message": "Emulator stopped successfully", "code": 0}

if __name__ == '__main__':
    app.run(debug=False, host="0.0.0.0", port=50003)