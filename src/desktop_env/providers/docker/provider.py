import logging
import os
import platform
import time
import docker
from docker.types import Mount
import psutil
import requests
from filelock import FileLock
from pathlib import Path

from desktop_env.providers.base import Provider

logger = logging.getLogger("desktopenv.providers.docker.DockerProvider")
logger.setLevel(logging.INFO)

WAIT_TIME = 3
RETRY_INTERVAL = 1
LOCK_TIMEOUT = 10
DEFAULT_DOCKER_IMAGE = os.environ.get("OSWORLD_DOCKER_IMAGE", "happysixd/osworld-docker")
DEFAULT_SHARED_DIR = os.environ.get(
    "OSWORLD_SHARED_DIR",
    str(Path(__file__).resolve().parents[3] / "shared_files")
)
CONTAINER_SHARED_PATH = os.environ.get("OSWORLD_CONTAINER_SHARED_PATH", "/shared")
SHARE_MODE = os.environ.get("OSWORLD_SHARE_MODE", "bind").lower()
if SHARE_MODE not in {"bind", "nfs"}:
    logger.warning("Unknown OSWORLD_SHARE_MODE '%s', fallback to 'bind'", SHARE_MODE)
    SHARE_MODE = "bind"


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


NFS_EXPORT_PATH = os.environ.get("OSWORLD_NFS_EXPORT", CONTAINER_SHARED_PATH)
NFS_PORT = _env_int("OSWORLD_NFS_PORT", 2049)
NFS_MOUNT_PORT = _env_int("OSWORLD_NFS_MOUNT_PORT", 20048)


class PortAllocationError(Exception):
    pass


