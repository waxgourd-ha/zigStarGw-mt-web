import os
import sys
import json
import uuid
import time
from datetime import datetime

import threading
import re
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import subprocess
import serial
import socket
from zeroconf import ServiceBrowser, Zeroconf

# 初始化配置（移除 SocketIO 相关代码）
app = Flask(__name__, static_folder='static')
CORS(app)  # 解决跨域问题

# 目录配置（使用绝对路径，避免固件地址错误）
if 'ZIGSTARGW_WEB_DIR' in os.environ:
    CURRENT_DIR = os.environ['ZIGSTARGW_WEB_DIR']
else:
    CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

if 'ZIGSTARGW_ROOT_DIR' in os.environ:
    ROOT_DIR = os.environ['ZIGSTARGW_ROOT_DIR']
else:
    ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

FIRMWARE_DIR = os.path.join(CURRENT_DIR, 'firmware')

os.makedirs(FIRMWARE_DIR, exist_ok=True)

# 固件信息持久化文件
FIRMWARE_META_FILE = os.path.join(FIRMWARE_DIR, 'firmware_meta.json')

# 加载已存在的固件信息
if os.path.exists(FIRMWARE_META_FILE):
    with open(FIRMWARE_META_FILE, 'r') as f:
        firmware_meta = json.load(f)
else:
    firmware_meta = {}  # {firmware_id: {"filename": 原始名, "size": 大小, "path": 绝对路径}}

task_store = {}

MagicNumber  = 0.138
runNumber=0
numberSize=0
thread_is_running = False
listener = None


# 加载保存的固件信息
def load_firmware_meta():
    global firmware_meta
    if os.path.exists(FIRMWARE_META_FILE):
        try:
            with open(FIRMWARE_META_FILE, 'r') as f:
                firmware_meta = json.load(f)
        except:
            firmware_meta = {}

# 保存固件信息到文件
def save_firmware_meta():
    try:
        with open(FIRMWARE_META_FILE, 'w') as f:
            json.dump(firmware_meta, f, indent=2)
    except Exception as e:
        print(f"保存固件信息失败: {str(e)}")


# 串口检测函数
def setSerialPorts():
    available_ports = []
    print(sys.platform)
    if sys.platform.startswith('win'):
        port_candidates = ['COM%s' % (i + 1) for i in range(32)]
    elif sys.platform.startswith('linux') or sys.platform.startswith('cygwin'):
        port_candidates = ['/dev/ttyUSB%s' % i for i in range(32)] + ['/dev/ttyACM%s' % i for i in range(32)]
    elif sys.platform.startswith('darwin'):
        port_candidates = ['/dev/tty.usbserial-%s' % i for i in range(32)] + ['/dev/tty.usbmodem-%s' % i for i in range(32)]
    else:
        raise EnvironmentError('不支持的操作系统')

    for port in port_candidates:
        try:
            s = serial.Serial(port)
            available_ports.append(port)
            s.close()
        except (OSError, serial.SerialException):
            continue
    return available_ports

class MyListener():
    def __init__(self):
        self.znp_list = dict()
        self.list = dict()

    def update_service(self, zeroconf, type, name):
        print("update_service, type:%s name:%s" % (type,name,))
        info = zeroconf.get_service_info(type, name)
        addr = socket.inet_ntoa(info.addresses[0])
        if addr in self.znp_list:
            self.znp_list[addr] = {
                "addr": addr,
                "port": str(info.port)
            }
            print("Update Service %s added, service info: %s" % (socket.inet_ntoa(info.addresses[0]),  str(info.port)))

    def remove_service(self, zeroconf, type, name):
        print("Service %s:%s removed" % (type,name,))
        try:
            if name in self.list:
                addr = self.list[name]["addr"]
                del self.list[name]
                del self.znp_list[addr]
        except Exception as e:
             print("Service %s %s removed fail" % (name,str(e)))


    def add_service(self, zeroconf, type, name):
        print("add_service, type:%s name:%s" % (type,name,))
        info = zeroconf.get_service_info(type, name)
        addr = socket.inet_ntoa(info.addresses[0])
        if addr not in self.znp_list:
            self.list[name] = {
                "addr": addr,
                "port": str(info.port)
            }
            self.znp_list[addr] = {
                "addr": addr,
                "port": str(info.port)
            }
            print("Add Service %s added, service info: %s" % (socket.inet_ntoa(info.addresses[0]),  str(info.port)))

# 前端页面路由
@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')

# 健康检查
@app.route('/api/healthcheck', methods=['GET'])
def get_health():
    return jsonify({"success": True, "code": 0}), 200

# port 
@app.route('/api/ports', methods=['GET'])
def get_ports():
    try:
        net_ports = listener.znp_list
        net_ports_list = [{"name": f"{v['addr']}:{v['port']}", "description": ""} for v in net_ports.values()]
        ports = setSerialPorts()
        mock_ports = [{"name": port, "description": f""} for port in ports]
        return jsonify({"success": True, "code": 0, "ports": net_ports_list + mock_ports})
    except Exception as e:
        return jsonify({"success": False, "code": 1000, "error": str(e)}), 500

# baud rates
@app.route('/api/baud_rates', methods=['GET'])
def get_baud_rates():
    return jsonify({"success": True, "code": 0, "baud_rates": [9600, 19200, 38400, 57600, 115200]})

# 删除固件API
@app.route('/api/firmware/delete/<firmware_id>', methods=['DELETE'])
def delete_firmware(firmware_id):
    try:
        if firmware_id not in firmware_meta:
            return jsonify({"success": False, "code": 1001, "error": ""}), 404
        
        # 删除文件
        firmware_path = firmware_meta[firmware_id]['path']
        if os.path.exists(firmware_path):
            os.remove(firmware_path)
        
        # 从元数据中移除
        del firmware_meta[firmware_id]
        save_firmware_meta()
        
        return jsonify({"success": True, "code": 0, "message": ""})
    except Exception as e:
        return jsonify({"success": False, "code": 1002, "error": ""}), 500

# 设为默认
@app.route('/api/firmware/default/<firmware_id>', methods=['POST'])
def set_default_firmware(firmware_id):
    try:
        if firmware_id not in firmware_meta:
            return jsonify({"success": False, "code": 1001, "error": ""}), 404
        
        for fid, meta in firmware_meta.items():
            if fid != firmware_id:
                firmware_meta[fid]['is_default'] = False
            else:
                firmware_meta[fid]['is_default'] = True  # 设置为默认
        save_firmware_meta()
        
        return jsonify({"success": True, "code": 0, "message": ""})
    except Exception as e:
        return jsonify({"success": False,  "code": 1003,  "error": ""}), 500

# 获取所有固件API
@app.route('/api/firmware/list', methods=['GET'])
def list_firmware():
    try:
        # 找到默认固件
        default_id = None
        for fid, meta in firmware_meta.items():
            if meta.get('is_default', False):
                default_id = fid
                break
                
        # 转换为列表并排序（最新上传的在前）
        firmware_list = sorted(
            [{"id": fid, **meta} for fid, meta in firmware_meta.items()],
            key=lambda x: x['upload_time'],
            reverse=True
        )
        
        return jsonify({
            "success": True,
            "code": 0,
            "firmwares": firmware_list,
            "default_id": default_id
        })
    except Exception as e:
        return jsonify({"success": False, "code": 1001, "error": ""}), 500

@app.route('/api/firmware/upload', methods=['POST'])
def upload_firmware():
    try:
        if 'firmware' not in request.files:
            return jsonify({"success": False,  "code": 1001, "error": ""}), 400
        file = request.files['firmware']
        if file.filename == '':
            return jsonify({"success": False,  "code": 1001, "error": ""}), 400
        
        allowed_ext = {'bin', 'hex'}
        ext = file.filename.rsplit('.', 1)[1].lower()
        if ext not in allowed_ext:
            return jsonify({"success": False,  "code": 1001, "error": ""}), 400
        
        # 绝对路径存储固件（避免地址错误）
        firmware_id = str(uuid.uuid4())[:8]
        firmware_filename = f"{firmware_id}.{ext}"
        firmware_path = os.path.join(FIRMWARE_DIR, firmware_filename)
        file.save(firmware_path)
        file_size = os.path.getsize(firmware_path)
        
        upload_time = datetime.now().isoformat()
        

        # 取消之前的默认固件
        for fid in firmware_meta:
            firmware_meta[fid]['is_default'] = False

        # 设置新固件为默认
        firmware_meta[firmware_id] = {
            "filename": file.filename,
            "size": file_size,
            "path": firmware_path,
            "upload_time": upload_time,
            "is_default": True  # 新上传的固件设为默认
        }

        # 保存到文件
        save_firmware_meta()

        print(f"[固件上传] 保存路径：{firmware_path}（大小：{file_size} KB）")
        return jsonify({
            "success": True,
            "code": 0,
            "firmware_id": firmware_id,
            "filename": file.filename,
            "size": file_size
        })
    except Exception as e:
        return jsonify({"success": False,  "code": 1000, "error": ""}), 500