class DockerProvider(Provider):
    def __init__(self, region: str):
        self.client = docker.from_env()
        self.server_port = None
        self.vnc_port = None
        self.chromium_port = None
        self.vlc_port = None
        self.container = None
        self.environment = {"DISK_SIZE": "4G", "RAM_SIZE": "4G", "CPU_CORES": "4"}  # Modify if needed

        temp_dir = Path(os.getenv('TEMP') if platform.system() == 'Windows' else '/tmp')
        self.lock_file = temp_dir / "docker_port_allocation.lck"
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)

    def _get_used_ports(self):
        """Get all currently used ports (both system and Docker)."""
        # Get system ports
        system_ports = set(conn.laddr.port for conn in psutil.net_connections())
        
        # Get Docker container ports
        docker_ports = set()
        for container in self.client.containers.list():
            ports = container.attrs['NetworkSettings']['Ports']
            if ports:
                for port_mappings in ports.values():
                    if port_mappings:
                        docker_ports.update(int(p['HostPort']) for p in port_mappings)
        
        return system_ports | docker_ports

    def _get_available_port(self, start_port: int) -> int:
        """Find next available port starting from start_port."""
        used_ports = self._get_used_ports()
        port = start_port
        while port < 65354:
            if port not in used_ports:
                return port
            port += 1
        raise PortAllocationError(f"No available ports found starting from {start_port}")

    def _wait_for_vm_ready(self, timeout: int = 300):
        """Wait for VM to be ready by checking screenshot endpoint."""
        start_time = time.time()
        
        def check_screenshot():
            try:
                response = requests.get(
                    f"http://localhost:{self.server_port}/screenshot",
                    timeout=(10, 10)
                )
                return response.status_code == 200
            except Exception:
                return False

        while time.time() - start_time < timeout:
            if check_screenshot():
                return True
            logger.info("Checking if virtual machine is ready...")
            time.sleep(RETRY_INTERVAL)
        
        raise TimeoutError("VM failed to become ready within timeout period")

    def start_emulator(self, path_to_vm: str, headless: bool, os_type: str):
        # Use a single lock for all port allocation and container startup
        lock = FileLock(str(self.lock_file), timeout=LOCK_TIMEOUT)
        logger.info(f"call start_emulator {path_to_vm} {headless} {os_type}")
        print(f"call start_emulator {path_to_vm} {headless} {os_type}")
        logger.info(f"Using share mode: {SHARE_MODE}")
        print(f"Using share mode: {SHARE_MODE}")
        shared_dir_path = Path(DEFAULT_SHARED_DIR).expanduser()
        shared_dir_path.mkdir(parents=True, exist_ok=True)
        try:
            with lock:
                # Allocate all required ports
                self.vnc_port = self._get_available_port(8006)
                self.server_port = self._get_available_port(5000)
                self.chromium_port = self._get_available_port(9222)
                self.vlc_port = self._get_available_port(8080)

                # Define additional volumes to mount
                data_dir = Path("data").resolve()
                vm_image_path = Path(path_to_vm).resolve()
                if not vm_image_path.exists():
                    raise FileNotFoundError(f"Missing VM image: {vm_image_path}")

                mounts = [
                    Mount(
                        target="/System.qcow2",
                        source=str(vm_image_path),
                        type="bind",
                        read_only=True,
                    ),
                    Mount(
                        target=CONTAINER_SHARED_PATH,
                        source=str(shared_dir_path),
                        type="bind",
                        read_only=False,
                    ),
                ]

                if data_dir.exists():
                    mounts.append(
                        Mount(
                            target="/data_from_host",
                            source=str(data_dir),
                            type="bind",
                            read_only=False,
                        )
                    )

                print(os.path.abspath(path_to_vm))
                print(str(shared_dir_path))
                self.container = self.client.containers.run(
                    DEFAULT_DOCKER_IMAGE,
                    environment=self.environment,
                    cap_add=["NET_ADMIN"],
                    devices=["/dev/kvm"],
                    mounts=mounts,
                    ports={
                        8006: self.vnc_port,
                        5000: self.server_port,
                        9222: self.chromium_port,
                        8080: self.vlc_port
                    }, # type: ignore
                    detach=True,
                    user="root",
                ) # type: ignore
                exec_result = self.container.exec_run(f"ls {CONTAINER_SHARED_PATH}")
                print(exec_result.output.decode())
                print(self.container.id)
                print('container_started')
                if SHARE_MODE == "nfs":
                    self._start_nfs_server()
                try:
                    self.container.reload()
                    logger.info(f"Container {self.container.short_id} status: {self.container.status}")
                    logs = self.container.logs(tail=200)
                    if logs:
                        logger.info("Container logs (tail):\n%s", logs.decode(errors="ignore"))
                except Exception as log_err:
                    logger.warning(f"Failed to read container logs: {log_err}")
            logger.info(f"Started container with ports - VNC: {self.vnc_port}, "
                       f"Server: {self.server_port}, Chrome: {self.chromium_port}, VLC: {self.vlc_port}")

            # Wait for VM to be ready
            self._wait_for_vm_ready()

        except Exception as e:
            # Clean up if anything goes wrong
            if self.container:
                try:
                    logs = self.container.logs(tail=200)
                    if logs:
                        logger.error("Container logs on failure:\n%s", logs.decode(errors="ignore"))
                except Exception as log_err:
                    logger.warning(f"Failed to read container logs: {log_err}")
                try:
                    self.container.stop()
                    self.container.remove()
                except:
                    pass
            raise e

    def get_ip_address(self, path_to_vm: str) -> str:
        if not all([self.server_port, self.chromium_port, self.vnc_port, self.vlc_port]):
            raise RuntimeError("VM not started - ports not allocated")
        return f"localhost:{self.server_port}:{self.chromium_port}:{self.vnc_port}:{self.vlc_port}"

    def save_state(self, path_to_vm: str, snapshot_name: str):
        raise NotImplementedError("Snapshots not available for Docker provider")

    def revert_to_snapshot(self, path_to_vm: str, snapshot_name: str):
        self.stop_emulator(path_to_vm)

    def stop_emulator(self, path_to_vm: str):
        if self.container:
            logger.info("Stopping VM...")
            try:
                self.container.stop()
                self.container.remove()
                time.sleep(WAIT_TIME)
            except Exception as e:
                logger.error(f"Error stopping container: {e}")
            finally:
                self.container = None
                self.server_port = None
                self.vnc_port = None
                self.chromium_port = None
                self.vlc_port = None

    def _start_nfs_server(self) -> None:
        """Start a simple NFS server inside the container to serve the shared directory."""
        if not self.container:
            raise RuntimeError("Container not available for NFS setup")

        setup_script = f"""bash -lc '
set -e
export DEBIAN_FRONTEND=noninteractive
if ! command -v unfsd >/dev/null 2>&1; then
    apt-get update
    apt-get install -y --no-install-recommends unfs3 rpcbind
fi
mkdir -p /var/lib/nfs
echo "{NFS_EXPORT_PATH} *(rw,sync,no_subtree_check,no_root_squash)" > /tmp/osworld_exports
rpcbind || true
pkill unfsd || true
unfsd -t -e /tmp/osworld_exports -n {NFS_PORT} -m {NFS_MOUNT_PORT} >/tmp/unfsd.log 2>&1 &
sleep 1
'"""
        result = self.container.exec_run(setup_script)
        if result.exit_code != 0:
            output = result.output.decode(errors="ignore") if result.output else ""
            logger.error("Failed to start NFS server: %s", output)
            raise RuntimeError(f"Failed to start NFS server inside container: {output}")

        logger.info(
            "NFS server exported %s on 10.0.2.2 with ports nfs=%d mountd=%d. "
            "Guest may mount with: sudo mkdir -p /mnt/shared && "
            "sudo mount -t nfs -o vers=3,proto=tcp,port=%d,mountport=%d,nolock 10.0.2.2:%s /mnt/shared",
            NFS_EXPORT_PATH,
            NFS_PORT,
            NFS_MOUNT_PORT,
            NFS_PORT,
            NFS_MOUNT_PORT,
            NFS_EXPORT_PATH,
        )