@app.route('/api/flash/start', methods=['POST'])
def start_flash():
    global numberSize,runNumber,thread_is_running
    numberSize=0
    runNumber=0
    thread_is_running = True
    try:
        data = request.get_json()
        required_fields = ['port', 'firmware_id']
        for field in required_fields:
            if field not in data:
                return jsonify({"success": False,  "code": 1000, "error": f"缺少参数：{field}"}), 400

        firmware_id = data['firmware_id']
        if firmware_id not in firmware_meta:
            return jsonify({"success": False,  "code": 1000, "error": "固件不存在"}), 404

        # 初始化任务（先存任务再启动线程，避免轮询时找不到）
        task_id = str(uuid.uuid4())[:12]
        firmware_info = firmware_meta[firmware_id]

        task_store[task_id] = {
            "thread": None,
            "data": [],  # 存储所有进度/日志数据的数组
            "data_index": 0,  # 已返回数据的索引
            "is_stopped": False,
            "firmware_path": firmware_info['path'],
            "total_size": firmware_info['size'],
            "start_time": time.time(),
            "success": False,
        }
        ## 判断port是否为ip:port格式
        if ':' in data['port']:
            ip, port = data['port'].split(':', 1)
            # 启动网络烧录线程
            thread = threading.Thread(
                target=run_flash_task,
                args=(task_id, ip,firmware_info['path'], port, True),
                daemon=True
            )
        else:
            # 启动本地烧录线程
            thread = threading.Thread(
                target=run_flash_task,
                args=(task_id, data['port'], firmware_info['path'], 50000, False),
                daemon=True
            )
        thread.start()
        task_store[task_id]['thread'] = thread
        
        return jsonify({
            "success": True,
            "task_id": task_id,
            "code": 0,
            "message": "烧录任务已启动"
        })
    except Exception as e:
        print(f"[错误] {str(e)}")
        thread_is_running = False
        return jsonify({"success": False, "code": 1000, "error": str(e)}), 500

@app.route('/api/flash/stop', methods=['POST'])
def stop_flash():
    try:
        data = request.get_json()
        if 'task_id' not in data:
            return jsonify({"success": False, "code": 1000, "error": "缺少任务ID"}), 400

        task_id = data['task_id']
        if task_id not in task_store:
            return jsonify({"success": False, "code": 1000, "error": "任务不存在"}), 404

        task_store[task_id]['is_stopped'] = True
        return jsonify({"success": True, "code": 1000, "message": "烧录任务已停止"})
    except Exception as e:
        return jsonify({"success": False, "code": 1000, "error": str(e)}), 500

# 进度查询API
@app.route('/api/flash/progress/<task_id>', methods=['GET'])
def get_flash_progress(task_id):

    try:
        # 1. 检查任务是否存在
        if task_id not in task_store:
            return jsonify({
                "success": False,  # 接口查询成功（只是任务不存在）
                "code": 1005,
                "task_id": task_id,
                "is_running": False,
                "completed": False,
                "error": "任务不存在或已结束",
            })
        
        task = task_store[task_id]
        result = {
            ## 布尔值，标识任务最终是否成功（仅任务完成后有效）。true 表示任务成功完成，false 表示任务失败或未完成。
            "success": False,
            "code": 0,
            ## 字符串，烧录任务的唯一标识 ID（用于跟踪和查询任务状态）。
            "task_id": task_id,
            ## 布尔值，标识任务是否正在运行。true 表示烧录过程正在进行，false 表示任务已完成或未开始。
            ##"is_running": task['thread'].is_alive() and not task['is_stopped'] if task.get('thread') else False,
            "is_running": thread_is_running,
            ## 布尔值，标识任务是否被手动停止。false 表示任务未被停止，true 表示任务已被手动停止。
            "is_stopped": task.get('is_stopped', False),
            ## 整数，当前烧录进度百分比（0-100）。当前 0 表示进度未更新（可能因工具未输出百分比）。
            "progress": runNumber,

            "log": [],
            ## 布尔值，标识烧录任务是否已完成。true 表示任务已完成，false 表示任务正在进行中，未结束。
            "completed": False, 
            ## 整数，任务已运行的时间（单位：秒）。当前值 5 表示任务已运行 5 秒。
            "duration": int(time.time() - task.get('start_time', time.time()))
        }

        current_data = task['data']
        data_index = task['data_index']
        new_data = current_data[data_index:]  # 本次新增的数据（未被前端读取过）
        
        # 更新已返回索引（下次从新位置开始读取）
        task['data_index'] = len(current_data)

        # 提取新增数据中的日志和最新进度
        if new_data:
            # 日志直接返回新增部分
            result['log'] = new_data
            
            # 从新增数据中获取最新的进度和大小（取最后一条有进度的记录）
            for item in reversed(new_data):
                if 'progress' in item:
                    result['progress'] = item['progress']
                if item.get('completed', False):
                    result['completed'] = True
                if item.get('success', False):
                    result['success'] = True
                # 找到最后一条有进度的记录即可跳出
                if 'progress' in item or item.get('completed', False):
                    break

        return jsonify(result)
    
    except Exception as e:
        # signal.alarm(0)  # 取消超时
        print(f"[接口异常] {str(e)}")
        return jsonify({
            "success": True,
            "code": 1006,
            "task_id": task_id,
            "error": "接口处理异常",
            "log": []
        })

# usb烧录逻辑
def run_flash_task(task_id, addr, firmware_path, baud_rate,net):
    global runNumber,numberSize,thread_is_running,MagicNumber
    success = False
    start_time = time.time()
    process = None
    cc2538_script_path = os.path.join(ROOT_DIR, 'cc2538_bsl.py')
    
    try:
        # 验证固件和脚本路径
        if not os.path.exists(firmware_path):
            error_msg = f"固件文件不存在！路径：{firmware_path}"
            print(f"[错误] {error_msg}")
            task_store[task_id]['data'].append({
                "log": error_msg,
                "code": 1007,
                "log_type": "error",
                "completed": True,
                "success": False,
                "duration": int(time.time() - start_time)
            })
            return
        
        if not os.path.exists(cc2538_script_path):
            error_msg = "烧录程序不存在！"
            print(f"[错误] {error_msg}")
            task_store[task_id]['data'].append({
                "log": error_msg,
                "code": 1008,
                "log_type": "error",
                "completed": True,
                "success": False,
                "duration": int(time.time() - start_time)
            })
            return
        
        if net:
             ## 开启设备bsl模式
            try:

                os.system(f"curl http://{addr}/cmdZigBSL")
                task_store[task_id]['data'].append({
                    "log": "...",
                    "code": 1014,
                    "log_type": "info",
                    "completed": False
                })
            except Exception as e:
                error_msg = f"{addr}"
                print(f"[错误] {str(e)}")
                task_store[task_id]['data'].append({
                    "log": error_msg,
                    "code": 1015,
                    "log_type": "error",
                    "completed": True,
                    "success": False,
                    "duration": int(time.time() - start_time)
                })
                return

            # 构造烧录命令
            cmd = [
                sys.executable,
                cc2538_script_path,
                "-p",
                f"socket://{addr}:{baud_rate}",
                "-e", "-w", "-v",
                firmware_path
            ]
        else:
            # 构造烧录命令
            cmd = [
                sys.executable,
                cc2538_script_path,
                "-p", addr,
                "-b", str(baud_rate),
                "-e", "-w", "-v",
                firmware_path
            ]
        print(cmd)

        # 推送启动日志
        task_store[task_id]['data'].append({
            "log": "",
            "code": 1009,
            "log_type": "info",
            "progress": numberSize,
            "completed": False
        })

        # 启动子进程
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )

        # 解析输出
        progress_re = re.compile(r"Write (\d+) bytes at (0x[0-9A-Fa-f]+)", re.IGNORECASE)
        status_re = re.compile(r"(Erasing|Writing|Verifying|CRC32)", re.IGNORECASE)
        msgs = 0

        while True:
            # 检查是否被停止
            if task_store[task_id]["is_stopped"]:
                process.terminate()
                task_store[task_id]['data'].append({
                    "log": "",
                    "code": 1010,
                    "log_type": "warning",
                    "completed": True,
                    "success": False,
                    "duration": int(time.time() - start_time)
                })
                break

            # 读取脚本输出
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break

            if line:
                msgs += 1
                numberSize = int(msgs * MagicNumber)
                if numberSize >100 :
                    numberSize = 100
                if numberSize == runNumber:
                    continue
                runNumber = numberSize

                line = line.strip()
                line = re.sub(r'\x1b\[[0-9;]*m', '', line)  # 过滤颜色码
                # 推送原始日志
                log_type = "info"
                if "error" in line.lower() or "failed" in line.lower():
                    log_type = "error"
                elif "success" in line.lower() or "done" in line.lower():
                    log_type = "success"
                elif "warning" in line.lower():
                    log_type = "warning"
                if log_type != "info":
                    task_store[task_id]['data'].append({
                        "log": f"[cc2538_bsl] {line}",
                        "code": 0,
                        "log_type": log_type,
                        "completed": False
                    })

                # 解析进度
                progress_match = progress_re.search(line)
                if progress_match:
                    try:
                        task_store[task_id]['data'].append({
                            "progress": numberSize,
                            "code": 0,
                            "log": f"烧录进度：{numberSize}%",
                            "log_type": "progress",
                            "completed": False
                        })
                    except:
                        print(f"无法解析进度: {line}")

                # 解析关键状态（用数组append）
                status_match = status_re.search(line)
                if status_match:
                    status = status_match.group(1).capitalize()
                    task_store[task_id]['data'].append({
                        "log": f"{status}...",
                        "code": 1017,
                        "log_type": "info",
                        "completed": False
                    })

        # 检查脚本执行结果
        exit_code = process.poll()
        print(f"[烧录结束] 退出码：{exit_code}")
        if exit_code == 0:
            success = True
            task_store[task_id]['data'].append({
                "log": "",
                "log_type": "success",
                "code": 0,
                "completed": True,
                "success": True,
                "duration": int(time.time() - start_time)
            })
        else:
            task_store[task_id]['data'].append({
                "log": f"{exit_code}",
                "log_type": "error",
                "code": 1012,
                "completed": True,
                "success": False,
                "duration": int(time.time() - start_time)
            })

    except Exception as e:
        print(f"[错误] {str(e)}")
        task_store[task_id]['data'].append({
            "log": f"{str(e)}",
            "code": 1013,
            "log_type": "error",
            "completed": True,
            "success": False,
            "duration": int(time.time() - start_time)
        })
    finally:
        task_store[task_id]['success'] = success
        thread_is_running = False
        import threading
        def clean_task():
            if task_id in task_store:
                del task_store[task_id]
        print(f"[任务清理创建 任务 {task_id} 将在30秒后清理]")
        # 30秒后执行清理
        threading.Timer(10, clean_task).start()

# 启动服务
if __name__ == '__main__':
    zeroconf = Zeroconf()
    listener = MyListener()
    browser1 = ServiceBrowser(zeroconf, "_zig_star_gw._tcp.local.", listener)
    browser2 = ServiceBrowser(zeroconf, "_zigstar_gw._tcp.local.", listener)
    browser3 = ServiceBrowser(zeroconf, "_uzg-01._tcp.local.", listener)
    app.run(
        host='0.0.0.0',
        port=5550,
        debug=False,
        use_reloader=False  # 禁用自动重载，避免进程冲突
    )